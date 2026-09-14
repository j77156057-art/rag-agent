import unittest
from unittest.mock import patch
import gpu_coordinator as g

class GpuProcessMonitorTests(unittest.TestCase):
    def setUp(self):
        g.GPU_MODE='serial'; g._leases.clear(); g._processes.clear(); g._recovery_events.clear()
    def tearDown(self):
        g._leases.clear(); g._processes.clear()
    def test_register_and_heartbeat(self):
        rec=g.register_process(321,'engine:test',1,'godot')
        self.assertEqual(rec['pid'],321); self.assertTrue(g.heartbeat_process(321))
        self.assertEqual(g.process_status()['processes'][0]['owner'],'engine:test')
    def test_dead_process_is_recovered_and_lease_released(self):
        g._leases[0]={'owner':'engine:test','purpose':'godot','since':0,'ttl':0}
        g.register_process(999,'engine:test',0,'godot')
        with patch.object(g,'_pid_alive',return_value=False): g._recover_processes(100)
        self.assertEqual(g._leases,{})
        self.assertEqual(g.process_status()['recovery_events'][0]['status'],'orphan_recovered')
    def test_probe_failure_is_explicit(self):
        with patch('gpu_coordinator.subprocess.run', side_effect=OSError('missing')):
            self.assertFalse(g._default_process_probe()['available'])

if __name__ == '__main__': unittest.main()
