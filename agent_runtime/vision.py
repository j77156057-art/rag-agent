"""Harness 视觉适配层。

原生视觉模型直接接收图片；文本模型则可选地交给一个已配置的视觉模型生成
受长度约束的描述，再作为普通上下文交给主 Agent。这里不落盘图片正文，便于
审计和后续替换为 MCP/远程视觉服务。
"""
import os
import time

from llm import LLMClient


def _truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def configured() -> bool:
    return bool((os.getenv("DOCMIND_VISION_MODEL") or "").strip())


def analyze_images(images, *, current_capability=None):
    """返回 ``(images_for_main_model, context_messages, audit)``。

    ``images`` 是 base64 字符串列表。原生视觉模型拿到原图；其它模型只有在
    明确配置 DOCMIND_VISION_MODEL 后才启用 Harness 兜底。
    """
    images = list(images or [])
    cap = current_capability or {}
    mode = cap.get("vision", "unknown")
    if not images:
        return None, (), {"mode": "none", "image_count": 0}
    if mode == "native":
        return images, (), {"mode": "native", "image_count": len(images)}
    provider = (os.getenv("DOCMIND_VISION_PROVIDER") or "ollama").strip()
    model = (os.getenv("DOCMIND_VISION_MODEL") or "").strip()
    if not model:
        return None, (
            "【图片输入】当前模型未确认支持图片识别，且未配置 Harness 视觉模型。"
            "请在模型设置中确认视觉能力，或配置 DOCMIND_VISION_MODEL 后重试。",
        ), {"mode": "unavailable", "image_count": len(images), "provider": provider}
    started = time.monotonic()
    try:
        client = LLMClient(
            provider=provider,
            model=model,
            api_key=os.getenv("DOCMIND_VISION_API_KEY") or None,
            base_url=os.getenv("DOCMIND_VISION_BASE_URL") or None,
        )
        # 兜底模型必须自身具备视觉能力，避免递归把图片再转回文本模型。
        if client.capability.get("vision") != "native" and not _truthy(os.getenv("DOCMIND_VISION_ALLOW_UNVERIFIED")):
            return None, (
                f"【图片输入】Harness 视觉模型 {provider}/{model} 未被能力画像确认，"
                "为避免误识别已跳过；可在环境变量中显式允许未验证模型。",
            ), {"mode": "unavailable", "image_count": len(images), "provider": provider, "model": model}
        prompt = (
            "请只描述图片中与软件/游戏调试有关的可观察事实：界面布局、文字、角色、"
            "材质、摄像机、错误提示和明显异常。不要猜测图片外信息，控制在 1600 字以内。"
        )
        result = client.chat(
            [{"role": "user", "content": prompt, "images": images}],
            stream=False,
            enable_thinking=False,
        )
        text = str(result or "").strip()[:6000]
        if not text:
            raise RuntimeError("视觉模型返回空描述")
        elapsed = int((time.monotonic() - started) * 1000)
        context = (
            f"【Harness 图片观察（{provider}/{model}，耗时 {elapsed}ms）】\n{text}\n"
            "以上是视觉模型对当前图片的观察，不是代码事实；请结合项目文件和工具复核后再提出修改。"
        )
        return None, (context,), {
            "mode": "harness", "image_count": len(images), "provider": provider,
            "model": model, "elapsed_ms": elapsed,
        }
    except Exception as exc:  # 视觉辅助失败不应阻断文本问答
        return None, (
            f"【图片输入】Harness 视觉辅助失败：{type(exc).__name__}: {str(exc)[:240]}。"
            "请依据代码和日志继续判断，不要假设已经读取到图片。",
        ), {
            "mode": "error", "image_count": len(images), "provider": provider,
            "model": model, "error": type(exc).__name__,
        }
