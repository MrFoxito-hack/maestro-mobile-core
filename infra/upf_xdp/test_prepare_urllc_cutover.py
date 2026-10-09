from copy import deepcopy
import unittest
from prepare_urllc_cutover import candidate


class CutoverCandidates(unittest.TestCase):
    def setUp(self):
        self.smf = {'smf': {'session': [{'dnn': '5g-plus', 'subnet': '10.47.0.0/16'}],
            'info': [{'s_nssai': [{'sst': 2, 'sd': '000002', 'dnn': ['5g-plus']}]}],
            'pfcp': {'client': {'upf': [{'address': '10.210.50.22'}]}},
            'chf': {'enabled': True, 'journal_dir': '/keep/journal', 'requested_units': 10000}}}
        self.upf = {'upf': {'session': [{'dnn': '5g-plus', 'subnet': '10.47.0.0/16'}],
            'pfcp': {'server': [{'address': '10.210.50.22'}]},
            'gtpu': {'server': [{'address': '10.210.50.22'}]}, 'charging_enforcement': True}}

    def test_only_quota_flags_change_and_original_is_preserved(self):
        for kind, original in (('smf', self.smf), ('upf', self.upf)):
            snapshot = deepcopy(original)
            result = candidate(original, kind)
            if kind == 'smf':
                self.assertFalse(result[kind]['chf']['enabled'])
                result[kind]['chf']['enabled'] = True
            else:
                self.assertFalse(result[kind]['charging_enforcement'])
                result[kind]['charging_enforcement'] = True
            self.assertEqual(result, snapshot)
            self.assertEqual(original, snapshot)

    def test_rejects_shared_embb_dnn(self):
        for kind, config in (('smf', self.smf), ('upf', self.upf)):
            config[kind]['session'].append({'dnn': 'internet', 'subnet': '10.45.0.0/16'})
            with self.assertRaises(ValueError):
                candidate(config, kind)

    def test_rejects_wrong_slice_and_peer(self):
        self.smf['smf']['info'][0]['s_nssai'][0]['sst'] = 1
        with self.assertRaises(ValueError):
            candidate(self.smf, 'smf')
        self.upf['upf']['pfcp']['server'][0]['address'] = '10.210.50.8'
        with self.assertRaises(ValueError):
            candidate(self.upf, 'upf')

    def test_refuses_inferred_quota_policy(self):
        del self.smf['smf']['chf']['enabled']
        with self.assertRaises(ValueError):
            candidate(self.smf, 'smf')


if __name__ == '__main__':
    unittest.main()
