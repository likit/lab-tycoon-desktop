"""LIS analysis executes the actual workflow, with receipt-aware entry and atomic writes."""
from datetime import datetime, timedelta
import json
import unittest

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.system.model import Workflow
from app.system.storage import WorkflowDocument
from app.system.models import LabOrder, LabOrderItem, LISWorkflowAnalysis, User, UserRole
from app.system.lis_analysis import analyze_lis_items


class LISAnalysisTests(unittest.TestCase):
    def setUp(self):
        from tests.test_simulation_lis import SimulationLISImportTests
        fixture = SimulationLISImportTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.convert()
        self.engine = fixture.engine
        with self.engine.begin() as connection:
            connection.execute(User.__table__.insert().values(id=1, username='analyst',
                hashed_password='isolated-test-identity', firstname='Demo', lastname='Operator', active=True))
        with Session(self.engine) as session, session.begin():
            operator = session.get(User, 1)
            operator.roles.append(UserRole(role_need='reporter'))
        with Session(self.engine) as session:
            self.ids = list(session.scalars(select(LabOrderItem.id).order_by(LabOrderItem.id)))
        workflow = Workflow()
        source = workflow.create_node('Orders', kind='order_adapter',
            metadata={'node_type': 'order adapter', 'adapter_type': 'simulation'})
        reception = workflow.create_connected_node(source.node_id, 'Reception', 60,
            metadata={'node_type': 'specimen registration', 'throughput': 60, 'capacity': 1})
        analyzer = workflow.create_connected_node(reception.node_id, 'Analyzer', 30,
            metadata={'node_type': 'automated analyzer', 'throughput': 30, 'capacity': 1,
                      'required_staff': 1, 'analyzer_tests': ['GLU', 'ALT']})
        report = workflow.create_connected_node(analyzer.node_id, 'Reporting', 60,
            metadata={'node_type': 'result reporting', 'throughput': 60})
        workflow.create_connected_node(report.node_id, 'Endpoint', 0, kind='equipment',
            metadata={'terminal_output': True, 'equipment_id': 'REPORT'})
        workflow.active_scenario.parameters.update(staff_capacity=1, system_variation=0, human_variation=0)
        self.document = WorkflowDocument('test', 'Active workflow', workflow)
        self.reception, self.analyzer = reception, analyzer

    def analyze(self, ids=None):
        return analyze_lis_items(ids or self.ids, workflow_document=self.document,
            operator_username='analyst', database_engine=self.engine, random_seed=1)

    def add_validation(self, *, grant_role=True):
        workflow = self.document.workflow
        report = next(node for node in workflow.graph.iter_nodes() if node.widget.node_type == 'result reporting')
        endpoint = next(node for node in workflow.graph.iter_nodes() if node.metadata.get('terminal_output'))
        workflow.graph.disconnect_nodes(report.node_id, endpoint.node_id)
        validation = workflow.create_connected_node(report.node_id, 'Validation', 120,
            metadata={'node_type': 'result validating', 'throughput': 120})
        workflow.graph.connect_nodes(validation.node_id, endpoint.node_id)
        if grant_role:
            with Session(self.engine) as session, session.begin():
                operator = session.get(User, 1)
                operator.roles.append(UserRole(role_need='approver'))
        return report, validation, endpoint

    def test_reporting_and_validation_save_event_times_and_operator(self):
        self.add_validation()
        summary = self.analyze([self.ids[0]])
        with Session(self.engine) as session:
            item = session.get(LabOrderItem, self.ids[0])
            self.assertEqual(item.reported_at - item.finished_at, timedelta(minutes=1))
            self.assertEqual(item.approved_at - item.reported_at, timedelta(seconds=30))
            self.assertEqual((item.reporter_id, item.approver_id, item.updater_id), (1, 1, 1))
            self.assertEqual(item.updated_at, item.approved_at)
            self.assertIsNone(item.order.approved_at)
            audit = session.get(LISWorkflowAnalysis, summary.run_id)
            stages = json.loads(audit.trace_json)[0]['stage_events']
            self.assertEqual([stage['role'] for stage in stages], ['reporter', 'approver'])

    def test_order_is_released_only_after_all_active_items_are_approved(self):
        self.add_validation()
        self.analyze()
        with Session(self.engine) as session:
            for order in session.scalars(select(LabOrder)):
                self.assertEqual(order.approved_at, max(item.approved_at for item in order.order_items))
                self.assertEqual(order.approver_id, 1)

    def test_no_reporting_stage_does_not_infer_report_from_terminal_station(self):
        workflow = self.document.workflow
        report = next(node for node in workflow.graph.iter_nodes() if node.widget.node_type == 'result reporting')
        endpoint = next(node for node in workflow.graph.iter_nodes() if node.metadata.get('terminal_output'))
        workflow.graph.remove_node(report.node_id)
        endpoint.widget.name = 'Reporting'
        workflow.graph.connect_nodes(self.analyzer.node_id, endpoint.node_id)
        with Session(self.engine) as session, session.begin():
            operator = session.get(User, 1)
            operator.roles = []
        self.analyze([self.ids[0]])
        with Session(self.engine) as session:
            item = session.get(LabOrderItem, self.ids[0])
            self.assertIsNotNone(item.finished_at)
            self.assertIsNone(item.reported_at)
            self.assertIsNone(item.approved_at)

    def test_reporting_permission_is_required_and_failure_saves_nothing(self):
        with Session(self.engine) as session, session.begin():
            operator = session.get(User, 1)
            operator.roles = []
        with self.assertRaisesRegex(ValueError, 'reporter role'):
            self.analyze()
        with Session(self.engine) as session:
            self.assertTrue(all(item.finished_at is None for item in session.scalars(select(LabOrderItem))))
            self.assertTrue(all(order.received_at is None for order in session.scalars(select(LabOrder))))
            self.assertEqual(session.scalar(select(func.count()).select_from(LISWorkflowAnalysis)), 0)

    def test_validation_permission_is_required(self):
        self.add_validation(grant_role=False)
        with self.assertRaisesRegex(ValueError, 'approver role'):
            self.analyze()
        with Session(self.engine) as session:
            self.assertTrue(all(item.reported_at is None and item.finished_at is None
                                for item in session.scalars(select(LabOrderItem))))

    def test_validation_without_reporting_is_rejected_atomically(self):
        report, validation, _ = self.add_validation()
        self.document.workflow.graph.remove_node(report.node_id)
        self.document.workflow.graph.connect_nodes(self.analyzer.node_id, validation.node_id)
        with self.assertRaisesRegex(ValueError, 'previously reported'):
            self.analyze()
        with Session(self.engine) as session:
            self.assertTrue(all(item.approved_at is None and item.finished_at is None
                                for item in session.scalars(select(LabOrderItem))))

    def test_unreceived_test_passes_reception_and_finishes_actual_analyzer_work(self):
        summary = self.analyze([self.ids[0]])
        self.assertAlmostEqual(summary.makespan * 60, 4)
        with Session(self.engine) as session:
            item = session.get(LabOrderItem, self.ids[0])
            audit = session.get(LISWorkflowAnalysis, summary.run_id)
            self.assertEqual(item.finished_at - item.order.received_at, timedelta(minutes=2))
            self.assertEqual(item.order.receiver_id, 1)
            self.assertIsNotNone(item._value)
            self.assertEqual(item.reported_at - item.finished_at, timedelta(minutes=1))
            self.assertEqual(item.reporter_id, 1)
            self.assertIsNone(item.approved_at)
            self.assertIsNone(session.get(LabOrderItem, self.ids[1]).finished_at)
            events = json.loads(audit.trace_json)[0]['history']
            self.assertTrue(any(e['widget'] == 'Reception' for e in events))
            self.assertTrue(any(e['event'] == 'test_finished' for e in events))
            self.assertTrue(any(e['widget'] == 'Endpoint' for e in events))
            self.assertEqual(json.loads(audit.item_ids_json), [self.ids[0]])

    def test_received_test_bypasses_reception_and_keeps_original_receipt(self):
        original_receipt = datetime(2026, 1, 2)
        with Session(self.engine) as session, session.begin():
            item = session.get(LabOrderItem, self.ids[0])
            item.order.received_at = original_receipt
        summary = self.analyze([self.ids[0]])
        self.assertAlmostEqual(summary.makespan * 60, 3)
        with Session(self.engine) as session:
            item = session.get(LabOrderItem, self.ids[0])
            self.assertEqual(item.order.received_at, original_receipt)
            events = json.loads(session.get(LISWorkflowAnalysis, summary.run_id).trace_json)[0]['history']
            self.assertFalse(any(e['widget'] == 'Reception' for e in events))
            self.assertTrue(any(e['widget'] == 'Endpoint' for e in events))

    def test_all_runs_together_with_shared_analyzer_capacity(self):
        summary = self.analyze()
        self.assertEqual(summary.analyzed, 4)
        self.assertAlmostEqual(summary.makespan * 60, 10)
        with Session(self.engine) as session:
            self.assertTrue(all(item.finished_at for item in session.scalars(select(LabOrderItem))))
            self.assertEqual(session.scalar(select(func.count()).select_from(LISWorkflowAnalysis)), 1)
            self.assertEqual(session.scalar(select(func.count()).select_from(LabOrder)), 2)

    def test_grouped_orders_match_simulation_capacity_and_keep_per_test_results(self):
        from app.system.simulation import WorkflowSimulationEngine
        from app.system.simulation.lab import _build_specimens_from_orders
        self.reception.widget.capacity = 50
        self.reception.widget.throughput = 60000
        self.analyzer.kind = 'equipment'
        self.analyzer.metadata['equipment_id'] = 'CHEM'
        self.analyzer.widget.capacity = 50
        self.analyzer.widget.required_staff = 0
        captured = {}

        def measure(engine):
            peak = 0
            previous = engine.on_activity
            def activity(event):
                nonlocal peak
                peak = max(peak, engine.node_resources[self.analyzer.node_id].count)
                if previous is not None:
                    previous(event)
            engine.on_activity = activity
            result = engine.run()
            return result, peak

        def runner(engine):
            self.assertEqual(len(engine.specimens), 2)
            self.assertTrue(all(specimen.metadata['all_tests'] == ['GLU', 'ALT']
                                for specimen in engine.specimens))
            orders = [specimen.payload for specimen in engine.specimens]
            reference = WorkflowSimulationEngine(engine.workflow, settings=engine.settings,
                scenario=engine.scenario, specimens=_build_specimens_from_orders(orders))
            reference_result, reference_peak = measure(reference)
            result, peak = measure(engine)
            self.assertAlmostEqual(result.makespan, reference_result.makespan)
            self.assertEqual(peak, reference_peak)
            self.assertEqual(peak, 2)
            self.assertEqual(result.node_stats[self.reception.node_id].specimens_processed, 2)
            self.assertEqual(result.node_stats[self.analyzer.node_id].specimens_processed, 2)
            captured['specimens'] = result.completed_specimens
            return result

        summary = analyze_lis_items(self.ids, workflow_document=self.document,
            operator_username='analyst', database_engine=self.engine, random_seed=1, simulation_runner=runner)
        self.assertEqual(summary.analyzed, 4)
        with Session(self.engine) as session:
            audit = session.get(LISWorkflowAnalysis, summary.run_id)
            traces = {entry['item_id']: entry for entry in json.loads(audit.trace_json)}
            self.assertEqual(set(traces), {str(item_id) for item_id in self.ids})
            for item in session.scalars(select(LabOrderItem).order_by(LabOrderItem.id)):
                self.assertIsNotNone(item.finished_at)
                self.assertIsNotNone(item.reported_at)
                self.assertIsNotNone(item._value)
                self.assertEqual(traces[str(item.id)]['specimen_id'], str(item.order_id))
                self.assertEqual(traces[str(item.id)]['test_code'], item.test.code)
            for specimen in captured['specimens']:
                arrivals = [event for event in specimen.history
                            if event['widget'] == 'Reception' and event['event'] == 'arrived']
                self.assertEqual(len(arrivals), 1)
                self.assertEqual(set(specimen.metadata['completed_tests']), {'GLU', 'ALT'})

    def test_grouped_analysis_only_includes_selected_tests_in_each_order(self):
        def runner(engine):
            self.assertEqual(len(engine.specimens), 2)
            self.assertEqual([specimen.metadata['all_tests'] for specimen in engine.specimens], [['GLU'], ['ALT']])
            return engine.run()
        selected = [self.ids[0], self.ids[-1]]
        summary = analyze_lis_items(selected, workflow_document=self.document,
            operator_username='analyst', database_engine=self.engine, random_seed=1, simulation_runner=runner)
        self.assertEqual(summary.analyzed, 2)
        with Session(self.engine) as session:
            for item in session.scalars(select(LabOrderItem)):
                self.assertEqual(item.finished_at is not None, item.id in selected)

    def test_grouped_order_joins_parallel_analyzers_before_reporting(self):
        self.analyzer.metadata['analyzer_tests'] = ['GLU']
        self.analyzer.widget.metadata['analyzer_tests'] = ['GLU']
        report = next(node for node in self.document.workflow.graph.iter_nodes()
                      if node.widget.node_type == 'result reporting')
        other = self.document.workflow.create_connected_node(self.reception.node_id, 'ALT Analyzer', 60,
            metadata={'node_type': 'automated analyzer', 'throughput': 60, 'capacity': 1,
                      'required_staff': 0, 'analyzer_tests': ['ALT']})
        self.document.workflow.graph.connect_nodes(other.node_id, report.node_id)
        summary = self.analyze(self.ids[:2])
        self.assertEqual(summary.analyzed, 2)
        self.assertAlmostEqual(summary.makespan * 60, 4)
        with Session(self.engine) as session:
            first, second = [session.get(LabOrderItem, item_id) for item_id in self.ids[:2]]
            self.assertEqual(first.finished_at - first.order.received_at, timedelta(minutes=2))
            self.assertEqual(second.finished_at - second.order.received_at, timedelta(minutes=1))
            self.assertEqual(first.reported_at, second.reported_at)
            self.assertGreater(first.reported_at, max(first.finished_at, second.finished_at))

    def test_received_order_skips_reception_once_for_all_selected_tests(self):
        received_at = datetime(2026, 1, 2)
        with Session(self.engine) as session, session.begin():
            session.get(LabOrderItem, self.ids[0]).order.received_at = received_at
        def runner(engine):
            self.assertEqual(len(engine.specimens), 1)
            self.assertTrue(engine.specimens[0].metadata['skip_entry_node'])
            result = engine.run()
            self.assertFalse(any(event['widget'] == 'Reception' for event in engine.specimens[0].history))
            return result
        summary = analyze_lis_items(self.ids[:2], workflow_document=self.document,
            operator_username='analyst', database_engine=self.engine, random_seed=1, simulation_runner=runner)
        self.assertEqual(summary.analyzed, 2)
        self.assertAlmostEqual(summary.makespan * 60, 5)
        with Session(self.engine) as session:
            self.assertEqual(session.get(LabOrderItem, self.ids[0]).order.received_at, received_at)

    def test_finished_and_cancelled_work_is_skipped_and_preserved(self):
        with Session(self.engine) as session, session.begin():
            item = session.get(LabOrderItem, self.ids[0])
            item.finished_at = datetime(2026, 1, 2)
            item._value = 'Existing result'
            session.get(LabOrderItem, self.ids[1]).cancelled_at = datetime(2026, 1, 2)
        summary = self.analyze()
        self.assertEqual((summary.analyzed, summary.skipped), (2, 2))
        with Session(self.engine) as session:
            self.assertEqual(session.get(LabOrderItem, self.ids[0])._value, 'Existing result')
            self.assertIsNone(session.get(LabOrderItem, self.ids[1]).finished_at)

    def test_incompatible_analyzer_rolls_back_receipt_results_and_audit(self):
        self.analyzer.widget.metadata['analyzer_tests'] = ['GLU']
        self.analyzer.metadata['analyzer_tests'] = ['GLU']
        with self.assertRaises(ValueError):
            self.analyze()
        with Session(self.engine) as session:
            self.assertTrue(all(item.finished_at is None for item in session.scalars(select(LabOrderItem))))
            self.assertTrue(all(order.received_at is None for order in session.scalars(select(LabOrder))))
            self.assertEqual(session.scalar(select(func.count()).select_from(LISWorkflowAnalysis)), 0)

    def test_invalid_operator_and_missing_reception_are_rejected(self):
        with self.engine.begin() as connection:
            connection.execute(User.__table__.update().values(active=False))
        with self.assertRaisesRegex(ValueError, 'active signed-in'):
            self.analyze()
        self.reception.widget.node_type = 'specimens processing'
        with self.assertRaisesRegex(ValueError, 'reception'):
            self.analyze()

    def test_analysis_window_sends_selected_tests_or_all_pending_tests(self):
        from unittest.mock import patch, Mock, MagicMock
        from app.system.windows import create_analysis_window
        window = MagicMock()
        window.read.side_effect = [('Analyze', {'tests': [1]}), ('Analyze All', {}), ('Close', {})]
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as auth_database, \
             patch('app.system.windows.engine', self.engine), \
             patch('app.system.windows.create_window', return_value=window), \
             patch('app.system.windows.run_lis_workflow_analysis') as run:
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            create_analysis_window()
        self.assertEqual(run.call_args_list[0].args[0], [self.ids[1]])
        self.assertEqual(run.call_args_list[1].args[0], self.ids)
        window.close.assert_called_once()

    def test_order_bulk_selection_is_scoped_and_excludes_finished_or_cancelled_work(self):
        from app.system.lis_analysis import pending_item_ids_for_orders
        with Session(self.engine) as session, session.begin():
            first = session.get(LabOrderItem, self.ids[0])
            selected_order = first.order_id
            first.finished_at = datetime(2026, 1, 2)
            other_order = session.get(LabOrderItem, self.ids[-1]).order
            other_order.cancelled_at = datetime(2026, 1, 2)
            other_order_id = other_order.id
        self.assertEqual(pending_item_ids_for_orders([selected_order], database_engine=self.engine), [self.ids[1]])
        self.assertEqual(pending_item_ids_for_orders([other_order_id], database_engine=self.engine), [])
        self.assertEqual(pending_item_ids_for_orders([], database_engine=self.engine), [])

    def test_picker_defaults_to_only_workflow_or_remembered_choice(self):
        from unittest.mock import patch
        from app.system.lis_analysis import AnalysisWorkflowChoice, default_analysis_workflow, remember_analysis_workflow
        first = AnalysisWorkflowChoice('saved:first', 'First', self.document)
        second = AnalysisWorkflowChoice('saved:second', 'Second', self.document)
        with patch('app.config.config_dict', {'default_analysis_workflow_key': 'saved:second'}):
            self.assertIs(default_analysis_workflow([first, second]), second)
            self.assertIs(default_analysis_workflow([first]), first)
        with patch('app.config.update_config_yaml') as save:
            remember_analysis_workflow(second)
        save.assert_called_once_with(default_analysis_workflow_key='saved:second',
                                     active_workflow_id='test', active_project_id=None)

    def test_analyze_modal_executes_and_remembers_selected_workflow(self):
        from unittest.mock import patch, Mock, MagicMock
        from app.system.windows import run_lis_workflow_analysis
        from app.system.lis_analysis import AnalysisWorkflowChoice, AnalysisSummary
        alternative = WorkflowDocument('alternative', 'Alternative', Workflow())
        first = AnalysisWorkflowChoice('saved:first', 'First', self.document)
        second = AnalysisWorkflowChoice('saved:second', 'Second', alternative)
        window = MagicMock()
        window.read.return_value = ('Analyze', {'workflow': 'Second'})
        summary = AnalysisSummary('run', 1, 0, 1, 'Alternative')
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as auth_database, \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[first, second]), \
             patch('app.system.lis_analysis.default_analysis_workflow', return_value=first), \
             patch('app.system.lis_analysis.remember_analysis_workflow') as remember, \
             patch('app.system.lis_analysis.analyze_lis_items') as execute, \
             patch('app.system.analysis_floor.LISAnalysisSession') as live_factory, \
             patch('app.system.floor_view.create_floor_view_window', return_value=summary) as floor, \
             patch('app.system.windows.create_window', return_value=window), \
             patch('app.system.windows.sg.popup_ok'):
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            live_factory.return_value.error = None
            self.assertEqual(run_lis_workflow_analysis([self.ids[0]]), summary)
        remember.assert_called_once_with(second)
        self.assertIs(live_factory.call_args.kwargs['workflow_document'], alternative)
        floor.assert_called_once_with(live_factory.return_value.floor_project,
            live_session=live_factory.return_value, modal=True, parent=window)
        execute.assert_not_called()

    def test_cancel_workflow_picker_does_not_change_default_or_analyze(self):
        from unittest.mock import patch, Mock
        from app.system.windows import run_lis_workflow_analysis
        from app.system.lis_analysis import AnalysisWorkflowChoice
        choice = AnalysisWorkflowChoice('saved:only', 'Only', self.document)
        window = Mock()
        window.read.return_value = ('Cancel', {})
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=True), \
             patch('app.auth.windows.Session') as auth_database, \
             patch('app.system.lis_analysis.analysis_workflow_choices', return_value=[choice]), \
             patch('app.system.lis_analysis.remember_analysis_workflow') as remember, \
             patch('app.system.lis_analysis.analyze_lis_items') as execute, \
             patch('app.system.windows.create_window', return_value=window):
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            self.assertIsNone(run_lis_workflow_analysis([self.ids[0]]))
        remember.assert_not_called()
        execute.assert_not_called()
