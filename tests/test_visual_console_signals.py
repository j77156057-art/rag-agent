# -*- coding: utf-8 -*-
"""P0：预览链路采集浏览器信号，堵住「截图正常但页面报错」的假通过。

这些用例只驱动 CDP 事件分类逻辑，不启动浏览器，因此在 CI 上也能跑。
"""
import unittest

from agent_runtime.visual_acceptance import _DevTools


def _tools() -> _DevTools:
    return _DevTools(connection=None)


class ConsoleSignalTests(unittest.TestCase):
    def test_console_error_is_recorded(self):
        devtools = _tools()
        devtools._handle_event({"method": "Runtime.consoleAPICalled",
                                "params": {"type": "error",
                                           "args": [{"value": "boom"}]}})
        self.assertEqual(devtools.console, [{"level": "error", "text": "boom"}])

    def test_console_warning_is_recorded_separately(self):
        devtools = _tools()
        devtools._handle_event({"method": "Runtime.consoleAPICalled",
                                "params": {"type": "warning", "args": [{"value": "careful"}]}})
        self.assertEqual(devtools.console, [{"level": "warning", "text": "careful"}])
        self.assertEqual([item for item in devtools.console if item["level"] == "error"], [])

    def test_console_log_is_ignored(self):
        devtools = _tools()
        devtools._handle_event({"method": "Runtime.consoleAPICalled",
                                "params": {"type": "log", "args": [{"value": "noise"}]}})
        self.assertEqual(devtools.console, [])

    def test_log_entry_added_is_recorded(self):
        devtools = _tools()
        devtools._handle_event({"method": "Log.entryAdded",
                                "params": {"entry": {"level": "error", "source": "network",
                                                     "text": "Failed to load resource"}}})
        self.assertEqual(devtools.console[0]["level"], "error")
        self.assertIn("Failed to load resource", devtools.console[0]["text"])

    def test_exception_thrown_is_a_runtime_error(self):
        devtools = _tools()
        devtools._handle_event({"method": "Runtime.exceptionThrown",
                                "params": {"exceptionDetails": {"text": "Uncaught TypeError"}}})
        self.assertEqual(devtools.runtime_errors, ["Uncaught TypeError"])

    def test_duplicate_errors_are_not_repeated(self):
        devtools = _tools()
        event = {"method": "Runtime.consoleAPICalled",
                 "params": {"type": "error", "args": [{"value": "same"}]}}
        devtools._handle_event(event)
        devtools._handle_event(event)
        self.assertEqual(len(devtools.console), 1)


class NetworkSignalTests(unittest.TestCase):
    def test_http_404_is_recorded_with_url(self):
        devtools = _tools()
        devtools._handle_event({"method": "Network.requestWillBeSent",
                                "params": {"requestId": "1",
                                           "request": {"url": "http://x/app.js"}}})
        devtools._handle_event({"method": "Network.responseReceived",
                                "params": {"requestId": "1",
                                           "response": {"status": 404, "url": "http://x/app.js",
                                                        "statusText": "Not Found"}}})
        self.assertEqual(len(devtools.failed_requests), 1)
        self.assertEqual(devtools.failed_requests[0]["kind"], "http_404")
        self.assertEqual(devtools.failed_requests[0]["url"], "http://x/app.js")

    def test_success_status_is_ignored(self):
        devtools = _tools()
        devtools._handle_event({"method": "Network.responseReceived",
                                "params": {"requestId": "2",
                                           "response": {"status": 200, "url": "http://x/ok.js"}}})
        self.assertEqual(devtools.failed_requests, [])

    def test_network_failure_uses_captured_url(self):
        devtools = _tools()
        devtools._handle_event({"method": "Network.requestWillBeSent",
                                "params": {"requestId": "3",
                                           "request": {"url": "http://x/api"}}})
        devtools._handle_event({"method": "Network.loadingFailed",
                                "params": {"requestId": "3", "errorText": "net::ERR_CONNECTION_REFUSED"}})
        self.assertEqual(devtools.failed_requests[0]["kind"], "network")
        self.assertEqual(devtools.failed_requests[0]["url"], "http://x/api")

    def test_abort_duplicate_of_http_status_is_dropped(self):
        devtools = _tools()
        devtools._handle_event({"method": "Network.requestWillBeSent",
                                "params": {"requestId": "4",
                                           "request": {"url": "http://x/missing.js"}}})
        devtools._handle_event({"method": "Network.responseReceived",
                                "params": {"requestId": "4",
                                           "response": {"status": 404, "url": "http://x/missing.js"}}})
        devtools._handle_event({"method": "Network.loadingFailed",
                                "params": {"requestId": "4", "errorText": "net::ERR_ABORTED"}})
        self.assertEqual(len(devtools.failed_requests), 1)

    def test_favicon_noise_is_ignored_everywhere(self):
        devtools = _tools()
        devtools._handle_event({"method": "Network.requestWillBeSent",
                                "params": {"requestId": "5",
                                           "request": {"url": "http://x/favicon.ico"}}})
        devtools._handle_event({"method": "Network.responseReceived",
                                "params": {"requestId": "5",
                                           "response": {"status": 404, "url": "http://x/favicon.ico"}}})
        devtools._handle_event({"method": "Runtime.consoleAPICalled",
                                "params": {"type": "error",
                                           "args": [{"value": "Failed to load resource: favicon.ico"}]}})
        self.assertEqual(devtools.failed_requests, [])
        self.assertEqual(devtools.console, [])

    def test_failure_list_is_bounded(self):
        devtools = _tools()
        for index in range(200):
            devtools._handle_event({"method": "Network.responseReceived",
                                    "params": {"requestId": str(index),
                                               "response": {"status": 500,
                                                            "url": f"http://x/{index}.js"}}})
        self.assertLessEqual(len(devtools.failed_requests), 30)


if __name__ == "__main__":
    unittest.main()
