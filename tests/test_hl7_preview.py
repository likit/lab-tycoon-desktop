import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.system.hl7_preview import result_message, escape


class HL7PreviewTests(unittest.TestCase):
    def setUp(self):
        self.item = SimpleNamespace(id=361, order_id=49, _value='220.0', cancelled_at=None,
            approved_at=datetime(2026, 10, 26, 9), reported_at=datetime(2026, 10, 26, 8),
            updated_at=None, comment='Saved result',
            test=SimpleNamespace(code='TG', label='Triglycerides', loinc_no='2571-8',
                scale='Quantitative', unit='mg/dL', ref_min=0, ref_max=150),
            order=SimpleNamespace(customer=SimpleNamespace(hn='HN123', firstname='Demo',
                lastname='Patient', dob=None, gender='Unknown')))

    def segments(self):
        return {line.split('|')[0]: line.split('|') for line in
                result_message(self.item, now=datetime(2026, 10, 26, 10), control_id='PREVIEW1').split('\r') if line}

    def test_message_fields_and_numeric_result(self):
        segments = self.segments()
        msh, pid, obr, obx = (segments[key] for key in ['MSH', 'PID', 'OBR', 'OBX'])
        self.assertEqual(msh[1], '^~\\&')
        self.assertEqual(msh[6], '20261026100000+0700')
        self.assertEqual(msh[8:12], ['ORU^R01^ORU_R01', 'PREVIEW1', 'T', '2.5.1'])
        self.assertEqual(msh[17], 'UNICODE UTF-8')
        self.assertEqual(pid[3], 'HN123^^^LabTycoon^MR')
        self.assertEqual(pid[5], 'Patient^Demo')
        self.assertEqual(obr[3], '49^LabTycoon')
        self.assertEqual(obr[22], '20261026090000+0700')
        self.assertEqual(obr[25], 'F')
        self.assertEqual(obx[2:4], ['NM', 'TG^Triglycerides^L^2571-8^^LN'])
        self.assertEqual(obx[5:8], ['220.0', '^mg/dL', '0-150'])
        self.assertEqual(obx[11], 'F')

    def test_status_pending_unverified_preliminary_final_cancelled(self):
        self.item.approved_at = None
        self.item.reported_at = None
        self.assertEqual(self.segments()['OBX'][11], 'R')
        self.item.reported_at = datetime.now()
        self.assertEqual(self.segments()['OBX'][11], 'P')
        self.item._value = None
        self.assertEqual(self.segments()['OBX'][11], 'I')
        self.item.cancelled_at = datetime.now()
        self.assertEqual(self.segments()['OBX'][11], 'X')
        self.assertEqual(self.segments()['OBR'][25], 'X')

    def test_escaping_and_text_do_not_inject_segments_or_components(self):
        self.item._value = 'Positive|a^b~c&d\\e\r\nOBX|fake'
        self.item.comment = 'line1\nline2'
        segments = self.segments()
        self.assertEqual(segments['OBX'][2], 'ST')
        self.assertEqual(segments['OBX'][5], escape(self.item._value))
        self.assertEqual(segments['NTE'][3], 'line1\\X0A\\line2')
        self.assertEqual(len(segments), 5)
        self.item._value = 'NaN'
        self.assertEqual(self.segments()['OBX'][2], 'ST')
        self.item.test.loinc_no = 'DEMO-1'
        self.assertEqual(self.segments()['OBX'][3], 'TG^Triglycerides^L')

    def test_preview_copies_cr_delimited_saved_message_without_writing(self):
        from app.system.windows import create_hl7_preview_window
        session, window = Mock(), Mock()
        session.get.return_value = self.item
        window.read.side_effect = [('Copy Message', {}), ('Close', {})]
        with patch('app.system.windows.Session') as factory, \
             patch('app.system.windows.create_window', return_value=window) as create:
            factory.return_value.__enter__.return_value = session
            create_hl7_preview_window.__wrapped__(361)
        payload = window.TKroot.clipboard_append.call_args.args[0]
        self.assertIn('\rOBX|1|NM|', payload)
        self.assertNotIn('\n', payload)
        self.assertTrue(create.call_args.kwargs['modal'])
        session.commit.assert_not_called()
        window.close.assert_called_once()
