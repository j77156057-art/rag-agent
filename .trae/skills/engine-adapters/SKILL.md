# DocMind Engine Adapter Skills

Use the adapter matching the project's configured engine. Always inspect project files before proposing changes, restrict edits to the active task region, and run the engine's headless verification after edits.

## Godot 4
- Project manifest: `project.godot`; scenes: `.tscn`; scripts: `.gd`.
- Validate: `godot --headless --path . --editor --quit`.
- Parse errors use `res://path/file.gd:line`.
- Prefer typed GDScript, preserve tabs, and never rewrite unrelated scene nodes.

## Unity
- Project manifest: `ProjectSettings/ProjectVersion.txt`; scenes/prefabs: `.unity`, `.prefab`; scripts: `.cs`.
- Validate with the configured Unity executable: `-batchmode -nographics -quit -projectPath <root>`.
- Treat `.meta` files as paired assets; never delete or rename an asset without its `.meta` file.
- Use serialized fields and preserve scene/prefab GUID references.

## Unreal Engine
- Project manifest: `.uproject`; source: `Source/`; assets: `.uasset`.
- Validate with `UnrealEditor -Unattended -NullRHI -ProjectOnly` when available.
- Never edit binary `.uasset` files directly; use editor automation or documented import tools.
- Preserve module names, Build.cs dependencies, and reflection macros.

## Shared workflow
1. Resolve the task region and allowed paths.
2. Read the engine manifest and related symbols.
3. Produce an impact list before edits.
4. Generate a minimal diff.
5. Run engine verification and task tests.
6. Record logs and keep a rollback point.
