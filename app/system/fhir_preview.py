"""Read-only FHIR R4 Observation representation of a saved LIS result."""
from datetime import timezone
from decimal import Decimal, InvalidOperation
import re
from zoneinfo import ZoneInfo


def _number(value):
    try:
        number = Decimal(str(value))
        if number.is_finite():
            return int(number) if number == number.to_integral_value() else float(number)
    except (InvalidOperation, ValueError):
        pass
    return None


def result_observation(item):
    """Map persisted result state; do not infer UCUM codes or collection times."""
    test, patient = item.test, item.order.customer
    value = item._value
    has_value = value is not None and str(value).strip() != ''
    status = ('cancelled' if item.cancelled_at else 'final' if item.approved_at else
              'preliminary' if has_value else 'registered')
    coding = [{'system': 'urn:lab-tycoon:test-code', 'code': test.code}]
    if test.loinc_no and re.fullmatch(r'\d+-\d', test.loinc_no.strip()):
        coding.append({'system': 'http://loinc.org', 'code': test.loinc_no.strip()})
    observation = dict(resourceType='Observation', id=f'lab-result-{item.id}', status=status,
        category=[{'coding': [{'system': 'http://terminology.hl7.org/CodeSystem/observation-category',
                                'code': 'laboratory', 'display': 'Laboratory'}]}],
        code={'coding': coding, 'text': test.label or test.code},
        contained=[{'resourceType': 'Patient', 'id': 'patient',
                    'identifier': [{'system': 'urn:lab-tycoon:hn', 'value': str(patient.hn)}],
                    'name': [{'text': patient.fullname}]}],
        subject={'reference': '#patient'},
        basedOn=[{'type': 'ServiceRequest', 'identifier': {
            'system': 'urn:lab-tycoon:order-id', 'value': str(item.order_id)}}])
    if has_value:
        numeric = _number(value) if test.scale == 'Quantitative' else None
        if numeric is not None:
            quantity = {'value': numeric}
            if test.unit:
                quantity['unit'] = test.unit
            observation['valueQuantity'] = quantity
        else:
            observation['valueString'] = str(value)
    if item.reported_at:
        issued = item.reported_at
        if issued.tzinfo is None:
            issued = issued.replace(tzinfo=ZoneInfo('Asia/Bangkok'))
        observation['issued'] = issued.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
    bounds = {}
    for name, value in [('low', test.ref_min), ('high', test.ref_max)]:
        numeric = _number(value)
        if numeric is not None:
            bounds[name] = {'value': numeric}
            if test.unit:
                bounds[name]['unit'] = test.unit
    if bounds:
        observation['referenceRange'] = [bounds]
    if item.comment:
        observation['note'] = [{'text': item.comment}]
    return observation


def order_bundle(order):
    """Collect one order's patient, per-test requests and observations with links."""
    from uuid import NAMESPACE_URL, uuid5

    def url(kind, key):
        return 'urn:uuid:' + str(uuid5(NAMESPACE_URL, f'urn:lab-tycoon:{kind}:{key}'))

    patient = order.customer
    patient_url = url('patient', patient.id)
    entries = [{'fullUrl': patient_url, 'resource': {
        'resourceType': 'Patient', 'id': f'patient-{patient.id}',
        'identifier': [{'system': 'urn:lab-tycoon:hn', 'value': str(patient.hn)}],
        'name': [{'text': patient.fullname}]}}]
    for item in sorted(order.order_items, key=lambda item: item.id):
        observation = result_observation(item)
        request_url = url('service-request', item.id)
        revoked = bool(order.cancelled_at or order.rejected_at or item.cancelled_at)
        request = dict(resourceType='ServiceRequest', id=f'lab-request-{item.id}',
            status='revoked' if revoked else 'completed' if item.approved_at else 'active',
            intent='order', priority='urgent' if order.priority == 'urgent' else 'routine',
            requisition={'system': 'urn:lab-tycoon:order-id', 'value': str(order.id)},
            code=observation['code'], subject={'reference': patient_url})
        if order.order_datetime:
            authored = order.order_datetime
            if authored.tzinfo is None:
                authored = authored.replace(tzinfo=ZoneInfo('Asia/Bangkok'))
            request['authoredOn'] = authored.isoformat()
        if order.comment:
            request['note'] = [{'text': order.comment}]
        if order.rejected_at and order.reason:
            request.setdefault('note', []).append({'text': 'Order rejected: ' + order.reason})
        observation.pop('contained')
        observation['subject'] = {'reference': patient_url}
        observation['basedOn'] = [{'reference': request_url}]
        if revoked:
            observation['status'] = 'cancelled'
        entries.extend([{'fullUrl': request_url, 'resource': request},
                        {'fullUrl': url('observation', item.id), 'resource': observation}])
    return dict(resourceType='Bundle', id=f'lab-order-{order.id}', type='collection', entry=entries)
