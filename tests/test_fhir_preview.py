import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.system.fhir_preview import result_observation


class FHIRPreviewTests(unittest.TestCase):
    def setUp(self):
        self.item = SimpleNamespace(id=361, order_id=49, _value='220.0',
            cancelled_at=None, approved_at=datetime(2026, 10, 26, 9),
            reported_at=datetime(2026, 10, 26, 8), comment='Saved result',
            test=SimpleNamespace(code='TG', label='Triglycerides', loinc_no='2571-8',
                                 scale='Quantitative', unit='mg/dL', ref_min=0, ref_max=150),
            order=SimpleNamespace(customer=SimpleNamespace(hn='HN123', fullname='Demo Patient')))

    def test_numeric_result_codes_patient_reference_and_units(self):
        result = result_observation(self.item)
        self.assertEqual(result['resourceType'], 'Observation')
        self.assertEqual(result['status'], 'final')
        self.assertEqual(result['valueQuantity'], {'value': 220, 'unit': 'mg/dL'})
        self.assertEqual(result['code']['coding'][1], {'system': 'http://loinc.org', 'code': '2571-8'})
        self.assertEqual(result['subject']['reference'], '#patient')
        self.assertEqual(result['contained'][0]['identifier'][0]['value'], 'HN123')
        self.assertEqual(result['issued'], '2026-10-26T01:00:00Z')
        self.assertEqual(result['referenceRange'][0]['low']['value'], 0)
        self.assertNotIn('system', result['valueQuantity'])
        json.dumps(result, allow_nan=False)

    def test_status_tracks_approval_and_cancellation(self):
        self.item.approved_at = None
        self.assertEqual(result_observation(self.item)['status'], 'preliminary')
        self.item._value = ''
        result = result_observation(self.item)
        self.assertEqual(result['status'], 'registered')
        self.assertNotIn('valueQuantity', result)
        self.assertNotIn('valueString', result)
        self.item.cancelled_at = datetime.now()
        self.assertEqual(result_observation(self.item)['status'], 'cancelled')

    def test_text_comparator_and_invalid_numeric_values_remain_strings(self):
        for value in ['Positive', '<5', 'NaN', 'Infinity']:
            self.item._value = value
            result = result_observation(self.item)
            self.assertEqual(result['valueString'], value)
            self.assertNotIn('valueQuantity', result)
        self.item._value = '0'
        self.assertEqual(result_observation(self.item)['valueQuantity']['value'], 0)

    def test_no_invented_loinc_collection_time_or_missing_optional_fields(self):
        self.item.test.loinc_no = 'DEMO-1'
        self.item.reported_at = None
        self.item.comment = None
        self.item.test.ref_min = self.item.test.ref_max = None
        result = result_observation(self.item)
        self.assertEqual(len(result['code']['coding']), 1)
        for key in ['issued', 'effectiveDateTime', 'note', 'referenceRange']:
            self.assertNotIn(key, result)

    def test_preview_reads_saved_data_and_does_not_write(self):
        from app.system.windows import create_fhir_preview_window
        session = Mock()
        session.get.return_value = self.item
        window = Mock()
        window.read.side_effect = [('Copy JSON', {}), ('Close', {})]
        with patch('app.system.windows.Session') as factory, \
             patch('app.system.windows.create_window', return_value=window) as create:
            factory.return_value.__enter__.return_value = session
            create_fhir_preview_window.__wrapped__(361)
        payload = window.TKroot.clipboard_append.call_args.args[0]
        self.assertEqual(json.loads(payload)['valueQuantity']['value'], 220)
        self.assertTrue(create.call_args.kwargs['modal'])
        session.commit.assert_not_called()
        session.add.assert_not_called()
        window.close.assert_called_once()
