"""Saved experiment settings shared by workflow and floor views."""
from app.system.arrivals import validate_arrivals
from app.ui_styles import create_window, DANGER_BUTTON_COLOR
from copy import deepcopy
from math import isfinite

import FreeSimpleGUI as sg

from app.auth.windows import login_required
from app.system.model import Scenario
from app.system.workforce import new_staff, scenario_workforce, validate_workforce, eligible_staff


def staff_nodes(workflow):
    return [node for node in workflow.graph.iter_nodes()
            if node.kind not in ('resource', 'order_adapter')
            and node.widget.node_type not in ('order adapter', 'Evaluation')
            and not node.metadata.get('terminal_output')]


def validate_assignments(workflow, roster, assignments):
    result = deepcopy(assignments)
    for node in staff_nodes(workflow):
        if not node.widget.enabled or int(node.widget.required_staff) == 0:
            continue
        try:
            allowed = eligible_staff(roster, result, node.node_id)
        except ValueError as exc:
            raise ValueError(f'{node.widget.name}: {exc}') from exc
        if len(allowed) < int(node.widget.required_staff):
            raise ValueError(f'{node.widget.name} requires {node.widget.required_staff} staff, '
                             f'but only {len(allowed)} eligible staff are available.')
    return result


def allowed_staff_dialog(node, roster, selected=None, *, scenario_name=None):
    """Return (saved, IDs); None IDs is the dynamic All choice."""
    ids = {member['id'] for member in roster}
    missing = set(selected or []) - ids
    labels = [member['name'] for member in roster]
    layout = [[sg.Text(f'Scenario: {scenario_name}' if scenario_name else 'Scenario staff permissions')],
              [sg.Text(f'{node.widget.name} · Required staff: {node.widget.required_staff} (shared)')],
              [sg.Checkbox('All staff', default=selected is None, key='all', enable_events=True)],
              [sg.Listbox(labels, default_values=[m['name'] for m in roster if m['id'] in (selected or [])],
                          select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE, size=(38, 8),
                          disabled=selected is None, key='names')],
              [sg.Text('Removed staff were assigned. Select their replacements.' if missing else
                       'Workers are shared across nodes; each person works at one node at a time.')],
              [sg.Button('Save'), sg.Button('Cancel')]]
    window = create_window('Allowed staff', layout, modal=True, finalize=True, keep_on_top=True)
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return False, selected
            if event == 'all':
                window['names'].update(disabled=values['all'])
            elif event == 'Save':
                chosen = None if values['all'] else [m['id'] for m in roster if m['name'] in values['names']]
                if int(node.widget.required_staff) > len(roster if chosen is None else chosen):
                    sg.popup_error('Select at least the required number of staff.', keep_on_top=True)
                    continue
                return True, chosen
    finally:
        window.close()


def node_staff_dialog(workflow, node):
    """Edit permissions for the workflow's persistent active scenario."""
    if node not in staff_nodes(workflow):
        raise ValueError('Select an analyzer or process node.')
    scenario = workflow.active_scenario
    if scenario is None:
        raise ValueError('Create a scenario first.')
    roster = scenario_workforce(scenario, scenario_settings(workflow, scenario)['staff_capacity'])
    saved, chosen = allowed_staff_dialog(node, roster, scenario.staff_assignments.get(node.node_id),
                                         scenario_name=scenario.name)
    if saved:
        scenario.workforce = roster
        scenario.parameters['staff_capacity'] = len(roster)
        scenario.staff_assignments[node.node_id] = chosen
    return saved


def scenario_settings(workflow, scenario):
    """Show explicit values without changing older scenarios just by viewing them."""
    staff = scenario.parameters.get('staff_capacity')
    if scenario.workforce:
        staff = len(validate_workforce(scenario.workforce))
    if staff is None:
        from app.system.simulation.lab import WorkflowRouter
        router = WorkflowRouter()
        try:
            active = set(router.reachable_node_ids(workflow, router.start_node_id(workflow)))
        except ValueError:
            # Incomplete graphs must remain editable before they can be run.
            active = {n.node_id for n in workflow.graph.iter_nodes() if n.widget.enabled}
        staff = max([1] + [int(n.widget.required_staff) for n in workflow.graph.iter_nodes()
                           if n.node_id in active and
                           (int(n.widget.required_staff) > 0 or float(n.widget.human_throughput) > 0)])
    return dict(staff_capacity=int(staff),
                arrival_minutes=float(scenario.parameters.get('interarrival_time', 0)) * 60,
                arrival_distribution=scenario.parameters.get('arrival_distribution', 'fixed'),
                arrival_seed=scenario.parameters.get('arrival_seed'),
                rejection_percent=float(scenario.parameters.get('rejection_probability', 0)) * 100,
                stat_percent=float(scenario.parameters.get('stat_probability', 0)) * 100,
                minimum_tests_per_order=int(scenario.parameters.get('minimum_tests_per_order', 1)),
                maximum_tests_per_order=int(scenario.parameters.get('maximum_tests_per_order', 0)),
                test_selection_seed=scenario.parameters.get('test_selection_seed'),
                system_variation=float(scenario.parameters.get('system_variation', .10)),
                human_variation=float(scenario.parameters.get('human_variation', .10)))


def configure_scenario(workflow, *, name, description='', staff, arrival_minutes,
                       system_variation=.10, human_variation=.10, source=None, duplicate=False,
                       workforce=None, staff_assignments=None, arrival_distribution=None, arrival_seed=None,
                       rejection_percent=None, stat_percent=None, minimum_tests_per_order=None,
                       maximum_tests_per_order=None, test_selection_seed=None):
    """Validate before mutation; duplicates preserve all existing overrides."""
    name = str(name).strip()
    if not name:
        raise ValueError('Scenario name is required.')
    if any(s.name == name and (s is not source or duplicate) for s in workflow.scenarios):
        raise ValueError('Choose a unique scenario name.')
    staff_value = float(staff)
    if not isfinite(staff_value) or staff_value < 1 or not staff_value.is_integer():
        raise ValueError('Available staff must be a positive whole number.')
    arrival = float(arrival_minutes)
    previous = source.parameters if source else {}
    rejection = float(previous.get('rejection_probability', 0)) * 100 if rejection_percent is None else float(rejection_percent)
    if not isfinite(rejection) or not 0 <= rejection <= 100:
        raise ValueError('Specimen rejection probability must be between 0 and 100 percent.')
    stat = float(previous.get('stat_probability', 0)) * 100 if stat_percent is None else float(stat_percent)
    if not isfinite(stat) or not 0 <= stat <= 100:
        raise ValueError('STAT orders must be between 0 and 100 percent.')
    minimum_tests = int(previous.get('minimum_tests_per_order', 1) if minimum_tests_per_order is None else minimum_tests_per_order)
    maximum_tests = int(previous.get('maximum_tests_per_order', 0) if maximum_tests_per_order is None else maximum_tests_per_order)
    if minimum_tests < 1 or maximum_tests < 0 or (maximum_tests and maximum_tests < minimum_tests):
        raise ValueError('Test-count range must have a minimum of 1 and a maximum no lower than the minimum (or All).')
    test_seed = previous.get('test_selection_seed') if test_selection_seed is None else test_selection_seed
    if test_seed not in (None, ''):
        try:
            test_seed = int(test_seed)
        except (TypeError, ValueError) as exc:
            raise ValueError('Test selection seed must be a whole number.') from exc
    else:
        test_seed = None
    distribution = arrival_distribution or previous.get('arrival_distribution', 'fixed')
    seed = previous.get('arrival_seed') if arrival_seed is None else arrival_seed
    _, distribution, seed = validate_arrivals(arrival / 60, distribution, seed)
    variations = [float(system_variation), float(human_variation)]
    if not isfinite(arrival) or arrival < 0:
        raise ValueError('Arrival interval must be a finite, non-negative number of minutes.')
    if any(not isfinite(v) or not 0 <= v <= 1 for v in variations):
        raise ValueError('Variation must be between 0 and 1 (for example, 0.10 = 10%).')
    scenario = source if source is not None and not duplicate else Scenario(name=name)
    if workforce is not None:
        roster = validate_workforce(workforce)
    else:
        roster = scenario_workforce(source or scenario, int(staff_value))
        roster = roster[:int(staff_value)]
        while len(roster) < int(staff_value):
            index = len(roster) + 1
            used_names = {m['name'].casefold() for m in roster}
            while f'Staff {index}'.casefold() in used_names:
                index += 1
            roster.append(new_staff(f'Staff {index}'))
    parameters = deepcopy(source.parameters) if source else {}
    assignments = validate_assignments(workflow, roster, staff_assignments if staff_assignments is not None
                                       else (source.staff_assignments if source else {}))
    parameters.update(staff_capacity=len(roster), interarrival_time=arrival / 60,
                      arrival_distribution=distribution, arrival_seed=seed, rejection_probability=rejection / 100, stat_probability=stat / 100,
                      minimum_tests_per_order=minimum_tests, maximum_tests_per_order=maximum_tests,
                      test_selection_seed=test_seed,
                      system_variation=variations[0], human_variation=variations[1])
    scenario.name, scenario.description, scenario.parameters = name, str(description), parameters
    scenario.workforce = roster
    scenario.staff_assignments = assignments
    if duplicate:
        scenario.widget_service_times = deepcopy(source.widget_service_times)
    if scenario not in workflow.scenarios:
        workflow.add_scenario(scenario)
    return scenario


def scenario_dialog(workflow, source=None, *, duplicate=False):
    defaults = source or Scenario(name='')
    settings = scenario_settings(workflow, defaults)
    name = (source.name + ' copy') if duplicate else defaults.name
    roster = scenario_workforce(defaults, settings['staff_capacity'])
    assignments = deepcopy(defaults.staff_assignments)
    nodes = staff_nodes(workflow)

    def node_rows():
        names = {member['id']: member['name'] for member in roster}
        return [[node.widget.name, node.widget.required_staff,
                 'All staff' if assignments.get(node.node_id) is None else
                 ', '.join(names.get(staff_id, '(removed staff)') for staff_id in assignments[node.node_id]) or 'Nobody']
                for node in nodes]
    layout = [[sg.Text('Name'), sg.Input(name, key='name', size=(34, 1))],
              [sg.Text('Description'), sg.Input(defaults.description, key='description', size=(34, 1))],
              [sg.Text('Simulation workforce (independent of LIS accounts)')],
              [sg.Table(values=[[m['name']] for m in roster], headings=['Staff name'],
                        key='workforce', num_rows=6, auto_size_columns=True, enable_events=True)],
              [sg.Button('Add staff'), sg.Button('Rename staff'), sg.Button('Remove staff')],
              [sg.Text(f'Available staff: {len(roster)}', key='staff_count')],
              [sg.Text('Node staff permissions for this scenario')],
              [sg.Table(values=node_rows(), headings=['Node', 'Required staff', 'Allowed staff'],
                        key='staff_nodes', num_rows=5, auto_size_columns=True, enable_events=True)],
              [sg.Button('Allowed staff')],
              [sg.Text('Arrival distribution'), sg.Combo(['Fixed', 'Exponential'],
                  default_value=settings['arrival_distribution'].title(), key='arrival_distribution', readonly=True)],
              [sg.Text('Interval / mean interval (minutes)'), sg.Input(str(settings['arrival_minutes']), key='arrival', size=(10, 1))],
              [sg.Text('Fixed: 0 releases all together. Exponential: positive mean gap; random arrivals.')],
              [sg.Text('Arrival seed (optional)'), sg.Input(
                  '' if settings['arrival_seed'] is None else str(settings['arrival_seed']), key='arrival_seed', size=(12, 1))],
              [sg.Text('Same seed repeats the arrival gaps. A run seed overrides this for floor simulation.')],
              [sg.Text('STAT orders (%)'), sg.Input(str(settings['stat_percent']), key='stat', size=(10, 1))],
              [sg.Text('STAT uses Urgent priority. Queue order is unchanged.')],
              [sg.Text('Tests per generated order (minimum / maximum)'),
               sg.Input(str(settings['minimum_tests_per_order']), key='minimum_tests', size=(5, 1)),
               sg.Input('All' if settings['maximum_tests_per_order'] <= 0 else str(settings['maximum_tests_per_order']),
                        key='maximum_tests', size=(7, 1))],
              [sg.Text('Each order gets a random subset of the analyzer tests.')],
              [sg.Text('Test selection seed (optional)'), sg.Input(
                  '' if settings['test_selection_seed'] is None else str(settings['test_selection_seed']),
                  key='test_seed', size=(12, 1))],
              [sg.Text('Specimen rejection probability (%)'), sg.Input(str(settings['rejection_percent']), key='rejection', size=(10, 1))],
              [sg.Text('Applied at reception before testing; 0 accepts all specimens.')],
              [sg.Checkbox('Advanced settings', key='advanced', enable_events=True)],
              [sg.pin(sg.Column([
                  [sg.Text('Equipment variation (0–1)'), sg.Input(str(settings['system_variation']), key='system', size=(10, 1))],
                  [sg.Text('Human variation (0–1)'), sg.Input(str(settings['human_variation']), key='human', size=(10, 1))],
              ], key='advanced_panel', visible=False))]]
    # This is a long form with tables between controls, rather than a table
    # viewer. Keep its row order and scroll the form on small/high-DPI desktops.
    screen_width, screen_height = sg.Window.get_screen_size()
    width = min(900, max(1, int(screen_width) - 80))
    height = min(750, max(1, int(screen_height) - 120))
    layout = [[sg.Column(layout, key='scenario_form', scrollable=True, vertical_scroll_only=True,
                         size=(max(1, width - 40), max(1, height - 70)),
                         size_subsample_width=1, size_subsample_height=1,
                         expand_x=True, expand_y=True)],
              [sg.Button('Save'), sg.Button('Cancel')]]
    window = create_window('Duplicate Scenario' if duplicate else 'Scenario Settings', layout,
                       modal=True, finalize=True, keep_on_top=True, auto_table_layout=False,
                       size=(width, height), resizable=True,
                       location=((int(screen_width) - width) // 2, (int(screen_height) - height) // 2))
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Cancel'):
                return None
            if event == 'advanced':
                window['advanced_panel'].update(visible=values['advanced'])
                window.refresh()
                window['scenario_form'].contents_changed()
            elif event == 'Allowed staff':
                selected_nodes = values.get('staff_nodes', [])
                if not selected_nodes:
                    sg.popup_error('Select a node first.', keep_on_top=True)
                    continue
                node = nodes[selected_nodes[0]]
                saved, chosen = allowed_staff_dialog(node, roster, assignments.get(node.node_id))
                if saved:
                    assignments[node.node_id] = chosen
                    window['staff_nodes'].update(values=node_rows())
            elif event in ('Add staff', 'Rename staff', 'Remove staff'):
                selected = values.get('workforce', [])
                if event != 'Add staff' and not selected:
                    sg.popup_error('Select a staff member first.', keep_on_top=True)
                    continue
                candidate = deepcopy(roster)
                if event == 'Remove staff':
                    candidate.pop(selected[0])
                else:
                    old_name = '' if event == 'Add staff' else roster[selected[0]]['name']
                    entered = sg.popup_get_text('Staff name', default_text=old_name, keep_on_top=True)
                    if entered is None:
                        continue
                    if event == 'Add staff':
                        candidate.append(new_staff(entered))
                    else:
                        candidate[selected[0]]['name'] = entered
                try:
                    roster = validate_workforce(candidate)
                except (ValueError, TypeError) as exc:
                    sg.popup_error(str(exc), keep_on_top=True)
                    continue
                window['workforce'].update(values=[[m['name']] for m in roster])
                window['staff_count'].update(f'Available staff: {len(roster)}')
                window['staff_nodes'].update(values=node_rows())
            elif event == 'Save':
                try:
                    maximum_text = str(values.get('maximum_tests', 'All')).strip()
                    maximum_tests = 0 if maximum_text.casefold() == 'all' else int(maximum_text)
                    test_seed_text = str(values.get('test_seed', '')).strip()
                    return configure_scenario(workflow, name=values['name'], description=values['description'],
                        staff=len(roster), workforce=roster, arrival_minutes=values['arrival'],
                        staff_assignments=assignments,
                        arrival_distribution=values.get('arrival_distribution', 'Fixed'),
                        arrival_seed=values.get('arrival_seed', ''),
                        rejection_percent=values.get('rejection', 0),
                        stat_percent=values.get('stat', 0),
                        minimum_tests_per_order=values.get('minimum_tests', 1),
                        maximum_tests_per_order=maximum_tests,
                        test_selection_seed=int(test_seed_text) if test_seed_text else None,
                        system_variation=values['system'], human_variation=values['human'],
                        source=source, duplicate=duplicate)
                except (ValueError, TypeError) as exc:
                    sg.popup_error(str(exc), keep_on_top=True)
    finally:
        window.close()


@login_required
def open_scenario_manager(workflow, *, on_change, run_simulation=None, parent=None):
    layout = [[sg.Text('Saved experiment settings', font=('Arial', 14, 'bold'))],
              [sg.Table(values=[], headings=['Active', 'Scenario', 'Staff', 'Arrival / mean (min)', 'Overrides'],
                        key='scenarios', num_rows=8, auto_size_columns=True, enable_events=True)],
              [sg.Button('Use scenario'), sg.Button('Create from defaults'), sg.Button('Edit'), sg.Button('Duplicate'),
               sg.Button('Delete'), sg.Button('Run', disabled=run_simulation is None), sg.Button('Close', button_color=DANGER_BUTTON_COLOR)]]
    window = create_window('Scenarios', layout, modal=True, finalize=True, parent=parent,
                           always_on_top=True)
    def refresh():
        window['scenarios'].update(values=[['Yes' if s is workflow.active_scenario else '', s.name, scenario_settings(workflow, s)['staff_capacity'],
            f"{scenario_settings(workflow, s)['arrival_distribution'].title()} · {scenario_settings(workflow, s)['arrival_minutes']:g}", len(s.widget_service_times)]
            for s in workflow.scenarios])
    refresh()
    try:
        while True:
            event, values = window.read()
            if event in (sg.WIN_CLOSED, 'Close'):
                break
            selected = values.get('scenarios', [])
            source = workflow.scenarios[selected[0]] if selected else None
            changed = False
            if event == 'Create from defaults':
                changed = scenario_dialog(workflow) is not None
            elif event in ('Use scenario', 'Edit', 'Duplicate', 'Delete', 'Run'):
                if source is None:
                    sg.popup_error('Select a scenario first.', keep_on_top=True)
                    continue
                if event == 'Use scenario':
                    workflow.select_scenario(source.scenario_id)
                    changed = True
                elif event in ('Edit', 'Duplicate'):
                    changed = scenario_dialog(workflow, source, duplicate=event == 'Duplicate') is not None
                elif event == 'Delete':
                    if len(workflow.scenarios) == 1:
                        sg.popup_error('Keep at least one scenario.', keep_on_top=True)
                    elif sg.popup_yes_no(f'Delete {source.name}?', keep_on_top=True) == 'Yes':
                        workflow.scenarios.remove(source)
                        workflow.select_scenario(workflow.active_scenario.scenario_id)
                        changed = True
                elif run_simulation:
                    from app.system.workflow_editor import _show_run_workflow_dialog
                    settings = _show_run_workflow_dialog(workflow, selected_scenario_id=source.scenario_id)
                    if settings:
                        on_change()
                        refresh()
                        try:
                            result = run_simulation(*settings)
                            sg.popup(f'Run {result.run_id}: {result.completed_specimens} specimens completed.', keep_on_top=True)
                        except (ValueError, TypeError) as exc:
                            sg.popup_error(str(exc), keep_on_top=True)
            if changed:
                on_change()
                refresh()
    finally:
        window.close()
