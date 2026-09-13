---
name: engine-project-setup
description: Let a local coding agent prepare a Godot, Unity, or Unreal project by detecting the official engine executable, writing project-root configuration, starting the engine, and verifying the connection.
---

# Engine project setup

Use when the user asks the workbench AI to install, connect, or configure a game engine.

## Actions

1. Scan the project root for `project.godot`, `ProjectSettings/ProjectVersion.txt`, or `.uproject`.
2. Detect an installed executable with `/api/engine/prepare`; if missing, show the catalog download URL and stop before downloading.
3. When the user has authorized downloading, use only the official catalog URL, a user selected D: drive target, and verify the downloaded executable before configuration.
4. Write `.docmind_engine.json` in the selected game project root, never in the engine install directory.
5. Start with `/api/engine/start`; use `embed:true` only in native desktop mode.
6. Check `/api/engine/status`, `/api/engine/logs`, and `/api/engine/verify`; report `embedded:false` honestly when running in a browser.

## Safety and scope

- Keep all project writes inside the active project root and task allowed paths.
- Do not silently download large binaries or execute an unknown installer.
- Preserve `.meta` files for Unity and never edit Unreal `.uasset` binaries directly.
- Record executable path, engine version, project root, and verification output in the task result.

## Relevant files

- `game_workbench.py`: engine catalog, prepare, start, verify
- `desktop_bridge.py`: HWND discovery and embedding
- `api.py`: `/api/engine/prepare`, `/api/engine/start`, `/api/engine/embed`
- `HANDOFF.md`: current implementation status and remaining work (sections 3/5; old HANDOFF_ENGINE_EMBEDDING.md removed 2026-09-13)
