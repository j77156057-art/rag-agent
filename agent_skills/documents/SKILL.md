---
name: documents
description: Create polished Word DOCX documents with clear structure, tables, and validation.
when_to_use: Use for Word files, DOCX reports, manuals, proposals, specifications, and editable formal documents.
---

# Word documents

Create an actual `.docx` file. Do not return Markdown and call it a Word document.

## Workflow

1. Confirm the intended reader, purpose, and filename from the request. Infer them when the context is clear.
2. Organize the content around one conclusion or task. Use a descriptive title, a short opening summary, and logically ordered sections.
3. Keep paragraphs connected and concise. Use bullets for genuine lists and tables for repeated fields or comparisons.
4. Call `create_artifact` with `format: "docx"`. Supply `sections`; each section may contain `heading`, `level`, `paragraphs`, `bullets`, and one `table` with `headers` and `rows`.
5. Treat the operation as complete only when the tool returns `ok: true`. Report the returned path, byte size, and validation counts.

## Quality rules

- Use concrete headings rather than slogans.
- Do not invent facts, approvals, dates, authors, or commitments.
- Keep tables narrow enough to read on a portrait page.
- Prefer normal prose over fragments unless the content is a checklist.
- Preserve uncertainty and conditions from the source material.

## Minimal payload

```json
{
  "format": "docx",
  "filename": "project-guide.docx",
  "title": "Project Guide",
  "subtitle": "Operations and access control",
  "sections": [
    {
      "heading": "Purpose",
      "level": 1,
      "paragraphs": ["This guide defines the operating model."],
      "bullets": ["Audience: administrators", "Scope: production"]
    }
  ]
}
```
