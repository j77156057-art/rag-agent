"""P1 GPU FIFO 租约队列测试。"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gpu_coordinator as g  # noqa: E402


class GpuLeaseQueueTest(unittest.TestCase):
    def setUp(self):
        # 每个用例从干净状态开始
        g.force_release()
        while g.status()["queue_length"]:
            time.sleep(0.01)

    def tearDown(self):
        g.force_release()

    def test_acquire_release_basic(self):
        self.assertTrue(g.acquire("owner-a", 0.1, purpose="llm"))
        st = g.status()
        self.assertEqual(st["active"], "owner-a")
        self.assertEqual(st["purpose"], "llm")
        g.release("owner-a")
        self.assertIsNone(g.status()["active"])

    def test_busy_with_zero_timeout_rejects_without_queue(self):
        self.assertTrue(g.acquire("holder", 0.1))
        self.assertFalse(g.acquire("other", 0))
        self.assertEqual(g.status()["queue_length"], 0)
        g.release("holder")

    def test_fifo_handover(self):
        order = []
        self.assertTrue(g.acquire("holder", 0.1))

        def waiter(name):
            if g.acquire(name, 3.0, purpose=name):
                order.append(name)
                time.sleep(0.05)
                g.release(name)

        t1 = threading.Thread(target=waiter, args=("w1",))
        t2 = threading.Thread(target=waiter, args=("w2",))
        # 不能靠 sleep(0.05) 赌线程调度：机器一忙 w2 会先入队，用例随机变红。
        # 改成等队列真的出现 w1 再放 w2，FIFO 断言就不再依赖时序运气。
        t1.start()
        self.assertTrue(self._wait_queue(["w1"]), g.status()["queue"])
        t2.start()
        self.assertTrue(self._wait_queue(["w1", "w2"]), g.status()["queue"])
        g.release("holder")
        t1.join(2); t2.join(2)
        self.assertEqual(order, ["w1", "w2"])
        self.assertIsNone(g.status()["active"])

    @staticmethod
    def _wait_queue(expected, timeout=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if [q["owner"] for q in g.status()["queue"]] == expected:
                return True
            time.sleep(0.005)
        return False

    def test_timeout_leaves_queue(self):
        self.assertTrue(g.acquire("holder", 0.1))
        self.assertFalse(g.acquire("slow", 0.25))
        self.assertEqual(g.status()["queue_length"], 0)
        g.release("holder")

    def test_same_owner_reentrant(self):
        self.assertTrue(g.acquire("same", 0.1))
        self.assertTrue(g.acquire("same", 0.1))  # 同 owner 直接放行
        g.release("same")
        self.assertIsNone(g.status()["active"])

    def test_ttl_expiry_and_handover(self):
        self.assertTrue(g.acquire("crashed", 0.1, ttl=0.2))
        got = {}

        def waiter():
            got["ok"] = g.acquire("next", 2.0)

        t = threading.Thread(target=waiter)
        t.start()
        t.join(2.5)
        self.assertTrue(got.get("ok"))
        self.assertEqual(g.status()["active"], "next")
        g.release("next")

    def test_force_release(self):
        g.acquire("x", 0.1)
        self.assertEqual(g.force_release(), "x")
        self.assertIsNone(g.status()["active"])
        self.assertIsNone(g.force_release())

    def test_release_wrong_owner_ignored(self):
        g.acquire("real", 0.1)
        g.release("impostor")
        self.assertEqual(g.status()["active"], "real")
        g.release("real")


if __name__ == "__main__":
    unittest.main()
