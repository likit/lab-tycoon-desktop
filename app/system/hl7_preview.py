"""Read-only HL7 v2.5.1 ORU^R01 preview for one saved LIS result."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4
from zoneinfo import ZoneInfo


def escape(value):
    """Escape field/component separators and line breaks inside scalar values."""
    replacements = {'\\': '\\E\\', '|': '\\F\\', '^': '\\S\\', '~': '\\R\\',
                    '&': '\\T\\', '\r': '\\X0D\\', '\n': '\\X0A\\'}
    return ''.join(replacements.get(char, char) for char in ('' if value is None else str(value)))


def _segment(name, fields):
    values = [''] * max(fields)
    for index, value in fields.items():
        values[index - 1] = value
    return name + '|' + '|'.join(values)


def _timestamp(value):
    if value is None:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo('Asia/Bangkok'))
    return value.strftime('%Y%m%d%H%M%S%z')


def result_message(item, *, now=None, control_id=None):
    """Return CR-delimited segments, preserving saved values and local unit text."""
    test, patient = item.test, item.order.customer
    raw = '' if item._value is None else str(item._value)
    has_value = bool(raw.strip())
    value_type, value = 'ST', escape(raw)
    if has_value and test.scale == 'Quantitative':
        try:
            numeric = Decimal(raw)
            if numeric.is_finite():
                value_type, value = 'NM', format(numeric, 'f')
        except InvalidOperation:
            pass
    status = ('X' if item.cancelled_at else 'F' if item.approved_at else
              'P' if has_value and item.reported_at else 'R' if has_value else 'I')
    code = '^'.join([escape(test.code), escape(test.label or test.code), 'L'])
    if test.loinc_no and re.fullmatch(r'\d+-\d', test.loinc_no.strip()):
        code += '^' + '^'.join([escape(test.loinc_no.strip()), '', 'LN'])
    now = now or datetime.now(ZoneInfo('Asia/Bangkok'))
    msh = _segment('MSH', {1: '^~\\&', 2: 'LabTycoon', 3: 'LabTycoon',
        6: _timestamp(now), 8: 'ORU^R01^ORU_R01',
        9: control_id or uuid4().hex[:20], 10: 'T', 11: '2.5.1', 17: 'UNICODE UTF-8'})
    gender = {'male': 'M', 'female': 'F', 'other': 'O'}.get(str(patient.gender).lower(), 'U')
    pid = _segment('PID', {1: '1', 3: escape(patient.hn) + '^^^LabTycoon^MR',
        5: escape(patient.lastname) + '^' + escape(patient.firstname),
        7: patient.dob.strftime('%Y%m%d') if patient.dob else '', 8: gender})
    obr = _segment('OBR', {1: '1', 3: str(item.order_id) + '^LabTycoon', 4: code,
        22: _timestamp(item.approved_at or item.reported_at or item.updated_at),
        25: 'X' if item.cancelled_at else 'F' if item.approved_at else 'P' if has_value else 'I'})
    low = '' if test.ref_min is None else str(test.ref_min)
    high = '' if test.ref_max is None else str(test.ref_max)
    reference = f'{low}-{high}' if low and high else f'>={low}' if low else f'<={high}' if high else ''
    obx = _segment('OBX', {1: '1', 2: value_type, 3: code, 5: value,
        6: '^' + escape(test.unit) if test.unit else '', 7: escape(reference), 11: status})
    segments = [msh, pid, obr, obx]
    if item.comment:
        segments.append(_segment('NTE', {1: '1', 3: escape(item.comment)}))
    return '\r'.join(segments) + '\r'
