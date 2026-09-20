---
name: presentations
description: Create concise PowerPoint PPTX presentations with a title slide, clear slide titles, and scannable bullets.
when_to_use: Use for PowerPoint, PPTX, presentations, briefings, project reviews, and meeting decks.
---

# Presentations

Create an actual `.pptx` file with `create_artifact`.

## Workflow

1. Establish the audience and the decision, update, or story the deck must support.
2. Give every slide one clear purpose and a literal title.
3. Keep each slide scannable. Prefer three to six short bullets instead of paragraphs.
4. Call `create_artifact` with `format: "pptx"`, a deck title, and `slides` containing `title` and `bullets`.
5. Finish only when the tool returns `ok: true` and the validation slide count is correct.

## Minimal payload

```json
{
  "format": "pptx",
  "filename": "quarterly-review.pptx",
  "title": "Quarterly Review",
  "subtitle": "Delivery, risk, and next decisions",
  "slides": [
    {"title": "What changed", "bullets": ["SSO enabled", "Access checks moved into retrieval"]},
    {"title": "Next decisions", "bullets": ["Select the enterprise identity provider"]}
  ]
}
```
