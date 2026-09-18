# -*- coding: utf-8 -*-
"""2026-09-18 三个 bug 的回归护栏：

① dev_apply_regions 入参解析——模型无论给 {"regions":[...]} / 裸数组 / `regions: <JSON>` /
   Python repr（单引号）都必须能落地，否则会报「参数缺失」并反复重试刷死循环（耗上下文）。
② 流式 tool_call 的 arguments 归一——个别 OpenAI 兼容后端把 arguments 给成对象，
   直接 `str += dict` 会抛 TypeError: can only concatenate str (not "dict") to str，
   整轮问答崩成「模型无响应」。StreamChat._capture_tool_deltas 必须把它转成 JSON 字符串。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent  # noqa: E402
import llm  # noqa: E402
import tools  # noqa: E402


class _Obj:
    """把关键字挂成属性的极简对象，模拟 OpenAI SDK 的 choice/delta/function。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class ParseRegionsPayloadTests(unittest.TestCase):
    SAMPLE = '[{"key": "values", "dir": "values", "name": "数值区"}]'

    def _ok(self, arg):
        regions, err = tools._parse_regions_payload(arg)
        self.assertEqual(err, "", f"应解析成功，实际 err={err!r}")
        self.assertIsInstance(regions, list)
        self.assertEqual(regions[0]["key"], "values")
        return regions

    def test_json_object_form(self):
        # 原生 tool_call 最常见：整个 arg 就是 {"regions":[...]}
        self._ok('{"regions": %s}' % self.SAMPLE)

    def test_bare_json_array(self):
        self._ok(self.SAMPLE)

    def test_keyed_text_form(self):
        self._ok("regions: %s" % self.SAMPLE)

    def test_python_repr_fallback(self):
        # 弱模型常给单引号 Python repr，json 解析失败要能用 literal_eval 兜底
        self._ok("[{'key': 'values', 'dir': 'values', 'name': '数值区'}]")

    def test_multiline_json_object(self):
        self._ok('{\n  "regions": %s\n}' % self.SAMPLE)

    def test_empty_arg_reports_usage(self):
        regions, err = tools._parse_regions_payload("")
        self.assertIsNone(regions)
        self.assertIn("参数缺失", err)

    def test_wrong_shape_is_rejected_with_reason(self):
        regions, err = tools._parse_regions_payload('{"foo": 1}')
        self.assertIsNone(regions)
        self.assertIn("regions", err)


class StreamToolArgumentsCoercionTests(unittest.TestCase):
    def _stream(self):
        return llm.StreamChat(iter(()))

    def test_dict_arguments_do_not_crash(self):
        # 关键回归：arguments 是 dict 时不得抛 TypeError，且要被归一成 JSON 字符串
        sc = self._stream()
        choice = _Obj(delta=_Obj(tool_calls=[
            _Obj(index=0, id="c1", function=_Obj(
                name="dev_apply_regions",
                arguments={"regions": [{"key": "values", "dir": "values"}]},
            )),
        ]))
        sc._capture_tool_deltas(choice)          # 不得抛异常
        args = sc._partial[0]["arguments"]
        self.assertIsInstance(args, str)
        self.assertIn('"regions"', args)

    def test_string_arguments_accumulate(self):
        # 正常分片仍是字符串拼接
        sc = self._stream()
        for frag in ('{"regions"', ": []}"):
            sc._capture_tool_deltas(_Obj(delta=_Obj(tool_calls=[
                _Obj(index=0, id="c1", function=_Obj(name="dev_apply_regions", arguments=frag)),
            ])))
        self.assertEqual(sc._partial[0]["arguments"], '{"regions": []}')


class MissingArgIsFailureMarkerTests(unittest.TestCase):
    def test_missing_arg_observation_is_failure(self):
        # 「参数缺失」必须命中失败文案，才会触发反思/强制收尾（否则被当成功→无限重试）
        self.assertTrue(agent._is_failure("参数缺失：请提供 regions: <JSON 数组…>。"))


if __name__ == "__main__":
    unittest.main()
