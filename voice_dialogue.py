"""Low-latency voice companion.

This agent deliberately has no tools and no write access. It receives the latest
main-agent result and can acknowledge, clarify, or hold a user's next question
while the main development agent is busy.
"""
from __future__ import annotations

from llm import LLMClient


def reply(user_text: str, main_result: str = "", *, llm=None) -> str:
    text = str(user_text or "").strip()
    if not text:
        return "请说出你想确认的内容。"
    client = llm or LLMClient()
    messages = [
        {"role": "system", "content": (
            "你是开发舱的语音协作助手。你没有任何工具、不能修改文件、不能承诺验收。"
            "只用简短自然的中文回答，最多三句话。主 Agent 正在开发时，负责陪用户沟通、"
            "确认问题并把需要执行的事项明确转交主 Agent；不要编造主 Agent 尚未提供的结果。"
        )},
        {"role": "user", "content": "主 Agent 最新结果：\n" + str(main_result or "（尚无结果，主 Agent 仍在执行）")
         + "\n\n用户语音转写：\n" + text},
    ]
    output = client.chat(messages, stream=False, temperature=0.2)
    if isinstance(output, str):
        return output.strip()
    return "".join(str(item or "") for item in output).strip()
