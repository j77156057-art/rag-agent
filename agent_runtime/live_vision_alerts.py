"""Parse visible anomaly candidates from a vision model response.

Model output is untrusted evidence. This module only validates its shape; the
client must confirm a candidate in a later frame before notifying the user.
"""

import json
import re


ALERT_TYPES = {"error_message", "crash", "render_failure", "layout_breakage", "unexpected_state"}


def parse_live_vision_result(raw):
    text = str(raw or "").strip()[:6000]
    candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
    try:
        data = json.loads(candidate)
    except (TypeError, ValueError):
        return text[:2000], []
    if not isinstance(data, dict):
        return text[:2000], []
    # JSON 已成功解析：observation 必须是字符串，数字/数组等异常形状归一为空，
    # 不做 str() 强转；缺键或空串时也绝不能回退展示原始 JSON 源码。
    raw_observation = data.get("observation")
    observation = (raw_observation.strip()[:2000]
                   if isinstance(raw_observation, str) else "")
    anomalies = data.get("anomalies")
    alerts = []
    if isinstance(anomalies, list):
        for item in anomalies[:3]:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "").strip()
            target = re.sub(r"\s+", " ", str(item.get("target") or "")).strip()[:100]
            evidence = re.sub(r"\s+", " ", str(item.get("evidence") or "")).strip()[:240]
            try:
                confidence = float(item.get("confidence"))
            except (ValueError, TypeError):
                continue
            if kind in ALERT_TYPES and target and evidence and 0.75 <= confidence <= 1:
                alerts.append({"type": kind, "target": target, "evidence": evidence,
                               "confidence": round(confidence, 2)})
    return observation, alerts
