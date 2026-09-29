import json

from agent_runtime.live_vision_alerts import parse_live_vision_result


def test_structured_alert_requires_visible_target_evidence_and_confidence():
    response = json.dumps({
        "observation": "画面中央出现错误弹窗",
        "anomalies": [
            {"type": "error_message", "target": "Error 404", "evidence": "弹窗文字为 Error 404", "confidence": 0.94},
            {"type": "crash", "target": "", "evidence": "猜测已崩溃", "confidence": 0.99},
            {"type": "unexpected_state", "target": "血条", "evidence": "可能不对", "confidence": 0.4},
        ],
    }, ensure_ascii=False)
    observation, alerts = parse_live_vision_result(response)
    assert observation == "画面中央出现错误弹窗"
    assert alerts == [{"type": "error_message", "target": "Error 404",
                       "evidence": "弹窗文字为 Error 404", "confidence": 0.94}]


def test_unstructured_legacy_result_does_not_raise_alert():
    assert parse_live_vision_result("画面似乎有问题") == ("画面似乎有问题", [])


def test_empty_observation_does_not_fall_back_to_raw_json_source():
    raw = '{"observation":"","anomalies":[]}'
    observation, alerts = parse_live_vision_result(raw)
    assert observation == ""
    assert alerts == []


def test_missing_observation_key_keeps_alerts_but_no_json_text():
    raw = json.dumps({
        "anomalies": [
            {"type": "error_message", "target": "Error 404", "evidence": "弹窗显示 Error 404", "confidence": 0.94},
        ],
    }, ensure_ascii=False)
    observation, alerts = parse_live_vision_result(raw)
    assert observation == ""
    assert alerts == [{"type": "error_message", "target": "Error 404",
                       "evidence": "弹窗显示 Error 404", "confidence": 0.94}]


def test_non_string_observation_is_normalized_to_empty():
    assert parse_live_vision_result('{"observation":123}') == ("", [])
    assert parse_live_vision_result('{"observation":["a","b"]}') == ("", [])
    assert parse_live_vision_result('{"observation":null}') == ("", [])
