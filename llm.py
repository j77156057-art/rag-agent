"""LLM 客户端：用 OpenAI 兼容协议统一封装 通义千问 / DeepSeek / Ollama / mock。

设计意图（与 Android 端 AIApiClient 一致）：把不同厂商的大模型收敛到同一套
OpenAI chat/completions 接口后面，运行时切换 provider 即可，业务代码无需改动。
"""
import json
import os
import random
import time
import urllib.error
import urllib.request
from gpu_coordinator import acquire as _gpu_acquire, release as _gpu_release, note_activity as _gpu_note_activity

from openai import OpenAI

from config import (
    LLM_PROVIDER, PROVIDERS, LLM_MODEL, LLM_API_KEY,
    LLM_MAX_TOKENS, LLM_ENABLE_THINKING, PROMPT_TOKEN_BUDGET, get_runtime,
    model_capability, prompt_token_budget,
)

# 默认单次 LLM 调用超时（秒）与重试策略（可用环境变量覆盖）
LLM_TIMEOUT = float(os.getenv("DOCMIND_LLM_TIMEOUT", "180"))
LLM_RETRIES = int(os.getenv("DOCMIND_LLM_RETRIES", "3"))       # 总尝试次数（含首次）
LLM_RETRY_BASE = float(os.getenv("DOCMIND_LLM_RETRY_BASE", "0.8"))  # 指数退避基数（秒）

# 可重试的错误特征：限流 / 5xx / 网络与超时。鉴权/参数类（401/403/404）不重试。
_RETRYABLE_HINTS = (
    "429", "rate limit", "rate_limit", "too many requests",
    "500", "502", "503", "504", "server error", "internal error",
    "timed out", "timeout", "temporarily unavailable", "overloaded",
    "connection", "reset by peer", "econnreset", "broken pipe", "eof",
)


def _status_of(exc):
    for attr in ("status_code", "code", "status", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    return None


def is_retryable(exc) -> bool:
    """判断异常是否值得重试（限流、5xx、网络/超时）。"""
    st = _status_of(exc)
    if isinstance(st, int) and (st == 429 or st >= 500):
        return True
    if st is not None and 400 <= st < 500:
        return False  # 明确的客户端错误不重试
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return any(h in msg for h in _RETRYABLE_HINTS)


def retry_call(fn, deadline=None, attempts=None, base=None):
    """带指数退避的重试包装。

    - 仅对 is_retryable 的异常重试；其余立即抛出（不浪费退避时间）。
    - deadline 为 time.monotonic() 基准的绝对截止时刻；逼近或到达即停止重试。
    - 流式场景：本包装只覆盖"建立连接"阶段；一旦开始吐 token 就不再重试
      （已发出的 token 无法撤回，宁可让上层走续写纠偏）。
    """
    attempts = int(attempts or LLM_RETRIES)
    base = LLM_RETRY_BASE if base is None else float(base)
    last = None
    for i in range(max(1, attempts)):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("LLM 调用已超出本轮截止时间")
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last = e
            if i >= attempts - 1 or not is_retryable(e):
                raise
            delay = min(base * (2 ** i), 8.0) * (0.7 + 0.6 * random.random())
            if deadline is not None and time.monotonic() + delay >= deadline:
                raise
            time.sleep(delay)
    raise last  # pragma: no cover


def _fn_get(fn, key, default=""):
    """兼容 dict / pydantic 对象两种形态读取 function 字段。"""
    if fn is None:
        return default
    if isinstance(fn, dict):
        return fn.get(key, default)
    return getattr(fn, key, default)


def normalize_tool_calls(raw):
    """把 OpenAI / Ollama 的 tool_calls 归一为 [{id, name, arguments(字符串)}]。"""
    out = []
    for c in raw or []:
        fn = c.get("function") if isinstance(c, dict) else getattr(c, "function", None)
        if fn is None and isinstance(c, dict):
            fn = c                       # 有些服务端直接给 {name, arguments}
        name = _fn_get(fn, "name", "") or ""
        args = _fn_get(fn, "arguments", "") or ""
        if not isinstance(args, str):
            args = json.dumps(args, ensure_ascii=False)
        cid = c.get("id") if isinstance(c, dict) else getattr(c, "id", "")
        if name:
            out.append({"id": cid or "", "name": name, "arguments": args})
    return out


def args_to_input(args_str):
    """把原生 function-calling 的 arguments 映射回文本协议的多行 Action Input。

    工具 schema 统一只暴露一个 `input` 字符串参数，所以绝大多数情况直接取它；
    若模型给了多个具名参数，则转成 `key: value` 多行文本，交给既有的
    `_normalize_tool_arg` 继续处理（护栏完全复用）。
    """
    if not args_str:
        return ""
    try:
        obj = json.loads(args_str)
    except (ValueError, TypeError):
        return str(args_str)
    if isinstance(obj, dict):
        if list(obj.keys()) == ["input"]:
            return str(obj["input"])
        if "input" in obj and len(obj) == 1:
            return str(obj["input"])
        return "\n".join(f"{k}: {v}" for k, v in obj.items())
    return str(obj)


class StreamChat:
    """包装 OpenAI 流式响应：迭代时只产出正文 content token，
    结束后可从 finish_reason 判断是否因长度被截断（"length"）。

    思考型模型（如 qwen3 系列）的 reasoning_content 不计入正文，但累计
    长度到 reasoning_chars，供上层判断"只有思考、没有正文"的情况。
    """

    def __init__(self, stream, usage_sink=None, tool_sink=None, reasoning_sink=None):
        self._stream = stream
        self._usage = usage_sink if usage_sink is not None else {}
        self._tool_sink = tool_sink if tool_sink is not None else []
        self._reasoning_sink = reasoning_sink
        self._partial = {}          # index -> {"id":.., "name":.., "arguments":..}
        self.finish_reason = None
        self.reasoning_chars = 0

    def _capture_tool_deltas(self, choice):
        """OpenAI 流式 tool_calls 是按 index 分片的，逐片累加。"""
        delta = getattr(choice, "delta", None)
        calls = getattr(delta, "tool_calls", None) if delta is not None else None
        if not calls:
            return
        for c in calls:
            idx = getattr(c, "index", 0) or 0
            slot = self._partial.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            cid = getattr(c, "id", None)
            if cid:
                slot["id"] = cid
            fn = getattr(c, "function", None)
            nm = _fn_get(fn, "name", "")
            if nm:
                slot["name"] = nm
            frag = _fn_get(fn, "arguments", "")
            if frag:
                slot["arguments"] += frag

    def _flush_tools(self):
        for idx in sorted(self._partial):
            slot = self._partial[idx]
            if slot.get("name"):
                self._tool_sink.append({"id": slot["id"], "name": slot["name"],
                                        "arguments": slot["arguments"]})

    def _capture_usage(self, chunk):
        """尾包（choices 为空）带 usage：OpenAI 需请求时开 include_usage。"""
        u = getattr(chunk, "usage", None)
        if u is None:
            return
        dump = u.model_dump() if hasattr(u, "model_dump") else (u if isinstance(u, dict) else {})
        for k in ("prompt_tokens", "completion_tokens"):
            v = dump.get(k)
            if isinstance(v, int):
                self._usage[k] = v

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._capture_usage(chunk)
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    self.finish_reason = choice.finish_reason
                self._capture_tool_deltas(choice)
                delta = choice.delta
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    self.reasoning_chars += len(reasoning)
                    if self._reasoning_sink is not None:
                        self._reasoning_sink.append(reasoning)
                content = getattr(delta, "content", None)
                if content:
                    yield content
        finally:
            # 消费方提前中断（如客户端断连）也要把已收到的工具调用交给上层
            self._flush_tools()


class _OllamaStream:
    """ollama /api/chat 流式（NDJSON）适配器：接口与 StreamChat 对齐。

    每行一个 JSON 片段：message.content 增量、message.thinking 思考增量；
    末行 done=true 带 done_reason（"stop" / "length"），映射到 finish_reason。
    """

    def __init__(self, resp, on_close=None, usage_sink=None, tool_sink=None,
                 reasoning_sink=None):
        self._resp = resp
        self._on_close = on_close
        self._usage = usage_sink if usage_sink is not None else {}
        self._tool_sink = tool_sink if tool_sink is not None else []
        self._reasoning_sink = reasoning_sink
        self._seen_tools = set()
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
                if self._reasoning_sink is not None:
                    self._reasoning_sink.append(thinking)
            # ollama 的 tool_calls 是"完整"对象（非分片），去重后并入 sink
            for call in normalize_tool_calls(msg.get("tool_calls")):
                sig = (call["name"], call["arguments"])
                if sig not in self._seen_tools:
                    self._seen_tools.add(sig)
                    self._tool_sink.append(call)
            content = msg.get("content")
            if content:
                yield content
            # ollama 原生 /api/chat 的末行带 prompt_eval_count / eval_count
            for k in ("prompt_eval_count", "eval_count"):
                v = obj.get(k)
                if isinstance(v, int):
                    self._usage[k] = v
            if obj.get("done"):
                # ollama done_reason: stop/length；无该字段时按 stop 处理
                self.finish_reason = obj.get("done_reason") or "stop"
        finally:
            if self._on_close: self._on_close()


class LLMClient:
    def __init__(self, provider=None, model=None, api_key=None, base_url=None):
        self.provider = provider or get_runtime("llm_provider") or LLM_PROVIDER
        if self.provider not in PROVIDERS:
            # 未知 provider 不静默回退：明确报错，避免把请求发到意料之外的服务
            raise ValueError(f"未知模型服务：{self.provider}")
        cfg = PROVIDERS[self.provider]
        self.model = model or get_runtime("llm_model") or LLM_MODEL or cfg["default_model"]
        # 模型能力画像：决定 num_ctx 扩充、思考参数能否透传（前端也据此渲染开关）
        self.capability = model_capability(self.provider, self.model)
        # 按真实窗口缩放的单轮 prompt token 预算（Agent 裁剪/压缩与 ollama num_ctx 共用）
        self.prompt_budget = prompt_token_budget(self.provider, self.model)
        # 最近一次调用的 token 用量（由 chat()/流式包装器原地更新），供 trace 账本读取
        self.last_usage = {}
        # 最近一次调用返回的原生 tool_calls（[{id,name,arguments}]），供 agent 的原生通道读取
        self.last_tool_calls = []
        self.max_retries = LLM_RETRIES
        self.retry_base = LLM_RETRY_BASE
        self.timeout = LLM_TIMEOUT
        # 解析出来的 key 留档：子代理需要用它新建**独立**的 LLMClient（避免共享实例的
        # last_usage / last_tool_calls 在并发下互相覆盖）
        self.api_key = ""
        # 自定义 OpenAI 兼容端点：显式传入 > 运行时覆盖 > LLM_BASE_URL 环境变量
        self.base_url = (
            base_url
            or (get_runtime("llm_base_url") if self.provider == "custom" else "")
            or (os.getenv("LLM_BASE_URL", "") if self.provider == "custom" else "")
            or cfg["base_url"]
        )

        if self.provider == "mock":
            # 离线演示模式：不发起任何网络请求
            self.client = None
            return

        if self.provider == "custom" and not self.base_url:
            raise ValueError("自定义 OpenAI 兼容服务缺少 base_url，请在模型设置中填写接口地址。")

        # 解析 API Key：优先显式传入 -> 运行时覆盖 -> 环境变量 LLM_API_KEY -> provider 专用变量
        key = api_key or get_runtime("llm_api_key") or LLM_API_KEY
        if cfg["api_key_env"]:
            key = key or os.getenv(cfg["api_key_env"], "")
        self.api_key = key or ""
        self.client = OpenAI(base_url=self.base_url, api_key=key or "EMPTY", timeout=self.timeout)

    def clone(self):
        """复制一份**独立**的客户端（同 provider/model/key），供并发子代理使用。"""
        return LLMClient(provider=self.provider, model=self.model, api_key=self.api_key,
                         base_url=self.base_url or None)

    def _resolve_thinking(self, enable_thinking):
        """本次调用是否开启思考：显式参数 > 运行时开关 > 全局环境变量。

        返回 (enabled, reason)：
        - native 思考模型（reasoner/qwq…）始终为 True，开关只控制前端是否展示思考流；
        - toggle 家族（qwen3）按开关透传 enable_thinking；
        - 不支持思考的模型恒 False（不能给服务端发陌生参数）。
        """
        rt = get_runtime("llm_enable_thinking")
        if enable_thinking is None:
            enable_thinking = (str(rt) in ("1", "true", "yes", "on")) if rt is not None \
                else LLM_ENABLE_THINKING
        mode = self.capability.get("thinking", "none")
        if mode == "native":
            return True
        if mode == "toggle":
            return bool(enable_thinking)
        return False

    def chat(self, messages, stream=False, temperature=0.3, timeout=None, deadline=None, tools=None,
             enable_thinking=None, reasoning_sink=None):
        """统一的对话入口。stream=True 时返回一个 token 生成器。

        timeout：单次调用超时（秒），默认 self.timeout。
        deadline：time.monotonic() 基准的绝对截止时刻；到达即不再重试并抛 TimeoutError。
        tools：OpenAI 风格函数 schema 列表；给出则走原生 function-calling 通道。
        enable_thinking：思考开关（None=按运行时/全局配置）；仅对画像为 toggle/native
                         的模型生效。
        reasoning_sink：可选 list，流式思考片段（reasoning_content / thinking）实时追加，
                        供 Agent 转成 SSE 事件给前端"深度思考"窗口。
        每次调用都重置 self.last_usage / self.last_tool_calls；成功后由 agent 读取。
        可重试错误（429/5xx/网络/超时）按指数退避自动重试。
        """
        self.last_usage = {}
        self.last_tool_calls = []
        if reasoning_sink is not None:
            reasoning_sink.clear()
        thinking_on = self._resolve_thinking(enable_thinking)
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("本轮已超出截止时间")

        if self.provider == "mock":
            return self._mock_chat(messages, stream=stream)

        if self.provider == "ollama":
            # ollama 的 /v1 OpenAI 兼容层会静默丢弃 options.num_ctx（实测 14584
            # 被忽略、加载仍为 4096）和 chat_template_kwargs，长 prompt 会被截断。
            # 改走原生 /api/chat：num_ctx / think / num_predict 全部服务端生效。
            # 消息内的 images: [base64...] 也是 ollama 原生多模态格式，直接透传。
            return retry_call(
                lambda: self._ollama_chat(
                    messages, stream=stream, temperature=temperature,
                    timeout=timeout, usage_sink=self.last_usage,
                    tool_sink=self.last_tool_calls, tools=tools,
                    thinking_on=thinking_on, reasoning_sink=reasoning_sink,
                ),
                deadline=deadline, attempts=self.max_retries, base=self.retry_base,
            )

        # OpenAI 兼容路径：把 ollama 风格的 images 字段转成多模态 content parts，
        # 否则 SDK 的 pydantic 序列化会因未知字段报错。
        oai_messages = self._to_openai_messages(messages)

        # max_tokens 兜底：思考型模型偶发不按格式收尾而无限生成，到顶后由
        # finish_reason=length 触发 Agent 的续写纠偏，避免单轮烧几分钟/上万 token。
        kwargs = {"max_tokens": LLM_MAX_TOKENS}
        # qwen3 toggle 家族（含 DashScope 兼容模式/自建端点）：按开关透传 enable_thinking。
        # 不支持思考的模型绝不带这个参数，避免 400；native 模型本身始终推理，无需传。
        if self.capability.get("thinking") == "toggle":
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": thinking_on}}
        call_timeout = self.timeout if timeout is None else timeout
        if tools:
            kwargs["tools"] = tools

        if stream:
            def _open(with_usage):
                extra = {"stream_options": {"include_usage": True}} if with_usage else {}
                resp = self.client.chat.completions.create(
                    model=self.model, messages=oai_messages, stream=True,
                    temperature=temperature, timeout=call_timeout, **kwargs, **extra
                )
                return StreamChat(resp, usage_sink=self.last_usage,
                                  tool_sink=self.last_tool_calls,
                                  reasoning_sink=reasoning_sink)
            try:
                return retry_call(lambda: _open(True), deadline=deadline,
                                  attempts=self.max_retries, base=self.retry_base)
            except Exception as e:  # noqa: BLE001
                # 服务端不认 stream_options 这类参数错误：退回不带 usage 的调用；
                # 网络/限流类错误则如实抛出（重试已用尽）。
                if is_retryable(e):
                    raise
                return _open(False)

        resp = retry_call(
            lambda: self.client.chat.completions.create(
                model=self.model, messages=oai_messages, stream=False,
                temperature=temperature, timeout=call_timeout, **kwargs
            ),
            deadline=deadline, attempts=self.max_retries, base=self.retry_base,
        )
        u = getattr(resp, "usage", None)
        if u is not None:
            dump = u.model_dump() if hasattr(u, "model_dump") else (u if isinstance(u, dict) else {})
            for k in ("prompt_tokens", "completion_tokens"):
                if isinstance(dump.get(k), int):
                    self.last_usage[k] = dump[k]
        msg = resp.choices[0].message
        self.last_tool_calls = normalize_tool_calls(getattr(msg, "tool_calls", None))
        return msg.content

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

    def _ollama_chat(self, messages, stream=False, temperature=0.3, timeout=None,
                     usage_sink=None, tool_sink=None, tools=None,
                     thinking_on=False, reasoning_sink=None):
        if not _gpu_acquire("ollama", 2): raise RuntimeError("GPU 正忙：ComfyUI 正在使用中，请稍后重试。")
        # 打点：空闲卸载计时器以"真正发起 Ollama 推理"为活动依据，
        # 仅持有租约（排队等待）不算活动，避免把等待误判成模型在用。
        _gpu_note_activity("ollama")
        # num_ctx 按模型画像的真实窗口缩放：大窗口模型不再被 14.5k 限死，
        # 小窗口模型也不会盲目塞爆（被服务端拒绝或挤爆显存）。
        budget = int(getattr(self, "prompt_budget", 0) or PROMPT_TOKEN_BUDGET)
        want_ctx = budget + LLM_MAX_TOKENS + 512
        win = int(self.capability.get("context_window") or 16384)
        num_ctx = min(want_ctx, win)
        # 窗口放得下完整预算时输出给满 LLM_MAX_TOKENS；放不下（小窗口模型）时
        # 至少保 512 输出，其余额度让给 prompt。
        if num_ctx >= want_ctx:
            num_predict = LLM_MAX_TOKENS
        else:
            num_predict = min(LLM_MAX_TOKENS, max(512, win - num_ctx + 512))
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": bool(stream),
            # ollama 默认 num_ctx=4096，会把 ~11000 token 的 prompt 静默截断
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
                # qwen3.6 模型 Modelfile 自带 presence_penalty=1.5，实测会诱发
                # 空 Action Input / JSON 参数等格式退化；显式归零贴近 OpenAI 默认
                "presence_penalty": 0.0,
                "frequency_penalty": 0.0,
            },
            # 显式 think：qwen3 toggle 家族按用户开关；原生思考模型 ollama 自行决定；
            # 不支持的模型给 false 也安全（实测 ollama 对无思考模板的模型忽略该字段）。
            "think": bool(thinking_on),
        }
        if tools:
            # ollama 原生 /api/chat 的 tools 直接收 function 对象列表（不含 type 包装）
            payload["tools"] = [t.get("function", t) for t in tools]
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._ollama_url("/api/chat"), data=data,
            headers={"Content-Type": "application/json"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        _t = self._OLLAMA_TIMEOUT if timeout is None else float(timeout)
        try:
            resp = opener.open(req, timeout=_t)
        except Exception:
            _gpu_release("ollama")
            raise
        if stream:
            def _stream_done():
                # 流式收尾再打一次点：长回答结束时间作为"最后活动"更准确
                _gpu_note_activity("ollama")
                _gpu_release("ollama")
            return _OllamaStream(resp, _stream_done, usage_sink=usage_sink,
                                 tool_sink=tool_sink, reasoning_sink=reasoning_sink)
        body = json.loads(resp.read().decode("utf-8"))
        _gpu_note_activity("ollama")
        _gpu_release("ollama")
        if usage_sink is not None:
            for k in ("prompt_eval_count", "eval_count"):
                v = body.get(k)
                if isinstance(v, int):
                    usage_sink[k] = v
        msg = body.get("message") or {}
        if tool_sink is not None:
            tool_sink.extend(normalize_tool_calls(msg.get("tool_calls")))
        return msg.get("content", "")

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
        # mock 无服务端 usage：用启发式估算填账本，保证离线演示下 trace 也有 token 数字
        _prompt_est = self._heuristic_tokens(
            "\n".join(m.get("content") for m in messages if isinstance(m.get("content"), str))
        )

        def _mark(text):
            self.last_usage = {
                "prompt_tokens": _prompt_est,
                "completion_tokens": self._heuristic_tokens(text or ""),
            }
            return text
        if has_image:
            answer = (
                "Thought: 用户附带了图片，mock 离线模型没有视觉能力，需如实告知。\n"
                "Final Answer: （演示模式·mock LLM）检测到您附带了图片，但 mock 离线模型"
                "不具备视觉能力，无法识别图片内容。请在 ⚙ 模型设置 中切换到支持视觉的"
                "本地模型（如 Ollama qwen3.6 系列）后重试。"
            )
            _mark(answer)
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

        _mark(answer)
        if stream:
            def g():
                for ch in answer:
                    yield ch

            return g()
        return answer
