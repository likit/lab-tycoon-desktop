"""Read-only equipment summaries shared by both floor-plan entry points."""
from app.system.staff_activity import staff_activity


STATE_COLORS = {'Idle': '#94a3b8', 'Loading': '#38bdf8', 'Unloading': '#38bdf8',
                'Processing': '#4ade80', 'Waiting for staff': '#fbbf24',
                'Full': '#fb7185', 'Collecting batch': '#c4b5fd', 'Queued': '#fbbf24'}


def equipment_location(node, equipment_types):
    if node.kind == 'order_adapter':
        return None
    explicit = node.metadata.get('equipment_id') or node.metadata.get('attend_equipment_id')
    if explicit:
        return explicit
    kind = node.metadata.get('equipment_type')
    if not kind:
        if node.widget.node_type in ('automated analyzer', 'manual analyzer'):
            kind = 'chemistry_analyzer'
        else:
            kind = {'registration': 'specimens_reception_station', 'processing': 'centrifuge',
                    'reporting': 'reporting_station', 'validating': 'reporting_station'}[staff_activity(node)]
    compatible = [key for key, value in equipment_types.items() if value == kind]
    return compatible[0] if len(compatible) == 1 else None


def equipment_overview(snapshot, workflow, equipment_types):
    """Keep staff locations separate from the machine's occupied slots."""
    live = snapshot.get('state') in ('playing', 'paused', 'saving')
    overview = {}
    for equipment_id in equipment_types:
        nodes = [node for node in workflow.graph.iter_nodes()
                 if node.widget.enabled and equipment_location(node, equipment_types) == equipment_id]
        ids = {node.node_id for node in nodes}
        counts = [snapshot.get('counts', {}).get(node_id, {}) for node_id in ids] if live else []
        work = [item for item in snapshot.get('work_items', []) if item['node_id'] in ids] if live else []
        waiting = [item for item in snapshot.get('staff_waiting', []) if item['node_id'] in ids] if live else []
        staff = [item for item in snapshot.get('staff_assignments', []) if item['node_id'] in ids] if live else []
        batches = [dict(item, node_id=node_id) for node_id, item in snapshot.get('batches', {}).items()
                   if node_id in ids and item['collected'] > 0] if live else []
        capacity = snapshot.get('equipment_capacity', {}).get(equipment_id, {})
        used = capacity.get('used', 0) if live else 0
        total = capacity.get('total')
        queued = sum(item.get('queued', 0) for item in counts)
        active = sum(item.get('active', 0) for item in counts)
        phases = {item['state'] for item in work}
        if waiting:
            state = 'Waiting for staff'
        elif 'Loading' in phases:
            state = 'Loading'
        elif 'Unloading' in phases:
            state = 'Unloading'
        elif total and used >= total:
            state = 'Full'
        elif active or used:
            state = 'Processing'
        elif batches:
            state = 'Collecting batch'
        elif queued:
            state = 'Queued'
        else:
            state = 'Idle'
        waiting_keys = {(event['node_id'], str(specimen_id)) for event in waiting
                        for specimen_id in event.get('specimen_ids', [event['specimen_id']])}
        jobs = [dict(item, state='Waiting for staff' if (item['node_id'], str(item['specimen_id']))
                     in waiting_keys else item['state']) for item in work]
        timed_jobs = [job['progress'] for job in jobs if job.get('progress')]
        progress = dict(min(timed_jobs, key=lambda item: item['remaining_seconds']),
                        concurrent=len(timed_jobs)) if timed_jobs else None
        overview[equipment_id] = dict(progress=progress, state=state, color=STATE_COLORS[state], queued=queued,
            active=active, used=used, total=total, batches=batches, jobs=jobs,
            staff=list(dict.fromkeys(name for item in staff for name in item.get('staff_names', []))),
            node_names=[node.widget.name for node in nodes])
    return overview


def inspection_text(equipment_id, summary):
    lines = [equipment_id, summary['state'], '']
    if summary['total'] is not None:
        lines.append(f"Occupied slots: {summary['used']}/{summary['total']}")
    lines.extend([f"Queue: {summary['queued']} specimens", f"Active: {summary['active']}",
                  'Staff: ' + (', '.join(summary['staff']) or 'None assigned'), ''])
    for batch in summary['batches']:
        lines.append(f"Batch: {batch['collected']}/{batch['size']} collected · {batch['remaining_seconds']:.0f}s remaining")
    lines.extend(['', 'Workflow steps', *summary['node_names'], '', 'Current orders'])
    if not summary['jobs']:
        lines.append('No queued or active orders.')
    for job in summary['jobs']:
        lines.append(f"Order {job['order_id']} · {job['state']}")
        if job.get('progress'):
            progress = job['progress']
            lines.append(f"  {progress['phase']}: {progress['fraction']:.0%} · {progress['remaining_seconds']:.0f}s remaining")
        lines.append('  Requested: ' + (', '.join(job['tests']) or 'No test details'))
        lines.append('  Completed: ' + (', '.join(job['completed_tests']) or 'None'))
    return '\n'.join(lines)
