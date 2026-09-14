import unittest, threading, time
import gpu_coordinator as g


class FifoOrderTests(unittest.TestCase):
    """main 协调器没有 priority 队列（P1-3 合并时刻意不回植）：同模式必须严格 FIFO。

    分支版 test_gpu_priority 验证"高优先级插队"，在合并后语义不存在；
    这里改为验证它的对偶性质——先排队者先获得，后到者不得越过队首。
    """
    def setUp(self):
        g.force_release()
        g.cancel_wait('low')
        g.cancel_wait('high')

    def test_strict_fifo_order(self):
        self.assertTrue(g.acquire_lease('holder', 0.1)['ok'])
        got = []

        def wait(name):
            res = g.acquire_lease(name, 2, purpose=name)
            if res.get('ok'):
                got.append(name)
                g.release(name)

        a = threading.Thread(target=wait, args=('low',))
        b = threading.Thread(target=wait, args=('high',))
        a.start()
        time.sleep(0.05)  # 保证 low 先入队
        b.start()
        time.sleep(0.05)
        g.release('holder')
        a.join()
        b.join()
        self.assertEqual(got, ['low', 'high'])


if __name__ == '__main__':
    unittest.main()
