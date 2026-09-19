# DocMind · An Agent Runtime (Harness) for Engineering Codebases

An **agent runtime that runs fully offline**: it turns "an agent that runs" into an **observable, orchestrable, regressable** system.
On top of it sits a local engineering workbench (code Q&A / controlled rewrite / scene & runtime visualization) — **but the subject of this project is the Harness; the workbench is only its landing scenario**.

**Two failure modes it targets**:

1. **Hallucination** — the AI claims "damage is calculated in `player.py`" when no such file exists;
2. **Loss of control** — after an edit you don't know what changed, how many tokens it burned, or whether it will happen again.

The answer is to split the agent into six layers and add engineering guardrails to each (details in the "Harness Capabilities" section):

| Layer | Responsibility | Key implementation |
|---|---|---|
| **E Execution loop** | ReAct + reflection retry, native function-calling, plan mode, sub-agent delegation, parallel read-only batches, DAG multi-agent orchestration with failure re-planning | `agent.py` · `orchestrator.py` |
| **T Tool registry** | 47 tools; argument validation, domain allowlist, **controlled writes** (read-before-write + size cap + syntax check + human approval) | `tools.py` |
| **C Context management** | prompt token budget, observation truncation, trail pruning (system prompt and current question never dropped), rolling history summary | `agent.py::_fit_budget` |
| **S State storage** | per-turn trace ledger (**metadata only, never prompt/answer text**), session isolation & persistence, survives restart | `agent_trace.py` · `sessions.py` |
| **L Lifecycle hooks** | hot-pluggable hooks / skills, human approval gate, per-provider pricing with cost circuit breaker | `hooks.py` · `skills.py` · `pricing.py` |
| **V Evaluation** | golden-set rule scoring + optional LLM-judge + **baseline regression gate** (exit code 1 on regression) | `agent_eval.py` · `run_golden.py` · `golden/` |

**Three design commitments**: ① **reuse the guardrails** — native function-calling and parallel batches go through the existing guardrails instead of opening new execution paths; ② **bounded** — trails, observations, history and batches are all truncated or summarized; ③ **no overreach** — the orchestrator only rewrites *not-yet-executed* tasks, writes are never concurrent, hook exceptions are always swallowed.

Runs with zero API key (mock / local Ollama / llama.cpp); cloud providers can be switched in at any time.

---

## Landing scenario: the local workbench

On top sits a **local, single-user** engineering workbench (game / Mod projects as the validation scenario): task regions, per-region Git, contract-direction validation, changeset rollback, selection AI, symbol / relation graph, engine embedding, scene canvas, runtime timeline, GPU coordination.

Its purpose is to give the Harness a **real, complex, side-effect-heavy** environment — **not a generic ALM platform: no multi-user collaboration or role permissions** (no auth by default on localhost; a single-token check can be enabled).

---

## ✨ What the Workbench Does Today (2026-09 state)

| Capability | One-liner |
|---|---|
| **Region development** | One button slices a project into regions (assets / values / behaviors / levels / ui / audio / net / bugs); each region gets its **own git repo**; Agent writes are constrained to the owning region (cross-region writes are rejected); dependencies form a one-way DAG and contract validation prevents cyclic coupling |
| **Controlled writes** | Every AI write goes through `apply_edit` / `create_file` (read-before-write + size cap + `.py` syntax check + human confirmation); cross-region moves validate dependency direction; changes can be committed per-region or **rolled back as a whole changeset across regions** |
| **Selection AI** | Select a block of code in the editor → explain / review / ask (via Agent, with evidence) or **rewrite** (via a direct fast-path + LCS diff preview, persisted only on accept) |
| **Symbol & relation graph** | Multi-language symbol extraction (Python `ast` / GDScript / Java…) + relation graph of inheritance / scene-mount / call edges; the scene canvas adds four extra edge types |
| **Scene canvas** | A **visual + editable** canvas for Godot `.tscn`: hierarchy-tree / spatial-coordinate layouts; node parent-child hierarchy, instances, position / transform, and resource references at a glance; add / delete / rename / reparent / duplicate / set-props / drag-to-write-position, **all undoable, and undo restores the file byte-for-byte** |
| **Runtime timeline** | Gameplay events (damage / death / spawn / variable change) drawn as a multi-track timeline: type filtering, time zoom, session grouping, numeric curves, JSON export, click-an-event-to-jump-to-code-line |

Supporting pieces: **engine embedding** (Godot / Unity / Unreal launch + Win32 HWND embed into the workbench), **Web playtest** (export WASM and play inside the canvas), **MCP bridge**, **GPU lease queue**, and **desktop packaging** (PyInstaller onedir, double-click to run).

> Product-level usage docs and boundary notes for the **MCP bridge** and **Web playtest export** (config model / API table / call prerequisites / capability boundaries) are in [`docs/integrations.md`](docs/integrations.md).

## 🧱 Tech Stack

- Backend: Python · FastAPI (HTTP + SSE) · Chroma dual collections (docs / code) · OpenAI-compatible multi-provider (qwen / deepseek / ollama / llamacpp / mock, plus native embedding) · PyInstaller + pywebview
- Frontend: Vue 3.5 · Vite 5 · TypeScript · CodeMirror 6 · Vue Flow · hand-written dark design system
- Verification: `unittest` **450/450** · scene-canvas self-check **54/54** · browser smoke **27/27** (Playwright + system Edge) · engine-embed real-machine self-check **68/68** · `npm run build` (Vite 5.4.21)
- Agent runtime (harness): per-turn trace ledger + token/cost accounting · session isolation & persistence · LLM retry/deadline · eval gate · native function-calling · multi-agent orchestrator · cost fuse · hot-pluggable hooks & skills (see "Harness" below)

## 📁 Structure

```
rag-agent/
├── api.py                 # HTTP / SSE entry point (159 routes: chat / ingest / workbench-fs /
│                          #   regions / engine / desktop-host / selection-ai / scene / runtime / MCP / GPU /
│                          #   trace / sessions / budget / hooks / skills / orchestrate)
├── agent.py               # ReAct loop, reflection-retry, code-first routing, evidence guardrails;
│                          #   run/_run instrumentation shell; native function-calling, plan mode,
│                          #   sub-agent delegation, parallel tool batches
├── tools.py               # 47 tools: 9 base + controlled-write + dev/region tools + delegate/orchestrate/dev_use_skill
├── regions.py             # Region 2.0: declarative config, contract validation (DAG acyclic / exports exist),
│                          #   changesets and rollback
├── scene_runtime.py       # Scene-canvas core: .tscn line-block parse → graph model → controlled edit
│                          #   (rollback-able + undoable)
├── workbench_fs.py        # Sandboxed file tree, read/write, git status / history / rollback, symbol map, graph
├── symbols.py             # Multi-language symbol extraction + code-aware chunking
├── game_workbench.py      # Engine catalog / launch / embed, task / asset / bug workflows, ComfyUI lease
├── unity_graph.py         # P1-2 Unity GUID reference graph (pure .meta / .unity text analysis)
├── gpu_coordinator.py     # GPU lease queue (serial / parallel / multi; FIFO; TTL; CUDA env injection)
├── mcp_client.py          # MCP (Model Context Protocol) bridge
├── web_export.py          # Godot Web export + local playtest
├── engine_adapters.py     # Thin engine adapters
├── desktop_bridge.py      # Win32: find host window / SetParent embed / resize / focus
├── desktop.py             # Desktop launcher (single-instance guard + pywebview window)
├── llm.py / embeddings.py / vectorstore.py / ingest.py / config.py
├── agent_trace.py         # per-turn trace + token ledger (JSONL, metadata only; /trace viewer)
├── sessions.py            # session isolation + persistence + rolling summary
├── pricing.py             # per-provider pricing + global/session budget fuse
├── hooks.py               # hot-pluggable tool/turn hooks (.docmind/hooks/*.py)
├── skills.py              # hot-pluggable skills (.docmind/skills/**/*.md + dev_use_skill)
├── agent_eval.py          # golden-question auto-scoring + baseline regression gate
├── orchestrator.py        # multi-agent orchestration: task DAG + parallel + re-plan + synthesis
├── frontend/              # Vue workbench (build output → ../web)
│   └── src/workbench/components/
│       ├── SceneCanvas.vue / SceneNodeCard.vue / SceneFileCard.vue   # scene canvas
│       ├── RuntimeTimeline.vue                                       # runtime timeline
│       ├── UnityGraph.vue                                            # Unity GUID graph (P1-2)
│       └── GpuPanel.vue                                              # GPU lease panel (P2-1)
├── tests/                 # unittest 450 items
├── verify_scene_canvas.py / verify_scene_canvas_ui.mjs   # scene-canvas self-check + browser smoke
├── verify_engine_embed.py # engine-embed real-machine self-check
├── HANDOFF.md             # ★ sole authority handoff doc (why / baseline / todos / pitfalls — read first)
├── DocMind_BUILD.md       # frozen-build archive (release flow appends here, never creates new file)
└── 分区开发设计.md         # Region 2.0 architecture design
```

## 🚀 Quick Start

```bash
# 1. Create venv and install deps (Alibaba mirror recommended in CN)
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. Configure (optional; mock mode needs no key by default)
cp .env.example .env      # set LLM_PROVIDER=qwen and fill DASHSCOPE_API_KEY

# 3. Start the backend
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000

# 4. Frontend: use vite dev for development, or build once for the backend to serve
cd frontend && npm install && npm run build      # output -> ../web

# 5. Open
#   Workbench : http://127.0.0.1:8000/workbench
#   RAG page  : http://127.0.0.1:8000
```

> Use `127.0.0.1` not `localhost` (local IPv6 resolution may fail to connect); when the path contains a single-quote username, always wrap shell args in double quotes.

### Use a real model (Qwen example)

Two options:

- **In-page switch (recommended, no restart)**: open the RAG page, click "⚙ Model settings" top-right, pick provider, fill key, click "Save & switch" — takes effect immediately (key stays in memory only).
- **Edit `.env`**: `LLM_PROVIDER=qwen` / `EMBEDDING_PROVIDER=qwen` / `DASHSCOPE_API_KEY=...`, then restart.

```bash
# Call the API without the frontend
curl -X POST http://127.0.0.1:8000/api/chat -F "question=What file formats does DocMind support?"
```

## 🖥️ Desktop (native window, one-click launch)

`desktop.py` assembles the backend + frontend into a native desktop window (Windows uses Edge WebView2), double-click to run:

```bash
.venv\Scripts\python.exe -m pip install pywebview   # once
.venv\Scripts\python.exe desktop.py                 # or double-click run_desktop.bat
```

- **Engine embedding**: Godot / Unity / Unreal windows are embedded into the workbench by Win32 HWND rules (`desktop_bridge.py`).
  **Verified real-machine on Godot 4.7.2 + a real Win32 host** (`verify_engine_embed.py` 68/68): SetParent + style stripping, layout by client rect (or a front-end-specified "engine viewport" rectangle), host-resize follow, **real synthesized mouse/keyboard (SendInput) delivered to the engine and echoed back**, window restored exactly on detach, no orphan process/window after stop, parent/child DPI consistent (tested at 150% scaling on this machine).
  Embedding is **reversible**: `detach` restores the original parent, window style, and screen position — without saving these, a bare `SetParent(NULL)` leaves the window invisible (still carrying `WS_CHILD`).
  The playtester has an "embed into workbench" toggle: with it on, clicking "launch desktop window" drops the game画面 onto the engine viewport in the popup while the workbench UI stays usable; there are also "focus / detach / stop desktop window" controls; closing the popup or switching tabs auto-detaches. In browser mode the toggle is auto-disabled with a "needs desktop" hint.
  Not yet covered: real-machine data at 100% / 125% scaling (this display is 150%; the script prints DPI and asserts by actual coordinates).
- **Package as a standalone exe (onedir distribution)**: `docmind.spec` produces `dist\DocMind\DocMind.exe` in one command; ship the whole `dist\DocMind` folder, the target machine needs no Python. Full flow in [DocMind_BUILD.md](DocMind_BUILD.md) and `.trae/skills/docmind-frozen-release/SKILL.md`.
- **Packaged build capability boundary**: the bundled `builtin:py` does in-process syntax checks (exe behaves like source); but playtest auto-test, cProfile, and `python_exec` need a real Python environment — use them in the source `.venv`. Region git operations require Git on the target machine.

## 🧭 Harness (the agent runtime)

Turns "an agent that runs" into "an agent runtime you can **observe, orchestrate and regression-test**". All of it lives in the repo root — local-only, no external dependency:

| Capability | What it does | Entry point |
|---|---|---|
| **Per-turn trace + token ledger** | One JSONL record per turn: `turn_id / session_id / messages hash / tool-call sequence / tokens in-out / per-step latency / finish_reason / outcome / cost_cny`. **Metadata only** (never stores prompt/answer text); auto-rotates at 8 MB | Page **`/trace`** (screenshot `docs/screenshots/trace-ledger.png`); `GET /api/trace`, `/api/trace/summary`, `POST /api/trace/clear` |
| **Session isolation + persistence** | `Agent(session_id=)` isolates sessions (empty = in-memory, identical to old behaviour); history is persisted and old turns get **summarised** past a threshold; no more shared singleton leaking history across sessions | `session_id` on `/api/chat`; `GET /api/sessions`, `DELETE /api/sessions/{id}` |
| **LLM resilience** | Retry + exponential backoff (429 / 5xx / timeout / network are retryable, **4xx explicitly is not**) + unified `timeout`/`deadline`; an SSE client disconnect aborts the turn and is accounted for | `DOCMIND_LLM_*`, `DOCMIND_TURN_DEADLINE_S` |
| **Eval automation** | Rule-based golden-question scoring (`must_include / any_of / must_not_include / regex / must_call / action bounds / no_error`) + optional LLM judge + a **baseline regression gate** (pass→fail exits non-zero) | `agent_eval.py`, `gate.py` in the `agent-golden-eval` skill (wired into the frozen-release pipeline) |
| **Native function-calling** | OpenAI-style schemas generated from the tool registry; `tool_calls` are normalised into the text protocol so **every existing guardrail still applies** and event types are unchanged | `DOCMIND_TOOL_MODE=react\|native\|auto` |
| **Multi-agent orchestrator** | Task-graph DAG (validated, topologically waved, same-wave parallel) + **downstream tasks receive upstream conclusions** + **automatic re-planning on failure** (proposals `add / drop / replace` — only tasks that have **not run yet** may change) + result synthesis + **sub-agent execution traces fed back** to the re-planner for failure attribution | `orchestrate` tool, `POST /api/orchestrate` |
| **Cost fuse** | Prices per provider/model (overridable via `.docmind_pricing.json`; local providers are always 0) + global/session budgets; **refuses before the turn, charges after it** | `GET/POST /api/budget` |
| **Hot-pluggable hooks & skills** | `pre/post_tool`, `pre/post_turn` hooks from `.docmind/hooks/*.py` (a broken hook is isolated); skill catalog from `.docmind/skills/**/*.md` injected into the system prompt with bodies fetched on demand via `dev_use_skill` | `POST /api/hooks/reload`, `POST /api/skills/reload` |
| **Parallel tool batches** | Multiple **read-only** calls from one turn run concurrently (results re-ordered back into place); a batch containing any write/side-effecting tool falls back to sequential — **writes are never concurrent** | `DOCMIND_PARALLEL_TOOLS`, `DOCMIND_PARALLEL_MAX` |

**Three design stances**: ① **guardrail reuse** — native FC and parallel batches do not open a second execution path, so semantics never fork; ② **bounded** — traces, observations, history and batches are all truncated or summarised so context/prompts cannot explode; ③ **no over-reach** — the orchestrator may only edit not-yet-executed tasks (side effects are never rolled back), writes are never parallel, hook exceptions are swallowed.

```bash
# Inspect the per-turn ledger (what tools ran, tokens, cost, outcome)
http://127.0.0.1:8000/trace

# Run a task graph (independent tasks run in parallel; dependents wait for upstream conclusions)
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/orchestrate -H "Content-Type: application/json" \
  -d '{"tasks":[{"id":"a","role":"researcher","task":"find where X is implemented"},
                {"id":"b","role":"reviewer","task":"review the above","depends_on":["a"]}],"synth":true}'
```

> **Honest boundaries**: sub-agents do **not** share the parent context (it is passed via upstream conclusions); re-planning only edits the **not-yet-executed** plan and **never rolls back** executed tasks; traces are **bounded summaries** (full text lives in `.docmind_traces.jsonl`); hooks have **no sandbox** (plain `.py` loaded in-process); skills are prompt injection only, with no executable scripts.

## 🧪 Tests & Self-Checks

```bash
# Full unit tests (450 items; git cases actually run when MinGit is on PATH)
.venv\Scripts\python.exe -B -m unittest discover -s tests

# Scene canvas — backend self-check: in-process FastAPI + temp Godot project, real routes, no port
.venv\Scripts\python.exe verify_scene_canvas.py

# Engine embedding — real-machine self-check (real Godot + real Win32 host; briefly pops a window
# and moves the mouse back)
.venv\Scripts\python.exe verify_engine_embed.py

# Scene canvas — browser smoke: start the demo server in another terminal, then run with managed node
.venv\Scripts\python.exe verify_scene_canvas.py --serve 8011
node verify_scene_canvas_ui.mjs http://127.0.0.1:8011
```

The browser smoke produces `docs/screenshots/scene-canvas.png` and `docs/screenshots/runtime-timeline.png`.

---

## 🗺️ Scene Canvas

Renders `.tscn` as an operable node graph. **Why not "draw the node tree as nested containers"**: real scenes go 4–6 levels deep; nested containers express "ownership" fine but the high-frequency operation in scene work is **spatial relation** (position / transform) — containers can't express "where these things should be drawn". So the canvas uses **flat nodes + four edge types**:

| Edge | Meaning | Style |
|---|---|---|
| `hierarchy` | parent-child (top → bottom) | gray solid |
| `script` | node ↔ script file | purple dashed |
| `instance` | node ↔ instantiated sub-scene | cyan dotted |
| `reference` | node ↔ texture / material resource | gray (hidden by default, toggleable) |

Two layouts: **hierarchy layout** (DFS pre-order horizontal tree) and **spatial layout** (drop by scene coordinates directly; drag writes back `position`). Double-click a file card to open that script / scene in the editor; the right inspector lists all properties.

**Safety & reversibility** (this is the real work, not just "looks like a graph"):

- All writes go through a double sandbox (`code_root` containment + region / contract protection); property values containing newlines are rejected (injection guard);
- Writes use a "line-block model" and **self-check after writing** (parent exists / no sibling name clash / parent block before child block); on any failure it **rolls back to the original**;
- Every operation returns an `undo` **shaped identically to the API request body** — the frontend returns it as-is to undo — with regression cases guaranteeing **byte-for-byte file restore after undo** (including blank-line position and property order).

## ⏱️ Runtime Timeline

While the game runs, events are written to `.docmind_runtime.jsonl` (the engine prints `DOCMIND_EVENT {json}` by convention and it gets captured). The timeline draws these as multi-track axes: filter by type / source / session / keyword, time zoom, numeric metric curves, JSON export, click-event-to-jump-to-code-line. Clearing uses a **byte cursor** reset, never truncating a log the engine is still writing.

---

## 🎮 Asset filtering tool (`search_assets`)

For "making a game / need art assets": a tool that lets the Agent filter assets on demand.

**Why not just crawl an asset-library API?** Kenney / OpenGameArt / itch.io either lack a stable public API or are behind Cloudflare (crawling is fragile and impolite). Industry practice (e.g. Arcane Assets MCP) is likewise a maintained local/remote asset-manifest JSON that the Agent searches over. So here a hand-curated, extensible `assets_catalog.json` (currently 26 entries: Kenney CC0 series + OpenGameArt samples) plus `search_assets`'s keyword expansion and scoring does "filter by question".

- **Capability**: filter by keyword (Chinese synonym expansion, e.g. "角色/精灵 → character/sprite"), type (`2d-sprites` / `tilesets` / `ui` / `audio` / `fonts` / `3d`), license (`CC0` / `CC-BY` / `CC-BY-SA-3.0`).
- **Extend**: append entries to `assets_catalog.json`, no tool change needed; real integration can swap `search_assets` in `tools.py` for a remote index / store API call.
- **Demo**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=Find me a CC0 character sprite asset"
  ```
  In mock mode the LLM auto-detects the "asset" intent and prefers `search_assets`.

## 🔧 Code execution (`python_exec`)

Lets the Agent actually "act, not just talk": turn a natural-language intent into executable Python run in a restricted subprocess, getting deterministic computation or a data-transform artifact.

- **Use**: numeric/statistical compute, light data processing, text transform, generate structured data for visualization / downstream tools. vs `calculate` (arithmetic only), `python_exec` runs arbitrary Python.
- **Robustness**: input comes from the LLM, which often wraps code in ```` ```python ```` fences or adds a "python" hint line — the tool regex-cleans that noise first, keeps only executable code; if empty after cleaning it returns "no valid code provided" instead of executing triple quotes. Runs via `subprocess.run([sys.executable, "-c", code], ...)` under current user rights, 12s timeout (infinite loop returns a timeout message, not a hang), stdout/stderr truncated to 1500 chars.
- **Demo**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=Compute the sum of squares from 1 to 100 in code"
  ```
  The Agent calls `python_exec` to run `sum(i*i for i in range(1,101))`, returns `338350`.

## 🌐 Web research (`web_search` / `web_fetch` / `web_research`)

When the knowledge base is thin, or the question is time-sensitive / external, the Agent autonomously goes online for evidence instead of just "I don't know".

- **`web_search`**: scrapes DuckDuckGo HTML results (`urllib.request` + regex for title/snippet/redirect link, restoring the real `uddg=` link) — **no API key**. Degrades clearly if offline or DDG changed; the Agent then uses the KB or says so, never fakes success. Swappable to SerpAPI / Bing by editing one `web_search` function in `tools.py`.
- **`web_fetch`**: reads public HTML body text for a given URL (used by `web_research` and citable in answers).
- **`web_research`**: search + fetch up to 3 sources, clean titles / final URLs / body, degrade on non-HTML / network failure; source URLs in the answer are clickable.
- **Demo**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=Look up the max video duration MiniMax H3 supports"
  ```

> Not yet polished: B-site dedicated search / subtitle extraction, GitHub API search, result caching, source-credibility scoring, multi-source conflict detection, login / CAPTCHA / paywall handling. Current ability covers ordinary tutorials, GitHub material, and engine-doc lookup — not claimed to reach Perplexity / Claude Research level.

## 🎬 Video prompt generation (`gen_video_prompt`)

Turns a Chinese creative line into a MiniMax H3 three-section video prompt you can paste straight into the ComfyUI `MiniMaxH3ImageToVideo` node — closing "text idea → generatable video".

- **Why deterministic assembly + LLM polish**: early direct-writing let weak models (e.g. 7B) read "three-section structure" as "three-act structure" and emit wrong format. Now the **three-section skeleton (integrated_multimodal_description / overall_soundscape / non_diegetic_music) is assembled deterministically** so it's always compliant; the LLM only polishes the Shot 1 English storyboard, falling back to a deterministic template on failure. Usable regardless of model strength.
- **Sound design auto-fits the scene**: keywords (rain / night / wind / sea / city / forest / snow…) auto-fill diegetic sound, plus a non-diegetic music template, with constraint notes (no negative words, FPS=24, 4–15s).
- **Demo**:
  ```bash
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=Give me a rainy-night-city video prompt"
  ```
  The Agent calls `gen_video_prompt`, output carries the three sections, diegetic sound auto-filled with "steady rain" + "distant night traffic" + "low city hum".

## 💻 Code Q&A mode (`search_code` / `read_file` / `grep`)

Upgrades the Agent from "reads docs only" to "also understands your project code": index a source directory once and it can find functions/classes, open specific files, locate symbols or errors by regex — the key to stopping AI "code pile-up / hallucination" on big projects: make the AI retrieve the existing structure first, then edit precisely, instead of stuffing one file.

- **How**: hand DocMind a code root to index once (all later code Q&A uses this index):
  ```bash
  curl -X POST http://127.0.0.1:8000/api/ingest_code -F "root=C:/path/to/your/game-project"
  ```
  Or fix `CODE_ROOT` in `.env` for auto-availability on start.
- **Three tools**:
  - `search_code(query)`: search indexed source/config for relevant functions, classes, config snippets (by symbol name + path) — "where is X implemented / what does function Y do".
  - `read_file(path)`: read a file in the code base (path relative to `code_root`), for full implementation.
  - `grep(pattern)`: regex search text/symbols in the code base, returns `file:line: content`, for locating a variable / an error.
- **Design points**:
  - **Code-aware chunking**: prose splits by paragraph; code must split by "file + function/class" — otherwise a function is cut in half, two unrelated functions glued — retrieval quality collapses. Python uses `ast` for exact `def/class` (incl. methods) start/end lines and names; other languages use regex heuristics for definition lines. Oversized slices fall back to line-based split.
  - **Separate collection**: code goes to its own Chroma collection (`docmind_code`), not polluting the doc collection; `search_knowledge` handles docs, `search_code` handles code.
  - **Path sandbox**: `read_file` / `grep` only allow access inside `code_root`; out-of-bounds is rejected, never leaking other local files.
  - **Skip noise**: indexing auto-skips `.git` / `node_modules` / `__pycache__` / `.venv` etc., and oversized files (>500KB), avoiding stuffing build artifacts / zips into the index.
- **Demo**:
  ```bash
  # after indexing, ask the Agent:
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=Which function implements damage calculation? Show me that code"
  # Agent first search_code locates DamageCalculator.compute, then read_file opens the file.
  ```
- **Note**: code Q&A relies on semantic retrieval; use a real embedding (`.env` `EMBEDDING_PROVIDER=qwen`); offline `local` mode gives random placeholder vectors — only for pipeline smoke, results have no semantic meaning. Switching embedding dimension only rebuilds the doc collection; the code collection can stay.
