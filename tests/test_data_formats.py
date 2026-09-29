import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_formats import detect_file, read_file, write_data_file
from ingest import load_text
import tools as agent_tools


class DataFormatTests(unittest.TestCase):
    def test_dirty_csv_encoding_headers_and_rows_are_normalized(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "dirty.csv")
            path.write_bytes("姓名,姓名,年龄\r\n 张三 , ,18\r\n李四,未知,\r\n".encode("gb18030"))
            parsed = read_file(path)
            self.assertEqual(parsed["format"], "csv")
            self.assertEqual(parsed["encoding"], "gb18030")
            self.assertEqual(parsed["headers"], ["姓名", "姓名_2", "年龄"])
            self.assertEqual(parsed["rows"][0], ["张三", None, 18])
            self.assertEqual(parsed["rows"][1], ["李四", None, None])

    def test_unknown_extension_is_detected_as_jsonl_and_bad_lines_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "records.data")
            path.write_text('{"id": 1, "name": "a"}\n坏行\n{"id": 2, "name": "b"}\n', encoding="utf-8")
            info = detect_file(path)
            parsed = read_file(path)
            self.assertEqual(info["format"], "jsonl")
            self.assertEqual(parsed["kind"], "records")
            self.assertEqual(len(parsed["errors"]), 1)
            self.assertEqual(len(parsed["records"]), 2)

    def test_invalid_json_returns_error_report_instead_of_aborting(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "broken.json")
            path.write_text('{"id": 1,\n', encoding="utf-8")
            parsed = read_file(path)
            self.assertEqual(parsed["kind"], "json")
            self.assertIsNone(parsed["value"])
            self.assertTrue(parsed["errors"])
            self.assertIn('"id"', parsed["text"])
            indexed = load_text(path)
            self.assertIn("[解析错误]", indexed)
            self.assertIn('"id"', indexed)

    def test_utf16_without_bom_is_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "rows.txt")
            path.write_bytes("姓名\n张三".encode("utf-16-le"))
            parsed = read_file(path)
            self.assertEqual(parsed["encoding"], "utf-16")
            self.assertIn("张三", parsed["text"])

    def test_generate_and_roundtrip_multiple_formats(self):
        with tempfile.TemporaryDirectory() as temporary:
            for fmt, spec in {
                "json": {"data": {"name": "测试", "items": [1, 2]}},
                "jsonl": {"records": [{"id": 1}, {"id": 2}]},
                "csv": {"headers": ["id", "name"], "rows": [[1, "A"], [2, "B"]]},
                "yaml": {"data": {"enabled": True, "count": 2}},
                "toml": {"data": {"title": "demo", "server": {"port": 8080}}},
                "xml": {"data": {"item": {"id": 1}}},
            }.items():
                path = Path(temporary, f"out.{fmt}")
                result = write_data_file(temporary, {"path": path.name, "format": fmt, **spec})
                self.assertTrue(result["ok"], fmt)
                self.assertTrue(path.is_file(), fmt)
                self.assertEqual(result["validation"]["format"], fmt)

    def test_binary_inspection_and_exact_byte_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            blob = Path(temporary, "payload.bin")
            result = write_data_file(temporary, {"path": blob.name, "format": "binary", "hex": "00 ff 41 42"})
            self.assertTrue(result["ok"])
            self.assertEqual(blob.read_bytes(), b"\x00\xffAB")
            inspected = read_file(blob)
            self.assertTrue(inspected["binary"])
            self.assertEqual(inspected["kind"], "binary")
            self.assertEqual(inspected["header_hex"], "00ff4142")
            self.assertEqual(len(inspected["sha256"]), 64)
            scene = Path(temporary, "export.scn")
            with self.assertRaises(ValueError):
                write_data_file(temporary, {"path": scene.name, "format": "binary", "hex": "00ff"})
            self.assertFalse(scene.exists())
            write_data_file(temporary, {"path": scene.name, "format": "binary", "hex": "525352430001"})
            self.assertEqual(read_file(scene)["format"], "godot_binary_scene")
            source = Path(temporary, "source.tscn")
            write_data_file(temporary, {"path": source.name, "format": "txt",
                                        "text": '[gd_scene format=3]\n\n[node name="Root" type="Node2D"]'})
            self.assertEqual(read_file(source)["format"], "text")

    def test_extensionless_cache_with_nuls_stays_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "file_cache")
            path.write_bytes(b"RSRC\x00\x01\x02\x03" + bytes(range(32)))
            inspected = read_file(path)
            self.assertEqual(inspected["kind"], "binary")
            self.assertEqual(inspected["format"], "binary")

    def test_extensionless_gitignore_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, ".gitignore")
            write_data_file(temporary, {"path": path.name, "format": "txt", "text": ".godot/\n*.bin"})
            self.assertEqual(read_file(path)["format"], "text")
            self.assertIn("*.bin", read_file(path)["text"])

    def test_harness_tools_inspect_binary_and_generate_text(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(agent_tools, "_get_code_root", return_value=temporary):
            source = Path(temporary, "cache.bin")
            source.write_bytes(b"\x00\x01\xffimportant-string")
            direct = agent_tools.read_file(source.name)
            self.assertIn("只读二进制检查", direct)
            self.assertIn("hex_preview", direct)
            inspected = json.loads(agent_tools.inspect_data_file(source.name))
            self.assertTrue(inspected["ok"])
            self.assertEqual(inspected["file"]["format"], "binary")
            generated = json.loads(agent_tools.generate_data_file(json.dumps({
                "path": ".gitignore", "format": "txt", "text": ".godot/\n"
            })))
            self.assertTrue(generated["ok"])
            self.assertIn(".godot/", Path(temporary, ".gitignore").read_text(encoding="utf-8"))

    def test_invalid_generated_toml_never_replaces_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "invalid.toml")
            with self.assertRaises(ValueError):
                write_data_file(temporary, {"path": path.name, "format": "toml", "data": {"value": None}})
            self.assertFalse(path.exists())

    def test_ingest_load_text_supports_xlsx_when_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "rows.csv")
            path.write_text("id,name\n1,A\n", encoding="utf-8")
            text = load_text(path)
            self.assertIn("id\tname", text)
            self.assertIn("1\tA", text)


if __name__ == "__main__":
    unittest.main()
