"""Evidence regression: real capture passes, missing ACKs/hash mismatch fail."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'infra'))
from nwdaf_qoe_acceptance import analyze
from nwdaf_acceptance import one

SOURCE = ROOT / '.work/nwdaf-qoe-4e81e9ab25'


@unittest.skipUnless((SOURCE / 'control.pcap').exists(), 'Local campaign evidence required')
class EvidenceRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='nwdaf-evidence-test-', dir=ROOT / '.work')
        self.directory = Path(self.temp.name).resolve()
        assert self.directory.parent == (ROOT / '.work').resolve()
        self.addCleanup(self.temp.cleanup)
        for filename in ('result.json', 'control.pcap', 'pfcp.json', 'sbi.json',
                         'baseline-player.json', 'closed-loop-player.json', 'qoe-comparison.json'):
            shutil.copyfile(SOURCE / filename, self.directory / filename)

    def test_real_pair(self):
        self.assertEqual(analyze(self.directory)['paired_control'], 'CONFIRMED')

    def test_missing_pfcp_responses_rejects_control(self):
        path = self.directory / 'pfcp.json'
        packets = json.loads(path.read_text(encoding='utf-8'))
        path.write_text(json.dumps([p for p in packets if one(p, 'pfcp.msg_type') != '53']), encoding='utf-8')
        result = analyze(self.directory)
        self.assertEqual(result['paired_control'], 'FAILED')
        self.assertFalse(result['checks']['two_remote_sessions_mitigated_and_restored'])

    def test_capture_integrity(self):
        with (self.directory / 'control.pcap').open('ab') as handle:
            handle.write(b'corrupt-test-fixture')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            analyze(self.directory)


if __name__ == '__main__':
    unittest.main()
