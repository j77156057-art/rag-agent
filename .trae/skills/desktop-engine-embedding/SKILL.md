---
name: desktop-engine-embedding
description: Connect a local game-engine process to a Windows desktop workbench by discovering its HWND, embedding it safely, and synchronizing lifecycle and size.
---

# Desktop engine embedding

Use this skill when adding or repairing native Windows embedding for Godot, Unity, Unreal, or another local engine.

## Required flow

1. Start the engine and retain its process ID.
2. Wait briefly, then enumerate visible top-level windows and select the window whose process ID matches.
3. Obtain the workbench host HWND from the native desktop shell. Browser tabs are not valid Win32 parents.
4. Call `SetParent`, add `WS_CHILD | WS_VISIBLE`, remove top-level caption styles, and call `MoveWindow`.
5. Return an explicit embedded/failed result; never claim embedding when the host or child HWND is missing.
6. On engine stop, clear the embedding state. On host resize, call the resize helper.

## Repository integration

- Reuse `desktop_bridge.py` for `find_window`, `find_host`, `embed`, and `resize`.
- Keep process lifecycle in `game_workbench.py` and expose narrow FastAPI endpoints.
- Preserve browser fallback: embedding is optional and must degrade to an independent engine window.
- Do not pass arbitrary HWND values from an untrusted remote client; desktop mode is local-only.
- Test syntax and the complete Python suite after changes.

## Engine-specific selection

- Godot: title hint `Godot`; launch with `--path <project>`.
- Unity: title hint `Unity`; launch with `-projectPath <project>`.
- Unreal: title hint `Unreal`; launch the `.uproject` with the editor executable.

For engine-specific editor protocols or scene/asset adapters, read `.trae/skills/engine-adapters/SKILL.md` as well.
