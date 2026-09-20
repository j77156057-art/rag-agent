---
name: pdf
description: Create readable PDF reports with stable pagination, Unicode text, lists, tables, and post-write validation.
when_to_use: Use when the user needs a PDF deliverable, printable report, policy, handout, or fixed-layout document.
---

# PDF documents

Create a real `.pdf` file with `create_artifact`; do not rename plain text or HTML to `.pdf`.

## Workflow

1. Identify the document purpose, reader, and expected level of detail.
2. Build a short title and opening summary, followed by sections in reading order.
3. Call `create_artifact` with `format: "pdf"`. Use the same `sections` structure as the `documents` skill.
4. Confirm that the result has `ok: true`, a non-zero byte size, and at least one validated page.
5. Return the exact path and validation result.

## Layout rules

- Keep headings short and descriptive.
- Avoid very wide tables; split them into sections when needed.
- Use bullets only for parallel items.
- Put critical decisions and warnings in prose near the relevant section.
- For Chinese content, write normal Unicode text; the renderer selects an available CJK font.

## Minimal payload

```json
{
  "format": "pdf",
  "filename": "security-overview.pdf",
  "title": "Security Overview",
  "sections": [
    {
      "heading": "Access model",
      "paragraphs": ["Access is evaluated before retrieval."],
      "table": {
        "headers": ["Role", "Capability"],
        "rows": [["Viewer", "Read permitted documents"], ["Admin", "Manage access"]]
      }
    }
  ]
}
```
