"""LLM 客户端：用 OpenAI 兼容协议统一封装 通义千问 / DeepSeek / Ollama / mock。

设计意图（与 Android 端 AIApiClient 一致）：把不同厂商的大模型收敛到同一套
OpenAI chat/completions 接口后面，运行时切换 provider 即可，业务代码无需改动。
"""
import os

from openai import OpenAI

from config import LLM_PROVIDER, PROVIDERS, LLM_MODEL, LLM_API_KEY, get_runtime


class LLMClient:
    def __init__(self, provider=None, model=None, api_key=None):
        self.provider = provider or get_runtime("llm_provider") or LLM_PROVIDER
        cfg = PROVIDERS[self.provider]
        self.model = model or get_runtime("llm_model") or LLM_MODEL or cfg["default_model"]

        if self.provider == "mock":
            # 离线演示模式：不发起任何网络请求
            self.client = None
            return

        # 解析 API Key：优先显式传入 -> 运行时覆盖 -> 环境变量 LLM_API_KEY -> provider 专用变量
        key = api_key or get_runtime("llm_api_key") or LLM_API_KEY
        if cfg["api_key_env"]:
            key = key or os.getenv(cfg["api_key_env"], "")
        self.client = OpenAI(base_url=cfg["base_url"], api_key=key or "EMPTY")

    def chat(self, messages, stream=False, temperature=0.3):
        """统一的对话入口。stream=True 时返回一个 token 生成器。"""
        if self.provider == "mock":
            return self._mock_chat(messages, stream=stream)

        if stream:
            def gen():
                resp = self.client.chat.completions.create(
                    model=self.model, messages=messages, stream=True, temperature=temperature
                )
                for chunk in resp:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content

            return gen()

        resp = self.client.chat.completions.create(
            model=self.model, messages=messages, stream=False, temperature=temperature
        )
        return resp.choices[0].message.content

    # ------------------------------------------------------------------
    # 离线 mock：模拟一个 ReAct 风格 Agent 的多步推理，便于无 key 演示与端到端验证
    # 支持：检索命中 -> 直接回答；检索未命中 -> 自我反思并换用联网搜索
    # ------------------------------------------------------------------
    def _mock_chat(self, messages, stream=False):
        last = messages[-1]["content"] if messages else ""

        # 取「当前」问题：取最后一条不含 Observation/Reflection 的 user 消息。
        # 注意要取最后一条而非第一条 —— HTTP 服务复用同一个 Agent 单例，
        # history 里会累积历史提问，取第一条会导致后续轮次一直套用旧问题意图。
        question = messages[-1]["content"] if messages else ""
        for m in reversed(messages):
            if m.get("role") == "user" and "Observation:" not in m["content"] and "Reflection:" not in m["content"]:
                question = m["content"]
                break

        # 素材检索意图识别：命中则优先调用 search_assets
        ASSET_KW = (
            "素材", "美术", "资源", "asset", "sprite", "精灵", "角色",
            "图标", "像素", "pixel", "tileset", "tile", "地形", "贴图",
            "ui", "音效", "音乐", "字体", "font", "cc0", "kenney",
            "opengameart", "itch", "模型", "3d", "动画",
        )
        is_asset = any(k in question.lower() for k in ASSET_KW)

        if "未找到" in last or "Reflection:" in last:
            # 检索未命中 / 被要求反思 -> 换用联网搜索（演示自我反思+换工具重试）
            answer = (
                "Thought: 前面的工具没有命中，我换用联网搜索来补充信息。\n"
                "Action: web_search\n"
                f"Action Input: {question}"
            )
        elif "Observation:" in last:
            # 已拿到有效检索结果，给出最终回答（演示用，引用首段观察内容）
            obs = last.split("Observation:")[-1]
            snippet = obs.strip().split("\n")[0][:200]
            answer = (
                "（演示模式·mock LLM）根据检索到的内容："
                f"{snippet} …… 综上所述，这就是与您问题相关的说明。"
            )
        else:
            if is_asset:
                # 用户想找游戏美术/素材，先在精选素材目录中筛选
                answer = (
                    "Thought: 用户想找游戏美术/素材，我先在精选素材目录中筛选。\n"
                    "Action: search_assets\n"
                    f"Action Input: {question}"
                )
            else:
                # 第一步：先决定检索知识库
                answer = (
                    "Thought: 我需要先检索知识库来获取准确信息。\n"
                    "Action: search_knowledge\n"
                    f"Action Input: {question}"
                )

        if stream:
            def g():
                for ch in answer:
                    yield ch

            return g()
        return answer
