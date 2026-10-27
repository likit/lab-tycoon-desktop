"""PPC 125 v1.0 host-order examples, field positions and frame checksums."""
from datetime import datetime
from types import SimpleNamespace
import unittest

from app.system.ppc125_preview import frame_record, order_preview


def order(codes=('AST', 'TP', 'ALB')):
    return SimpleNamespace(id=49, priority='routine', order_datetime=datetime(2009, 1, 19, 10, 5, 34),
        customer=SimpleNamespace(fullname='', gender='Unknown'),
        order_items=[SimpleNamespace(id=i, cancelled_at=None, test=SimpleNamespace(code=code))
                     for i, code in enumerate(codes, 1)])


class PPC125PreviewTests(unittest.TestCase):
    def test_manual_header_and_patient_checksums(self):
        self.assertEqual(frame_record('H|\\^&', 1), b'\x021H|\\^&\r\x03E5\r\n')
        self.assertEqual(frame_record('P|1|||||||||||||^', 2),
                         b'\x022P|1|||||||||||||^\r\x03E9\r\n')
        self.assertEqual(frame_record('L|1|N', 6), b'\x026L|1|N\r\x0309\r\n')

    def test_host_order_fields_one_record_per_test_and_no_results(self):
        result = order_preview(order(), sample_id='3977777', disk='1', position='1')
        self.assertEqual(result.records[0], 'H|\\^&')
        self.assertEqual(result.records[-1], 'L|1|N')
        self.assertEqual(len(result.records), 6)
        for i, record in enumerate(result.records[2:-1], 1):
            fields = record.split('|')
            self.assertEqual(len(fields), 26)
            self.assertEqual(fields[:3], ['O', str(i), '3977777^^1^1^N'])
            self.assertEqual(fields[4], '^^^' + ['AST', 'TP', 'ALB'][i-1])
            self.assertEqual(fields[5:7], ['R', '20090119100534'])
            self.assertEqual(fields[15], '1')
            self.assertEqual(fields[25], 'O')
        self.assertNotIn('\rR|', '\r'.join(result.records))
        self.assertIn('<ENQ>', result.exchange)
        self.assertIn('<ACK> (simulated)', result.exchange)
        self.assertTrue(result.exchange.endswith('<EOT>'))

    def test_manual_sample_with_sample_number_matches_published_checksum(self):
        # Manual pp. 11-12 supplies both sample ID and sample number in this example.
        record = order_preview(order(), sample_id='3977777').records[2]
        record = record.replace('3977777^^', '3977777^4^')
        self.assertTrue(frame_record(record, 3).endswith(b'\x0351\r\n'))

    def test_frame_rollover_and_every_checksum_is_over_fn_through_etx(self):
        result = order_preview(order(tuple(f'T{i}' for i in range(10))), sample_id='49')
        self.assertEqual([frame[1:2] for frame in result.frames[:9]], list(b'123456701'[i:i+1] for i in range(9)))
        for frame in result.frames:
            self.assertEqual(frame[-4:-2], f'{sum(frame[1:-4]) % 256:02X}'.encode())

    def test_mapping_priority_specimen_and_unsupported_optional_name(self):
        model = order(('GLU', 'ALT'))
        model.priority = 'urgent'
        model.customer.fullname = 'ผู้ป่วยทดสอบ'
        model.order_items[1].cancelled_at = datetime.now()
        result = order_preview(model, sample_id='S001', disk='2', position='10', diluent='Y',
                               specimen='2', test_codes={'GLU': 'GLUC'})
        fields = result.records[2].split('|')
        self.assertEqual((fields[2], fields[4], fields[5], fields[15]), ('S001^^2^10^Y', '^^^GLUC', 'S', '2'))
        self.assertEqual(len(result.records), 4)
        self.assertIn('patient name omitted', '\n'.join(result.notes))
        self.assertEqual(result.records[1].split('|')[5], '')

    def test_reject_invalid_delimiters_lengths_missing_codes_and_empty_orders(self):
        for sample in ['A|O', 'A\rO', 'ไทย', 'a'*23]:
            with self.assertRaises(ValueError):
                order_preview(order(), sample_id=sample)
        for options in [dict(disk='10'), dict(position='100'), dict(diluent='Q'),
                        dict(specimen='6'), dict(test_codes={}), dict(test_codes={'AST':'a'*11})]:
            with self.assertRaises(ValueError):
                order_preview(order(), sample_id='49', **options)
        with self.assertRaises(ValueError):
            order_preview(order(()), sample_id='49')
