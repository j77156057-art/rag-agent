# -*- coding: utf-8 -*-
"""D1 回归：项目化请求不得改写「全局/持久化 code_root」（=当前项目指针）。

背景（QA 独立验证确认的中低危真 Bug）：`current_project_id()==A` 时，带
`X-DocMind-Project: <pidB>` 调 `POST /api/ingest_code`（或 reset_code）会把**全局**
`_RUNTIME['code_root']` 与 `STATE_FILE.code_root` 改成 B，而当前项目仍是 A —— 二者脱节，
导致请求作用域之外（daemon 线程 / 启动期 / 脚本 / 跨请求）读到错误项目。

修法：全局/持久化 `code_root`（及 `project_rules`）只在「无项目 或 目标=当前项目」时写入；
目标为非当前项目时，只完成项目作用域内的索引工作（写进 `projects.code_collection(pid)`），
并返回中文 notice，不静默。

覆盖：
① 带 B 头 ingest（当前=A）→ 全局/持久化 code_root 不变、current 仍 A、索引写入 B 的集合；
② 不带头 / 头=A ingest → 全局与持久化照旧被写（生命线回归）；
③ 带 B 头 reset_code → 不清全局；无头（当前 A）reset_code → 照旧清空。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import config  # noqa: E402
import projects  # noqa: E402
import sessions  # noqa: E402


class IngestGuardBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_d1_")
        self._old_state_file = config.STATE_FILE
        self._old_sessions_dir = sessions.SESSIONS_DIR
        self._old_runtime = dict(config._RUNTIME)
        self._old_agents = dict(api._SESSION_AGENTS)
        config.STATE_FILE = os.path.join(self.tmp, ".docmind_state.json")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        self._env = mock.patch.dict(os.environ, {"DOCMIND_PROJECTS": "1",
                                                 "DOCMIND_OLLAMA_PROBE": "0"})
        self._env.start()
        config.set_runtime("llm_provider", "mock")  # 避免 Agent→LLMClient 触发网络探活

        self.root_a = self._mkdir("Alpha")
        self.root_b = self._mkdir("Beta")
        self.pid_a = projects.ensure_project(self.root_a, "Alpha")
        self.pid_b = projects.ensure_project(self.root_b, "Beta")
        self.assertTrue(projects.set_current(self.pid_a))
        # 全局/持久化 code_root 初始指向当前项目 A
        config.set_runtime("code_root", os.path.abspath(self.root_a))
        config.save_state("code_root", os.path.abspath(self.root_a))

        # 打桩掉真实索引 / 集合 / 引擎，专注检验「全局指针是否被改写」
        self.captured = {}

        def _fake_count(coll):
            self.captured["count_coll"] = coll
            return 0

        def _fake_reset(coll):
            self.captured["reset_coll"] = coll

        def _fake_ingest(root, collection=None):
            self.captured["ingest_coll"] = collection
            return 7

        self._patches = [
            mock.patch.object(api, "count", side_effect=_fake_count),
            mock.patch.object(api, "reset_collection", side_effect=_fake_reset),
            mock.patch.object(api, "ingest_code_directory", side_effect=_fake_ingest),
            mock.patch.object(api, "load_project_rules", return_value=""),
            mock.patch.object(api, "engine_running_roots", return_value=[]),
        ]
        for p in self._patches:
            p.start()

        async def _fake_stop(_root):
            return [], []

        self._stop = mock.patch.object(api, "_stop_other_engines", new=_fake_stop)
        self._stop.start()

        from starlette.testclient import TestClient
        self.client = TestClient(api.app)

    def tearDown(self):
        self._stop.stop()
        for p in self._patches:
            p.stop()
        self._env.stop()
        config.STATE_FILE = self._old_state_file
        sessions.SESSIONS_DIR = self._old_sessions_dir
        config._RUNTIME.clear()
        config._RUNTIME.update(self._old_runtime)
        api._SESSION_AGENTS.clear()
        api._SESSION_AGENTS.update(self._old_agents)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        return d


class IngestNonCurrentProjectTests(IngestGuardBase):
    def test_ingest_with_noncurrent_header_keeps_global_pointer(self):
        r = self.client.post("/api/ingest_code", data={"root": os.path.abspath(self.root_b)},
                             headers={"X-DocMind-Project": self.pid_b})
        self.assertEqual(r.status_code, 200, r.text)
        # 全局与持久化指针必须原封不动（仍指 A）
        self.assertEqual(config.get_runtime("code_root"), os.path.abspath(self.root_a),
                         "全局 _RUNTIME['code_root'] 不得被非当前项目请求改写")
        self.assertEqual(config.load_state("code_root"), os.path.abspath(self.root_a),
                         "STATE_FILE.code_root 不得被非当前项目请求改写")
        self.assertEqual(projects.current_project_id(), self.pid_a, "当前项目不得被切换")
        # 请求自身仍如实报告其目标 root，并给出中文 notice（不静默）
        self.assertEqual(r.json()["code_root"], os.path.abspath(self.root_b))
        self.assertIn("当前项目未切换", r.json().get("notice", ""))

    def test_ingest_writes_into_target_project_collection(self):
        self.client.post("/api/ingest_code", data={"root": os.path.abspath(self.root_b)},
                         headers={"X-DocMind-Project": self.pid_b})
        expect = projects.code_collection(self.pid_b)
        self.assertEqual(expect, "docmind_code__" + self.pid_b)
        self.assertEqual(self.captured.get("reset_coll"), expect, "reset 必须作用于 B 的集合")
        self.assertEqual(self.captured.get("ingest_coll"), expect, "索引必须写进 B 的集合")
        self.assertNotEqual(expect, projects.code_collection(self.pid_a))

    def test_ingest_registration_root_stays_correct(self):
        self.client.post("/api/ingest_code", data={"root": os.path.abspath(self.root_b)},
                         headers={"X-DocMind-Project": self.pid_b})
        self.assertEqual(projects.get_project(self.pid_b)["root"], os.path.abspath(self.root_b))


class IngestCurrentProjectLifelineTests(IngestGuardBase):
    def test_ingest_without_header_tracks_current_project(self):
        r = self.client.post("/api/ingest_code", data={"root": os.path.abspath(self.root_a)})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.get_runtime("code_root"), os.path.abspath(self.root_a))
        self.assertEqual(config.load_state("code_root"), os.path.abspath(self.root_a))
        self.assertNotIn("当前项目未切换", r.json().get("notice", ""))

    def test_ingest_with_current_header_still_writes_global(self):
        r = self.client.post("/api/ingest_code", data={"root": os.path.abspath(self.root_a)},
                             headers={"X-DocMind-Project": self.pid_a})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.get_runtime("code_root"), os.path.abspath(self.root_a))
        self.assertEqual(config.load_state("code_root"), os.path.abspath(self.root_a))


class ResetCodeGuardTests(IngestGuardBase):
    def test_reset_with_noncurrent_header_does_not_wipe_global(self):
        r = self.client.post("/api/reset_code", headers={"X-DocMind-Project": self.pid_b})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.get_runtime("code_root"), os.path.abspath(self.root_a),
                         "非当前项目的 reset_code 不得清空全局 code_root")
        self.assertEqual(config.load_state("code_root"), os.path.abspath(self.root_a))
        self.assertEqual(self.captured.get("reset_coll"), projects.code_collection(self.pid_b),
                         "reset 必须仍清 B 的集合")

    def test_reset_without_header_wipes_global(self):
        r = self.client.post("/api/reset_code")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(config.get_runtime("code_root"), "")
        self.assertEqual(config.load_state("code_root"), "")


if __name__ == '__main__':
    unittest.main()
