# -*- coding: utf-8 -*-
"""Agent ReAct 循环护栏单测：空参拦截、步数耗尽强制收尾、证据兜底、python_exec cwd。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as agent_mod
import tools as tools_mod
from config import set_runtime


class _ScriptedLLM:
    """按调用顺序返回预设 ReAct 文本；stream 形态返回 list（finish_reason=None）。"""

    def __init__(self, scripts):
        self._scripts = list(scripts)
        self.calls = 0

    def chat(self, messages, stream=True, **kwargs):
        i = min(self.calls, len(self._scripts) - 1)
        self.calls += 1
        return [self._scripts[i]]

    def count_tokens(self, text):
        return 0


def _act(tool, inp):
    return f"Thought: t\nAction: {tool}\nAction Input: {inp}"


_FINAL_OK = "Thought: t\nFinal Answer: 已确认结论。"


def _run(agent):
    events = []
    for ev in agent.run("q", stream=True):
        events.append(ev)
    return events


class AgentGuardTests(unittest.TestCase):
    def setUp(self):
        self._old_tools = agent_mod.TOOLS

    def tearDown(self):
        agent_mod.TOOLS = self._old_tools
        set_runtime("code_root", "")

    def test_empty_arg_is_blocked_without_tool_step(self):
        """空 Action Input：不执行工具、不计步数，回填后模型可正常收尾。"""
        called = []

        def fake_search(arg):
            called.append(arg)
            return "不应被调用"

        agent_mod.TOOLS = {"search_code": {"func": fake_search}}
        a = agent_mod.Agent(llm=_ScriptedLLM([
            _act("search_code", ""),   # 空参，应被拦截
            _FINAL_OK,
        ]))
        events = _run(a)
        self.assertEqual(called, [])  # 工具函数从未执行
        self.assertTrue(any("缺少参数" in e.get("text", "") for e in events))
        finals = [e["text"] for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertIn("已确认结论", finals[0])

    def test_step_budget_forces_final_from_observations(self):
        """8 步后第 9 个动作先被强制收尾提示，模型据此给出 Final Answer。"""
        counter = {"n": 0}

        def fake_tool(arg):
            counter["n"] += 1
            return f"ok-{arg}"

        agent_mod.TOOLS = {"fake_tool": {"func": fake_tool}}
        scripts = [_act("fake_tool", f"inp{i}") for i in range(1, 9)]
        scripts.append(_act("fake_tool", "inp9"))  # 触发强制收尾 nudge
        scripts.append(_FINAL_OK)                 # 模型基于观察收尾
        a = agent_mod.Agent(llm=_ScriptedLLM(scripts))
        events = _run(a)
        self.assertEqual(counter["n"], 8)          # 第 9 次未执行
        self.assertTrue(any("收尾" in e.get("text", "")
                            for e in events if e["type"] == "reflection"))
        finals = [e["text"] for e in events if e["type"] == "final"]
        self.assertEqual(finals, ["已确认结论。"])

    def test_step_budget_fallback_carries_evidence(self):
        """强制收尾后模型仍要调工具：确定性兜底，含已执行清单与最后观察，不编造结论。"""
        def fake_tool(arg):
            return f"obs-{arg}"

        agent_mod.TOOLS = {"fake_tool": {"func": fake_tool}}
        scripts = [_act("fake_tool", f"inp{i}") for i in range(1, 9)]
        scripts.append(_act("fake_tool", "inp9"))   # 强制 nudge
        scripts.append(_act("fake_tool", "inp10"))  # 仍要工具 -> 证据兜底
        a = agent_mod.Agent(llm=_ScriptedLLM(scripts))
        events = _run(a)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        text = finals[0]["text"]
        self.assertIn("最大工具调用步数", text)
        self.assertIn("fake_tool(inp1)", text)
        self.assertIn("fake_tool(inp8)", text)
        self.assertIn("obs-inp8", text)           # 最后真实观察
        self.assertNotIn("已确认结论", text)

    def test_python_exec_runs_within_code_root(self):
        """配置 code_root 后，python_exec 的相对路径按代码根解析。"""
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "marker_t5.txt")
            with open(marker, "w", encoding="utf-8") as f:
                f.write("x")
            set_runtime("code_root", d)
            out = tools_mod.python_exec(
                "import os; print(os.path.exists('marker_t5.txt'))"
            )
            self.assertIn("True", out)

    def test_repeated_empty_args_terminate_via_repeat_guard(self):
        """连续空参：第 1 次回填，第 2/3 次重复升级，第 4 次强制收尾，仍不收尾则证据兜底。"""
        called = []

        def fake_search(arg):
            called.append(arg)
            return "不应被调用"

        agent_mod.TOOLS = {"search_code": {"func": fake_search}}
        scripts = [_act("search_code", "") for _ in range(6)]
        a = agent_mod.Agent(llm=_ScriptedLLM(scripts))
        events = _run(a)
        self.assertEqual(called, [])  # 工具函数始终未执行
        finals = [e["text"] for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        self.assertIn("反复", finals[0])
        self.assertIn("已执行", finals[0])      # 证据兜底形态，而非裸停止语
        blocked = [e for e in events if e["type"] == "action" and "已拦截" in e["text"]]
        self.assertEqual(len(blocked), 1)       # 只有第 1 次发空参回填，后续被重复护栏截停

    def test_repeat_budget_forces_final_from_observations(self):
        """同参第 4 次（repeats=3）先强制收尾，模型可据此直接给出 Final Answer。"""
        counter = {"n": 0}

        def fake_tool(arg):
            counter["n"] += 1
            return f"obs-{arg}"

        agent_mod.TOOLS = {"fake_tool": {"func": fake_tool}}
        scripts = [_act("fake_tool", "inp1") for _ in range(4)]
        scripts.append(_FINAL_OK)  # 强制收尾后模型基于观察给出答案
        a = agent_mod.Agent(llm=_ScriptedLLM(scripts))
        events = _run(a)
        self.assertEqual(counter["n"], 1)       # 工具只真正执行 1 次
        self.assertTrue(any("重复空转" in e.get("text", "")
                            for e in events if e["type"] == "reflection"))
        finals = [e["text"] for e in events if e["type"] == "final"]
        self.assertEqual(finals, ["已确认结论。"])

    def test_forced_final_blocks_different_args_too(self):
        """强制收尾后模型换一个【不同参数】的工具调用：同样拦截并证据兜底。"""
        counter = {"n": 0}

        def fake_tool(arg):
            counter["n"] += 1
            return f"obs-{arg}"

        agent_mod.TOOLS = {"fake_tool": {"func": fake_tool}}
        scripts = [_act("fake_tool", "inp1") for _ in range(4)]
        scripts.append(_act("fake_tool", "other"))  # 换新参数也不许再探索
        a = agent_mod.Agent(llm=_ScriptedLLM(scripts))
        events = _run(a)
        self.assertEqual(counter["n"], 1)
        finals = [e for e in events if e["type"] == "final"]
        self.assertEqual(len(finals), 1)
        text = finals[0]["text"]
        self.assertIn("反复", text)
        self.assertIn("fake_tool(inp1)", text)
        self.assertIn("obs-inp1", text)
        self.assertNotIn("fake_tool(other)", text)  # 换参调用未执行、不入证据
        self.assertNotIn("已确认结论", text)

    def test_empty_knowledge_call_redirects_to_code_tools(self):
        """已配置代码库时，空参 search_knowledge/web_search 被强制改道代码工具。"""
        with tempfile.TemporaryDirectory() as d:
            set_runtime("code_root", d)
            grounded = "【系统提示】知识库中已上传以下文档：LICENSE。\n\n用户问题：单曲循环在哪实现？LOOP_ONE"
            msg = agent_mod._empty_arg_obs("web_search", grounded)
            self.assertIn("search_code", msg)
            self.assertIn("禁止再调用", msg)
            self.assertIn("LOOP_ONE", msg)              # 真实用户问题被提取出来
            self.assertNotIn("LICENSE", msg)            # 系统前缀不进回填文案

    def test_python_exec_unescapes_literal_newlines(self):
        """模型把多行写成单行（字面量 \\n）时，SyntaxError 后反转义一次再执行。"""
        out = tools_mod.python_exec(r'print("a")\nprint("b")')
        self.assertIn("a\nb", out)
        self.assertNotIn("SyntaxError", out)

    def test_read_file_supports_line_range(self):
        """read_file 的 start:/end: 读取指定行段，带行号与总行数。"""
        with tempfile.TemporaryDirectory() as d:
            fpath = os.path.join(d, "ranged.txt")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write("\n".join(f"line{i}" for i in range(1, 301)))
            set_runtime("code_root", d)
            out = tools_mod.read_file("ranged.txt\nstart: 10\nend: 12")
            self.assertIn("第 10-12 行，共 300 行", out)
            self.assertIn("10: line10", out)
            self.assertIn("12: line12", out)
            self.assertNotIn("line9", out)


    def test_parse_inline_action_arg(self):
        """弱模型把参数写在 Action 同行括号里时，也能解析出工具名与参数。"""
        p = agent_mod.parse_response('Thought: t\nAction: search_code("Lyric")')
        self.assertEqual(p["action"], "search_code")
        self.assertEqual(p["action_input"], "Lyric")
        p2 = agent_mod.parse_response("Thought: t\nAction: grep(onCompletion)")
        self.assertEqual(p2["action_input"], "onCompletion")

    def test_normalize_keyword_style_args(self):
        """query:/pattern: 等关键字参数风格被还原为纯值，read_file 逗号行号被拆行。"""
        self.assertEqual(
            agent_mod._normalize_tool_arg("search_code", 'query: "enum Language"'),
            "enum Language",
        )
        self.assertEqual(
            agent_mod._normalize_tool_arg("grep", 'pattern: "onCompletion"'),
            "onCompletion",
        )
        norm = agent_mod._normalize_tool_arg(
            "read_file", 'path: "a/b.java", start: 315, end: 360'
        )
        self.assertEqual(norm, "a/b.java\nstart: 315\nend: 360")
        # 非关键字风格保持原样
        self.assertEqual(agent_mod._normalize_tool_arg("grep", "LOOP_ONE"), "LOOP_ONE")

    def test_run_passes_normalized_arg_to_tool(self):
        """端到端：run() 派发给工具函数/事件展示的必须是归一化后的参数（接线回归）。"""
        received = []

        def fake_read_file(arg):
            received.append(arg)
            return "ok"

        agent_mod.TOOLS = {"read_file": {"func": fake_read_file}}
        a = agent_mod.Agent(llm=_ScriptedLLM([
            'Thought: t\nAction: read_file(path: "a/b.java", start: 10, end: 30)',
            _FINAL_OK,
        ]))
        events = _run(a)
        self.assertEqual(received, ["a/b.java\nstart: 10\nend: 30"])
        shown = [e["text"] for e in events if e["type"] == "action"]
        self.assertEqual(shown, ["read_file(a/b.java\nstart: 10\nend: 30)"])

    def test_normalize_grep_path_scope(self):
        """grep 的同行/多行 path: 限定归一化为 <正则>\\npath: <范围>，纯正则保持不变。"""
        self.assertEqual(
            agent_mod._normalize_tool_arg(
                "grep", 'pattern: "language|en", path: "app/A.java"'
            ),
            "language|en\npath: app/A.java",
        )
        self.assertEqual(
            agent_mod._normalize_tool_arg("grep", 'pattern: "LOOP_ONE"\npath: "app/x.java"'),
            "LOOP_ONE\npath: app/x.java",
        )
        self.assertEqual(
            agent_mod._normalize_tool_arg("grep", "onCompletion\npath: app/M.java"),
            "onCompletion\npath: app/M.java",
        )
        self.assertEqual(agent_mod._normalize_tool_arg("grep", "onCompletion"), "onCompletion")

    def test_grep_scopes_to_file_or_dir(self):
        """grep 的 path: 限定只搜指定文件/目录；路径不存在与越界都被拦截。"""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "sub"))
            with open(os.path.join(d, "sub", "A.java"), "w", encoding="utf-8") as f:
                f.write("class A { int MARKER_A = 1; }\n")
            with open(os.path.join(d, "sub", "B.java"), "w", encoding="utf-8") as f:
                f.write("class B { int MARKER_B = 1; }\n")
            with open(os.path.join(d, "C.java"), "w", encoding="utf-8") as f:
                f.write("class C { int MARKER_C = 1; }\n")
            set_runtime("code_root", d)
            # 文件作用域：规范两行形式
            out = tools_mod.grep("MARKER_\npath: sub/A.java")
            self.assertIn("sub\\A.java:1", out.replace("/", "\\"))
            self.assertNotIn("MARKER_B", out)
            # 目录作用域：sub 下 A/B 命中，根目录 C 不命中
            out_dir = tools_mod.grep("MARKER_, path: sub")
            self.assertIn("MARKER_A", out_dir)
            self.assertIn("MARKER_B", out_dir)
            self.assertNotIn("MARKER_C", out_dir)
            # 路径不存在
            self.assertIn("路径不存在", tools_mod.grep("MARKER_\npath: sub/missing.java"))
            # 越界
            self.assertIn("拒绝访问", tools_mod.grep("MARKER_\npath: ../../escape.java"))

    def test_parse_explicit_action_input_still_wins(self):
        """显式 Action Input 行优先，行内括号不作为退路。"""
        text = 'Thought: t\nAction: search_code("ignored")\nAction Input: ApiProvider'
        p = agent_mod.parse_response(text)
        self.assertEqual(p["action"], "search_code")
        self.assertEqual(p["action_input"], "ApiProvider")


class WebGateTests(unittest.TestCase):
    """联网开关：默认关闭时 web_* 工具文本通道拒绝执行、原生 schema 剔除。"""

    def setUp(self):
        self._old_tools = agent_mod.TOOLS

    def tearDown(self):
        agent_mod.TOOLS = self._old_tools

    def test_web_tool_blocked_in_text_channel_when_off(self):
        calls = []

        def fake_web(arg):
            calls.append(arg)
            return "不应被调用"

        def fake_local(arg):
            return "本地结果"

        agent_mod.TOOLS = {
            "web_search": {"func": fake_web},
            "search_code": {"func": fake_local},
        }
        a = agent_mod.Agent(llm=_ScriptedLLM([
            _act("web_search", "今天的新闻"),
            _FINAL_OK,
        ]))
        self.assertFalse(a.web_enabled, "新 Agent 的联网必须默认关闭")
        events = _run(a)
        self.assertEqual(calls, [], "联网关闭时 web_search 绝不能执行")
        obs = [e["text"] for e in events if e["type"] == "observation"]
        self.assertTrue(any("联网未开启" in t for t in obs))

    def test_web_tool_runs_when_enabled(self):
        calls = []

        def fake_web(arg):
            calls.append(arg)
            return "· 标题\n  https://example.test/a"

        agent_mod.TOOLS = {"web_search": {"func": fake_web}}
        a = agent_mod.Agent(llm=_ScriptedLLM([
            _act("web_search", "今天的新闻"),
            _FINAL_OK,
        ]))
        a.web_enabled = True
        _run(a)
        self.assertEqual(calls, ["今天的新闻"])

    def test_effective_tool_schemas_filter_web_tools(self):
        a = agent_mod.Agent(llm=_ScriptedLLM([_FINAL_OK]))
        names_off = set(a._effective_tool_names())
        for name in agent_mod._WEB_TOOLS:
            self.assertNotIn(name, names_off, f"联网关闭时 {name} 不应暴露给模型")
        self.assertIn("search_code", names_off)

        a.web_enabled = True
        names_on = set(a._effective_tool_names())
        for name in agent_mod._WEB_TOOLS:
            self.assertIn(name, names_on, f"联网开启后 {name} 应出现在 schema 中")

    def test_system_prompt_declares_web_status(self):
        a = agent_mod.Agent(llm=_ScriptedLLM([_FINAL_OK]))
        joined_off = " ".join(str(m.get("content", "")) for m in a._build_messages("q"))
        self.assertIn("联网已关闭", joined_off)
        a.web_enabled = True
        joined_on = " ".join(str(m.get("content", "")) for m in a._build_messages("q"))
        self.assertIn("联网已开启", joined_on)


if __name__ == "__main__":
    unittest.main()
