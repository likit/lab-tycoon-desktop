"""Transactional conversion into normal LIS records using an isolated database."""
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select, func, inspect
from sqlalchemy.orm import Session

from app.system.models import (Base, Customer, LabOrder, LabOrderItem, Test, Specimens, TestMethod,
                               SimulationRun, SimulationOrder, SimulationPatient, SimulationLISImport, SimulationLISBatchOrder,
                               ensure_db_schema, LISDemonstrationOrder)
from app.system.simulation_lis import import_simulation_orders, SimulationLISOrderSource


class SimulationLISImportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f'sqlite:///{Path(self.directory.name) / "demo.db"}')
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.execute(Specimens.__table__.insert().values(id=1, label='Serum'))
            connection.execute(TestMethod.__table__.insert().values(id=1, method='Demo method'))
            for index, code in enumerate(('GLU', 'ALT'), 1):
                connection.execute(Test.__table__.insert().values(id=index, code=code, label=code,
                    tmlt_name=code, loinc_no=f'DEMO-{index}', scale='Quantitative', unit='mg/dL',
                    order_type='single', specimens_id=1, method_id=1, active=True))
        self.add_run('run-a', ['GLU', 'ALT'])

    def add_run(self, run_id, codes):
        with Session(self.engine) as session, session.begin():
            run = SimulationRun(id=run_id, project_id='project', scenario_id='scenario',
                scenario_name='Demo', workflow_name='Workflow', started_at=datetime(2026, 1, 1),
                requested_orders=2)
            patient = SimulationPatient(patient_id='P1', hn='P1', fullname='Synthetic Patient')
            run.patients.append(patient)
            for index in range(2):
                run.orders.append(SimulationOrder(order_id=f'O{index}', patient_id='P1', patient=patient,
                    ordered_at=datetime(2026, 1, 1, 9, index), priority='urgent' if index == 0 else 'routine',
                    test_codes_json=json.dumps(codes), metadata_json='{}'))
            session.add(run)

    def convert(self, run='run-a', **kwargs):
        return import_simulation_orders(run, database_engine=self.engine, **kwargs)

    def count(self, model):
        with Session(self.engine) as session:
            return session.scalar(select(func.count()).select_from(model))

    def test_conversion_creates_shared_synthetic_customer_and_pending_lis_items(self):
        summary = self.convert()
        self.assertEqual((summary.created, summary.skipped), (2, 0))
        self.assertEqual(self.count(Customer), 1)
        self.assertEqual(self.count(LabOrderItem), 4)
        self.assertEqual(self.count(SimulationOrder), 2)
        source = SimulationLISOrderSource('run-a', database_engine=self.engine)
        orders = source.list_orders()
        self.assertEqual([o.priority for o in orders], ['urgent', 'routine'])
        self.assertTrue(orders[0].customer.fullname.startswith('[DEMO]'))
        self.assertEqual(orders[0].order_datetime, datetime(2026, 1, 1, 9))
        self.assertEqual({item.test.code for item in orders[0].order_items}, {'GLU', 'ALT'})
        self.assertTrue(all(order.received_at is None for order in orders))
        self.assertTrue(all(item._value is None and item.reported_at is None and item.approved_at is None
                            for order in orders for item in order.order_items))

    def test_reimport_creates_fresh_pending_batch_and_preserves_previous_edits(self):
        first_batch = self.convert()
        with Session(self.engine) as session, session.begin():
            order = session.scalar(select(LabOrder).order_by(LabOrder.id))
            order.received_at = datetime(2026, 1, 2)
            order.order_items[0]._value = '101'
            order.order_items[0].reported_at = datetime(2026, 1, 2)
            order.order_items[0].approved_at = datetime(2026, 1, 3)
            order_id = order.id
        summary = self.convert()
        self.assertEqual((summary.created, summary.skipped), (2, 0))
        self.assertNotEqual(summary.batch_id, first_batch.batch_id)
        self.assertEqual(self.count(LabOrder), 4)
        self.assertEqual(self.count(Customer), 1)
        with Session(self.engine) as session:
            order = session.get(LabOrder, order_id)
            self.assertEqual(order.received_at, datetime(2026, 1, 2))
            self.assertEqual(order.order_items[0]._value, '101')
            self.assertEqual(order.order_items[0].approved_at, datetime(2026, 1, 3))
        fresh = SimulationLISOrderSource('run-a', batch_id=summary.batch_id, database_engine=self.engine).list_orders()
        self.assertEqual(len(fresh), 2)
        self.assertTrue(all(order.received_at is None and order.approved_at is None for order in fresh))
        self.assertTrue(all(item._value is None and item.finished_at is None and item.reported_at is None
                            and item.approved_at is None for order in fresh for item in order.order_items))
        self.assertNotIn(order_id, [order.id for order in fresh])

    def test_unknown_code_requires_explicit_mapping_and_failure_is_atomic(self):
        self.add_run('unknown', ['GLU', 'SIM-TEST'])
        with self.assertRaisesRegex(ValueError, 'Map these codes'):
            self.convert('unknown')
        self.assertEqual(self.count(LabOrder), 0)
        self.assertEqual(self.count(Customer), 0)
        self.assertEqual(self.count(SimulationLISImport), 0)
        self.assertEqual(self.count(SimulationLISBatchOrder), 0)
        summary = self.convert('unknown', test_mapping={'SIM-TEST': 'ALT'})
        self.assertEqual(summary.created, 2)

    def test_inactive_test_cannot_be_used(self):
        with self.engine.begin() as connection:
            connection.execute(Test.__table__.update().where(Test.code == 'GLU').values(active=False))
        with self.assertRaises(ValueError):
            self.convert()
        self.assertEqual(self.count(Customer), 0)

    def test_same_patient_id_in_different_runs_is_not_merged_and_source_is_scoped(self):
        self.convert()
        self.add_run('run-b', ['GLU'])
        self.convert('run-b')
        self.assertEqual(self.count(Customer), 2)
        source = SimulationLISOrderSource('run-b', database_engine=self.engine)
        self.assertEqual(source.count(), 2)
        first_id = SimulationLISOrderSource('run-a', database_engine=self.engine).list_orders()[0].id
        self.assertIsNone(source.get_order(first_id))
        self.assertEqual({item.test.code for order in source.list_orders() for item in order.order_items}, {'GLU'})

    def test_invalid_run_or_invalid_test_payload_does_not_import(self):
        with self.assertRaises(ValueError):
            self.convert('missing')
        with self.engine.begin() as connection:
            connection.execute(SimulationOrder.__table__.update().values(test_codes_json='{}'))
        with self.assertRaises(ValueError):
            self.convert()
        self.assertEqual(self.count(LabOrder), 0)

    def test_existing_database_gets_import_provenance_table(self):
        SimulationLISBatchOrder.__table__.drop(self.engine)
        ensure_db_schema(self.engine)
        self.assertIn('simulation_lis_batch_orders', inspect(self.engine).get_table_names())
        self.convert()
        self.assertEqual(self.count(SimulationLISBatchOrder), 2)

    def test_legacy_imports_remain_accessible_and_do_not_block_new_batches(self):
        first = self.convert()
        with self.engine.begin() as connection:
            mappings = connection.execute(select(SimulationLISBatchOrder.__table__)).mappings().all()
            connection.execute(SimulationLISBatchOrder.__table__.delete())
            connection.execute(SimulationLISImport.__table__.insert(), [
                dict(simulation_order_id=row['simulation_order_id'], lis_order_id=row['lis_order_id'],
                     imported_at=row['imported_at']) for row in mappings])
        fresh = self.convert()
        self.assertNotEqual(first.batch_id, fresh.batch_id)
        source = SimulationLISOrderSource('run-a', batch_id=fresh.batch_id, database_engine=self.engine)
        self.assertEqual(source.count(), 2)
        legacy_id = mappings[0]['lis_order_id']
        self.assertIsNone(source.get_order(legacy_id))
        all_batches = SimulationLISOrderSource('run-a', database_engine=self.engine)
        self.assertEqual(all_batches.count(), 4)
        self.assertIsNotNone(all_batches.get_order(legacy_id))

    def test_conversion_dialog_requires_signin(self):
        from unittest.mock import patch
        from app.system.simulation_lis import choose_simulation_source
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=False), \
             patch('app.auth.windows.sg.popup_error'), \
             patch('app.system.simulation_lis.Session') as database:
            self.assertIsNone(choose_simulation_source())
        database.assert_not_called()

    def scenario_source(self, codes=None):
        from app.system.model import Workflow
        from app.system.storage import WorkflowDocument
        from app.system.lis_analysis import AnalysisWorkflowChoice
        from app.system.simulation_lis import ScenarioLISOrderSource
        workflow = Workflow()
        adapter = workflow.create_node('Orders', kind='order_adapter',
            metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
        workflow.create_connected_node(adapter.node_id, 'Analyzer', 60,
            metadata={'node_type': 'automated analyzer', 'throughput': 60,
                      'analyzer_tests': codes if codes is not None else ['GLU', 'ALT']})
        workflow.active_scenario.parameters['interarrival_time'] = 1 / 60
        document = WorkflowDocument('workflow', 'Teaching workflow', workflow)
        choice = AnalysisWorkflowChoice('saved:workflow', 'Teaching workflow', document)
        return ScenarioLISOrderSource(choice, workflow.active_scenario.scenario_id, database_engine=self.engine)

    def test_dialog_selects_only_scenario_without_importing_or_generating(self):
        from unittest.mock import patch, Mock
        from app.system.simulation_lis import choose_simulation_source
        expected = self.scenario_source()
        chooser = Mock()
        label = f'{expected.scenario.name} — {expected.choice.label}'
        chooser.read.return_value = ('Select', {'scenario': label})
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as auth_database, \
             patch('app.system.simulation_lis.engine', self.engine), \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[expected.choice]), \
             patch('app.ui_styles.create_window', return_value=chooser) as create:
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            source = choose_simulation_source()
        self.assertEqual(source.scenario_id, expected.scenario_id)
        self.assertIsNone(source.batch_id)
        self.assertEqual(source.count(), 0)
        self.assertEqual(self.count(LabOrder), 0)
        create.assert_called_once()
        chooser.close.assert_called_once()

    def test_scenario_generation_uses_real_selected_tests_without_simulation_run(self):
        from app.system.simulation_lis import generate_scenario_lis_orders
        source = self.scenario_source()
        before = self.count(SimulationRun)
        generated = generate_scenario_lis_orders(source, count=3)
        orders = generated.list_orders()
        self.assertEqual(len(orders), 3)
        self.assertEqual(self.count(SimulationRun), before)
        self.assertEqual(self.count(LISDemonstrationOrder), 3)
        self.assertEqual({item.test.code for order in orders for item in order.order_items}, {'GLU', 'ALT'})
        self.assertTrue(all(order.received_at is None and order.approved_at is None for order in orders))
        self.assertTrue(all(item.finished_at is None and item._value is None for order in orders for item in order.order_items))
        self.assertEqual((orders[1].order_datetime - orders[0].order_datetime).total_seconds(), 60)

    def test_unrestricted_analyzer_generates_catalogue_tests_instead_of_placeholder(self):
        from app.system.simulation_lis import generate_scenario_lis_orders
        generated = generate_scenario_lis_orders(self.scenario_source([]), count=2)
        self.assertEqual([order.order_items[0].test.code for order in generated.list_orders()], ['ALT', 'GLU'])

    def test_each_scenario_load_creates_fresh_batch_and_keeps_previous_results(self):
        from app.system.simulation_lis import generate_scenario_lis_orders
        first = generate_scenario_lis_orders(self.scenario_source(), count=2)
        first_id = first.list_orders()[0].id
        with Session(self.engine) as session, session.begin():
            item = session.get(LabOrder, first_id).order_items[0]
            item._value = '101'
            item.approved_at = datetime(2026, 1, 3)
        second = generate_scenario_lis_orders(first, count=2)
        self.assertNotEqual(first.batch_id, second.batch_id)
        self.assertEqual(second.count(), 4)
        self.assertEqual(self.count(LabOrder), 4)
        self.assertIsNotNone(second.get_order(first_id))
        with Session(self.engine) as session:
            new_ids = set(session.scalars(select(LISDemonstrationOrder.lis_order_id)
                          .where(LISDemonstrationOrder.batch_id == second.batch_id)))
        self.assertEqual(len(new_ids), 2)
        self.assertTrue(all(item._value is None for order in second.list_orders() if order.id in new_ids
                            for item in order.order_items))
        self.assertEqual(first.get_order(first_id).order_items[0]._value, '101')
        from app.system.simulation_lis import ScenarioLISOrderSource
        reopened = ScenarioLISOrderSource(first.choice, first.scenario_id, database_engine=self.engine)
        self.assertEqual(reopened.count(), 4)

    def test_invalid_scenario_test_selection_creates_nothing(self):
        from app.system.simulation_lis import generate_scenario_lis_orders
        with self.assertRaisesRegex(ValueError, 'inactive or missing'):
            generate_scenario_lis_orders(self.scenario_source(['SIM-TEST']), count=2)
        self.assertEqual(self.count(LabOrder), 0)
        self.assertEqual(self.count(Customer), 0)

    def test_cancelling_source_selection_creates_nothing(self):
        from unittest.mock import patch, Mock
        from app.system.simulation_lis import choose_simulation_source
        chooser = Mock()
        chooser.read.return_value = ('Cancel', {})
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as auth_database, \
             patch('app.system.simulation_lis.engine', self.engine), \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[self.scenario_source().choice]), \
             patch('app.ui_styles.create_window', return_value=chooser):
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            self.assertIsNone(choose_simulation_source())
        self.assertEqual(self.count(LabOrder), 0)
        self.assertEqual(self.count(Customer), 0)
