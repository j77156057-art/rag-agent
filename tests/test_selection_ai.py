"""P2 选区 AI 快通道测试：消息构造（强约束纯代码）与进入 SSE 前的长度/空值护栏。

成功路径（真实 LLM token 流）依赖本地模型，不在单测覆盖，由浏览器实测验证。
护栏类 4xx 在 provider/LLM 调用之前返回，故不联网、确定性可测。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import (  # noqa: E402
    SelectionAiReq,
    SELECTION_MAX_CHARS,
    SELECTION_CTX_MAX_CHARS,
    SELECTION_INSTR_MAX_CHARS,
    _selection_rewrite_messages,
)
from api import app  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


class RewriteMessagesTests(unittest.TestCase):
    def _req(self, **kw):
        base = dict(
            path="behaviors/player.gd",
            lang="gdscript",
            start_line=12,
            end_line=14,
            selection="func take_damage(a):\n    hp -= a",
        )
        base.update(kw)
        return SelectionAiReq(**base)

    def test_two_messages_with_strict_code_only_system(self):
        msgs = _selection_rewrite_messages(self._req())
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        sys_p, user_p = msgs[0]["content"], msgs[1]["content"]
        # 强约束：只输出替换代码、不要围栏/解释
        self.assertIn("只输出", sys_p)
        self.assertIn("markdown", sys_p)
        self.assertIn("不要解释", sys_p)
        # 保持对外接口
        self.assertIn("签名", sys_p)
        # user 携带定位信息与选区
        self.assertIn("behaviors/player.gd", user_p)
        self.assertIn("gdscript", user_p)
        self.assertIn("第 12–14 行", user_p)
        self.assertIn("func take_damage", user_p)
        # 默认改写要求
        self.assertIn("优化", user_p)
        # 未给文件全文时不出现上下文参考段
        self.assertNotIn("文件内容参考", user_p)

    def test_custom_instruction_passthrough(self):
        msgs = _selection_rewrite_messages(self._req(instruction="给这个函数加暴击参数"))
        self.assertIn("给这个函数加暴击参数", msgs[1]["content"])

    def test_file_context_included(self):
        msgs = _selection_rewrite_messages(
            self._req(file_context="extends CharacterBody2D\n\nfunc take_damage(a):\n    hp -= a\n")
        )
        user_p = msgs[1]["content"]
        self.assertIn("文件内容参考", user_p)
        self.assertIn("extends CharacterBody2D", user_p)

    def test_blank_lang_and_instruction_fallback(self):
        msgs = _selection_rewrite_messages(self._req(lang="  ", instruction="   "))
        self.assertIn("资深 text 工程师", msgs[0]["content"])
        self.assertIn("优化", msgs[1]["content"])

    def test_unknown_line_location(self):
        msgs = _selection_rewrite_messages(self._req(start_line=0, end_line=0))
        self.assertIn("行号未知", msgs[1]["content"])


class EndpointGuardTests(unittest.TestCase):
    """4xx 护栏均在任何 provider/LLM 调用前返回。"""

    @classmethod
    def setUpClass(cls):
        # 不使用 with：避免触发 startup（可能初始化重资源），仅同步执行端点函数
        cls.client = TestClient(app)

    def _post(self, payload):
        return self.client.post("/api/selection_ai", json=payload)

    def test_empty_selection_400(self):
        r = self._post({"path": "a.gd", "selection": "   \n "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("选区为空", r.json()["error"])

    def test_selection_too_long_413(self):
        r = self._post({"path": "a.gd", "selection": "x" * (SELECTION_MAX_CHARS + 1)})
        self.assertEqual(r.status_code, 413)

    def test_context_too_long_413(self):
        r = self._post({
            "path": "a.gd",
            "selection": "var x = 1",
            "file_context": "y" * (SELECTION_CTX_MAX_CHARS + 1),
        })
        self.assertEqual(r.status_code, 413)

    def test_instruction_too_long_413(self):
        r = self._post({
            "path": "a.gd",
            "selection": "var x = 1",
            "instruction": "z" * (SELECTION_INSTR_MAX_CHARS + 1),
        })
        self.assertEqual(r.status_code, 413)

    def test_missing_selection_field_422(self):
        # pydantic 缺必填字段
        r = self._post({"path": "a.gd"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
