"""Saved scenario semantics, validation, units and run isolation."""
import unittest
from unittest.mock import Mock, patch

from app.system.model import Scenario, Workflow
from app.system.scenario_editor import configure_scenario, scenario_settings, scenario_dialog, open_scenario_manager
from app.system.simulation import WorkflowSimulationSettings


class ScenarioEditorTests(unittest.TestCase):
    def setUp(self):
        self.workflow = Workflow()

    def configure(self, **kwargs):
        values = dict(name='Morning', staff=4, arrival_minutes=1)
        values.update(kwargs)
        return configure_scenario(self.workflow, **values)

    def test_saved_settings_drive_engine_with_minutes_converted_to_hours(self):
        scenario = self.configure()
        settings = WorkflowSimulationSettings.from_scenario(specimens_number=10,
            workflow=self.workflow, scenario=scenario)
        self.assertEqual(settings.staff_capacity, 4)
        self.assertAlmostEqual(settings.interarrival_time, 1 / 60)
        restored = Workflow.from_dict(self.workflow.to_dict())
        self.assertEqual(scenario_settings(restored, restored.scenarios[1])['arrival_minutes'], 1)

    def test_named_workforce_roundtrip_rename_and_engine_capacity(self):
        roster = [dict(id='alice', name='Alice'), dict(id='bob', name='Bob')]
        source = self.configure(workforce=roster)
        self.assertEqual(source.parameters['staff_capacity'], 2)
        roster[0]['name'] = 'External mutation'
        self.assertEqual(source.workforce[0]['name'], 'Alice')
        restored = Workflow.from_dict(self.workflow.to_dict()).scenarios[1]
        self.assertEqual(restored.workforce, source.workforce)
        renamed = [dict(m) for m in source.workforce]
        renamed[0]['name'] = 'Alicia'
        self.configure(source=source, workforce=renamed)
        self.assertEqual(source.workforce[0], dict(id='alice', name='Alicia'))
        source.parameters['staff_capacity'] = 99
        settings = WorkflowSimulationSettings.from_scenario(specimens_number=1,
            workflow=self.workflow, scenario=source)
        self.assertEqual(settings.staff_capacity, 2)

    def test_invalid_workforce_is_atomic_and_duplicate_is_independent(self):
        source = self.configure(workforce=[dict(id='a', name='Alice')])
        for roster in ([], [dict(id='a', name=' ')],
                       [dict(id='a', name='Alice'), dict(id='b', name='alice')],
                       [dict(id='a', name='Alice'), dict(id='a', name='Bob')]):
            before = self.workflow.to_dict()
            with self.assertRaises(ValueError):
                self.configure(source=source, workforce=roster)
            self.assertEqual(self.workflow.to_dict(), before)
        duplicate = self.configure(name='Copy', source=source, duplicate=True, staff=1)
        duplicate.workforce[0]['name'] = 'Changed'
        self.assertEqual(source.workforce[0]['name'], 'Alice')

    def test_duplicate_copies_overrides_with_new_identity_and_no_shared_state(self):
        source = self.configure()
        source.parameters['custom'] = 'keep'
        source.widget_service_times['Analyzer'] = 20
        duplicate = self.configure(name='Evening', staff=2, source=source, duplicate=True)
        self.assertNotEqual(duplicate.scenario_id, source.scenario_id)
        self.assertEqual(duplicate.widget_service_times, source.widget_service_times)
        self.assertEqual(duplicate.parameters['custom'], 'keep')
        duplicate.widget_service_times['Analyzer'] = 40
        self.assertEqual(source.widget_service_times['Analyzer'], 20)
        self.assertEqual(source.parameters['staff_capacity'], 4)

    def test_edit_preserves_identity_and_unexposed_settings(self):
        source = self.configure()
        source.parameters['custom'] = 'keep'
        source.widget_service_times['Analyzer'] = 20
        scenario_id = source.scenario_id
        self.configure(source=source, name='Renamed', staff=3)
        self.assertEqual(source.scenario_id, scenario_id)
        self.assertEqual(source.parameters['custom'], 'keep')
        self.assertEqual(source.widget_service_times['Analyzer'], 20)
        self.assertEqual(len(self.workflow.scenarios), 2)

    def test_invalid_inputs_do_not_mutate_saved_scenario(self):
        source = self.configure()
        for invalid in (dict(name=''), dict(staff=0), dict(staff=1.5), dict(staff='nan'),
                        dict(arrival_minutes=-1), dict(arrival_minutes='inf'),
                        dict(system_variation=1.1), dict(human_variation='nan'),
                        dict(name='Default Scenario')):
            before = self.workflow.to_dict()
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.configure(source=source, **invalid)
            self.assertEqual(self.workflow.to_dict(), before)

    def test_legacy_display_resolves_staff_without_mutating_scenario(self):
        adapter = self.workflow.create_node('Orders', metadata={'node_type': 'order adapter'})
        node = self.workflow.create_node('Registration')
        self.workflow.graph.connect_nodes(adapter.node_id, node.node_id)
        node.widget.required_staff = 3
        source = self.workflow.scenarios[0]
        before = source.to_dict()
        self.assertEqual(scenario_settings(self.workflow, source)['staff_capacity'], 3)
        self.assertEqual(source.to_dict(), before)

    def test_cancel_dialog_does_not_create_scenario(self):
        window = Mock()
        window.read.return_value = ('Cancel', {})
        before = self.workflow.to_dict()
        with patch('app.system.scenario_editor.sg.Window', return_value=window) as constructor:
            constructor.get_screen_size.return_value = (1366, 768)
            self.assertIsNone(scenario_dialog(self.workflow))
        self.assertEqual(self.workflow.to_dict(), before)
        window.close.assert_called_once()

    def test_manager_requires_authentication(self):
        with patch('app.auth.windows.session_manager.is_logged_in', return_value=False), \
             patch('app.auth.windows.sg.popup_error') as denied, \
             patch('app.system.scenario_editor.sg.Window') as window:
            open_scenario_manager(self.workflow, on_change=Mock())
        denied.assert_called_once()
        window.assert_not_called()

    def test_run_staff_override_does_not_change_saved_scenario(self):
        from tests.test_live_simulation import workflow
        from app.system.live_simulation import LiveSimulationSession
        model, _, _ = workflow()
        source = configure_scenario(model, name='Saved', staff=2, arrival_minutes=1)
        before = source.to_dict()
        session = LiveSimulationSession(model, scenario=source, count=1, staff_capacity=4)
        self.assertEqual(session.engine.staff_capacity, 4)
        self.assertEqual(session.scenario.parameters['staff_capacity'], 4)
        self.assertEqual(source.to_dict(), before)

    def test_active_scenario_survives_save_and_defaults_across_runs(self):
        from tests.test_live_simulation import workflow
        from app.system.live_simulation import LiveSimulationSession
        model, _, _ = workflow()
        evening = configure_scenario(model, name='Evening', staff=3, arrival_minutes=2)
        model.select_scenario(evening.scenario_id)
        restored = Workflow.from_dict(model.to_dict())
        self.assertEqual(restored.active_scenario.scenario_id, evening.scenario_id)
        for _ in range(2):
            live = LiveSimulationSession(restored, count=1)
            self.assertEqual(live.scenario.scenario_id, evening.scenario_id)
            self.assertEqual(live.engine.staff_capacity, 3)
            self.assertAlmostEqual(live.engine.settings.interarrival_time, 2 / 60)
        self.assertEqual(restored.active_scenario_id, evening.scenario_id)

    def test_legacy_and_deleted_active_scenarios_fall_back_safely(self):
        payload = self.workflow.to_dict()
        payload.pop('active_scenario_id')
        restored = Workflow.from_dict(payload)
        self.assertIs(restored.active_scenario, restored.scenarios[0])
        evening = self.configure()
        self.workflow.select_scenario(evening.scenario_id)
        self.workflow.scenarios.remove(evening)
        self.assertIs(self.workflow.active_scenario, self.workflow.scenarios[0])
        self.assertEqual(self.workflow.to_dict()['active_scenario_id'], self.workflow.scenarios[0].scenario_id)
        with self.assertRaises(ValueError):
            self.workflow.select_scenario('missing')

    def test_run_dialog_remembers_selected_scenario_but_cancel_does_not_change_it(self):
        from app.system.workflow_editor import _show_run_workflow_dialog
        evening = self.configure()
        label = f'{evening.name} [{evening.scenario_id[:8]}]'
        window = Mock()
        window.read.return_value = ('-RUN-', {'-RUN-ORDERS-': '2', '-RUN-SEED-': '',
                                             '-RUN-SCENARIO-': label})
        with patch('app.system.workflow_editor.create_window', return_value=window):
            self.assertEqual(_show_run_workflow_dialog(self.workflow), (evening.scenario_id, 2, None))
        self.assertIs(self.workflow.active_scenario, evening)
        window.read.return_value = ('Cancel', {})
        with patch('app.system.workflow_editor.create_window', return_value=window):
            self.assertIsNone(_show_run_workflow_dialog(self.workflow,
                selected_scenario_id=self.workflow.scenarios[0].scenario_id))
        self.assertIs(self.workflow.active_scenario, evening)

    def test_node_staff_dialog_edits_only_active_scenario(self):
        from app.system.scenario_editor import node_staff_dialog
        node = self.workflow.create_node('Processing')
        active = self.configure()
        self.workflow.select_scenario(active.scenario_id)
        with patch('app.system.scenario_editor.allowed_staff_dialog', return_value=(True, ['chosen'])) as dialog:
            self.assertTrue(node_staff_dialog(self.workflow, node))
        self.assertEqual(active.staff_assignments[node.node_id], ['chosen'])
        self.assertEqual(self.workflow.scenarios[0].staff_assignments, {})
        self.assertEqual(dialog.call_args.kwargs['scenario_name'], active.name)
