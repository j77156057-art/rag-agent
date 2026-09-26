"""Bounded raster evidence for workflow visual feedback."""
from __future__ import annotations

import base64
import binascii
import io
import re
import warnings
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_IMAGE_BASE64 = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 64
FEEDBACK_STATUSES = frozenset({"pending", "processing", "awaiting_review", "failed", "accepted", "needs_changes"})


def evidence_path(storage: Path, workflow_id: str, feedback_id: str, phase: str) -> Path:
    if phase not in {"before", "after"}:
        raise ValueError("截图阶段无效")
    if any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) for value in (workflow_id, feedback_id)):
        raise ValueError("反馈标识无效")
    base = (storage / "visual_feedback").resolve()
    path = (base / workflow_id / feedback_id / (phase + ".png")).resolve()
    if base not in path.parents:
        raise ValueError("截图路径无效")
    return path


def decode_snapshot(encoded: str) -> tuple[bytes, dict]:
    """Accept actual PNG/JPEG/WebP bytes and re-encode as a metadata-free PNG."""
    if len(encoded) > MAX_IMAGE_BASE64:
        raise ValueError("截图过大，请缩小画面后重试")
    if encoded.startswith("data:"):
        header, separator, encoded = encoded.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
            raise ValueError("截图格式无效")
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("截图大小无效")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("截图格式无效")
                width, height = image.size
                if width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("截图尺寸过大")
                image.load()
                output = io.BytesIO()
                image.convert("RGB").save(output, format="PNG")
        png = output.getvalue()
        if len(png) > MAX_IMAGE_BYTES:
            raise ValueError("截图过大，请缩小画面后重试")
        return png, {"width": width, "height": height, "bytes": len(png)}
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("截图内容无效") from exc
