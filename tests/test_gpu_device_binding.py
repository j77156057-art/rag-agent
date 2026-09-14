import unittest, os
import gpu_coordinator as g


class DeviceBindingTests(unittest.TestCase):
    def test_status_reports_device(self):
        old = os.environ.get('DOCMIND_GPU_INDEX')
        os.environ['DOCMIND_GPU_INDEX'] = '2'
        try:
            g.force_release()
            # 显式 gpu=2：租约结果与 status().device_index 都应反映该卡
            res = g.acquire_lease('dev-test', 0.1, gpu=2)
            self.assertTrue(res['ok'])
            self.assertEqual(res['gpu'], 2)
            self.assertEqual(g.status()['device_index'], 2)
            # 环境注入：显式卡号优先
            self.assertEqual(g.process_environment(2)['CUDA_VISIBLE_DEVICES'], '2')
            self.assertEqual(g.process_environment(2)['DOCMIND_GPU_INDEX'], '2')
            g.release('dev-test')
            # 无租约时 process_environment(None) 回落到 DOCMIND_GPU_INDEX
            self.assertEqual(g.process_environment()['CUDA_VISIBLE_DEVICES'], '2')
        finally:
            if old is None:
                os.environ.pop('DOCMIND_GPU_INDEX', None)
            else:
                os.environ['DOCMIND_GPU_INDEX'] = old


if __name__ == '__main__':
    unittest.main()
