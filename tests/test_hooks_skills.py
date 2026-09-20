# -*- coding: utf-8 -*-
"""hooks 热插拔与 skill 热插拔（离线）。"""
import os
import tempfile
import unittest

import agent as agent_mod
import agent_trace
import hooks
import sessions
import skills


class _SeqLLM:
    provider = "fake"
    model = "seq"

    def __init__(self, scripts):
        self.scripts = scripts
        self.i = 0
        self.last_usage = {}
        self.last_tool_calls = []

    def chat(self, messages, stream=True, **kw):
        s = self.scripts[min(self.i, len(self.scripts) - 1)]
        self.i += 1
        self.last_usage = {"prompt_tokens": 3, "completion_tokens": 1}
        self.last_tool_calls = []
        if stream:
            def g():
                for ch in s:
                    yield ch
            return g()
        return s

    def count_tokens(self, text):
        return max(1, len(text) // 3)


class _HookBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_hook_")
        self._old = hooks.HOOKS_DIR
        hooks.HOOKS_DIR = self.tmp

    def tearDown(self):
        hooks.HOOKS_DIR = self._old
        hooks.reload()

    def _write(self, name, code):
        with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
            f.write(code)


class HookRegistryTests(_HookBase):
    def test_all_kinds_registered_and_run(self):
        self._write("h.py", (
            "def pre_tool(name, arg):\n"
            "    return {'arg': arg + ' [h1]'}\n"
            "def post_tool(name, arg, obs):\n"
            "    return {'obs': obs + ' <post>'}\n"
            "def pre_turn(q):\n"
            "    return {'question': q + ' [t]'}\n"
            "def post_turn(record):\n"
            "    record['seen'] = True\n"
        ))
        r = hooks.reload()
        self.assertEqual(r["loaded"], 1)
        self.assertEqual(hooks.list_hooks()["counts"],
                         {"pre_tool": 1, "post_tool": 1, "pre_turn": 1, "post_turn": 1})
        self.assertEqual(hooks.run_pre_tool("t", "x"), (False, "", "x [h1]"))
        self.assertEqual(hooks.run_post_tool("t", "x", "OBS"), "OBS <post>")
        self.assertEqual(hooks.run_pre_turn("Q"), (False, "", "Q [t]"))

    def test_pre_tool_block(self):
        self._write("b.py", (
            "def pre_tool(name, arg):\n"
            "    if name == 'danger':\n"
            "        return {'block': True, 'reason': 'nope'}\n"
        ))
        hooks.reload()
        blocked, reason, arg = hooks.run_pre_tool("danger", "x")
        self.assertTrue(blocked)
        self.assertEqual(reason, "nope")

    def test_broken_hook_is_isolated(self):
        self._write("ok.py", "def pre_tool(n, a):\n    return {'arg': a + ' ok'}\n")
        self._write("bad.py", "raise RuntimeError('boom')\n")
        r = hooks.reload()
        self.assertEqual(r["loaded"], 1)
        self.assertTrue(any("boom" in e["error"] for e in r["errors"]))
        # 好钩子仍然生效
        self.assertEqual(hooks.run_pre_tool("t", "x")[2], "x ok")

    def test_hook_exception_does_not_break_run(self):
        self._write("boom.py", "def pre_tool(n, a):\n    raise ValueError('kaboom')\n")
        hooks.reload()
        self.assertEqual(hooks.run_pre_tool("t", "x"), (False, "", "x"))

    def test_missing_dir_is_noop(self):
        hooks.HOOKS_DIR = os.path.join(self.tmp, "nope")
        self.assertEqual(hooks.reload()["loaded"], 0)
        self.assertFalse(hooks.has_any())


class SkillRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_skill_")
        self._old = skills.SKILLS_DIR
        self._old_builtin = skills.BUILTIN_SKILLS_DIR
        skills.SKILLS_DIR = self.tmp
        skills.BUILTIN_SKILLS_DIR = os.path.join(self.tmp, "builtin")

    def tearDown(self):
        skills.SKILLS_DIR = self._old
        skills.BUILTIN_SKILLS_DIR = self._old_builtin
        skills.reload()

    def test_loads_frontmatter_and_plain_markdown(self):
        os.makedirs(os.path.join(self.tmp, "audio"))
        with open(os.path.join(self.tmp, "audio", "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: 音频工程\ndescription: 混音与总线\nwhen_to_use: 问音频时\n---\n正文A")
        with open(os.path.join(self.tmp, "net.md"), "w", encoding="utf-8") as f:
            f.write("# 联网研究\n正文B")
        r = skills.reload()
        self.assertEqual(r["loaded"], 2)
        self.assertIn("音频工程", skills.use_skill("音频工程"))
        self.assertIn("正文B", skills.use_skill("net"))

    def test_catalog_lists_without_body(self):
        with open(os.path.join(self.tmp, "s.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: S\ndescription: D\nwhen_to_use: W\n---\nSECRETBODY")
        skills.reload()
        cat = skills.catalog_text()
        self.assertIn("S", cat)
        self.assertNotIn("SECRETBODY", cat)

    def test_use_skill_accepts_name_prefix(self):
        with open(os.path.join(self.tmp, "s.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: S\ndescription: D\n---\nBODY")
        skills.reload()
        self.assertIn("BODY", skills.use_skill("name: S"))

    def test_missing_skill_reports_available(self):
        with open(os.path.join(self.tmp, "s.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: S\ndescription: D\n---\nB")
        skills.reload()
        out = skills.use_skill("不存在")
        self.assertIn("未找到技能", out)
        self.assertIn("S", out)

    def test_hot_reload_picks_up_new_skill(self):
        skills.reload()
        self.assertEqual(skills.list_skills()["count"], 0)
        with open(os.path.join(self.tmp, "new.md"), "w", encoding="utf-8") as f:
            f.write("# 新技能\n内容")
        skills.reload()
        self.assertEqual(skills.list_skills()["count"], 1)

    def test_user_skill_overrides_builtin_and_references_are_ignored(self):
        builtin = os.path.join(skills.BUILTIN_SKILLS_DIR, "documents")
        os.makedirs(builtin)
        with open(os.path.join(builtin, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: documents\ndescription: built in\n---\nBUILTIN")
        with open(os.path.join(builtin, "reference.md"), "w", encoding="utf-8") as f:
            f.write("# Must not become a skill")
        with open(os.path.join(skills.SKILLS_DIR, "documents.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: documents\ndescription: custom\n---\nCUSTOM")
        result = skills.reload()
        self.assertEqual(result["loaded"], 1)
        self.assertIn("CUSTOM", skills.use_skill("documents"))
        self.assertEqual(skills.list_skills()["items"][0]["source"], "user")


class AgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_hs_")
        self._hooks_dir = hooks.HOOKS_DIR
        self._skills_dir = skills.SKILLS_DIR
        self._builtin_skills_dir = skills.BUILTIN_SKILLS_DIR
        self._trace = agent_trace.TRACE_FILE
        self._sess = sessions.SESSIONS_DIR
        hooks.HOOKS_DIR = os.path.join(self.tmp, "hooks")
        skills.SKILLS_DIR = os.path.join(self.tmp, "skills")
        skills.BUILTIN_SKILLS_DIR = os.path.join(self.tmp, "builtin-skills")
        agent_trace.TRACE_FILE = os.path.join(self.tmp, "t.jsonl")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "s")
        os.makedirs(hooks.HOOKS_DIR)
        os.makedirs(skills.SKILLS_DIR)

    def tearDown(self):
        hooks.HOOKS_DIR = self._hooks_dir
        skills.SKILLS_DIR = self._skills_dir
        skills.BUILTIN_SKILLS_DIR = self._builtin_skills_dir
        agent_trace.TRACE_FILE = self._trace
        sessions.SESSIONS_DIR = self._sess
        hooks.reload()
        skills.reload()

    def test_skill_catalog_injected_into_prompt(self):
        with open(os.path.join(skills.SKILLS_DIR, "s.md"), "w", encoding="utf-8") as f:
            f.write("---\nname: 音频工程\ndescription: 混音\n---\nBODY")
        skills.reload()
        a = agent_mod.Agent(llm=_SeqLLM([]))
        msgs = a._build_messages("q")
        self.assertTrue(any("可用技能" in (m.get("content") or "") for m in msgs))

    def test_pre_tool_hook_rewrites_arg_in_real_run(self):
        with open(os.path.join(hooks.HOOKS_DIR, "h.py"), "w", encoding="utf-8") as f:
            f.write("def pre_tool(name, arg):\n    return {'arg': '2+2'}\n")
        hooks.reload()
        llm = _SeqLLM(["Action: calculate\nAction Input: 1+1", "Final Answer: 完成"])
        a = agent_mod.Agent(llm=llm)
        events = list(a.run("算", stream=True))
        obs = " ".join(e["text"] for e in events if e["type"] == "observation")
        self.assertIn("4", obs, obs)   # 参数被钩子改写成 2+2

    def test_pre_tool_hook_blocks_execution(self):
        with open(os.path.join(hooks.HOOKS_DIR, "h.py"), "w", encoding="utf-8") as f:
            f.write("def pre_tool(name, arg):\n    return {'block': True, 'reason': '出于安全'}\n")
        hooks.reload()
        llm = _SeqLLM(["Action: calculate\nAction Input: 1+1", "Final Answer: 完成"])
        a = agent_mod.Agent(llm=llm)
        events = list(a.run("算", stream=True))
        obs = " ".join(e["text"] for e in events if e["type"] == "observation")
        self.assertIn("[钩子拦截]", obs)
        self.assertIn("出于安全", obs)

    def test_pre_turn_hook_can_block_turn(self):
        with open(os.path.join(hooks.HOOKS_DIR, "h.py"), "w", encoding="utf-8") as f:
            f.write("def pre_turn(q):\n    return {'block': True, 'reason': '维护中'}\n")
        hooks.reload()
        a = agent_mod.Agent(llm=_SeqLLM(["Final Answer: 不该出现"]))
        events = list(a.run("q", stream=True))
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("维护中", events[-1]["text"])
        self.assertEqual(agent_trace.recent(1)[0]["outcome"], "hook_blocked")


if __name__ == "__main__":
    unittest.main()
