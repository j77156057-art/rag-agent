"""Validate model-proposed desktop targets and compare a later window frame."""
from __future__ import annotations

import json
import math
import re

from PIL import Image


def parse_visual_target(raw):
    text = str(raw or "").strip()[:5000]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("found") is not True:
        return None
    box = value.get("bbox")
    if not isinstance(box, list) or len(box) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(item) for item in box)
        confidence = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in (x1, y1, x2, y2, confidence)):
        return None
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1 and 0.8 <= confidence <= 1):
        return None
    label = re.sub(r"\s+", " ", str(value.get("label") or "")).strip()[:100]
    evidence = re.sub(r"\s+", " ", str(value.get("evidence") or "")).strip()[:240]
    if not label or not evidence:
        return None
    return {"bbox": [x1, y1, x2, y2], "label": label,
            "evidence": evidence, "confidence": round(confidence, 2)}


def parse_visual_verification(raw):
    """Accept only an evidenced before/after judgement; suggestions are not actions."""
    text = str(raw or "").strip()[:5000]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("status") not in {"met", "unmet", "uncertain"}:
        return None
    status = value["status"]
    evidence = re.sub(r"\s+", " ", str(value.get("evidence") or "")).strip()[:400]
    if not evidence:
        return None
    try:
        confidence = float(value.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return None
    if status != "uncertain" and confidence < 0.8:
        status = "uncertain"
    next_target = re.sub(r"\s+", " ", str(value.get("next_target") or "")).strip()[:160]
    return {"status": status, "evidence": evidence,
            "confidence": round(confidence, 2),
            "next_target": next_target if status == "unmet" else ""}


def target_fingerprint(raw, width, height, bbox):
    """Downsample a padded target neighborhood from a BGRA capture."""
    image = Image.frombytes("RGB", (int(width), int(height)), bytes(raw), "raw", "BGRX")
    x1, y1, x2, y2 = bbox
    pad_x = max(0.03, (x2 - x1) * 0.5)
    pad_y = max(0.03, (y2 - y1) * 0.5)
    left = max(0, int((x1 - pad_x) * width))
    top = max(0, int((y1 - pad_y) * height))
    right = min(width, max(left + 1, int((x2 + pad_x) * width)))
    bottom = min(height, max(top + 1, int((y2 + pad_y) * height)))
    return image.crop((left, top, right, bottom)).resize((24, 24), Image.Resampling.BILINEAR).tobytes()


def target_still_matches(before, after, *, max_mean_delta=18):
    if not before or len(before) != len(after):
        return False
    return sum(abs(a - b) for a, b in zip(before, after)) / len(before) <= max_mean_delta
