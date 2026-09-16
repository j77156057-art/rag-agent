# -*- coding: utf-8 -*-
"""运行时状态根隔离回归（C2）。

测试进程默认把 STATE_ROOT 指到临时目录：所有运行时状态（会话/预算/trace/GPU/钩子/
技能/.chroma）都应派生自 STATE_ROOT，而不污染仓库根。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent_trace
import config
import gpu_coordinator as g
import hooks
import pricing
import sessions
import skills


def _under(path, root) -> bool:
    path = os.path.abspath(str(path))
    root = os.path.abspath(str(root))
    return path == root or path.startswith(root + os.sep)


class StateRootIsolationTests(unittest.TestCase):
    def test_state_root_differs_from_base_dir(self):
        self.assertNotEqual(
            os.path.abspath(config.STATE_ROOT), os.path.abspath(config.BASE_DIR),
            "测试进程的 STATE_ROOT 应被隔离到临时目录，而非仓库根",
        )

    def test_all_runtime_state_paths_derived_from_state_root(self):
        # 每项: 名称 -> (实际路径, 可覆盖它的显式 env)。显式 env 优先于 STATE_ROOT
        # 派生（如 .env 里的 CHROMA_DIR），故该项被显式覆盖时跳过 STATE_ROOT 断言。
        paths = {
            "config.CHROMA_DIR": (config.CHROMA_DIR, "CHROMA_DIR"),
            "config.STATE_FILE": (config.STATE_FILE, None),
            "sessions.SESSIONS_DIR": (sessions.SESSIONS_DIR, "DOCMIND_SESSIONS_DIR"),
            "pricing.PRICING_FILE": (pricing.PRICING_FILE, "DOCMIND_PRICING_FILE"),
            "pricing.BUDGET_FILE": (pricing.BUDGET_FILE, "DOCMIND_BUDGET_FILE"),
            "agent_trace.TRACE_FILE": (agent_trace.TRACE_FILE, "DOCMIND_TRACE_FILE"),
            "gpu.STATE_FILE": (g.STATE_FILE, "DOCMIND_GPU_STATE_FILE"),
            "gpu.SAMPLES_FILE": (g.SAMPLES_FILE, "DOCMIND_GPU_SAMPLES_FILE"),
            "hooks.HOOKS_DIR": (hooks.HOOKS_DIR, "DOCMIND_HOOKS_DIR"),
            "skills.SKILLS_DIR": (skills.SKILLS_DIR, "DOCMIND_SKILLS_DIR"),
        }
        for name, (p, env_var) in paths.items():
            with self.subTest(path=name):
                if env_var and os.getenv(env_var):
                    continue  # 显式 env 覆盖优先，允许不在 STATE_ROOT 下
                self.assertTrue(_under(p, config.STATE_ROOT),
                                f"{name} 未落在 STATE_ROOT 下：{p}")

    def test_default_paths_derive_from_injected_state_root(self):
        """无显式 CHROMA_DIR 覆盖时，CHROMA_DIR / STATE_FILE 均应派生自 STATE_ROOT。

        用子进程验证，避免 reload 污染当前进程；CHROMA_DIR 置空串以压过 .env 的默认。
        """
        root = tempfile.mkdtemp(prefix="docmind_root_")
        env = dict(os.environ)
        env["DOCMIND_STATE_ROOT"] = root
        env["CHROMA_DIR"] = ""  # 空串=视为未设置，走 STATE_ROOT 派生
        code = (
            "import json, sys;"
            "import config;"
            "print(json.dumps({'STATE_ROOT': config.STATE_ROOT,"
            "'STATE_FILE': config.STATE_FILE, 'CHROMA_DIR': config.CHROMA_DIR}))"
        )
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(os.path.abspath(data["STATE_ROOT"]), os.path.abspath(root))
        self.assertTrue(_under(data["STATE_FILE"], root))
        self.assertTrue(_under(data["CHROMA_DIR"], root))

    def test_persistence_does_not_write_repo_root(self):
        """跑一遍会话落盘 + 预算落盘；仓库根不应出现新的状态文件。"""
        repo_sessions = os.path.join(config.BASE_DIR, ".docmind_sessions")
        repo_budget = os.path.join(config.BASE_DIR, ".docmind_budget.json")
        sessions_before = os.path.exists(repo_sessions)
        budget_before = os.path.exists(repo_budget)

        sid = "__iso_root_test__"
        sessions.save(sid, [{"role": "user", "content": "hi"},
                            {"role": "assistant", "content": "ok"}])
        pricing.charge(0.01, session_id=sid)
        try:
            # 模块自身路径必须在 STATE_ROOT（隔离生效）
            self.assertTrue(_under(sessions.SESSIONS_DIR, config.STATE_ROOT))
            self.assertTrue(_under(pricing.BUDGET_FILE, config.STATE_ROOT))
            # 若仓库根原本没有这些文件，落盘后也不应凭空出现
            if not sessions_before:
                self.assertFalse(os.path.exists(repo_sessions),
                                 "会话落盘不得写进仓库根")
            if not budget_before:
                self.assertFalse(os.path.exists(repo_budget),
                                 "预算落盘不得写进仓库根")
        finally:
            sessions.delete(sid)
            pricing.reset(sid)


if __name__ == "__main__":
    unittest.main()
