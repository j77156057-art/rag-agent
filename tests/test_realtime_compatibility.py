from unittest.mock import patch

from fastapi.testclient import TestClient

import api


def test_realtime_status_explicitly_reports_sampled_frame_gateway_mode():
    with patch("agent_runtime.realtime_provider.describe", return_value={"configured": "", "available": [], "degraded_to": "sampled-frames"}):
        response = TestClient(api.app).get("/api/vision/realtime-status")
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "sampled-frames"
    assert body["mode_label"] == "兼容抽帧"
    assert body["native_available"] is False
    assert body["limitations"]


def test_realtime_status_reports_native_provider_availability_before_connecting():
    with patch("agent_runtime.realtime_provider.describe", return_value={"configured": "dashscope_omni", "available": ["dashscope_omni"]}), \
            patch("agent_runtime.realtime_provider.resolve", return_value={"ok": True, "name": "dashscope_omni", "capabilities": []}):
        body = TestClient(api.app).get("/api/vision/realtime-status").json()
    assert body["mode"] == "native-realtime"
    assert body["native_provider"] == "dashscope_omni"
    assert body["native_available"] is True
    assert "hello.ok" in body["mode_reason"]
