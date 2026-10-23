"""LIS analysis executes the actual workflow, with receipt-aware entry and atomic writes."""
from datetime import datetime, timedelta
import json
import unittest

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.system.model import Workflow
from app.system.storage import WorkflowDocument
from app.system.models import LabOrder, LabOrderItem, LISWorkflowAnalysis, User
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

    def test_unreceived_test_passes_reception_and_finishes_actual_analyzer_work(self):
        summary = self.analyze([self.ids[0]])
        self.assertAlmostEqual(summary.makespan * 60, 4)
        with Session(self.engine) as session:
            item = session.get(LabOrderItem, self.ids[0])
            audit = session.get(LISWorkflowAnalysis, summary.run_id)
            self.assertEqual(item.finished_at - item.order.received_at, timedelta(minutes=2))
            self.assertEqual(item.order.receiver_id, 1)
            self.assertIsNotNone(item._value)
            self.assertIsNone(item.reported_at)
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
             patch('app.system.lis_analysis.analyze_lis_items', return_value=summary) as execute, \
             patch('app.system.windows.create_window', return_value=window), \
             patch('app.system.windows.sg.popup_ok'):
            auth_database.return_value.__enter__.return_value.scalar.return_value = Mock(active=True)
            self.assertEqual(run_lis_workflow_analysis([self.ids[0]]), summary)
        remember.assert_called_once_with(second)
        self.assertIs(execute.call_args.kwargs['workflow_document'], alternative)

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
