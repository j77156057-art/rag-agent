# -*- coding: utf-8 -*-
"""P3 收尾回归：`sessions.maybe_compact` 的摘要读写必须按项目桶隔离。

背景（P3 残留风险）：`maybe_compact` 内部通过 `summary_text(session_id)` 读取「上一版摘要」，
若不透传 project_id，会按「当前项目」读取——当请求指向**非当前项目**时，会把别的项目的
摘要读进来、并据它拼接出新摘要写回去，破坏跨项目隔离。

覆盖：
① 触发压缩时（读 `prev` + 写回），摘要必须基于**本项目**的旧摘要，不得混入它项目文本；
② `project_id=None` 仍按「当前项目」工作（回归，等价改动前）；
③ `maybe_compact` 的三处 `summary_text` 读取分支（未触发 / kept 未变 / 触发压缩）
   都遵守项目参数——逐一验证，防止漏传。

全部离线、隔离：把 `config.STATE_FILE` 与 `sessions.SESSIONS_DIR` 指到临时目录。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import projects  # noqa: E402
import sessions  # noqa: E402


class CompactProjectBase(unittest.TestCase):
    SID = "sX"
    SUM_A = "A-OLD-摘要-仅属于A"
    SUM_B = "B-SECRET-摘要-仅属于B"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_compact_")
        self._old_state_file = config.STATE_FILE
        self._old_sessions_dir = sessions.SESSIONS_DIR
        self._old_runtime = dict(config._RUNTIME)
        config.STATE_FILE = os.path.join(self.tmp, ".docmind_state.json")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        self._env = mock.patch.dict(os.environ, {"DOCMIND_PROJECTS": "1"})
        self._env.start()

        self.pid_a = projects.ensure_project(self._mkdir("Alpha"))
        self.pid_b = projects.ensure_project(self._mkdir("Beta"))
        # 为 A、B 各写一段**不同**的旧摘要（同一 session_id）
        self.assertTrue(sessions.save(self.SID, [{"user": "a1", "assistant": "r1"}],
                                      summary=self.SUM_A, project_id=self.pid_a))
        self.assertTrue(sessions.save(self.SID, [{"user": "b1", "assistant": "r1"}],
                                      summary=self.SUM_B, project_id=self.pid_b))

    def tearDown(self):
        self._env.stop()
        config.STATE_FILE = self._old_state_file
        sessions.SESSIONS_DIR = self._old_sessions_dir
        config._RUNTIME.clear()
        config._RUNTIME.update(self._old_runtime)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _turns(n):
        return [{"user": f"问题{i}-需要被压缩进摘要", "assistant": f"回答{i}"} for i in range(n)]

    def _trigger(self, project_id, n=6):
        """强制触发压缩：trigger/keep 取极小值，轮数 > KEEP_TURNS_MIN。"""
        return sessions.maybe_compact(self.SID, self._turns(n), llm=None,
                                      trigger_tokens=1, keep_tokens=1,
                                      project_id=project_id)


class CompactIsolationTests(CompactProjectBase):
    def test_compaction_reads_and_keeps_own_project_summary(self):
        kept, summary = self._trigger(self.pid_a)
        self.assertIn(self.SUM_A, summary, "压缩结果必须基于 A 自己的旧摘要")
        self.assertNotIn(self.SUM_B, summary, "不得把 B 项目的摘要混入 A")
        self.assertLess(len(kept), 6, "应确实发生了压缩")
        # 未写回 B 桶：B 的旧摘要保持不变
        self.assertEqual(sessions.summary_text(self.SID, self.pid_b), self.SUM_B)

    def test_compaction_for_other_project_isolated(self):
        _, summary_b = self._trigger(self.pid_b)
        self.assertIn(self.SUM_B, summary_b)
        self.assertNotIn(self.SUM_A, summary_b)
        self.assertEqual(sessions.summary_text(self.SID, self.pid_a), self.SUM_A)

    def test_none_project_id_uses_current_project(self):
        # 当前 = A：不带 project_id 时按 A 的旧摘要压缩
        self.assertTrue(projects.set_current(self.pid_a))
        _, s_a = sessions.maybe_compact(self.SID, self._turns(6), llm=None,
                                        trigger_tokens=1, keep_tokens=1)
        self.assertIn(self.SUM_A, s_a)
        self.assertNotIn(self.SUM_B, s_a)
        # 当前切到 B：不带 project_id 时按 B 的旧摘要压缩（回归：仍随「当前项目」）
        self.assertTrue(projects.set_current(self.pid_b))
        _, s_b = sessions.maybe_compact(self.SID, self._turns(6), llm=None,
                                        trigger_tokens=1, keep_tokens=1)
        self.assertIn(self.SUM_B, s_b)
        self.assertNotIn(self.SUM_A, s_b)


class CompactAllReadBranchesTests(CompactProjectBase):
    """逐一覆盖 maybe_compact 内三处 summary_text 读取分支都遵守 project_id。"""

    def test_branch_not_triggered_returns_project_summary(self):
        # 不触发（trigger 极大）→ 走第一个 return 分支
        turns = self._turns(3)
        out_turns, summary = sessions.maybe_compact(self.SID, turns, llm=None,
                                                    trigger_tokens=10 ** 9,
                                                    keep_tokens=10 ** 9,
                                                    project_id=self.pid_a)
        self.assertEqual(out_turns, turns, "未触发时应原样返回 turns")
        self.assertEqual(summary, self.SUM_A)
        _, summary_b = sessions.maybe_compact(self.SID, turns, llm=None,
                                              trigger_tokens=10 ** 9, keep_tokens=10 ** 9,
                                              project_id=self.pid_b)
        self.assertEqual(summary_b, self.SUM_B)

    def test_branch_kept_unchanged_returns_project_summary(self):
        # over 阈但轮数 ≤ KEEP_TURNS_MIN → 走「kept >= len(turns)」分支
        turns = self._turns(sessions.KEEP_TURNS_MIN)
        out_turns, summary = sessions.maybe_compact(self.SID, turns, llm=None,
                                                    trigger_tokens=1, keep_tokens=1,
                                                    project_id=self.pid_a)
        self.assertEqual(out_turns, turns)
        self.assertEqual(summary, self.SUM_A)
        _, summary_b = sessions.maybe_compact(self.SID, turns, llm=None,
                                              trigger_tokens=1, keep_tokens=1,
                                              project_id=self.pid_b)
        self.assertEqual(summary_b, self.SUM_B)


if __name__ == '__main__':
    unittest.main()
