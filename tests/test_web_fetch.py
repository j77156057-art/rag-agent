import io
import unittest
from unittest.mock import patch
import tools

class WebFetchTests(unittest.TestCase):
    def test_rejects_non_http(self):
        self.assertIn('只允许', tools.web_fetch('file:///secret.txt'))

    def test_extracts_html_metadata_and_text(self):
        resp = io.BytesIO(b'<html><head><title>Guide</title></head><body><script>x</script><p>Hello world</p></body></html>')
        resp.headers = {'Content-Type': 'text/html; charset=utf-8'}
        resp.geturl = lambda: 'https://example.test/guide'
        with patch('tools.urllib.request.urlopen', return_value=resp):
            out = tools.web_fetch('https://example.test/guide')
        self.assertIn('标题：Guide', out); self.assertIn('Hello world', out); self.assertNotIn('script', out)

    def test_non_html_is_reported(self):
        resp = io.BytesIO(b'PNGDATA'); resp.headers = {'Content-Type': 'image/png'}; resp.geturl=lambda: 'https://x.test/a.png'
        with patch('tools.urllib.request.urlopen', return_value=resp):
            self.assertIn('仅支持 HTML', tools.web_fetch('https://x.test/a.png'))

    def test_network_failure_is_explicit(self):
        with patch('tools.urllib.request.urlopen', side_effect=TimeoutError('late')):
            self.assertIn('网页读取失败', tools.web_fetch('https://example.test'))

if __name__ == '__main__': unittest.main()
