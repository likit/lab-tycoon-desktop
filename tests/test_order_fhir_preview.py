import json
import unittest
from unittest.mock import patch
from sqlalchemy.orm import Session

from app.system.fhir_preview import order_bundle
from app.system.models import LabOrderItem
from tests.test_lis_analysis import LISAnalysisTests


class OrderFHIRPreviewTests(unittest.TestCase):
    def setUp(self):
        fixture = LISAnalysisTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.engine = fixture.engine
        with Session(self.engine) as session:
            self.order_id = session.get(LabOrderItem, fixture.ids[0]).order_id

    def test_all_order_items_share_patient_and_resolvable_request_links(self):
        with Session(self.engine) as session:
            order = session.get(LabOrderItem, 1).order
            bundle = order_bundle(order)
            count = len(order.order_items)
        self.assertEqual(bundle['type'], 'collection')
        self.assertEqual(len(bundle['entry']), 1 + 2 * count)
        resources = {entry['fullUrl']: entry['resource'] for entry in bundle['entry']}
        for resource in resources.values():
            if resource['resourceType'] in ('ServiceRequest', 'Observation'):
                self.assertEqual(resources[resource['subject']['reference']]['resourceType'], 'Patient')
            if resource['resourceType'] == 'Observation':
                self.assertEqual(resources[resource['basedOn'][0]['reference']]['code'], resource['code'])
                self.assertEqual(resource['status'], 'registered')
                self.assertNotIn('contained', resource)
        json.dumps(bundle, allow_nan=False)

    def test_urgent_cancelled_order_keeps_all_items_and_values(self):
        with Session(self.engine) as session:
            order = session.get(LabOrderItem, 1).order
            from datetime import datetime
            order.priority = 'urgent'
            order.cancelled_at = datetime.now()
            order.order_items[0]._value = '123'
            bundle = order_bundle(order)
            requests = [e['resource'] for e in bundle['entry'] if e['resource']['resourceType'] == 'ServiceRequest']
            observations = [e['resource'] for e in bundle['entry'] if e['resource']['resourceType'] == 'Observation']
            self.assertTrue(all(r['priority'] == 'urgent' and r['status'] == 'revoked' for r in requests))
            self.assertTrue(all(o['status'] == 'cancelled' for o in observations))
            self.assertEqual(observations[0]['valueQuantity']['value'], 123)

    def test_dialog_loads_whole_order_without_saving(self):
        from app.system.windows import create_order_fhir_preview_window
        with patch('app.system.windows.engine', self.engine), \
             patch('app.system.windows._show_fhir_json') as display:
            create_order_fhir_preview_window.__wrapped__(self.order_id)
        bundle = json.loads(display.call_args.args[0])
        self.assertEqual(bundle['resourceType'], 'Bundle')
        self.assertEqual(sum(e['resource']['resourceType'] == 'ServiceRequest' for e in bundle['entry']), 2)
