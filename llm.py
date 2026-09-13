"""LLM 客户端：用 OpenAI 兼容协议统一封装 通义千问 / DeepSeek / Ollama / mock。

设计意图（与 Android 端 AIApiClient 一致）：把不同厂商的大模型收敛到同一套
OpenAI chat/completions 接口后面，运行时切换 provider 即可，业务代码无需改动。
"""
import json
import os
import urllib.request
from gpu_coordinator import acquire as _gpu_acquire, release as _gpu_release

from openai import OpenAI

from config import (
    LLM_PROVIDER, PROVIDERS, LLM_MODEL, LLM_API_KEY,
    LLM_MAX_TOKENS, LLM_ENABLE_THINKING, PROMPT_TOKEN_BUDGET, get_runtime,
)


class StreamChat:
    """包装 OpenAI 流式响应：迭代时只产出正文 content token，
    结束后可从 finish_reason 判断是否因长度被截断（"length"）。

    思考型模型（如 qwen3 系列）的 reasoning_content 不计入正文，但累计
    长度到 reasoning_chars，供上层判断"只有思考、没有正文"的情况。
    """

    def __init__(self, stream):
        self._stream = stream
        self.finish_reason = None
        self.reasoning_chars = 0

    def __iter__(self):
        for chunk in self._stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                self.finish_reason = choice.finish_reason
            delta = choice.delta
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                self.reasoning_chars += len(reasoning)
            content = getattr(delta, "content", None)
            if content:
                yield content


class _OllamaStream:
    """ollama /api/chat 流式（NDJSON）适配器：接口与 StreamChat 对齐。

    每行一个 JSON 片段：message.content 增量、message.thinking 思考增量；
    末行 done=true 带 done_reason（"stop" / "length"），映射到 finish_reason。
    """

    def __init__(self, resp, on_close=None):
        self._resp = resp
        self._on_close = on_close
        self.finish_reason = None
        self.reasoning_chars = 0

    def __iter__(self):
        try:
          for raw in self._resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message") or {}
            thinking = msg.get("thinking")
            if thinking:
                self.reasoning_chars += len(thinking)
            content = msg.get("content")
            if content:
                yield content
            if obj.get("done"):
                # ollama done_reason: stop/length；无该字段时按 stop 处理
                self.finish_reason = obj.get("done_reason") or "stop"
        finally:
            if self._on_close: self._on_close()


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

        if self.provider == "ollama":
            # ollama 的 /v1 OpenAI 兼容层会静默丢弃 options.num_ctx（实测 14584
            # 被忽略、加载仍为 4096）和 chat_template_kwargs，长 prompt 会被截断。
            # 改走原生 /api/chat：num_ctx / think / num_predict 全部服务端生效。
            # 消息内的 images: [base64...] 也是 ollama 原生多模态格式，直接透传。
            return self._ollama_chat(messages, stream=stream, temperature=temperature)

        # OpenAI 兼容路径：把 ollama 风格的 images 字段转成多模态 content parts，
        # 否则 SDK 的 pydantic 序列化会因未知字段报错。
        oai_messages = self._to_openai_messages(messages)

        # max_tokens 兜底：思考型模型偶发不按格式收尾而无限生成，到顶后由
        # finish_reason=length 触发 Agent 的续写纠偏，避免单轮烧几分钟/上万 token。
        kwargs = {"max_tokens": LLM_MAX_TOKENS}
        # 本地 qwen3 系思考模型：显式关闭 reasoning，避免预算被思考吃光而不行动。
        if not LLM_ENABLE_THINKING and self.provider in ("llamacpp", "ollama"):
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        if stream:
            resp = self.client.chat.completions.create(
                model=self.model, messages=oai_messages, stream=True,
                temperature=temperature, **kwargs
            )
            return StreamChat(resp)

        resp = self.client.chat.completions.create(
            model=self.model, messages=oai_messages, stream=False,
            temperature=temperature, **kwargs
        )
        return resp.choices[0].message.content

    @staticmethod
    def _sniff_image_mime(b64):
        """按 base64 解码头部魔数判断图片 MIME（供 OpenAI data URL 使用）。"""
        import base64
        try:
            head = base64.b64decode(b64[:32] + "==")
        except Exception:
            return "image/jpeg"
        if head.startswith(b"\x89PNG"):
            return "image/png"
        if head.startswith(b"\xff\xd8"):
            return "image/jpeg"
        if head.startswith(b"GIF8"):
            return "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        return "image/jpeg"

    def _to_openai_messages(self, messages):
        """把内部消息（ollama 风格 images: [b64...]）转成 OpenAI 多模态格式：
        content 变为 [{"type":"text"...}, {"type":"image_url","image_url":{"url":"data:..."}}]。
        """
        out = []
        for m in messages:
            imgs = m.get("images")
            if not imgs:
                out.append(m)
                continue
            parts = [{"type": "text", "text": m.get("content") or ""}]
            for b64 in imgs:
                mime = self._sniff_image_mime(b64)
                parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                )
            nm = dict(m)
            nm.pop("images", None)
            nm["content"] = parts
            out.append(nm)
        return out

    # ollama 原生 /api/chat 单次请求的连接/读超时：冷加载 22GB 模型可达 1-2 分钟
    _OLLAMA_TIMEOUT = 600

    def _ollama_url(self, path):
        root = str(self.client.base_url).rstrip("/").removesuffix("/v1").rstrip("/")
        return root + path

    def _ollama_chat(self, messages, stream=False, temperature=0.3):
        if not _gpu_acquire("ollama", 2): raise RuntimeError("GPU 正忙：ComfyUI 正在使用中，请稍后重试。")
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": bool(stream),
            # ollama 默认 num_ctx=4096，会把 ~11000 token 的 prompt 静默截断
            "options": {
                "num_ctx": PROMPT_TOKEN_BUDGET + LLM_MAX_TOKENS + 512,
                "num_predict": LLM_MAX_TOKENS,
                "temperature": temperature,
                # qwen3.6 模型 Modelfile 自带 presence_penalty=1.5，实测会诱发
                # 空 Action Input / JSON 参数等格式退化；显式归零贴近 OpenAI 默认
                "presence_penalty": 0.0,
                "frequency_penalty": 0.0,
            },
        }
        if not LLM_ENABLE_THINKING:
            payload["think"] = False
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._ollama_url("/api/chat"), data=data,
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            resp = opener.open(req, timeout=self._OLLAMA_TIMEOUT)
        except Exception:
            _gpu_release("ollama")
            raise
        if stream:
            return _OllamaStream(resp, lambda: _gpu_release("ollama"))
        body = json.loads(resp.read().decode("utf-8"))
        _gpu_release("ollama")
        return (body.get("message") or {}).get("content", "")

    def count_tokens(self, text):
        """估算/精算文本 token 数，用于上下文预算裁剪。
        llamacpp/ollama 走本地服务的 /tokenize 精算；任何失败或其它 provider
        退回到按 CJK/代码分别估的保守启发式。
        """
        if not text:
            return 0
        try:
            if self.provider == "llamacpp":
                root = str(self.client.base_url).rstrip("/").removesuffix("/v1").rstrip("/")
                url = root + "/tokenize"
                payload = json.dumps({"content": text}).encode("utf-8")
            elif self.provider == "ollama":
                root = str(self.client.base_url).rstrip("/").removesuffix("/v1").rstrip("/")
                url = root + "/api/tokenize"
                payload = json.dumps({"model": self.model, "prompt": text}).encode("utf-8")
            else:
                return self._heuristic_tokens(text)
            req = urllib.request.Request(
                url, data=payload, headers={"Content-Type": "application/json"}
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            tokens = data.get("tokens")
            return len(tokens) if tokens is not None else self._heuristic_tokens(text)
        except Exception:
            return self._heuristic_tokens(text)

    @staticmethod
    def _heuristic_tokens(text):
        # 保守估算：CJK 约 1.1 token/字，ASCII（代码/英文）约 3.2 字符/token
        cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
        other = len(text) - cjk
        return int(cjk * 1.1 + other / 3.2) + 40

    # ------------------------------------------------------------------
    # 离线 mock：模拟一个 ReAct 风格 Agent 的多步推理，便于无 key 演示与端到端验证
    # 支持：检索命中 -> 直接回答；检索未命中 -> 自我反思并换用联网搜索
    # ------------------------------------------------------------------
    def _mock_chat(self, messages, stream=False):
        # mock 没有视觉能力：用户带图时明确告知，不假装能识别图片内容
        has_image = any(bool(m.get("images")) for m in messages)
        if has_image:
            answer = (
                "Thought: 用户附带了图片，mock 离线模型没有视觉能力，需如实告知。\n"
                "Final Answer: （演示模式·mock LLM）检测到您附带了图片，但 mock 离线模型"
                "不具备视觉能力，无法识别图片内容。请在 ⚙ 模型设置 中切换到支持视觉的"
                "本地模型（如 Ollama qwen3.6 系列）后重试。"
            )
            if stream:
                def g_img():
                    for ch in answer:
                        yield ch
                return g_img()
            return answer

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
