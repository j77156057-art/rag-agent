---
name: agent-model-routing
description: Route simple work to local models and difficult work to approved cloud models while enforcing connector and filesystem permissions.
---

Use `/api/agent/route` before expensive reasoning. Keep simple edits, lookups, and explanations local; upgrade only when complexity or file count warrants it. Automatic cloud switching is disabled by default and requires `AGENT_AUTO_CLOUD=1`, a configured cloud provider/API key, and a visible route event in the task trace.

Use `/api/agent/permission` before every write. The DocMind Agent project itself is always denied. Project files are allowed only inside the active root and task scope. External paths require explicit user authorization and a separate approval record; never silently modify them.

Connectors are opt-in. Inspect their status, minimize data sent, and log the connector/model used. Preserve Git snapshots and run verification after changes.
