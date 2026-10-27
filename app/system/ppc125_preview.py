"""PPC 125 Protocol v1.0 host-to-analyzer teaching preview (manual pp. 8-13)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PPC125Preview:
    records: tuple[str, ...]
    frames: tuple[bytes, ...]
    exchange: str
    notes: tuple[str, ...]


def _scalar(value, label, length, *, required=True):
    value = str(value or '').strip()
    if required and not value:
        raise ValueError(f'{label} is required.')
    if len(value) > length or any(ord(c) < 32 or ord(c) > 126 or c in '|\\^&' for c in value):
        raise ValueError(f'{label} must be at most {length} printable ASCII characters without | \\ ^ &.')
    return value


def frame_record(record, number):
    """Manual pp. 3-4: checksum covers ASCII FN through ETX, modulo 256."""
    content = str(number % 8).encode('ascii') + record.encode('ascii') + b'\r\x03'
    checksum = f'{sum(content) % 256:02X}'.encode('ascii')
    return b'\x02' + content + checksum + b'\r\n'


def visible_frame(frame):
    controls = {2: '<STX>', 3: '<ETX>', 13: '<CR>', 10: '<LF>'}
    return ''.join(controls.get(value, chr(value)) for value in frame)


def order_preview(order, *, sample_id, disk='1', position='1', diluent='N',
                  specimen='1', test_codes=None):
    """Use Sample ID mode; no transport or database changes are performed."""
    sample_id = _scalar(sample_id, 'Sample ID', 22)
    disk = _scalar(disk, 'Disk', 1)
    position = _scalar(position, 'Cup position', 2)
    if not disk.isdigit() or not position.isdigit():
        raise ValueError('Disk and cup position must be numeric (one and two digits respectively).')
    if diluent not in ('Y', 'N') or specimen not in ('1', '2', '3', '4', '5'):
        raise ValueError('Choose a valid diluent and specimen type.')
    items = sorted((item for item in order.order_items if not item.cancelled_at), key=lambda item: item.id)
    if not items:
        raise ValueError('This order has no non-cancelled tests to demonstrate.')
    notes = ['Demonstration only. Includes all non-cancelled tests, including completed tests.',
             'Disk, cup position and specimen type are preview settings, not saved assignments.',
             'LIS test codes are defaults; match the analyzer test menu before using an interface.']
    name = order.customer.fullname.strip()
    try:
        name = _scalar(name, 'Patient name', 30, required=False)
    except ValueError:
        name = ''
        notes.append('Optional patient name omitted: it exceeds 30 characters or contains unsupported characters.')
    gender = {'male': 'M', 'female': 'F'}.get(str(order.customer.gender).lower(), 'U')
    patient_fields = [''] * 15
    for index, value in {1: 'P', 2: '1', 4: sample_id, 6: name, 9: gender}.items():
        patient_fields[index - 1] = value
    # The manual represents age in P-15 as number^Y/M/D. Leave it empty
    # rather than deriving age from placeholder DOBs on generated demo patients.
    patient_fields[14] = '^'
    records = ['H|\\^&', '|'.join(patient_fields)]
    mapping = test_codes if test_codes is not None else {item.test.code: item.test.code for item in items}
    for index, item in enumerate(items, 1):
        code = _scalar(mapping.get(item.test.code, ''), f'Analyzer code for {item.test.code}', 10)
        fields = [''] * 26
        values = {1: 'O', 2: str(index), 3: f'{sample_id}^^{disk}^{position}^{diluent}',
                  5: f'^^^{code}', 6: 'S' if order.priority == 'urgent' else 'R',
                  7: order.order_datetime.strftime('%Y%m%d%H%M%S') if order.order_datetime else '',
                  16: specimen, 26: 'O'}
        for field, value in values.items():
            fields[field - 1] = value
        records.append('|'.join(fields))
    records.append('L|1|N')
    frames = tuple(frame_record(record, index) for index, record in enumerate(records, 1))
    lines = ['LIS → PPC 125: <ENQ>', 'PPC 125 → LIS: <ACK> (simulated)']
    for frame in frames:
        lines.extend(['LIS → PPC 125: ' + visible_frame(frame), 'PPC 125 → LIS: <ACK> (simulated)'])
    lines.append('LIS → PPC 125: <EOT>')
    return PPC125Preview(tuple(records), frames, '\n'.join(lines), tuple(notes))
