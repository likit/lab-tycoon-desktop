"""Choose staff work poses from the process, independently of attendance location."""

STAFF_ACTIVITIES = ('registration', 'processing', 'reporting', 'validating')


def staff_activity(node, equipment_type=None):
    node_type = node.widget.node_type.strip().casefold()
    explicit = {
        'specimen registration': 'registration',
        'specimens processing': 'processing',
        'automated analyzer': 'processing',
        'manual analyzer': 'processing',
        'result reporting': 'reporting',
        'result validating': 'validating',
        'result validation': 'validating',
    }
    # Older generic nodes inherit "specimens processing" without an explicit
    # process selection. Preserve their Reporting/Validating name semantics.
    if node_type in explicit and node_type != 'specimens processing':
        return explicit[node_type]
    name = node.widget.name.casefold()
    if 'validat' in name:
        return 'validating'
    if 'report' in name:
        return 'reporting'
    if 'registr' in name or 'reception' in name:
        return 'registration'
    if node_type == 'specimens processing':
        return 'processing'
    return {'specimens_reception_station': 'registration',
            'reporting_station': 'reporting'}.get(equipment_type, 'processing')
