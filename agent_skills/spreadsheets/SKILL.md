---
name: spreadsheets
description: Create Excel XLSX workbooks with typed values, readable headers, sensible widths, and workbook validation.
when_to_use: Use for Excel files, XLSX trackers, inventories, comparison tables, exports, and structured data deliverables.
---

# Spreadsheets

Create an actual `.xlsx` file with `create_artifact`.

## Workflow

1. Define what each row represents and give every column a precise header.
2. Split unrelated datasets into separate sheets.
3. Keep numbers and booleans typed as JSON numbers and booleans instead of strings.
4. Call `create_artifact` with `format: "xlsx"` and `sheets` containing `name`, `headers`, and `rows`.
5. Confirm `ok: true` and verify the returned sheet names.

## Safety and quality

- Formula-like strings are escaped by default to prevent formula injection.
- Set `allow_formulas: true` only when the user explicitly asks for formulas and the values are trusted.
- Use short, unique sheet names and avoid empty columns.
- Do not invent missing values; leave them blank or mark them clearly.

## Minimal payload

```json
{
  "format": "xlsx",
  "filename": "access-review.xlsx",
  "title": "Access Review",
  "sheets": [
    {
      "name": "Documents",
      "headers": ["Document", "Scope", "Owner"],
      "rows": [["Operations Manual", "Restricted", "IT"]]
    }
  ]
}
```
