"""_ReactTokenFilter：ReAct 协议行不得作为正文流式展示。

回归背景：旧实现一旦在开头识别到 Thought:/Action: 就放行后续全部 token，
导致工具轮的 Thought/Action 原文（含 Final Answer: 标记）先流进答案气泡，
最后才被 final 事件整体替换成干净答案（用户感知为「要等答案全部出来才显示」）。
"""
from agent import _ReactTokenFilter


def _feed(chunks):
    f = _ReactTokenFilter()
    return "".join(f.feed(c) for c in chunks) + f.flush()


def test_tool_round_emits_nothing():
    # 取证轮（Thought + Action）不产生任何正文 token
    out = _feed([
        "Thought:",
        " I need to search",
        "\nAction: search_code",
        '\nAction Input: "player hp"',
    ])
    assert out == ""


def test_final_round_strips_marker_and_thought():
    out = _feed(["Thought:", " done", "\nFinal Answer:", " hello", " world"])
    assert out == " hello world"
    assert "Final Answer" not in out
    assert "Thought" not in out


def test_final_marker_split_across_chunks():
    # 标记被 chunk 切断（13 字符的 Final Answer: 必须靠尾部窗口拼接）
    out = _feed(["Thought: x", "\nFinal Answe", "r: 答案正文"])
    assert out == " 答案正文"


def test_response_starts_directly_with_final_marker():
    out = _feed(["Final Answer:", " 直接答案"])
    assert out == " 直接答案"


def test_plain_answer_passes_through_after_probe():
    # 非协议开头的普通回答在小探测窗口后原样放行（含前导 token）
    out = _feed(["根据代码", "，答案是", "42"])
    assert out == "根据代码，答案是42"


def test_long_tool_round_then_final_answer():
    # 长工具轮不能因为缓冲裁剪丢掉后续 Final Answer
    long_thought = "x" * 5000
    out = _feed([
        "Thought: " + long_thought,
        "\nAction: f()",
        "\nFinal Answer: 收尾结论",
    ])
    assert out == " 收尾结论"
