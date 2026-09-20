import json
import os
import tempfile
import unittest
from unittest import mock

import artifact_tools
import agent


class ArtifactToolsTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="dm_artifacts_")
        self.patch = mock.patch.object(artifact_tools, "get_runtime", lambda key: self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def create(self, payload):
        result = json.loads(artifact_tools.create_artifact(json.dumps(payload, ensure_ascii=False)))
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(os.path.isfile(result["path"]))
        self.assertGreater(result["bytes"], 0)
        return result

    def test_creates_and_validates_docx(self):
        result = self.create({"format": "docx", "filename": "guide.docx", "title": "指南",
                              "sections": [{"heading": "范围", "paragraphs": ["内容"]}]})
        self.assertGreaterEqual(result["validation"]["paragraphs"], 2)

    def test_creates_and_validates_pdf(self):
        result = self.create({"format": "pdf", "filename": "guide.pdf", "title": "指南",
                              "sections": [{"heading": "范围", "paragraphs": ["内容"]}]})
        self.assertGreaterEqual(result["validation"]["pages"], 1)

    def test_creates_and_validates_pptx(self):
        result = self.create({"format": "pptx", "filename": "review.pptx", "title": "Review",
                              "slides": [{"title": "Status", "bullets": ["Done"]}]})
        self.assertEqual(result["validation"]["slides"], 2)

    def test_creates_and_validates_xlsx_and_escapes_formulas(self):
        result = self.create({"format": "xlsx", "filename": "review.xlsx",
                              "sheets": [{"name": "Data", "headers": ["Value"],
                                          "rows": [["=HYPERLINK(\"x\")"]]}]})
        from openpyxl import load_workbook
        workbook = load_workbook(result["path"], data_only=False)
        self.assertEqual(workbook["Data"]["A2"].value, "'=HYPERLINK(\"x\")")
        workbook.close()

    def test_rejects_directory_in_filename(self):
        result = json.loads(artifact_tools.create_artifact(json.dumps({
            "format": "pdf", "filename": "../outside.pdf", "title": "x"
        })))
        self.assertFalse(result["ok"])
        self.assertIn("filename", result["error"])

    def test_existing_file_gets_unique_name(self):
        first = self.create({"format": "docx", "filename": "same.docx", "title": "One"})
        second = self.create({"format": "docx", "filename": "same.docx", "title": "Two"})
        self.assertNotEqual(first["path"], second["path"])

    def test_artifact_generation_uses_existing_write_guardrails(self):
        self.assertIn("create_artifact", agent._WRITE_TOOLS)
        self.assertIn("create_artifact", agent._NO_PARALLEL_TOOLS)
        self.assertTrue(agent._has_write_intent("请生成一份 PDF 报告"))
        self.assertFalse(agent._has_write_intent("请审查这份 PDF 报告"))


if __name__ == "__main__":
    unittest.main()
