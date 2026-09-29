import unittest
from unittest.mock import patch

import voice
import voice_dialogue


class _FakeVoiceLlm:
    def __init__(self):
        self.messages = None

    def chat(self, messages, stream=False, temperature=0.2):
        self.messages = messages
        return "主 Agent 还在执行，我已记下你的反馈。"


class VoiceBridgeTests(unittest.TestCase):
    def test_unconfigured_stt_explicitly_returns_browser_fallback(self):
        with patch.dict("os.environ", {
            "DOCMIND_STT_URL": "", "DOCMIND_STT_API_KEY": "",
        }, clear=False):
            self.assertFalse(voice.configured("STT"))
            result = voice.transcribe(b"audio", "voice.webm")
            self.assertFalse(result["ok"])
            self.assertEqual(result["fallback"], "browser")

    def test_companion_has_no_tool_context_and_keeps_reply_short(self):
        llm = _FakeVoiceLlm()
        result = voice_dialogue.reply("这里不对，请记下来", "主 Agent 已完成第一版", llm=llm)
        self.assertIn("已记下", result)
        self.assertEqual(len(llm.messages), 2)
        self.assertIn("不能修改文件", llm.messages[0]["content"])
        self.assertIn("这里不对", llm.messages[1]["content"])

    def test_voice_status_and_dialogue_api(self):
        import api
        from starlette.testclient import TestClient
        fake = lambda text, main_result="": "收到：" + text
        with patch("voice_dialogue.reply", side_effect=fake):
            with TestClient(api.app) as client:
                status = client.get("/api/voice/status").json()
                self.assertTrue(status["ok"])
                response = client.post("/api/voice/dialogue", json={"text": "这里不对", "main_result": "执行中"})
                self.assertTrue(response.json()["ok"])
                self.assertIn("这里不对", response.json()["text"])


if __name__ == "__main__":
    unittest.main()
