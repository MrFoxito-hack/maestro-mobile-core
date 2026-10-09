"""Run with python3 -m unittest on Linux; no networking or BPF mutation."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

import urllc_xdp_agent as agent


class AgentSafety(unittest.TestCase):
    def test_root_namespace_is_rejected(self):
        with patch.object(agent.os, 'stat', return_value=Mock(st_ino=1)), \
             patch.object(agent, 'NAMESPACE', Mock(stat=Mock(return_value=Mock(st_ino=2)))):
            with self.assertRaisesRegex(ValueError, 'Wrong network namespace'):
                agent.namespace_check()

    def test_charged_session_is_rejected_before_native_record(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'upf.yaml'
            config.write_text('upf:\n  charging_enforcement: true\n')
            with patch.object(agent,'CONFIG',config), patch.object(agent,'read') as read:
                with self.assertRaisesRegex(ValueError,'charging enforcement'):
                    agent.native()
                read.assert_not_called()

    def test_missing_explicit_unmetered_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/'upf.yaml';config.write_text('upf: {}\n')
            with patch.object(agent,'CONFIG',config):
                with self.assertRaises(ValueError):agent.native()

    def test_activate_erases_old_generations_before_opening_gate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent,'RUNTIME',Path(directory)), \
             patch.object(agent,'namespace_check'), patch.object(agent,'native',return_value={
                'ue':'10.47.0.3','generation':'12','pid':1}), \
             patch.object(agent,'read',return_value={'timestamp_ns':time.monotonic_ns()}), \
             patch.object(agent,'own_hooks',return_value=True), patch.object(agent,'write'):
            calls=[]
            with patch.object(agent,'flag',side_effect=lambda x:calls.append(('gate',x))), \
                 patch.object(agent,'clear_sessions',side_effect=lambda:calls.append(('clear',))), \
                 patch.object(agent,'install',side_effect=lambda *a:calls.append(('install',))):
                agent.activate('10.47.0.3')
            self.assertEqual(calls,[('gate',0),('clear',),('install',),('gate',1)])

    def test_partial_map_install_cannot_open_gate(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent,'RUNTIME',Path(directory)), \
             patch.object(agent,'namespace_check'), patch.object(agent,'native',return_value={
                'ue':'10.47.0.3','generation':'12','pid':1}), \
             patch.object(agent,'read',return_value={'timestamp_ns':time.monotonic_ns()}), \
             patch.object(agent,'own_hooks',return_value=True), patch.object(agent,'flag') as flag, \
             patch.object(agent,'clear_sessions'), patch.object(agent,'install',side_effect=OSError()):
            with self.assertRaises(OSError):agent.activate('10.47.0.3')
            flag.assert_called_once_with(0)

    def test_stale_watchdog_cannot_enable(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent,'RUNTIME',Path(directory)), \
             patch.object(agent,'namespace_check'), patch.object(agent,'native',return_value={'ue':'10.47.0.3'}), \
             patch.object(agent,'read',return_value={'timestamp_ns':0}), patch.object(agent,'flag') as flag:
            with self.assertRaisesRegex(ValueError,'Watchdog'):agent.activate('10.47.0.3')
            flag.assert_not_called()

    def test_confirm_rejects_replaced_native_generation(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(agent,'RUNTIME',Path(directory)), \
             patch.object(agent,'namespace_check'), patch.object(agent,'native',return_value={
                'ue':'10.47.0.3','generation':'new'}), patch.object(agent,'read',return_value={}), \
             patch.object(agent,'write') as write:
            with self.assertRaises(ValueError):agent.confirm('10.47.0.3','old')
            write.assert_not_called()


if __name__=='__main__': unittest.main()
