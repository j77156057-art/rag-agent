# DocMind 项目交接清单（给接手 AI）

### 2026-09-18 当前交接检查点（`5655f85`）

- 当前分支：`main`；本地 `HEAD` 与 `origin/main` 已同步。最近一次功能提交为 `5655f85 feat: add Godot hot reload`。
- 当前工作区只存在用户 Godot 工程/导入产物：`.godot/`、`addons/`、`project.godot`、`export_presets.cfg`、`docs/screenshots/*.png.import`。这些文件未纳入本次提交，禁止使用 `git add -A`。
- 已完成并已提交：运行时状态按项目隔离、工作台往返状态与竞态修复、场景画布与真实时间线、Godot 原生嵌入、Godot 快速热重载、GPU 软件侧协调、ComfyUI Z-Image/H3 链路、Agent 路由/权限/连接器、Unreal bridge 协议与受控写入边界。
- Godot 热重载的准确口径：Web 试玩使用进程内 `reload_current_scene()`；桌面原生运行实例采用“保存启动参数/嵌入矩形 → 停止旧进程 → 启动新进程 → 恢复嵌入”的快速进程重启，游戏内存状态会重置。它不是无状态丢失的进程内脚本替换。
- 本次接手优先级：
  1. 在当前机器启动真实 Godot 工程，验收原生热重载：修改 `.gd`/`.tscn` 后点击热重载，确认 PID 变化、嵌入矩形恢复、GPU 租约只保留新进程。
  2. 若要做“保存文件即自动刷新”，先设计文件监听与确认提示；不要默认自动重启，避免调试状态被静默清空。
  3. Unreal 深度适配仍缺真实 Unreal Editor 端到端查询/受控写回；Unity 编辑器插件、PlayMode 控制和 `.meta` 成对维护也仍缺真实编辑器验收。
  4. 物理多 GPU `multi` 模式和跨进程 CUDA UUID 隔离仍缺至少两张 NVIDIA GPU 的实机验收；单卡负对照已完成，不能把它写成多卡已验证。
  5. 桌面发布仍缺在用户桌面实际执行的独立 EXE 启动、覆盖升级和卸载验证；安装器已生成，但当前自动执行策略曾阻止本机自动启动验证。
  6. 项目状态隔离已落地，但全局 Ollama/联网配置、GPU 协调器和 Chroma 仍是实例级资源；需要长时间多项目并发压测后再收口。
- 交接验证基线：Python 全量回归最近记录为 `939 项通过、1 项跳过`；前端 `typecheck` 与 `npm run build` 通过。继续改动后必须重新运行受影响专项和全量测试，并把结果追加到第 4 节时间线。

### 2026-09-18 项目切换与流式竞态修复

- 项目上下文：任务 ID、分区和允许路径改为按 `project_id` 保存；切换项目会重建任务面板，保存与选区 AI 不会读取旧项目的全局任务范围。
- 流式回答：项目切换/离开页面会中止 SSE；迟到事件被丢弃；已收到的部分回答会恢复为“已中断”提示，绝不自动重放工具。
- 编辑器：延迟磁盘读取期间若用户已输入、标签已关闭或项目已切换，不再覆盖草稿。
- 画布与类型：修复 Vue Flow store 类型、场景文件卡双击打开、节点聚焦、资源类别映射和任务历史 API 类型；新增 `npm run typecheck`。
- 专项验证：`verify_workbench_races.mjs` 5 项通过（延迟读取、项目切换中止流、任务范围隔离、首页并发发送、错误检查）；工作台回归 9 组、场景画布 27/27 继续通过；`npm run typecheck` 与 `npm run build` 通过。
- 依赖：前端增加 `vue-tsc` 与 `@types/node`，锁文件已更新。
- 仍有边界：复制标签页可能复制 sessionStorage 会话 ID；服务端已执行的工具无法回滚；真实引擎、GPU 和 ComfyUI 实机状态仍按 §5 单独验收。

### 2026-09-18 工作台回归修复与桌面更新

- 修复问答页 ↔ 工作台往返丢失：问答页与工作台从后端恢复当前会话历史；未发送聊天草稿、CodeMirror 未保存草稿按项目保存在 sessionStorage；项目切换会清理旧标签并重新回灌。
- 修复文件树新建入口：选中目录时在该目录创建，选中文件时在其父目录创建；分区新增表单加入项目内相对路径校验，并提供 AI 规划入口。
- AI 设置、任务与生成、GPU 面板改为 body Teleport，窄窗口仍可操作；后端断连不再自动伪装成演示项目，演示仅在 URL `?demo=1` 显式启用。
- 运行时时间线不再为无时间戳事件编造时间；仅有真实时间戳的事件绘图，无时间事件单独提示并原样导出。切换项目会清理运行台状态。
- 新增 `verify_workbench_regressions.mjs`：在 `verify_scene_canvas.py --serve 8011` 的临时项目上完成 9 组浏览器回归（往返、草稿、面板、目录创建、分区校验、时间线、空态、错误无演示数据）全部通过。
- 验证：`npm run build` 通过；`.venv\Scripts\python.exe -m unittest discover -s tests -q` **935 项运行、0 失败、1 跳过**；本轮以 `dist\regression-20260918\DocMind` 生成独立新包，EXE SHA-256 `1EFE15A27EB3208FB72AF302603DBC28CCC2B9AD105608255E860A36F9B71F98`，server-only 冷启动 `/api/health`、`/workbench` 与哈希资源均返回 200。画布浏览器专项 `verify_scene_canvas_ui.mjs` **27/27**。
- 桌面安装 `D:\WorkBuddy\DocMind` 已复制更新包内文件（1060 个），未删除/覆盖非包内用户状态文件；覆盖前文件备份 `D:\Temp\docmind-before-regression-20260918-034052`。安装目录 EXE 与新包 SHA 一致。自动审批策略拒绝后续从安装目录启动的复验命令（仅提示 blocked by policy），故原生桌面启动/窗口嵌入复验未宣称通过。
- 本轮数据恢复边界：sessionStorage 草稿支持同标签页跳转/刷新，不承诺关闭标签后恢复；未完成 SSE 回答跨页面续传仍需后续实现。
- 未验证：真实 Godot/Unity/Unreal 编辑器通信、真实 CUDA 多进程曲线、ComfyUI 实机链路；这些需硬件/外部进程条件，不能由本地回归替代。


> **更新时间**：2026-09-18（引擎嵌入硬化 + Web 试玩多项目路由 + 运行游戏面板三处修复完成并入库，见 §4 `116f6a5`）｜ **基线提交**：`eb8516a`（第 18 次冻结构建）
> **全量测试**：**450 项全部通过**（441 + 9 新增轨迹回传用例）｜ **场景画布自检**：`verify_scene_canvas.py` 54/54
> **引擎嵌入实机自检**：`verify_engine_embed.py` **68/68**（真 Godot 4.7.2 + 真 Win32 宿主，含真实合成键鼠与 UI 调用路径）｜ **浏览器冒烟**：`verify_scene_canvas_ui.mjs` **27/27** ｜ **前端构建**：`npm run build` 通过
> 本文是项目唯一权威交接文档，取代并删除了旧版 `HANDOFF.md`、`AI_BRIEF.md`、`DEV_WORKBENCH_AUDIT.md`、`HANDOFF_ENGINE_EMBEDDING.md`、`HANDOFF_REMAINING_WORK.md`（旧 HANDOFF.md 由本同名文件接管）。
> **铁律：规划项一律写在第 5 节，不得描述为已完成；做完一项就把它移到第 4 节时间线并注明提交号。**

---

## 1. 项目发起原因（为什么有这个项目）

游戏/软件开发者用 AI 写代码时，最大的问题是**幻觉与代码堆叠**：AI 凭印象编造"伤害计算在 player.py"，并把数值、UI、行为逻辑混写在少数文件里，越改越乱、一改就崩、无法回滚。

DocMind 的应对分两层，也是项目的两个演进阶段：

1. **本地 RAG + ReAct Agent（2026-09-09 基线）**：让 AI 先**检索/定位真实代码与文档**（文件+行号+证据）再回答，而不是直接编代码。零 API Key 可跑（mock + 本地模型），FastAPI + SSE 服务化。
2. **游戏开发 AI 工作台（2026-09-11 起）**：在 RAG 之上加「仓库级工作流」——任务分区、每区独立 Git、契约方向校验、变更集回滚、选区 AI、符号/关系图、引擎嵌入、GPU 协调。目标形态：**一个本地单人的、面向游戏/Mod 工程的轻量研发脚手架**（不是通用 ALM/DevOps 平台，不做多用户/数据库/鉴权）。

当前处于阶段 2：P0（IDE 工作区）、P1（符号/关系图、选区 AI、Git 治理）、引擎嵌入与 MCP 桥接均已落地；**场景画布与 Godot 嵌入实机闭环是下一个主战场**（见 §5）。

---

## 2. 当前基线（2026-09-14）

- **后端**：Python + FastAPI（`api.py`，SSE），入口 `api:app`；Chroma 双集合（文档 `docmind` / 代码 `docmind_code`）。
- **前端两个页面**（都不是 SPA 路由，是独立 HTML 入口）：

  | 页面 | 路径 | 来源 | 定位 |
  |---|---|---|---|
  | **RAG 问答页** | `/` | 手写 `web/index.html`（不经 Vite 构建） | **默认入口**：问答 / 上传 / 索引代码目录 / 模型设置 |
  | **开发工作台** | `/workbench` | Vite 构建 `frontend/workbench.html` → `web/workbench.html` | 第二入口：CodeMirror、文件树、符号/关系图、分区与 Git、引擎面板、**场景画布**、**运行时时间线**、ChatDock |

  **两页必须能互跳**：问答页顶栏有「开发工作台 →」，工作台顶栏有「问答」回链（`tests/test_desktop_entry.py` 钉住）。
  桌面壳默认打开问答页；入口可用 `DOCMIND_HOME` 覆盖（例如 `/workbench`）。
  桌面壳历史包袱：它曾经硬编码打开 `/workbench/`，而浏览器回退路径打开 `/`，两条路进不同页面，用户看懵过。
  工作台的重组件用 `defineAsyncComponent` 异步分块，首屏 JS 体积不受影响（工作台 172KB / gzip 63KB，第 19 次构建实测）。
- **桌面分发**：PyInstaller **onedir** 控制台模式 `dist/DocMind/DocMind.exe`（当前**第 21 次**冻结构建，2026-09-16 19:22；本轮为**设计评审驱动的缺陷修复**——导入期副作用消除 / 运行时状态根可注入 + 测试隔离 / 前后端失败体契约 / 逐请求开关注入 / GPU 状态文件拆分 / calculate 指数与 Prompt 预算加固；exe 20,536,866 字节，SHA-256 `f67eac87…`）；随包 MinGit。
- **引擎嵌入（P0-1 已实机闭环，且 UI 可用）**：Godot 4.7.2（`D://Tools//Godot//Godot_v4.7.2-stable_win64.exe`）+ 真 Win32 宿主窗口下实测通过——
  置父/样式摘除、按客户区（或前端指定矩形）对齐、宿主 resize 跟随、**真实合成键鼠（SendInput）送达引擎并回显**、
  解除嵌入后窗口原样还原、停止后无孤儿进程/窗口、父子 DPI 一致（本机 **150% 缩放 = 144 DPI** 实测）。
  UI 侧试玩器有「嵌入工作台」开关：勾上后点「桌面窗口启动」，游戏画面直接落在弹窗里那块引擎视窗上，
  界面照常可用；另有「聚焦 / 解除嵌入 / 停止桌面窗口」。关弹窗或切走 tab 会自动解除嵌入（视窗元素没了，
  继续嵌着只会让引擎画到别处）。浏览器模式下开关自动禁用并提示需要桌面端。
- **LLM/Embedding**：mock / qwen / deepseek / ollama / llamacpp 多 Provider，页面内免重启切换；本机 Ollama(`11434`, bge-m3) 与 llama.cpp(`8080`, Qwen 35B) 免 Key；622fdbc 新增 native embedding。
- **验证基线**：后端 `unittest discover` **707/707 通过**（skipped=1；含 `test_gpu_coordinator.py`、`test_agent_trace.py`、`test_llm_resilience.py`、`test_agent_eval.py`、`test_native_tools.py`、`test_subagent_plan.py`、`test_hooks_skills.py`、`test_pricing.py`、`test_parallel.py`、`test_orchestrator.py` 等；本轮新增 10 个测试文件：`test_failure_markers.py` / `test_web_search_markers.py` / `test_calculate.py` / `test_import_side_effects.py` / `test_state_root_isolation.py` / `test_state_isolation_hardening.py` / `test_persisted_state_lifespan.py` / `test_batch_d_agent.py` / `test_async_llm_construction.py` / `test_cloud_agent_registration.py`）；
  `verify_scene_canvas.py` 走真实 HTTP 路由 **54/54**（含"每个 op 的 undo 逐字节还原"）；
  `verify_engine_embed.py` 真 Godot + 真 Win32 宿主 **68/68**；
  `verify_scene_canvas_ui.mjs` 真浏览器 **27/27**（含"空间布局落点与场景坐标严格成比例"）；前端 build 通过。

---

## 3. 架构与模块地图

### 3.1 后端（仓库根的 Python 模块）

| 模块 | 职责 |
|---|---|
| `api.py`（78KB） | HTTP/SSE 总入口：chat、ingest、工作台 fs、regions、engine/*、desktop/host、selection_ai、MCP、GPU 等全部路由 |
| `agent.py` | ReAct 循环（Thought→Action→Observation）、反思重试、弱模型 terminal 工具、代码优先路由（c543047）；**`run()` 现为埋点外壳**：预算熔断 + pre/post_turn 钩子 + trace + 会话落盘/摘要 + 按 provider 计价；`_run()` 接统一 deadline；`Agent(llm, session_id=, tool_mode=, plan_mode=, depth=, tool_allowlist=)` 支持会话隔离、**原生 function-calling 通道**（`native`/`auto`，tool_calls 归一进文本协议复用全部护栏）、**plan 模式**（上抛 `plan` 事件）、**子代理委派**（`delegate` → 受限子代理，各自持独立 `LLMClient`）、**并行批次**（一轮多条只读 tool_call 用 `ThreadPoolExecutor` 并发执行、结果保序回填；批内含写/副作用工具自动退回顺序）、**多代理编排**（`orchestrate` 工具 / `Agent.orchestrate()` 走任务图，见 `orchestrator.py`）、**`last_turn_record`**（每回合的 trace 记录留档，用于子代理轨迹/成本回传） |
| `agent_trace.py`（新） | **逐轮 trace + token 账本**：每回合一条 JSONL（turn_id / session_id / messages 哈希 / 工具序列 / tokens in-out / 各步延迟 / finish_reason / 结局），**只记元数据不记正文**，超上限轮转；页面 `GET /trace`、数据 `GET /api/trace` |
| `sessions.py`（新） | **会话隔离与持久化**：按 session_id 落盘 history，超阈值把早期轮次压成摘要（只留最近 KEEP 轮原文）；`GET /api/sessions`、`DELETE /api/sessions/{id}` |
| `agent_eval.py`（新） | **黄金题自动打分 + 回归门**：规则打分（must_include / any_of / must_not_include / regex / must_call / 动作边界 / no_error）+ 可选 LLM-judge；与 baseline 对比 pass→fail 或通过率下滑即退出码 1 |
| `pricing.py`（新） | **按 provider 计价 + 预算熔断**：单价表（`<BASE_DIR>/.docmind_pricing.json` 可覆盖，本地 provider 恒 0）+ 全局/会话累计花费落 `.docmind_budget.json`；回合前 `check()` 拒绝超限、回合后 `charge()`；`GET/POST /api/budget` |
| `hooks.py`（新） | **工具/回合钩子热插拔**：`.docmind/hooks/*.py` 的 `pre_tool` / `post_tool` / `pre_turn` / `post_turn`；异常隔离、坏钩子不影响主流程；`GET /api/hooks`、`POST /api/hooks/reload` |
| `skills.py`（新） | **技能热插拔**：扫 `.docmind/skills/**/*.md`（frontmatter），只把目录注入系统提示、正文由 `dev_use_skill` 按需取；`GET /api/skills`、`POST /api/skills/reload` |
| `orchestrator.py`（新） | **多代理编排器**（纯调度，不依赖 Agent）：任务图校验（id 重复/依赖缺失/循环依赖/超量）、迭代调度（每轮挑依赖已落定者并行）、**下游注入上游结论**、**回溯式重规划**（`replanner(failed, results, attempt)` 可返回 `{add, drop, replace}`：追加补救任务、取消未执行任务、原地改写未执行任务；**只能动尚未执行的任务**，已执行者不可删改——本系统不回滚已产生的副作用；非法项逐条忽略；受 `max_replans`/总量约束；每轮改动记入 `revisions` 审计）、上游失败/被取消阻断下游（`optional` 除外）、可选 `synth_runner` 合成并标注冲突；**结果里带子代理执行轨迹**（`trace`：逐步 action+观察片段 / outcome / 用量），失败任务的轨迹会在 `format_report` 里显示一行摘要；调用方通过 `runner(task, context)` 回调提供"怎么跑子任务"，故可完全离线单测 |
| `tools.py`（105KB） | 工具注册表 `TOOLS`：9 基础 + 受控写（apply_edit/create_file/run_command）+ 11 个 dev_* 分区工具 + 研判分区工具 |
| `regions.py`（47KB） | 分区 2.0：DEFAULT_REGIONS 8 区、regions.json 覆盖、契约校验（DAG 无环/导出存在）、init/scaffold/fill_exports、变更集与回滚 |
| `game_workbench.py`（60KB） | Godot/Unity/Unreal catalog、引擎启停与自动嵌入、运行时事件、任务/资产/bug 工作流；**P2-1 ComfyUI 租约覆盖完整生成周期**：`comfy_queue` 用一次性提交 owner 拿租约（带 Ollama 驱逐钩子）→ 成功后 `reown` 为 `comfyui:{prompt_id}`（TTL 600s `DOCMIND_COMFY_JOB_TTL` 兜底），`comfy_history` 见终态（outputs/completed/error/failed）幂等释放，`comfy_cancel` 调 ComfyUI `/interrupt` 后释放。提交默认要求 `DOCMIND_COMFY_MIN_FREE_MB=1024` 显存余量：余量不足先触发 Ollama 卸载钩子，腾不出则直接拒绝不排队 |
| `workbench_fs.py`（56KB） | 沙箱文件树、读写、git 状态/历史/回滚/恢复、符号地图、关系图（继承/挂载/调用边） |
| `unity_graph.py`（新） | **P1-2 Unity GUID 引用图**：扫 `.meta` 建 guid 索引（含文件夹/孤儿 meta/冲突检测），解析 `.unity/.prefab/.asset/.mat` 等序列化文本的 guid 引用（大小写归一、同源聚合 ×N），产出与关系图同构的 nodes/edges；缺失 guid 聚合为外部节点（Packages 包资源与真断裂引用同口径，不夸大为错误） |
| `symbols.py`（40KB） | 多语言符号抽取（Python ast / GDScript / Java 等），代码感知分块 |
| `ingest.py`（22KB） | 文档/代码摄取与递归分块；跳过 .chroma/dist/.venv/_archived_builds/uploads 等 |
| `mcp_client.py`（20KB，新） | MCP（Model Context Protocol）桥接客户端，168 行测试 |
| `web_export.py`（18KB，新） | 游戏 Web 导出与本地预览（Web player） |
| `scene_runtime.py`（1198 行） | **场景画布内核**：.tscn 行块解析 → 图模型（节点/外部引用/四类边/几何量）＋受控编辑（add/delete/rename/reparent/duplicate/set_props/move/restore，写完自检失败自动回滚，每个 op 回传可原样回放的 undo）＋运行时事件检索（筛选/统计/会话切分/游标清空） |
| `desktop_bridge.py`（6.4KB） | Win32：find_host/find_window/embed/resize/**focus（新增）** |
| `gpu_coordinator.py`（重写+收尾） | **P2-1 GPU 协调**：三模式 `DOCMIND_GPU_MODE=serial（默认，全机 FIFO 锁）/parallel（不协调，仅采样）/multi（每卡独立租约）`；`acquire_lease()` 返回 `{ok,gpu,reason,evicted,reentrant}`，显存门槛不足直接拒绝不排队，**严格 FIFO（multi 下队首要的卡忙时后续者不得用别的空闲卡插队）**；`reown/force_release(owner)/cancel_wait/queue_position/register_hook/note_activity/configure/recent_samples`；后台守护线程做 5s 采样环（240 点≈20 分钟）、TTL 回收、Ollama 空闲卸载（`DOCMIND_OLLAMA_IDLE_UNLOAD`，60s 冷却，有非 ollama 租约时不卸）；探测可 `set_gpu_probe` 注入。**驱逐钩子只在"显存门槛拒绝"（无租约的外部驻留）时跑，活跃持有者排队不白卸**；真实 nvidia-smi 探测 0.5s TTL 缓存（`DOCMIND_GPU_PROBE_CACHE_TTL`，注入探测不缓存）。**multi 只返回卡号，CUDA_VISIBLE_DEVICES 隔离必须由调用方在子进程启动层做，本机单卡从未实测隔离，不得宣称** |
| `desktop.py` | 控制台启动器、单实例保护、pywebview 窗口与宿主 HWND 注册 |
| `llm.py` / `embeddings.py` / `vectorstore.py` / `config.py` | 多 Provider LLM、嵌入、Chroma 封装、配置与 frozen 资源定位 |
| `engine_adapters.py` | 引擎适配薄封装；`verify*.py` 是独立校验脚本 |
| `docmind.spec` / `make_lnk_pure.py` / `make_shortcut.py` | PyInstaller 配置、纯 Python 手写 .lnk、快捷方式 |

### 3.2 前端 `frontend/src/workbench/`

- `App.vue` 总装；`api.ts` 类型化接口（注意：verify_contracts 等业务"失败"是 HTTP 200 + `{ok:false}`，须独立 fetch，不能走抛错的通用 request）；`composables/workbench.ts` 共享状态。
- 组件：`FileTree.vue`/`FileTreeNode.vue`（树+dirty/tracked）、CodeMirror 编辑器、`SelectionAiPanel.vue`（P2 选区 AI + diff 接受）、`GitHistoryDialog.vue`（P3 历史/回滚）、`RewriteDiffDialog.vue`、`RegionMapDialog.vue`（分区 DAG 治理）、`TaskEnginePanel.vue`、`ChatDock.vue`（markdown 渲染在 `markdown.ts`）。
- **场景画布与时间线（2026-09-14）**：`SceneRuntimePanel.vue` 是弹窗外壳（试玩 / 场景画布 / 运行时时间线 三 tab）；
  `SceneCanvas.vue`（Vue Flow 画布主控：布局、编辑、撤销栈、检查器）+ `SceneNodeCard.vue`（场景节点卡）+ `SceneFileCard.vue`（脚本/实例化/资源文件卡）；
  `RuntimeTimeline.vue`（多轨道时间线）。`api.ts` 新增 `sceneApi`（走 `rawJson` 非抛错通道，因为 `ok:false` 里带着 stale/rolled_back 分支信息）。
- **`UnityGraph.vue`（P1-2）**：零依赖力导向 Unity GUID 引用图（顶栏「Unity 图」）。
- **`GpuPanel.vue`（P2-1）**：顶栏 GPU 按钮（实时显占用 GB，持租约时变琥珀色）+ 自管 5s 轮询浮层——每卡显存条/利用率/温度、20 分钟占用 SVG 迷你曲线、持有者一键回收、排队项一键取消、Ollama 空闲卸载秒数设置（持久化）；`api.ts` 新增 `gpuApi` 与 `comfyApi.cancel`（TaskEnginePanel 新增「取消生成」按钮，调 `/interrupt` 并释放作业租约）。

### 3.3 Skill（`.trae/skills/`，接手必读）

`docmind-frozen-release`（冻结发布标准流程，**强制编辑 DocMind_BUILD.md 不新建文件**）、`engine-project-setup`、`engine-adapters`、`desktop-engine-embedding`（HWND 规范）、`agent-golden-eval`（评测，发布时不处理）。

---

## 4. 已完成里程碑时间线

| 日期 | 里程碑（提交） |
|---|---|
| 2026-09-09 | 基线：RAG Agent + T2–T4 受控写工具 + 项目规则注入；分区开发 2.0 落地（8 区/契约/变更集）；Agent 智能研判分区；onedir 桌面打包 |
| 2026-09-10 | T5 真实工程验证：批量代码摄取、工具输入消毒；编辑确认模式、run_command 护栏、代码重置端点；增强提示词（llamacpp） |
| 2026-09-11 | P0 工作台 IDE 任务 1–4 + git plumbing；Godot 文本资产索引（.gd/.tscn/.tres…）；随包 MinGit；code_root 持久化+vendor 分包；**P1 符号语义地图 → 关系图（继承/挂载边）→ 调用边（高置信、字符串注释掩码）**；P2 选区 AI（解释/Review/提问走 Agent、改写走直连快通道+LCS diff 接受）；P3 Git 历史/回滚+分区 DAG 治理；代码审查 11 项修复；第 4–11 次冻结构建 |
| 2026-09-12 | 分区一键创建/补齐导出桩（scaffold/fill_exports，15 例新测）；Java 符号抽取；第 12–14 次冻结构建 |
| 2026-09-13 | `c543047` Agent 代码优先路由+健壮动作解析+证据护栏；**`622fdbc` MCP bridge、Web player/导出、GPU lease 队列、native embedding、desktop_bridge focus、场景面板大改、ChatDock**（+4638 行，6 个新测试文件）；Vue Flow 区域画布 spike 验证通过（`b8e869c` 提交，已随 P0-2 转正后移除，见 §6） |
| 2026-09-14 | **P0-1 Godot HWND 嵌入实机闭环**（Godot 4.7.2 + 真 Win32 宿主）：`desktop_bridge.py` 加固为可逆嵌入 + 客户区/矩形两种尺寸模式 + DPI 感知 + 可靠的跨线程 focus；`engine_*` 增加嵌入状态机与 detach/focus/resize/place/stop_all（停止先解除父子再杀进程树，防孤儿）；`desktop.py` 接 resized/shown/closing 事件并在启动前声明 DPI 感知；新增 `verify_engine_embed.py`（60 项实机断言，含真实合成键鼠回显）。修 3 个真 bug：嵌入后无法二次 embed、`windows_of_pids` 永远返回空、resize 用外框尺寸裁画面 |
| 2026-09-14 | **`809a3b9` P0-2 场景画布转正 + P1-1 运行时时间线**：`scene_runtime.py` 重写为行块解析/图模型/受控编辑（+1035 行）；新增 `/api/scene/graph`、`/api/scene/op`、`/api/runtime/sessions`、`/api/runtime/clear`，`/api/runtime/events` 支持筛选；前端新增 `SceneCanvas.vue`/`SceneNodeCard.vue`/`SceneFileCard.vue`/`RuntimeTimeline.vue` 与 `sceneApi`；移除 spike 入口与 `src/spike/`；修 gpu 队列抖动用例；补 `/favicon.ico`；构建前清理 `web/assets`。测试 202/202、后端自检 50/50、浏览器冒烟 23/23 |
| 2026-09-14 | **`981b116` 接手校准**：基线对齐 `eeb73ab` 后的 10 个外部提交；全量测试核对、HANDOFF 数字/状态修正（未推送） |
| 2026-09-14 | **`6e9b95b` P1-2 Unity GUID 引用图（纯文本静态分析，不启动编辑器）**：新增 `unity_graph.py` 与 `GET /api/unity/guid-graph`；`.meta` 建 GUID 索引（类型分类、文件夹/孤儿 meta、GUID 冲突检测），`.unity/.prefab/.asset/.mat/.controller/.anim` 等序列化文本按行提取引用（大小写归一、同对聚合计数、首行号），Library/Temp 等跳过，2 万文件上限与 skipped 统计；产出与关系图同构 nodes/edges，工程内解析不到的 guid 聚合成"缺失/外部"节点（默认折叠，口径包含 Packages 包资源，不夸大为断裂）。前端新增 `UnityGraph.vue`（零依赖力导向、类型 chips 过滤、搜索邻接高亮、缺失红虚线、节点详情侧栏含出/入边清单与 GUID 复制、双击/按钮打开文件），顶栏「Unity 图」入口。新增 `tests/test_unity_graph.py` 4 例（223 → **227** 全绿）；合成 Unity 工程浏览器冒烟全过（默认图/缺失展开/侧栏出入边/搜索/类型过滤），`npm run build` 通过。**仍待**（本机未装 Unity 编辑器）：Editor HTTP 插件、Console/PlayMode、资产改动同步 `.meta` |
| 2026-09-14 | **`6e9b95b` P2-1 GPU 协调补完（与 P1-2 同提交）**：`gpu_coordinator.py` 重写为 serial/parallel/multi 三模式 + `acquire_lease/reown/cancel_wait/force_release(owner)/register_hook/note_activity/configure/recent_samples`，严格 FIFO（multi 下也不许跨空闲卡插队），显存门槛直接拒绝不排队，驱逐钩子锁外只跑一次；后台守护线程（FastAPI lifespan 启停）做 5s 采样环（240 点）、TTL 回收、Ollama 空闲卸载（`api.py` 钩子对 `/api/ps` 驻留模型逐一 `keep_alive=0`，嵌入模型走 `/api/embeddings` 兜底；空闲秒数/采样间隔经 `.docmind_state.json` 跨重启恢复）。ComfyUI 租约覆盖完整生成周期（提交临时 owner→`reown comfyui:{prompt_id}`，TTL 600s 兜底；`comfy_history` 终态释放；新增 `comfy_cancel` 打 `/interrupt` 与 `POST /api/comfy/cancel`）；`llm.py`/`embeddings.py` 在 Ollama 推理前后 `note_activity`。新增 `POST /api/gpu/cancel|force-release|configure`；前端新增 `GpuPanel.vue`（每卡占用/温度/迷你曲线/队列取消/强制回收/空闲卸载设置）+ `gpuApi`，TaskEnginePanel 加「取消生成」。新增 `tests/test_gpu_coordinator.py` 23 例（含假双卡 HTTP ComfyUI 服务的完整作业生命周期；227 → **250** 全绿），`npm run build` 通过；真机 RTX 5070 Ti 浏览器冒烟：真实显存/温度/采样曲线/设置持久化/遮罩开关全过。**未实测，不得宣称**：multi 模式 CUDA 进程隔离（协调器只返回卡号，需调用方在子进程启动层设 `CUDA_VISIBLE_DEVICES`）、多卡物理环境 |
| 2026-09-14 | **`82d092c` P2-1 GPU 协调收尾硬化 + 真机空闲卸载闭环**：①驱逐时机收窄——钩子只在显存门槛拒绝（无租约的外部驻留）时触发，活跃持有者排队不再白卸载 Ollama（卸载不释放租约）；②`comfy_queue` 默认要求 `DOCMIND_COMFY_MIN_FREE_MB=1024` 余量，使"显存不够→卸载 Ollama→重试授予"链路真正有牙；③真实 nvidia-smi 探测加 0.5s TTL 缓存（注入探测不缓存，避免状态轮询/pump 扎堆拉子进程）；④`acquire_lease` 返回真实 `reentrant`。真机端到端抓出并修掉一个 Ollama 竞态：qwen3.6:35b + bge-m3 同驻时，紧跟大模型卸载的嵌入模型 `keep_alive=0` 返回成功但仍驻留——钩子改为卸载后 0.8s 复查 `/api/ps` 对幸存者补一轮（最多两轮）；**真机复验 PASS（双模型后台空闲触发→/api/ps 清空，RTX 5070 Ti）**。新增 8 例测试（精准驱逐/缓存/reentrant/低显存拒绝/钩子两轮重试等；250 → **258** 全绿）。**仍未实测**：物理多卡与 multi 的 CUDA 隔离（本机单卡，保持不宣称） |
| 2026-09-15 | **门面文档重写（`a1838a6`）**：`README_en.md` / `DEMO.md` 从「9 工具 + 单页 RAG 问答」时代重写为当前工作台形态——分区开发 / 受控改写 / 选区 AI / 符号关系图 / 场景画布 / 运行时时间线 / 引擎嵌入（Godot 实机）/ GPU 协调 / ComfyUI·Unity·Unreal 适配 / 联网研究；测试数同步为 298/298、场景 54/54、浏览器 27/27、引擎嵌入 68/68 实机，与 `README.md` 对齐 |
| 2026-09-14 | **H3 实机端到端验收（`e096834`）**：`comfy_ui_to_api_workflow` 重写为**子图拍平 + `/object_info` 驱动 widget 映射**（修掉 H3 官方 UI workflow 提交 ComfyUI 的 400/500）；实机经 DocMind 管线完成 **39 帧短生成**并产出 `MiniMax_H3_00008_.mp4`（`preview_url`/`mime` 正确），`comfy_retry` 重排成功、`comfy_cancel` 标记 `terminated`；新增 `tests/test_comfy_h3_converter.py` 回归（子图拍平 / autogrow `values.a` / 接口槽 `-10` / UUID 别名），并修掉 converter fallback 把无 link 的 widget 输入误当连接丢弃的回归 |
| 2026-09-15 | **harness 运维层四件套（`ab6e253`）**：① **逐轮 trace + token 账本**（`agent_trace.py`：turn_id / session_id / messages 哈希 / 工具序列 / tokens in-out / 各步延迟 / finish_reason / 结局；只记元数据不记正文，超限轮转；`/trace` 查看页 + `GET /api/trace`）；`llm.py` 捕获 OpenAI/Ollama/mock 三路 usage；`agent.run` 埋点（aborted / outcome）。② **会话隔离 + 持久化 + 滚动摘要**（`sessions.py`；`Agent(llm, session_id=)` 取代 `api.py` 单例串台；`/api/chat` 加 `session_id`；`GET/DELETE /api/sessions`；golden runner 移除 `/api/config` 重置 hack）。③ **LLM 弹性**：重试 + 指数退避（429/5xx/timeout 可重试、4xx 不重试）+ 统一 `timeout`/`deadline`，SSE 断连即 `close()` 内层生成器中止回合。④ **评测自动化**：`agent_eval.py` 规则打分（must_include/any_of/must_not_include/regex/must_call/动作边界/no_error）+ 可选 LLM-judge + `--baseline` 回归门（回退退出码 1）。新增 33 例测试（313 → **346** 全绿）；新端点经 TestClient 端到端 **14/14**（trace 落账 / 会话隔离 / `/trace` 页面 / 删除会话） |
| 2026-09-17 | **`1664fa3` AI 越界访问权限模式(安全/高权限) + 场景画布视觉与交互增强**：①权限模式 `config.py` 新增 `EXTERNAL_ACCESS_MODE`(safe 默认) 跨重启持久化；`api.py` `ConfigReq`/`set_config` 暴露与切换(高权限须先配 `DOCMIND_EXTERNAL_DIRS` 否则 400)；`tools.py` `read_external_file` 受模式门禁，新增 `create_external_file`/`edit_external_file`/`delete_external_file` 白名单 CRUD 并注册 `TOOLS`；`agent.py` 补越界工具说明。②画布视觉：浅色渐变背景+浅色点阵，节点/文件卡投影+hover 上浮，层级边色软化，右上角图例。③画布交互：搜索定位+聚焦选中、子树折叠/展开、选中/悬停关系高亮、祖先面包屑+导出 SVG/PNG（`SceneCanvas`/`SceneNodeCard`/`SceneFileCard`）。④`ChatDock` 仅贴近底部时自动滚动(修流式被强制拉到底栏)；`web_search` 默认 `after:<去年>` 提升时效(`WEB_SEARCH_PREFER_RECENT=0` 可关)。前端 `vite build` 通过，UI 复查 27/27 无回归 |
| 2026-09-17 | **`6ed42e2` 引擎嵌入优化（perf）**：P0 引擎控制端点(engine_start/stop/embed/detach/focus/resize/place) 由 async 直调同步函数改为 `await run_in_threadpool`，解除 engine_start 内 6s 轮询嵌入与 engine_stop 的 terminate_tree 对 FastAPI 事件循环的阻塞；P1-2 `game_workbench` 新增 `engine_reap_dead()`+`start_engine_watchdog()`（daemon 线程清理崩溃引擎的 `_EMBED_STATE` 残留 hwnd，GPU 租约由 gpu_coordinator 死亡看门狗统一释放）；P1-3 `SceneRuntimePanel` 新增 `matchMedia(resolution)` 监听跨 DPI 变化重算嵌入比例并重 place。验证 后端 23 项 + 看门狗 8 项 + 四 SFC/vite build 通过 |
| 2026-09-17 | **`b33fb54` 引擎嵌入 P2/P3**：P2-4 焦点回流——`engine_focus` 支持 `keep_attached`（长挂输入队列，键盘持续送达嵌入引擎），前端嵌入成功后 `focusEngine(true)` 长挂 + 引擎视窗 `@mousedown` 主动取回焦点；P2-7 切走 play tab 自动 `detach`、切回且引擎在跑+自动嵌入开时 `nativeEmbed()` 仅重嵌不重启；**修复静默陷阱**：`api.py` 重名类 `EngineFocusReq`(空 `pass`) 覆盖带 `keep_attached` 的版本导致 `/api/engine/focus` 端点 `req.keep_attached` 缺失、调用即 500，已删空定义并 import 断言字段存在；P3-8 `verify_engine_embed.py` 新增 [12] 强杀进程后看门狗自动弹 `_EMBED_STATE` 残留、[13] DPI 变化后 `engine_place` 按重算矩形精确落点。验证 后端 19 项 + 看门狗 8 项 + vite build 通过。P2-5(Unity/Unreal 实机嵌入)/P2-6(编辑热重载) 本机无对应引擎与 godot 插件 RPC，未落地 |
| 2026-09-17 | **`e4ba48f` 引擎嵌入深扫修复（perf/robust）**：深扫 `desktop_bridge.py` 第一轮漏掉的 3 处真实问题——① **place 抢焦点**（A，高价值 bug）：`place()` 的 `SetWindowPos` 缺 `SWP_NOACTIVATE(0x10)`，前端 resize/DPI 触发 `engine_place` 重定位时把键盘焦点抢到游戏、打断工作台打字，已补 `SWP_FRAMECHANGED|SWP_SHOWWINDOW|SWP_NOACTIVATE`；`embed()` 仍不带 NOACTIVATE（用户主动嵌入、焦点交游戏为预期）。② **keep_attached 挂接泄漏**（B）：`focus(keep_attached=True)` 调 `AttachThreadInput` 挂接跨线程输入队列，但 `detach()`/`forget()` 从不解除；新增 `_detach_input_queues(state)` 按 `(attached_by, 伙伴线程)` 对 `AttachThreadInput(..., False)` 清理，消除线程输入队列永久挂接。③ **状态并发**（C）：看门狗 daemon 并发 pop `_CHILD_STATE` 与 API 线程池/宿主 resize 并发读写；新增 `_STATE_LOCK = threading.RLock()` 保护 embed 的 `if-not-in` 赋值与 place/fill_host/detach/forget/embedded_children/fill_all 全部复合访问（RLock 保证 fill_all→fill_host 重入安全）。验证：`verify_engine_embed.py` [4] 新增「`engine_place` 后前景窗口≠引擎窗口」（SWP_NOACTIVATE）断言；py_compile OK；`test_api_routes`/`test_native_tools` 19 项 OK；`test_engine_watchdog` 8 项 OK；RLock 重入实测通过 |
| 2026-09-17 | **`060627f` 引擎嵌入健壮性深扫（D/E/F/G）**：用户反馈"嵌入还不够好"，继续补强 `desktop_bridge.py`——① **D 选窗口**：`find_window` 原返回 `EnumWindows` 第一个匹配，引擎有 splash/控制台/主视口多个顶层窗口时会抓错；新增 `_rank_windows` 按「标题命中 title_hint > 非最小化 > 可见面积大」打分选最优（抽纯函数，单测 5 例）。② **E 停止提速**：`terminate_tree` 原对每个子进程串行 `WaitForSingleObject(满超时)`，Godot console+GUI 多进程要把超时累加好几轮；改一次性 `TerminateProcess` 后 `WaitForMultipleObjects`（按 64 一批）整体超时并行等，停止明显变快。③ **F 最小化不塌缩**：宿主最小化/未布局时 `GetClientRect` 返 0×0 → 铺满模式嵌入直接失败、`fill_host` 把引擎 `MoveWindow(0,0,0,0)` 塌成不可见；新增 `state['last_host_client']` 缓存有效宿主矩形，`_target_size` 在 client_rect 为 0 时回退缓存（抽纯函数，单测 4 例覆盖显式尺寸/回退/offset/无缓存报错）。④ **G 挂接泄漏**：`focus(keep_attached=True)` 每次 `state['attached']=attached` 覆盖旧挂接，前台线程变化后旧挂接永久残留；改记 `(by,partner)` 成对并累积合并到 `state['attached_pairs']`，`_detach_input_queues` 按所有对解除（兼容旧字段）。验证：新增 `tests/test_engine_embed_robust.py` 9 例全绿 + `test_engine_watchdog` 8 例全绿；全量 720 跑（**1 失败为 `test_web_fetch` 网络故障转移旧断言**，源自 1664fa3 的 `after:<去年>` 时效后缀，与本改动无关、非回归**） |
| 2026-09-17 | **`ddd0c7d` web_search 站点限定 + 百度后端（用户要求「bing 失败能用别的平台，能否指定平台搜索」）**：`web_search` 入参新增 `site:`/`platform:`——`platform:` 别名自动映射为域名（github/b站/微博/贴吧/知乎/csdn/stackoverflow 等），两者都自动拼成 `site:域名` 收敛到指定站（GitHub/哔哩哔哩/微博/百度贴吧…）；新增百度后端 `_baidu_search`（解析 `www.baidu.com/s` 的 `result`/`c-abstract` 块，国内网络比 Bing 更稳），auto 故障转移顺序由 `ddg→bing` 改为 `ddg→baidu→bing`，`WEB_SEARCH_BACKEND` 支持 `baidu` 单独强制；`TOOLS["web_search"]` description 与 `SYSTEM_PROMPT` 同步说明站点限定用法。新增 `tests/test_web_search_site.py` 11 例（parse/platform 别名映射/site 透传/百度作为 ddg 失败兜底/强制 baidu/HTML 解析）+ 更新 `test_web_search_markers.py` 把 baidu 纳入 auto 链 mock；全量 731 跑、仅 1 失败为前述 `test_web_fetch` 旧断言（非回归） |
| 2026-09-15 | **harness 能力补齐五件套（`ce7ff77`）**：① **原生 function-calling**：`tools.tool_schemas()` 由 TOOLS 生成 schema，`llm.chat(tools=)` 打通 OpenAI/ollama 并归一 `tool_calls`，`tool_mode=react|native|auto`，tool_calls 归一进文本协议后**复用全部护栏**、事件类型不变；② **子代理 + plan 模式**：`delegate` 工具 → 受限子代理（角色白名单 researcher/coder/reviewer/tester、独立会话、深度/步数上限、token 计入父回合），`plan_mode` 首轮上抛 `plan` 事件；③ **hooks + 技能热插拔**：`hooks.py`（pre/post_tool、pre/post_turn，异常隔离）+ `skills.py`（扫 SKILL.md，目录注入系统提示、正文由 `dev_use_skill` 取），`/api/hooks|/api/skills` 均可热重载；④ **成本熔断**：`pricing.py` 按 provider/model 计价 + 全局/会话累计预算，回合前拒绝、回合后 charge，trace 记 `cost_cny`，`/api/budget`；⑤ **golden 门入冻结流水线**：`gate.py`（健康检查→run_golden→agent_eval --baseline，缺题库/模型优雅 SKIP 并输出 BUILD.md 一行摘要），已接入 `docmind-frozen-release` 阶段 0。新增 41 例测试（346 → **387** 全绿）；新端点端到端 **13/13**；gate 跳过路径离线验证通过 |
| 2026-09-15 | **harness 并行化（`89264ce`）**：① **并行工具批次**——原生通道一轮返回多条 tool_call 且**全部只读安全**时用 `ThreadPoolExecutor` 并发（上限 `DOCMIND_PARALLEL_MAX`，默认 4），结果**保序**聚合后一次回填多条 observation；批内含写/副作用工具（`_NO_PARALLEL_TOOLS`：apply_edit/run_command/python_exec/dev_commit…）则整批**退回顺序**，绝不并发写；单条异常只标记该条。② **子代理并行扇出**——`LLMClient.clone()` + 子代理改用**独立** client，消除共享实例 `last_usage`/`last_tool_calls` 的并发竞态；一轮多 `delegate` 并行执行、token 分别计入父回合；`Turn` 累加操作加锁。新增 `test_parallel.py` 9 例（真并发墙钟、3 线程、保序、失败隔离、写工具回退、clone 独立性、并行扇出），全量 387 → **396** 全绿 |
| 2026-09-15 | **多代理编排器（`c31467a`）**：新增 `orchestrator.py`（**纯调度、不依赖 Agent、可离线单测**）——任务图校验（id 重复/依赖缺失/自依赖/超量）、`topological_waves()` 拓扑分波、同波并行、**下游任务注入上游结论**（补齐"子代理不共享父上下文"）、上游失败**阻断**下游（`optional` 豁免）、可选 `synth_runner` 合成并标注冲突。agent 侧抽出 `_run_child`（delegate 与 orchestrate 共用）、新增 `Agent.orchestrate()` 与 `orchestrate` 工具（JSON 入参）、子代理步数耗尽时**过程要点兜底**（标 `degraded`，不再给下游空结论）；`api.py` 加 `POST /api/orchestrate`。新增 `test_orchestrator.py` 21 例，全量 396 → **417** 全绿；端点端到端 8/8 |
| 2026-09-15 | **编排动态重规划（`f6b2179`）**：`run_plan` 改为**迭代调度器**——每轮挑「依赖已落定且未被阻断」的任务并行跑；某轮出现失败且预算未耗尽时调用 `replanner(failed, results, attempt)` 追加**补救任务**并继续（`_accept_new_tasks` 走 `parse_plan(known_ids=…)` 校验，故补救任务可依赖**已完成的旧任务**；坏项/重复 id/悬空依赖逐条丢弃；受 `max_replans` 与总量槽位限制）；不重规划或预算耗尽才按原语义阻断下游。agent 侧新增 `_replanner`（一次 LLM 调用产 JSON 补救任务，提示要求"换做法而非原样重试"）与 `orchestrate(replan=, max_replans=)`；工具 JSON 与 `POST /api/orchestrate` 同步支持；新增 `DOCMIND_ORCH_MAX_REPLANS`（默认 2）。顺带把 `run_plan` 入口改成幂等 `parse_plan`（原始 dict 与规范化任务都能吃）。`test_orchestrator.py` 21 → 34 例，全量 417 → **430** 全绿；端点端到端 **11/11** |
| 2026-09-15 | **回溯式重规划（`78d1504`）**：`replanner` 提案升级为 `{add, drop, replace}`（裸任务数组仍等价于 `{add}`，向后兼容）——**add** 追加补救任务、**drop** 取消尚未执行的任务（记为 `dropped` 并阻断其下游）、**replace** 原地改写未执行任务的 role/task/deps（常用来把被阻断的下游**救回来**）。**安全边界**：只能动**尚未执行**的任务，已执行者不可删改（记入 `ignored`），本编排器**不回滚已产生的副作用**。非法项逐条忽略不抛异常；每次改动记入 `report.revisions` 审计。修了两个要点：被 drop 的任务移出 `by_id` 后，`ok` 与依赖判定改用 `.get` 兜底且把 `optional` 留在结果条目里（可选任务被 drop 后下游仍豁免）；`drop` 不就地修改调用方传入的 plan。agent 侧 `_replanner` 提示升级并放行 dict 形态提案。`test_orchestrator.py` 34 → 45 例，全量 430 → **441** 全绿；端点端到端 **13/13** |
| 2026-09-15 | **执行轨迹回传（`2689035`）**：`replanner` 此前只拿到失败任务的错误串与各任务结论，无法判断"为什么没成"。现在 `Agent.run` 把每回合 trace 留在 **`last_turn_record`**，`_run_child` 采集**有界**逐步轨迹（`{action, obs片段}` + thoughts/reflections + 子回合 outcome/llm_calls/tokens/耗时/成本；上限 `DOCMIND_ORCH_TRACE_STEPS`=6 步、每步观察 `DOCMIND_ORCH_TRACE_OBS_CHARS`=240 字，超出只计数），随结果一起进 `results`（`/api/orchestrate` 返回值里也有）；`_replanner` 把失败任务的轨迹逐条渲染进提示（无轨迹时明写"未留下可用执行轨迹"）；`format_report` 给失败/被取消任务补一行轨迹摘要。`test_orchestrator.py` 45 → 54 例，全量 441 → **450** 全绿；端点端到端 **16/16** |
| 2026-09-15 | **黄金题题库建成（`golden/questions.json` 8 题 + `golden/results_baseline.jsonl` 8/8 通过）**：门不再默认 SKIP——`docmind-frozen-release` 阶段 0 的 `GOLDEN_QUESTIONS` 已指向入仓题库。源码类题（G2/G3/G4/G6/G7）依赖 `code_root` 指向本仓库且 `ingest_code` 已索引；SKILL 已写明**隔离 chroma 的专用评测服务**起法（`CHROMA_DIR` 复制自 `.chroma` + `CODE_ROOT=<repo>` + POST `/api/ingest_code`），避免污染你 `:8000` 的游戏代码索引。断言用 `any_of`+`must_not_include`+`no_error` 鲁棒匹配、不依赖逐字；离线规则打分常驻 `tests/test_agent_eval.py` |
| 2026-09-15 | **连接器启停 UI 落地**：ChatDock「引擎」弹层新增 **断开**（关闭 stdio 长驻会话，新增 `POST /api/mcp/close` + `mcp_client.close_server` 复用）、**真实连接状态**（新增 `GET /api/mcp/status` + `mcp_client.active_servers()`，弹层打开时回填 connected 态）、**新增/移除连接器**（`POST /api/mcp/servers` / `/remove` 接入 UI 表单）；修正原模板 `resultOf(s.key)` 误传字符串（应为 server 对象）导致「已连接 · N 工具」状态**永不显示**的 bug。新增 `tests/test_mcp_connector.py` 6 例全绿。**未实机**：`active` 仅在真实 stdio 引擎（Godot/uvx）连接后填入，本沙箱无引擎未跑该路径 |
| 2026-09-15 | **B 档浅实现收口（`simulate_growth` 深化）**：原 2026-09-11 审计六项仅 `simulate_growth` 仍是等比数列玩具，其余五项已于 2026-09-14 做深（impact_analysis 语义检索 / generate_test_scene 真实 AST / performance_sample cProfile / approval TTL 门禁 / verify_contracts 环检测）。本次把 `simulate_growth` 改为 **geometric / linear / logistic S 形 / diminishing 四模型 + 摘要统计**（翻倍数等级 / 峰值增量及其等级 / logistic 拐点等级），`/api/simulate_growth` 与 `game_simulate` 工具加 `model`/`k`，新增 `tests/test_simulate_growth.py` 10 例（含 TestClient 端点形态）全绿；HANDOFF §5 原「仍有效」审计标记已校正，**B 档清单清空** |
| 2026-09-15 | **连接器自主切换策略层落地**：`mcp_client` 新增 `capabilities_of`（engine + 显式 capabilities 推导能力标签）/ `connector_directory`（Agent 目录，含能力/适用说明/启用态，不打开会话）/ `select_connector`（按任务语义打分排序；点名引擎只在该引擎内选，避免误路由）；Agent 新增 `dev_list_connectors`/`dev_route_connector`/`dev_list_connector_tools` 三工具，`dev_mcp_call` 失败时提示回退，`SYSTEM_PROMPT` 接入「发现→路由→列工具→调用→切换」闭环；`/api/agent/connector-route` 暴露策略入口、`/api/agent/connectors` 追加 `capabilities`/`best_for`。新增 `tests/test_connector_routing.py` 13 例全绿（中文子串扫描 + 引擎专指过滤）；隔离端口 8079 真打 `connector-route`（`Godot 场景`→godot score 9、`Unity 构建`→[] 不误路由）与 `connectors` 富化字段。HANDOFF §5 Agent 路由「仍待做」项已落地 |

| 2026-09-15 | **第二十次冻结合建（P2-2 ComfyUI 精确取消修复）**：comfy_cancel 改走 `POST /queue` delete 按 prompt_id 定向取消并精确释放 GPU 租约；492/492 单测、场景 54/54、浏览器 27/27、引擎嵌入 68/68 实机、npm run build、PyInstaller 直接构建进 `dist/DocMind`；exe 19,832,954 B、SHA-256 6b5e4207…；黄金题门 SKIP（环境无法稳定起已索引评测服务） |
| 2026-09-16 | **设计评审驱动的缺陷修复（第 21 次冻结构建）**：对 4 个未推送提交（`1a1b597` / `9d9dd63` / `7186548` / `5c359d8`）做架构评审 + 缺陷排查（架构师与 QA 双线并行、逐条独立复现），按 **A→B→C→D 四批**修复，再由**未参与改动的第二位 QA** 复核（抓到 1 个由本轮修复引入的 P1 回归 + 4 个 P2）并追加 **R 批**修复、复验 PASS。① **导入期副作用消除**：`gpu_coordinator` 模块级 `restore_runtime_state()`（导入即 `unlink`，被拦时抛 `BaseException` 连锁炸 import）→ 显式 `init()` 由 lifespan 调用；`config` 模块级 `makedirs(CHROMA_DIR)` → 惰性 `ensure_dirs()`；`_apply_persisted_state()` 移入 lifespan；② **运行时状态根可注入 + 测试隔离**：`config.STATE_ROOT` + `state_path()`，10 项状态派生，测试进程默认隔离到临时目录（判据 `__main__.__spec__.name == "unittest.__main__"`），隔离时相对 env 也落 `STATE_ROOT`；③ **前后端失败体契约**：`modelApi.save/probeOllama/lookupModelContext` 改走 `rawJson`，`res.model_error` 分支从死代码恢复；④ **逐请求 llm/开关注入**：`Agent.run()` 5 个仅关键字覆盖（`web_enabled`/`thinking_enabled`/`tool_mode`/`plan_mode`/`llm`）+ `finally` 四出口还原；云端路由不再新建并注册 Agent（修掉模块级 `agent` 孤儿与会话黏云端）；⑤ **GPU 状态文件拆分**：采样样本独立 `SAMPLES_FILE`，修掉「两套 schema 互相覆盖 → 崩溃恢复静默失效」；⑥ **加固**：`calculate` 指数静态限幅（`9**9**9` 卡死 >6s → 毫秒级拒绝，`2**3**2` 不再误杀）、async 内 `LLMClient` 构造走线程池、`_history_window` 由 O(N) 次 `/api/tokenize` 降为 1 次、`context_stats` 与压缩口径对齐（新增 `compact_percent`）；⑦ 子代理继承联网/思考开关、搜索与 `read_file` 失败标记补全、两个早退分支补 `executed.add(sig)`（空转 15 轮 → 及时提示）、Prompt 预算下限不再超窗。**707/707 单测、场景 54/54、前端 build、最小 PATH 冻结冒烟 13/13 全 PASS、前端 14 产物逐字节一致**；exe 20,536,866 B、SHA-256 `f67eac87…`；黄金题门 SKIP（环境无法稳定起已索引评测服务） |
| 2026-09-16 | **R5 并发限制修复 + 第 22 次冻结构建**：前端改为**每标签页独立 `session_id`**（`sessionStorage` 的 `docmind_session_id`；`askGrounded` 走 form 字段、`contextApi.get()` 走 query；ChatDock「清空对话」接到当前标签页会话）——消除「同会话真并发串逐请求开关 / `llm`」；**后端零改动、不加锁**。过程中**冒烟抓出我方 D 批引入的 P1 回归**：`_apply_persisted_state()` 从导入期移入 lifespan 后**顺序反转**，用持久化值**覆盖**了调用方启动前显式 `set_runtime('code_root', …)`（`verify_scene_canvas.py --serve` / `verify_engine_embed.py` / `verify_regions.py` 都是这个用法）→ 临时 Godot 工程被顶掉 → 场景图空 → 画布无节点 → 冒烟在等 `.vue-flow__node-sceneNode` 超时。**修法**：持久化恢复改「**显式设置优先**」（`"code_root" not in _RUNTIME` 才恢复）+ 回归测试 2 例。**影响面仅校验脚本**——`desktop.py`/`run.py` 不预设 code_root，**用户正常启动不受影响**。验证：单测 **709/709**、`verify_scene_canvas.py` **54/54**、`verify_scene_canvas_ui.mjs` **27/27**、`verify_engine_embed.py` **68/68**（真机 0 跳过 0 未证实）、前端 build、最小 PATH 冻结冒烟 **13/13**；exe **20,536,890 B**、SHA-256 `4934b9c1…`；黄金题门 SKIP |
| 2026-09-16 | **修复 `/workbench.html` 死链（第 23 次冻结构建）**：用户实测桌面窗口里点「代码工作台 →」得到 `{"detail":"Not Found"}`。根因：`web/index.html:486` / `web/trace.html:70` 的入口链接写的是 `href="/workbench.html"`，而 FastAPI 服务端只有无后缀路由 `/workbench`（`.html` 仅存在于 `/static` 挂载，纯静态托管下才有效）→ API 服务端 404。**修法**：`api.py` 补 `GET /workbench.html` → 307 `/workbench` 与 `GET /trace.html` → 307 `/trace` 两个别名（两种托管方式都对，不改链接、不破坏静态发布）；并把「`/workbench` 或 `/workbench.html` 二者之一即可」这条**太宽松**的测试断言，升级为**真的能打开**（解析实际 href + `TestClient` 请求断言 200）。验证：单测 **711/711**、端到端六路由全 200、最小 PATH 冻结冒烟 **13/13**；exe **20,537,351 B**、SHA-256 `6121fa61…`；黄金题门 SKIP |
| 2026-09-17 | **顶层「画布 / 运行」tab 接到真实功能（第 24 次冻结构建）**：`WorkspaceTabs.vue` 的「画布」「运行」本是 roadmap 占位符（◌ + tooltip「阶段 3/4」、无 `@click`），用户误以为功能没做完；其实场景画布与运行游戏早已实现（入口在工具栏「运行游戏」弹窗 `SceneRuntimePanel.vue`）。本次用 `composables/workbench.ts` 新增共享态 `runtimeOpen`/`runtimeTab`/`openRuntime`/`closeRuntime` 作桥，`SceneRuntimePanel.vue` 与 `WorkspaceTabs.vue` 双向桥接——顶层 tab 可点开对应面板（画布→`scene`、运行→`play`）并高亮、本地关闭/切 tab 写回共享态；去掉「阶段 3/4」误导文案。纯前端 3 文件，后端零改动。验证：前端 `npm run build` 通过、711/711 单测（skipped=1）、`dist/DocMind/web/assets/workbench-*.js` 含 `openRuntime`、桌面镜像后 SHA 与 dist 一致（exe 20,537,322 B / SHA-256 `4e847b0e…`）；**未重跑** 场景 54/54、浏览器冒烟 27/27、引擎嵌入 68/68（改动面仅前端 tab 接线，未触碰 `scene_runtime.py`/`desktop_bridge.py`/`engine_adapters.py`/`game_workbench.py`，与第 21 次同口径留痕）；黄金题门 SKIP |
| 2026-09-17 | **`44eab9a` 完整设置页（网络搜索 / MCP / 智能体）+ web_search 配置驱动重构**：用户要求「参考搜索服务商配置页，做完整的后端+前端设置页」。①后端：`config.py` 新增 `WEB_SEARCH_*`/`WEB_FETCH_*` 运行时 getter/setter 与 `_apply_web_search_state` 跨重启持久化；`api.py` `ConfigReq`/`get_config`/`set_config` 增 7 字段（密钥不回显、`save` 回 `warnings`）；`tools.py` `web_search`/`web_fetch` 由 `WEB_SEARCH_BACKEND` 环境变量改为 `get_web_search_provider()` 配置驱动，内置 `ddg→baidu→bing` 故障转移保留，新增 Exa/Tavily/SearXNG/Bocha/Firecrawl/智谱/Querit/Parallel/MCP-Exa 共 8 个 API 后端（含 `web_search_prefer_builtin` 开关）。②前端：新增 `SettingsView.vue` 三面板（网络搜索服务商+API Key/地址+内置 Web 工具开关 / URL 获取服务商；MCP 列表+添加/移除；智能体本地预设增删），`api.ts` 补 `WebSearchProvider`/`WebFetchProvider`/`ProviderOption`/`SettingsConfigInfo`/`SaveSettingsReq` + `WEB_SEARCH_PROVIDERS`(13)/`WEB_FETCH_PROVIDERS`(4) + `settingsApi`；`App.vue` 顶栏加「设置」齿轮入口接入弹层。③测试：`test_web_search_site.py` 改配置驱动（`patch("tools.get_web_search_provider")`），`test_web_fetch.py` 兜底用例迁移到配置驱动并关近因排序，修掉重构引入的 `test_forced_ddg_does_not_fail_over` 失败。验证：前端 `npm run build` 通过、单测 **736/736**（skipped=1，0 失败）；密钥不回显、保存 warnings 经 `rawJson` 读取，契约对齐 `api.ts` |
| 2026-09-17 | **对话/检索历史跨重启丢失修复（BugFix）**：用户反馈「工作台关闭后重开，AI 助手对话/检索历史是空的」。定位：后端**其实已**按 `session_id` 落盘（`agent.py:946` `_sessions.save` → `<STATE_ROOT>/.docmind_sessions/*.json`；`Agent.__init__:604` 从盘恢复），断链全在前端——① `api.ts::getSessionId()` 用 **`sessionStorage`**（关窗即清）→ 重开生成新 id，后端对该新 id 自然无历史；② `ChatDock.vue` 的 `messages` 初始为空且 `onMounted` 从不回灌；③ 后端无「取回某会话消息」接口（`/api/sessions` 仅概览，`/api/sessions/{id}` 只有 DELETE）。修法：① `api.py:1579` 新增 `GET /api/sessions/{session_id}`（`session_store.load`，缺省回空 turns 不 404；与同路径 DELETE 不同方法共存，防静默覆盖）；② `api.ts` `getSessionId()` 改 **localStorage 优先**（跨重启存活）+ 新增 `setSessionId()` + `harnessApi.sessionDetail()`；③ `ChatDock.vue` 新增 `rebuildFromTurns()`/`restoreHistory()`——挂载回灌，当前 id 无历史则自动续接**最近一段会话**；④ `HarnessPanel.vue` 对话列表加「继续」按钮（切会话 + 回灌）。**验证（QA 独立视角）**：后端自写 6 例 + 工程师 4 例 + `test_api_routes` 9 例全绿，全量 **746/746（skipped=1，0 失败）**；前端 `vite build` 通过（252 模块）。QA 抓到并修复一处**回灌竞态**：`restoreHistory` 的守卫在 await 前同步求值，用户在 await 窗口内发消息会被 `messages.value = rebuilt` 覆盖且 SSE 回复因 `live()` 失配静默丢弃 → `ChatDock.vue:305-308` 补 await 后复检（`sending` 一律放弃；非 force 且消息数变化也放弃）。**已知取舍**：改 localStorage 后浏览器**多标签页共享同一会话**（原「每标签页独立 id」的并发隔离弱化；桌面单窗口无影响，必要时用「清空对话」开新会话）；**未证实**：真实桌面壳（pywebview/edgechromium）重开端的 localStorage 持久性（本机无桌面运行环境，仅构建级验证） |
| 2026-09-17 | **第二十五次冻结构建 + 桌面安装换入**：把「完整设置页（`44eab9a`）+ 对话/检索历史持久化（`5c4db6c`）」推进为可分发的冻结版并换入用户桌面安装。exe **20,563,506 字节**、SHA-256 `a52f202d…`、整包约 391.7 MB；单测全量 **746/746**（skipped=1）。冻结冒烟（最小 PATH=仅 System32、`DOCMIND_SERVER_ONLY=1`、端口 **8044**，未碰 :8000）：`build_time=2026-09-17 19:35:54`、`/`+`/workbench`+`/trace` 200、新端点 `GET /api/sessions/{id}` 200、服务端 `workbench-*.js` 与源 **SHA-256 一致**、包内卫生扫描无 `.chroma`/`.env`/状态文件。桌面安装 `D:\WorkBuddy\DocMind` 实机启动（8055）**1s 就绪**且**用户 `code_root`（`godot_sample`）与配置完整保留**。**换入策略**：冻结版前端目录取 `config.py:15-22` 的 `EXE_DIR/_internal/web`，故只替换 `DocMind.exe` + `_internal/web/`（并同步顶层残留 `web/`），保留 `_internal/` 下用户运行时数据与 MinGit/unins000；旧 web 备份 `D:\Temp\desktop_web_prev`。**新踩坑**：spec 末尾对 `dist/DocMind/MinGit` 的 `shutil.rmtree` 被沙箱批量删除守卫拦截 → 构建前先 `mv` 旧 dist + 清 `build/docmind` 缓存即一次通过；另记录「资源真实路径为 `/assets/<hash>` 且 `/workbench/` 是 307」。详见 `DocMind_BUILD.md` 第二十五次重建节 |
| 2026-09-17 | **多会话 / 多项目改造 P0–P5（`0ef179e` / `16b1010` / `ba0aa80` / `d4f1e02` / `4c1d583`）**：用户要求「优化多标签共享会话 + 让项目在开发台选 + 两个项目同开引擎是否冲突」。先做竞品调研（11 个 agent 应用）+ 架构调研，再按 6 期实施（每期「工程师实现 → QA 独立验证 → 提交推送」）。**P0 会话隔离**：`getSessionId/setSessionId` 改回 sessionStorage-only（每标签独立）；标签存活探测以 **Web Locks 首选**（关标签才释放、不受后台节流）→ BroadcastChannel 心跳 → localStorage 租约 → 不可用一律按「非唯一」（隔离优先）；`ChatDock` 去掉「静默续接最近会话」改为**仅在唯一标签时**自动续上；运行台「对话」加当前高亮/「+新会话」/删当前自动切新；`web/index.html` 补 `session_id`（修掉首页所有标签落 `default`）。**P1 引擎单实例**：`engine_running_roots()` + `engine_start`/`ingest_code` 自动停其它项目引擎，`engine_stop` 失败仅警告不阻断。**P2 项目数据层**：新增 `projects.py`（`project_id = prj-<sha1(normcase(abspath))[:12]>`，同目录复用同 id；注册表存 STATE_FILE 的 `projects`/`current_project_id`）；**条件化迁移**（仅注册表为空才用 `code_root` 建首个项目）；`sessions.py` 会话按 `<项目>/<会话>.json` 分桶 + **legacy 读穿透**（读列并上旧扁平目录、删两处都删、写只写桶）→ 保证既有历史不消失；`DOCMIND_PROJECTS=0` 一键回退。**P3 端点项目化**：核心是让 **`config.get_runtime('code_root')` 请求上下文感知**（`ContextVar` + 中间件按 `X-DocMind-Project` 头绑定）→ `tools/regions/workbench_fs/agent` 几十处既有调用**零签名改动**即项目化；新增项目 CRUD 5 端点；`_agent_for` 复合键（无上下文=session_id、有=`pid::sid`）。**P4 前端**：`api.ts` 新增 `getProjectId/setProjectId/withProject` 并把项目头注入**全部请求出口**（4 封装 + 3 裸 fetch；组件内 6 处自建 fetch 收口为 `agentApi`）；顶栏「项目下拉 + 打开项目」，切换带**脏标签确认**（取消则完全不切换）、清标签/画布/引擎面板、重拉文件树、对话区按新项目重载、失败回滚。**P5 宿主与租约**：`desktop_bridge` 宿主改**按项目分桶**（旧单参=全局桶，行为不变；读取项目桶缺失回落全局桶）；`acquire_lease(exclusive=False)` **软租约**（登记但不进互斥槽：不参与互斥/不挡硬租约/不被硬租约挡），`engine_start` 用软租约使两引擎可共享单卡、`min_free` 降级为警告；`desktop.py` 新增 `probe_multi_window_support()`，**默认仍单窗口**。验证：全量 **931/931**（skipped=1，0 失败）；P4 另有**真实浏览器 E2E 12/12**（playwright-core + 系统 Edge，切项目后 `code_root`/`current`/顶栏均为 B）。**QA 独立抓到并已修 3 个真 Bug**：① P2 迁移无条件覆盖 `current_project_id`（项目选择每次重启被改回）；② P3 带非当前项目头的 `ingest_code` 污染全局/持久化 `code_root`（改为仅目标==当前项目才写全局）；③ P5 宿主注册被请求项目绑走（切项目后 `host_hwnd=None`、引擎不再自动嵌入 → 注册只认显式 pid + 读取回落全局桶）。**未证实（按铁律留痕，不得宣称已完成）**：真机同时开两个 pywebview（edgechromium）窗口并各拿稳定 HWND、多宿主下多引擎并发真渲染、`multi` 模式物理多卡；P5 这三项已用「普通窗口当假宿主」做**降级 API 层验证**替代 |

| 2026-09-18 | **`116f6a5` 引擎嵌入硬化 + Web 试玩多项目路由 + 运行游戏面板三处修复**：① **引擎嵌入硬化**：`desktop_bridge.embed` 在 rect 缺失时退化为「宿主客户区内有界框」（居中留边 `max(32,0.10*min(w,h))`，`mode='rect'`），`fill=True` 成唯一铺满开关（封堵 150% DPI 下引擎黑屏铺满盖住工作台 UI）；flags 透传 `EngineEmbedReq.fill`→`engine_embed(fill=)`→`engine_start(fill=)`→`embed(fill=)`。② **Web 试玩多项目路由**：`/play/{token}` 被 iframe GET 调用不带 `X-DocMind-Project` 头，原 `_project_root_or_error()` 回退全局 `code_root` 导致与导出工程（如 godot_sample）不一致 → `未知试玩会话`；改 `web_export.root_for_token(token)` 按 token 反查根（缓存→项目注册表→code_root 兜底）。③ **运行游戏面板三处修复**：`SceneRuntimePanel.clearEvents()` 改调 `runtimeApi.clear('all')`（连引擎日志游标一起归零，不再一点开就复活）；切到场景画布且无路径时 `ensureSceneLoaded()` 自动加载主场景（`scene_runtime.main_scene` 读 `project.godot` 的 `run/main_scene` + `GET /api/scene/main`）；`RuntimeTimeline.vue` 加 `.rt-legend` 说明如何读泳道/方块/来源。④ **测试护栏**：`test_api_routes.test_sse_streams_without_extra_gpu_lease` 桩目标 `api.agent`→`api.Agent`（类方法，mock 才生效）；新增 `test_scene_ops.MainSceneTests` 3 例、`test_web_export.test_root_for_token_cache_and_fallback` 1 项。验证：全量 **935/935（skipped=1，0 失败）**、`test_api_routes` 9/9、前端 `npm run build` 通过（252 模块）；**未实机**：`verify_engine_embed.py` 60 项 GUI 验收沙箱跑不了，需本机跑 |
> 逐次构建的改动/验证/哈希核对明细见 `DocMind_BUILD.md`（20 次完整记录，继续追加不要新建文件）。

---

## 5. 待办清单（规划项，未完成；按优先级）

> 每项含【要做什么】【原因】【方案】【验收】。状态以 `eeb73ab` 的代码为准，2026-09-14 接手时逐项核对。

### Agent 模型路由与权限（基础层 + UI 已落地，2026-09-14 接手核对）

`agent_policy.py`、`/api/agent/route`、`/api/agent/routing`、`/api/agent/connectors`、`/api/agent/permission`、`/api/agent/secrets`(GET/DELETE)、`/api/agent/approvals`(含 decide 与 before/after unified diff)、`/api/agent/external-write` 和 Skill `agent-model-routing` 均已落地；前端 `AgentPolicyPanel.vue` 已在工作台顶栏接线（路由状态/连接器清单/外部路径审批/Diff 批准拒绝）。`/api/chat` SSE 首事件返回路由建议并注入上下文；`AGENT_AUTO_CLOUD=1` + 云端密钥时复杂请求走云端 Agent，缺密钥自动回退本地；云端发送前经 `redact_for_cloud` 脱敏并截断上下文；`dev_mcp_call` 校验连接器启用状态、可选 task_id 绑定与参数路径越权；外部写入需 approved approval_id + 精确路径，写前生成 `.docmind.bak`；密钥 DPAPI/Fernet 往返、撤销、授权均有回归测试。外部授权写项目内 `.docmind_permissions.jsonl` 审计日志，Agent 自身项目始终拒绝写入。
**连接器策略层已落地（2026-09-15）**：ReAct 现可自主挑/切连接器——`mcp_client` 新增 `capabilities_of`/`connector_directory`/`select_connector`（按任务语义给已启用连接器打分排序；点名某引擎只在该引擎内选，绝不把 Unity 任务误路由到 Godot）；Agent 新增 `dev_list_connectors`/`dev_route_connector`/`dev_list_connector_tools` 三工具，`dev_mcp_call` 失败时提示回退，`SYSTEM_PROMPT` 接入「发现→路由→列工具→调用→切换」闭环；`/api/agent/connector-route` 暴露策略入口，`/api/agent/connectors` 追加 `capabilities`/`best_for`。新增 `tests/test_connector_routing.py` 13 例全绿。连接器**启停/配置管理 UI** 见上条（2026-09-15 `mcp_client`+`api.py`+`ChatDock.vue`+`api.ts`）。

### P1-2　Unity 深度适配（GUID 引用图已落地，编辑器联机未做）

已完成：`/api/engine/inspect?engine=unity`（Unity 版本、`.unity/.prefab` 清单、`.meta` GUID）与 **`/api/unity/guid-graph` GUID 引用图**（`unity_graph.py` + 工作台「Unity 图」弹窗，见 §4 2026-09-14 条目；纯文本分析，无需编辑器）。仍待（本机未安装 Unity 编辑器，无法实机验收）：Editor HTTP 插件、Console/PlayMode 控制、资产增删改时同步维护 `.meta` GUID（资产操作必须成对处理 meta）。

### P1-3　Unreal 深度适配

扫描 `.uproject/.uplugin/Source/Build.cs` 建 C++ 符号图；Editor Python/HTTP 插件查 Level Actor/Blueprint；AutomationTool 编译验证；**禁止直接改 `.uasset`**。

### P2-1　GPU 协调补完（软件侧已全部完成 `6e9b95b`+`82d092c`，剩两项纯硬件验收）

软件侧已交付并验证（2026-09-14，**单卡** NVIDIA GeForce RTX 5070 Ti Laptop，12227MB）：serial 全链路（租约/FIFO/TTL/取消/定向回收/显存门槛/精准驱逐）、5s×240 采样环、Ollama 双模型空闲自动卸载**真机端到端 PASS**（qwen3.6:35b + bge-m3 同驻→后台触发→`/api/ps` 清空，含卸载后 ps 复查两轮）、ComfyUI 全作业周期租约（含 `/interrupt` 取消）、GpuPanel 真机冒烟、`test_gpu_coordinator.py` 31 例（258/258 全绿）。

下列两项本机**不具备验收条件**。硬件到位前，HANDOFF/GpuPanel/任何提交信息或对外表述**都不得宣称"多卡调度已验证"或"已做 CUDA 隔离"**；现状口径统一为：多卡选择只存在于租约层（假双卡单测覆盖），运行时无任何设备隔离。

**待验收项 A：物理多卡租约调度（只缺真硬件，无需写功能代码）**
- 【要做什么】在 ≥2 张 NVIDIA GPU 的 Windows 机器上，用真实 `nvidia-smi` 验收 multi 模式的调度行为。
- 【原因】现有多卡用例全部通过 `set_gpu_probe` 注入假探测数据，未覆盖真实多卡 CSV 解析、两卡真实占用差与多进程真实竞争时序。
- 【方案】设置环境变量 `DOCMIND_GPU_MODE=multi` 后启动服务（采样间隔保持默认 `DOCMIND_GPU_POLL_INTERVAL=5`；其余代码不需要改动）。
- 【验收】①`GET /api/gpu/status` 返回的 `gpus[]` 条数、index/name/显存/利用率/温度与 `nvidia-smi` 一一对应；②两个不带 `gpu` 参数的并发 `acquire_lease` 分别落在两张物理卡（自动挑空余量大者），第三请求按卡忙情况排队或拒绝；③队首指定的卡忙时，释放**另一张**卡不得让队首跨卡获得（严格 FIFO 的真机版，对应用例 `test_fifo_head_blocks_later_waiter`）；④`POST /api/gpu/cancel {owner}` 只取消排队不动持有租约，`POST /api/gpu/force-release {owner}` 只回收指定卡、另一卡 holder 不变；⑤GpuPanel 出现每卡一条的迷你曲线与独立 holder/queue 展示；⑥退化检查：单卡机器把模式误设为 multi 时 `gpus[]` 长度 1，租约功能不报错。

**待验收项 B：CUDA 设备隔离的接线与验收（P1-3 合并后：引擎消费侧已接线，真机单卡/负对照待验，物理多卡仍待）**
- 【要做什么】协调器的边界是"在 `acquire_lease` 返回值里给出 `gpu` 物理序号"；真正的设备隔离必须由**拉起 GPU 子进程的调用方**在进程启动前把该序号写进子进程环境（NVIDIA 为 `CUDA_VISIBLE_DEVICES=<gpu>`，AMD 对应 `HIP_VISIBLE_DEVICES`）。
- 【原因】CUDA 只在进程初始化瞬间读这个变量，启动后再改无效；而当前唯一的 GPU 重负载方 ComfyUI（127.0.0.1:8188）与 Ollama（11434）都是**用户自管的外部常驻服务**，DocMind 只发 HTTP、从不 `Popen` 它们——multi 模式对这两个外部服务仍不产生隔离效果。
- 【方案】未来若由 DocMind 直接 spawn GPU worker（如本地批量出图/内置推理），在唯一的进程启动封装处用租约结果构造子进程环境后再启动（接口级描述：以父进程环境为底，覆写单键 `CUDA_VISIBLE_DEVICES` 为 `lease["gpu"]`，并发 worker 各持各的租约序号）；`game_workbench.comfy_queue` 注释里已标注该接线点。
- 【验收】①两 worker 分别取得 k0/k1，各自在子进程内枚举可见设备：设备数为 1 且设备 UUID/名称与物理 k0、k1 对应（注意隔离后进程内设备序号会被重映射为 0，只能用 UUID/名称核对物理身份）；②负对照：给子进程一个不存在的序号（如 `CUDA_VISIBLE_DEVICES=99`）时子进程明确报告无可见设备——以此证明变量真正生效，而非"机器本来就只有一卡"；③worker 异常退出后其租约由 TTL 回收，对应卡重新可被分配，无需人工 force-release；④serial 模式（不设该变量）下原有单卡流程行为不变；⑤验收完成后再同步更新本文件与 GpuPanel 文案口径。
- 【P1-3 合并更新（2026-09-15）】消费方已落地：`engine_start`（owner `engine:<root>`，ttl=0）与 `engine_verify`（owner `verify:<root>`）在 Popen/run 前 `acquire_lease`，并经 `gpu_coordinator.process_environment(lease["gpu"])` 注入 `CUDA_VISIBLE_DEVICES`/`DOCMIND_GPU_INDEX`；启动失败/各异常分支/`engine_stop` 全部释放。协调器新增 `process_environment()` 与 `GET /api/gpu/environment`。**状态**：注入接线有自动化护栏（test_start_injects_env_and_stop_releases）；真机负对照已于 RTX 5070 Ti Laptop 实测：`CUDA_VISIBLE_DEVICES=0` → `torch.cuda.device_count()=1`（设备名正确），`=99` → `0`，证明环境变量真实生效（ComfyUI 便携 Python 3.13 + torch 2.13）；物理多卡 UUID 核对（验收①③）仍需硬件，项 A 口径不变；ComfyUI/Ollama 外部常驻服务仍不享受隔离，未宣称。

### P2-2　ComfyUI 流水线

**状态：功能基本完成，2026-09-15 收尾精确取消后无已知工程缺口。** 下列能力均已落地（见 §10 多条记录与对应 `tests/test_comfy_*`）：
- 自动轮询 `comfy_watch` / `comfy_wait`、失败重试 `comfy_retry`（上限 2 次）、结果网格（前端 `TaskEnginePanel` 多媒体预览）、音频/3D 预览（按扩展名 + MIME 分类 `asset_kind`/`preview_supported`）、许可证/来源元数据（`license`/`source_url`/`author`/`workflow_sha256` + `comfy_validate_provenance` 人工审核提示）、重复资源分析 `comfy_resource_duplicates`（SHA-256）、未用资源 `comfy_unused_resources`。
- **H3 短生成 / 取消 / 重试 实机验收已完成（`e096834`，2026-09-14）**：此前因缺 `TE-Speed-MiniMaxH3-OSS` 自定义节点 + 官方 UI workflow 提交 ComfyUI 报 400/500 而阻塞。现已确认该节点安装就绪，`comfy_ui_to_api_workflow` 重写为「子图拍平 + `/object_info` 驱动 widget 映射」后，经 DocMind 管线实机完成 **39 帧短生成**（`MiniMax_H3_00008_.mp4`，`preview_url`/`mime` 正确）、`comfy_retry` 重排失败作业并重生成成功、`comfy_cancel` 标记 `terminated`、并新增 `tests/test_comfy_h3_converter.py` 回归（23/23 ComfyUI 用例全绿）。详见 §4 时间线 `e096834` 行与 §10「H3 实机验收收尾」条目。
- **精确 prompt_id 取消修复（2026-09-15）**：原 `comfy_cancel` 发 `POST /interrupt` 并误带 `prompt_id` 体——真实 ComfyUI 忽略该体、只中断"当前全局任务"，**取消不掉指定队列任务**（属 bug，非文档缺口）。已改为 `POST /queue` 带 `{"delete":[prompt_id]}`：ComfyUI 的 `delete_prompt` 会精确移除队列任务、且若其正在执行则自动 `interrupt`（不误伤其他任务）。同步改写 `tests/test_comfy_cancel.py`（断言 `/queue` delete 体与 `deleted` 字段）、`tests/test_gpu_coordinator.py`（fake server 增 `/queue` 处理、断言 `deletes==1`），并给 `api.py` 端点 docstring 与前端 `api.ts` cancel 返回类型补 `deleted` 字段。全量 `discover` 492 项 OK（skip=1 为 ComfyUI 不可达的真机转换测试）。
  - **真机验收（2026-09-15，8188 起 ComfyUI 后）**：直连 `/prompt` 排两条 Z-Image Turbo 任务 → `A=running`、`B=pending`（B 在队列排队，正是原 bug 场景）；`comfy_cancel(B)` 返回 `deleted=True` 且 **B 被精确移除、A 未误伤**；`comfy_cancel(A)` 返回 `deleted=True`、触发 `interrupt`。**例外（非 DocMind bug）**：Z-Image Turbo 执行不即时响应 ComfyUI `interrupt` 标志，被中断任务会长期留在 `queue_running` 直至自然完成——属 ComfyUI/模型层行为，端点契约本身正确。验收脚本 `D:/Temp/comfy_cancel_accept.py`。

### P3　冻结发布（标准流程，已执行至第 21 次）

按 `docmind-frozen-release` Skill：py_compile → **全量单测** → `verify_scene_canvas.py`(54) → `verify_scene_canvas_ui.mjs`(27) → `verify_engine_embed.py`(68) → `npm run build` → PyInstaller（项目 .venv）→ 最小 PATH 冷启动冒烟 → 前端产物 SHA-256 核对 → **在 `DocMind_BUILD.md` 追加记录（不新建文件）**；只白名单提交，`agent-golden-eval/` 不提交。
第 15–21 次均已执行（最新 `DocMind_BUILD.md` 第 21 次章节=2026-09-16 19:22 构建；exe 20,536,866 字节，SHA-256 `f67eac87…`）。
> 第 21 次的**流程偏离（已在 `DocMind_BUILD.md` 记录理由）**：未重跑 `verify_scene_canvas_ui.mjs`(27) 与 `verify_engine_embed.py`(68)——本轮改动面只有 `agent/api/config/llm/tools/sessions/pricing/agent_trace/gpu_coordinator/hooks/skills/vectorstore` 与前端 `api.ts`/`ChatDock.vue`/`ModelSettingsDialog.vue`，**未触碰** `scene_runtime.py` / `desktop_bridge.py` / `engine_adapters.py` / `game_workbench.py`，这两条验证路径的代码面不变；`verify_scene_canvas.py` 已实跑 **54/54**。
**第 19 次交付卡点（已解除，2026-09-15 20:43）**：用户确认 8000 实例本就不在（探活 HTTP 000），并清理了残留 python 进程（`C:\Users\<you>\.workbuddy\binaries\python\versions\3.13.12\python.exe` 与 `rag-agent\.venv\Scripts\python.exe`）。随后换入完成：源 `D:/Temp/docmind_rel19/DocMind/`（exe 19,814,751、SHA-256 `7c816242…`）→ `dist/DocMind`，**换后 exe SHA-256 与源一致（`7c816242…`）= 完整性校验通过**。注意：未用 `/MIR` 而用 `robocopy /E /XD .docmind`——目标 `dist/DocMind` 含**运行时目录 `.docmind/`**（预算/轨迹/gpu_state/chroma 指针），`/MIR` 会误删，故排除保护；仅镜像构建文件（exe + MinGit + _internal）。交付完成，无需重打包。
> 第 19 次的**两处流程偏离**（已在 `DocMind_BUILD.md` 记录理由）：① 未重跑 `npm run build`——本轮前端只有手写静态页 `web/trace.html`，`frontend/` 与 `web/assets/*` 零改动；② 未换入 `dist/DocMind`（同上卡点）。
> **黄金题门**：第 19 / 20 / 21 次均为 **SKIPPED**（本机无法稳定起「已索引本仓库代码库」的可评测服务；离线规则打分由 `tests/test_agent_eval.py` 守在 707 全量里），已如实留痕。

### 已知限制与后续项（2026-09-16 设计评审 / 缺陷排查产出）

1. **同会话「真并发」串开关/`llm` → 已修复（2026-09-16）**：机制是「后端每个 `session_id` 一个长驻 `Agent`，逐请求开关只保证串行」＋「`event_stream` 是同步生成器、由 Starlette `iterate_in_threadpool` 在**工作线程**消费 ⇒ 同会话两请求**真并行**」＋「前端 `askGrounded` 原不传 `session_id` ⇒ 各标签页共用 `default`」。**修法**：前端改为**每标签页一个 `session_id`**（`sessionStorage` 的 `docmind_session_id`，`getSessionId()`；`askGrounded` 以 **form 字段**带上、`contextApi.get()` 以 **query** 带上），**后端零改动、不加锁**（跨线程加锁不安全且会串行化流式响应）。
   **残余（已知限制）**：浏览器**「复制标签页」会把 `sessionStorage` 一并复制** ⇒ 副本与原标签页仍同 sid，两者同时提问仍会命中竞态；「新开标签页 / 手输 URL」这条常见路径已修好。彻底覆盖需让 id 参与 `window.name` 或 per-page-load 随机量（各浏览器对"复制标签页"是否继承 `window.name` 行为不一、收益窄），**暂不做**。
   **语义变更（刻意）**：不同标签页现在是**各自独立**的会话（不共享历史/上下文）；同一标签页刷新仍保留；此前累积在 `default` 会话里的历史**不再被前端使用**（文件仍在磁盘 `STATE_ROOT/.docmind_sessions/`，可 `DELETE /api/sessions/default` 清理）。
   【验证】`verify_scene_canvas_ui.mjs` **27/27**、`verify_engine_embed.py` **68/68**、独立 QA 复验 **PASS**（含「同 id 负向对照：历史递增=共享」「未用 id → `active:false`」「无参 `/api/context` 向后兼容」）。
2. **运行时状态根尚未覆盖「项目级」文件**（P2 残留）：`game_workbench` 的 `.docmind_comfy.json` / `.docmind_tasks.jsonl` / `.docmind_engine.*`、`semantic_tags` 的 `<root>/.docmind/semantic_tags.json`、`secrets_store` 的 `.docmind_secrets.json` / `.docmind_secret.key`、`agent_policy` 的 `.docmind_permissions.jsonl` / `.docmind_external_approvals.jsonl`、`mcp_client` 的 `.docmind_mcp.json`、`scene_runtime` 的 `.docmind_runtime.*`、`web_export` 的 `<root>/.docmind/web`、`regions` 的 `.docmind_backups` 仍按 `code_root` / `BASE_DIR` 落盘（本轮按「用户项目内容不搬」原则未动）。当前测试不会污染仓库根（已有断言），若要让测试**完全**隔离需逐个迁移。
3. **仓库 `.env` 的 `CHROMA_DIR=./.chroma`**（未跟踪的用户文件）：**非隔离**进程仍把该相对路径解析到 cwd；隔离进程（测试）已改为落 `STATE_ROOT`。若想让 dev 也走统一口径，把该键从 `.env` 删掉即可（默认值派生自 `STATE_ROOT`，dev 下等价）。
4. **`api.py::selection_ai_ep`** 内**嵌套同步生成器**里的 `client = LLMClient()` 未包线程池（D2 范围外，保持原样）：该生成器若将来在事件循环线程被迭代，同样有同步探活阻塞风险。
5. **`read_file` 之外的其它工具失败文案**：本轮已把搜索 / 网页读取 / 文件读取三类补进 `_FAILURE_MARKERS` 并加表驱动测试；若后续新增工具返回新的失败文案，需同步加进该白名单（表驱动测试会反查漏项）。

### harness 能力层（`ab6e253` 运维四件套 + `ce7ff77` 能力五件套 + 并行化 已完成；下列为剩余项）

已补齐十项：逐轮 trace/token 账本、会话隔离+持久化+摘要、LLM 重试/退避/deadline/断连中止、黄金题自动打分+回归门、原生 function-calling、子代理+plan、hooks/技能热插拔、按 provider 计价+预算熔断、golden 门入冻结流水线、**并行工具批次 + 子代理并行扇出**（只读工具并发、结果保序、写/副作用工具自动退回顺序、子代理各持独立 `LLMClient`）。

**仍缺（未实现，不得宣称）**：
- **不能回滚已执行的任务**：回溯只能改**尚未执行**的计划（add / drop / replace）；已经跑过、已产生副作用（比如改过文件）的任务**不会**被撤销或重跑——这是有意的安全边界，不是能力缺失，但也不得对外宣称"可回滚"。
- **执行轨迹是"有界摘要"**：喂给 `replanner` 的轨迹只保留最近 `DOCMIND_ORCH_TRACE_STEPS`（默认 6）步、每步观察截断到 `DOCMIND_ORCH_TRACE_OBS_CHARS`（默认 240 字），且不含子代理的完整原文/原始工具输出——超长轨迹与原文只能去 `.docmind_traces.jsonl` 里按 `turn_id` 查。
- **replanner 看不到父代理自己的回合轨迹**：它拿到的是各子任务的轨迹摘要，不含主代理这一轮的选择过程。
- **结论可信度校验**：`synth` 只做**合成与冲突标注**，不会独立复核某个子任务的结论是否属实（没有 fact-check 环节）。
- **批次内去重与依赖排序 / 成本感知调度 / 每分钟限流 / 跨重启日配额强一致** 已于 2026-09-15 落地（见 §4 `1709486` 之后的 harness 增强提交）：`run_plan` 新增 `dedup_tasks`（按 role+归一化任务去重并依赖重路由）、每波前 `pricing.check` + `vram_provider` 预算/显存感知停波、`replanner` 第四参 ctx 透传 `{budget,vram_free,attempt}`；`pricing` 改跨进程文件锁强一致 + 每分钟调用/费用限额。下列边界仍成立：
- **hooks 无沙箱**：钩子是以 `importlib` 在服务进程内直接加载的 `.py`，权限等同本服务本身（只适合可信本地代码）。
- **技能仅是提示词注入**：`SKILL.md` 只提供指引文本，不携带可执行脚本或权限声明，也不参与工具白名单。
- **成本熔断粒度（仍缺）**：已按 provider/model 单价 + 会话/全局累计 + 每分钟限流 + 跨重启日配额强一致；**仍**未做「按工具计费」（无法区分同一次 LLM 调用里不同子任务的花费分摊）——这是有意简化，不得宣称可按工具出账。
- **golden 门依赖本地模型**：无本地模型时优雅 SKIP；CI 上常驻的只有离线规则打分（`tests/test_agent_eval.py`）。

### 其他已记录的改进点

- **B 档浅实现（2026-09-11 审计结论，已于 2026-09-14–09-15 逐项做深，原「仍有效」标记已失效）**：六项中五项在 2026-09-14 已深化——`impact_analysis` 改语义检索优先（bge-m3 向量，子串 grep 仅兜底）、`generate_test_scene` 改真实 AST 符号 + 可运行测试骨架、`performance_sample` 改 cProfile 真实剖析、`approval` 升级为 TTL 门禁（`is_approved`）、默认分区 `verify_contracts` 做环检测/依赖存在/导出文件校验；最后一项 `simulate_growth`（原等比数列玩具）于 **2026-09-15 深化**为 geometric/linear/logistic S 形/diminishing 四模型 + 摘要统计（翻倍数/峰值增量/拐点等级），`/api/simulate_growth` 与 `game_simulate` 工具同步加 `model`/`k`，新增 `tests/test_simulate_growth.py` 10 例全绿。**B 档清单已清空**。
- **门面文档**：`README.md` 已于 2026-09-14 刷新（反映工作台/分区/引擎/画布现状）；`README_en.md` 与 `DEMO.md` 已于 2026-09-15 按当前形态重写（工作台六件事 / 分区 / 场景画布 / 运行时时间线 / 引擎嵌入 / GPU 协调 / ComfyUI·Unity·Unreal 适配 / 联网研究，测试数同步为 298/298），见 §4 时间线。
- 新落地的 MCP bridge 与 Web player 的产品级使用文档与边界说明**已补**：`docs/integrations.md`（2026-09-15，覆盖配置模型、API 表面、使用前提、已知边界）。
- **引擎嵌入的残留**（不影响"已可用"）：本机显示器当前是 **150% 缩放**，100%/125% 未实测——
  `verify_engine_embed.py` 会打印当前 DPI 并按实际坐标断言，改了缩放直接重跑即可补档。
- **P2-6 编辑即热重载（基础能力已落地，自动监听仍是后续项）**：`POST /api/engine/reload` 与运行台按钮已实现。Web 端使用进程内 `reload_current_scene()`；桌面原生端采用可恢复嵌入参数的快速进程重启，游戏内存状态会重置。
  - 【当前未完成】没有默认的 `.gd/.tscn/.shader` 文件监听，也没有“检测到外部修改后询问是否重载”的确认流程；桌面原生端也没有跨版本、无需插件的通用进程内脚本替换协议。
  - 【后续方案】增加文件监听器和显式确认提示；若要做到真正进程内重载，先确认目标 Godot 版本与插件 RPC，再单独设计协议和回归测试。
  - 【验收】当前基础热重载已由 `tests/test_engine_gpu_lease.py` 和 `/api/engine/reload` 路由测试覆盖；真实桌面 Godot 运行、修改文件后重载、PID/嵌入/GPU 租约闭环仍需本机实测。
- **桌面壳内的 UI 自动化没做成**（不是没做，是做不了）：pywebview 的 `evaluate_js` 在 WebView2 上不稳定
  （实测第二次调用耗 15.7s 且返回 None），拿它当断言基础会得到假失败。目前覆盖方式是
  「浏览器冒烟记 UI 降级 + `verify_engine_embed.py` 记后端契约（含 UI 的实际调用路径）」两段拼起来，
  中间那层"真桌面壳里点一下"由**人工三步验收**兜：
  桌面端启动 → 试玩器 → 勾「嵌入工作台」点「桌面窗口启动」→ 画面应出现在弹窗中。
- **场景画布尚未支持的能力**（刻意留给后续，不是 bug）：Unity `.unity/.prefab` 的 GameObject 层级画布（P1-2 已完成的是资产级 GUID 引用图，不是对象级场景树）、节点属性引用边（`node_paths=PackedStringArray`）的自动跟随改写（改名/换父时只改 `parent` 前缀，NodePath 属性需人工核对）、多场景同时打开、画布上的 Undo/Redo 跨会话持久化。

---

## 6. 画布方案决策记录（spike → 转正，2026-09-14）

**spike 已随 P0-2 转正而移除**：删除了 `frontend/spike-canvas.html`、`frontend/src/spike/`（5 个文件）、
`vite.config.ts` 的 spike 入口、`web/spike-canvas.html` 旧产物，并 `npm uninstall @vue-flow/node-resizer`
（转正后的画布不提供节点缩放）。spike 当年验证过的 4 条结论仍然有效，一并留档：

1. Vue Flow 的 `extent:'parent'` 能把子节点钳在父容器内（左拖 420px 停在区域左缘）；
2. 拖父容器时子节点整体跟随（deltaX 全部一致）；
3. handle 拖拽连线可用，跨容器虚线渲染正确；
4. 滚轮缩放 / 空白平移 / MiniMap 正常。

**为什么最终没有沿用「容器嵌套」建模**（转正时改成了 **扁平节点 + 四类边**，这是本次最重要的设计取舍）：

- 容器嵌套表达的是「归属」，到了场景树上就退化成「父节点=容器」。三层以上嵌套时尺寸传播、
  `extent` 钳制、`fitView` 都很脆，而真实 .tscn 深度动辄 4–6 层；
- 场景开发真正高频操作的是**空间关系**（position / transform），容器嵌套根本表达不了
  "这几个东西在画布上应该画在哪"；
- 扁平模型下，`position` 可以直接当画布坐标用（见 `SceneCanvas.vue` 的「空间布局」），
  拖拽即可回写 `position`；层级关系用边表达，信息密度反而更高。
- 代价：失去了"拖父节点带动整棵子树"的手感。用检查器的「换父节点」下拉 + 层级布局弥补。

**spike 的 4 个坑仍然适用**（已固化在 `SceneCanvas.vue`）：
节点尺寸用顶层 `width/height`（放 `style` 会被重置）、子节点字段叫 `parentNode`、
附属件从独立包导入、v1 交互层是 d3（合成自动化事件只认 mouse 事件，pointer 无效）。

---

## 7. 运行环境与常用命令

- 路径含单引号用户名 `C:\Users\<you>\...`：shell 一律**双引号**包裹；服务地址用 `127.0.0.1` 不用 localhost；沙箱内 curl 加 `--noproxy '*'`。
- venv：`.venv\Scripts\python.exe`（已装全部依赖；打包必须用它，托管 python 缺 webview/chromadb 会出坏 exe）。

```powershell
# 后端开发
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
# 工作台 http://127.0.0.1:8000/workbench/ ；spike http://127.0.0.1:5173/spike-canvas.html

# 全量测试 / 语法检查
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
.\.venv\Scripts\python.exe -m py_compile api.py game_workbench.py desktop.py desktop_bridge.py scene_runtime.py gpu_coordinator.py mcp_client.py web_export.py

# 前端（frontend/ 目录）
npm run dev      # vite dev server :5173
npm run build    # 产物输出 ../web，emptyOutDir:false

# 引擎嵌入：实机自检（真 Godot + 真 Win32 宿主；会短暂弹窗并把鼠标移回原处）
.\.venv\Scripts\python.exe verify_engine_embed.py
.\.venv\Scripts\python.exe verify_engine_embed.py --godot "D:\Tools\Godot\Godot_v4.7.2-stable_win64.exe" --keep

# 场景画布：后端自检（进程内起 FastAPI + 临时 Godot 工程，走真实路由，不打端口）
.\.venv\Scripts\python.exe verify_scene_canvas.py

# 场景画布：浏览器冒烟（需另开一个终端先跑 `--serve`，见脚本头注释）
.\.venv\Scripts\python.exe verify_scene_canvas.py --serve 8011
node verify_scene_canvas_ui.mjs http://127.0.0.1:8011
# 截图落在 docs/screenshots/{scene-canvas,runtime-timeline}.png

# 冻结打包（仓库根，发布前先读 .trae/skills/docmind-frozen-release/SKILL.md）
.\.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm
```

---

## 8. 必看的坑（2026-09-14 仍有效）

1. **单实例保护会 re-attach 旧进程**：旧 `DocMind.exe` 不杀，双击永远看到旧服务。验证跑的是哪版看 `GET /api/config` 的 `build_time`。
2. **PyInstaller onedir 漏拷 exe**：必要时手动 `cp build/docmind/DocMind.exe dist/DocMind/DocMind.exe`；批量删旧 dist 会被沙箱守卫拦截，改名移走替代删除。
3. **onedir 无 `sys._MEIPASS`**：config.py 资源定位必须处理 `_internal/` 分支，否则挪走 dist 即失效。
4. **切 embedding 维度必须重置两个集合**（docmind + docmind_code），否则 256/1024 维冲突报错。
5. **mock provider 不会真正调代码工具**；测代码/画布用 ollama/qwen/llamacpp。Ollama 离线时嵌入降级 local（随机向量，检索无意义）。
6. **llama.cpp 默认 ctx 4096**：RAG 长上下文报 400，启动加 `--ctx-size 8192`。
7. **分区 git 需本地提交身份**：init_regions 已自动 `git config user.email/user.name`，手工建区要自行设置。
8. **新增工具三步**：tools.py 函数+TOOLS 注册 → agent.py SYSTEM_PROMPT 选择指引 → 需要时 api.py 加端点。
9. **AI 写操作铁律**：必须带任务范围、保留 Git 快照；只读契约名单文件禁止改写；前端 AI 改写不自动落盘（Ctrl+S + 409/422 护栏 + 陈旧坐标检测）。
10. **后台任务 failed 可能是假警报**：外壳被回收但子进程继续，只信 curl 探活。
    （反例：本机 8000 端口常驻着你自己的 DocMind 实例，起验证服务请换端口，别去抢占/杀它。）

### 前端/画布相关的坑（2026-09-14 新增，都是真踩过的）

11. **Vue Flow 的样式要手动 import**：`@vue-flow/core/dist/style.css` + `theme-default.css`、
    controls/minimap 各自一份。漏了**不报错**，只是 `.vue-flow__node` 退化成 `position:static`，
    节点在画布里堆成一列——数量、连线、点击全都"正常"，只有位置是错的。改画布样式后务必看一眼截图。
12. **`manualChunks` 切 @vue-flow 会让它的 CSS 永远不加载**：CSS 被切进独立 chunk，而动态加载
    异步组件时 Vite 只 link 异步入口自己的 CSS。`@vue-flow` 也不要写进 `@vue` 那条规则
    （路径里含 `"@vue"` 会被一起捞走，白送 218KB 进首屏）。现有规则里它显式 return undefined，
    跟画布同批加载。
13. **Vue Flow v1 的事件载荷是单个对象** `{ event, node, nodes, … }`，不是 `(event, node)` 两个参数。
    写成 `(_e, n) => n.id` 会在**点击节点时**才抛 `Cannot read properties of undefined`。参照
    `SceneCanvas.vue` 的 `pickNode()`，两种形态都兜住。
14. **.tscn 里空行归属于"上一个块"**：解析时算进上一个块的 span。所以任何"顺手把连续空行收敛一下"
    的清理都会破坏「删掉再插回来 = 逐字节还原」。撤销栈依赖这条不变量（有回归用例守着）。
    同理，从文件里搬过来的块（reparent/duplicate）自带尾随空行，`_insert_block` 不能无条件再补一条。
15. **undo 必须与接口请求体同形**：曾经 undo 用 `props`、接口用 `properties`，前端原样回传被当成
    空 payload 静默拒掉——撤销按钮看着正常，实际什么都没发生。`properties` 里对**被删除**的属性
    要写回旧值（只记 `remove` 等于没撤销），配 `order` 保留原位。
16. **测试用例之间别共用 fixture 状态**：用例顺序执行时，前一个用例改名/删节点会让后一个用例引用的
    路径失效，报出来的错还很误导。每个用例从同一份原始场景重新写盘。
17. **`getBoundingClientRect` 量不出"节点位置对不对"之前，先确认 CSS 加载了**：第 11 条的现场就是
    靠"矩形差值 ÷ 布局坐标差值"反推出来的——比例不是常数就说明有东西没生效。
18. **引擎嵌入的坑（2026-09-14 实机踩齐）**
    - `EnumWindows` 只枚举**顶层**窗口。引擎一旦嵌进去就变成子窗口，再 `find_window` 永远找不到 ——
      "二次嵌入/重新定位"必须复用已保存的子窗口句柄，否则会误报"引擎窗口没出现"。
    - 宿主 resize 不能用**窗口外框**尺寸给子窗口定尺寸（外框含标题栏与边框，会把画面裁掉一截）；
      要用 `GetClientRect`。150% 缩放下差异更明显。
    - 后台进程 `SetForegroundWindow` 会被系统拒绝；要先把本线程 `AttachThreadInput` 到**当前前台线程**再设，
      否则合成/真实输入都不会到目标窗口。
    - `keybd_event` 没有返回值，无法区分"没插进去"和"插进去了但没送达"。用 `SendInput`（返回实际插入条数）。
    - **Godot 的 stdout 重定向到文件时是块缓冲**：事件明明发生了，日志里要等很久才可见。
      只读 stdout 做输入断言会得到"功能没生效"的假失败。自检探针要**自己 flush 一份事件文件**，
      并且**跑完统一核对**，不要和缓冲抢时间。
    - `PrintWindow` 在 **user32**（不在 gdi32）；而且它抓不到 Vulkan 内容（GPU 合成），
      截图能证明几何但不能证明渲染画面。
19. **pywebview 的坑（2026-09-14 想做桌面壳内 UI 自动化时踩的）**
    - `webview.start(func, args)` 的 `args` **必须是元组**：写成 `start(drive, win)` 时 `win` 被当成 `args`，
      `func(*args)` 抛 `missing 1 required positional argument`，窗口留在屏幕上没人管。
    - **`evaluate_js` 在 WebView2 上不稳定**：实测第一次 2.5s 返回正常，第二次 15.7s 且返回 `None`。
      拿它做轮询断言会得到"功能没生效"的假失败。要驱动页面就用 playwright 走 CDP，别用它。
    - 页面 ready 之前调 `evaluate_js` 会把事件线程卡住（窗口一直白屏、脚本也不前进）。
      即便只是自检脚本，也要挂 `loaded` 事件 + 看门狗 `win.destroy()`，别把白窗口留在用户屏幕上。
    - 自检脚本里用临时工程起引擎时，**别把启动代码写到 `finally` 之后**——临时目录那时已被删，
      `Popen(cwd=...)` 会抛 `FileNotFoundError`，报出来的却是"找不到引擎可执行文件"，极具误导性。
21. **批处理文件（.bat）必须存为 ANSI/GBK 并显式 `chcp 936`**（2026-09-14 实机踩）：
    cmd 按**当前代码页**解析 .bat 字节。文件若按 UTF-8 保存、系统默认代码页是 936，
    中文注释会被误读成乱码，乱码的“前导字节”还会**吃掉行尾 CRLF、甚至吃掉下一行开头的字符**，
    于是报出这些完全指不到根因的错：
    `'op.py' 不是内部或外部命令`（`desktop.py` 被从中间切开）、
    `'鍙屽紩鍙峰寘瑁癸紙~dp0"' 不是内部或外部命令`（中文乱码后 `%` 被吃掉）。
    **两条硬规则**：① 会执行的命令行只能是纯 ASCII（中文只放 REM/echo 文本里，最坏只是显示乱码）；
    ② 含中文就写 `chcp 936` 并按 ANSI/GBK 另存。`tests/test_launchers.py` 守着这两条。
22. **用外层脚本工具往 Python 里写代码时，换行可能被吞成字面量转义**：
    曾出现整个函数被压成一行、用 `` `r`n `` 当换行写进 `game_workbench.py` → `SyntaxError`，
    应用直接起不来。**改完 .py 必须 `py_compile` 一次**；写完带反斜杠转义的字符串（如 `
`）
    尤其要回读文件确认，别只看脚本“执行成功”。自检脚本里写文件也同理，用原始字符串更稳。
24. **打包版与源码版是两套互不相通的本地状态**（2026-09-14 用户踩，以为功能丢了）：
    `STATE_FILE = BASE_DIR/.docmind_state.json`，而 `BASE_DIR` 冻结时是 `dist/DocMind/_internal`、
    源码时是仓库根。同理 `.chroma` 索引、上传目录都各自一套。
    于是「用打包版打开 → 工作台报『未配置代码库根目录』」不是功能坏了，是**换了个从没配过的实例**。
    判断方法：看日志落在哪（`docmind_desktop.log` 在仓库根 = 源码版；在 `dist/DocMind/` = 打包版）。
    别把源码树的状态文件塞进包里当默认值——里面是绝对路径，换机器就失效。
25. **浏览器默认会请求 `/favicon.ico`**：不接这条路由，每个页面都留一条 404，浏览器冒烟的
    "无失败请求"断言永远红。图标走 `frontend/public/favicon.ico` → Vite 拷进 `web/` → 后端路由。

### 运行时状态 / 共享对象相关的坑（2026-09-16 设计评审后新增，都是真踩过的）

26. **导入期绝不做磁盘 I/O**（2026-09-16 血案，本轮已修）：`gpu_coordinator` 曾在**模块导入期**调
    `restore_runtime_state()` → `STATE_FILE.unlink()`。一旦删除被拦（沙箱守卫抛的是 `SystemExit`，
    属 `BaseException`），应用层 `try/except Exception` **兜不住**，会连锁炸掉
    `gpu_coordinator → llm → agent` 的 import。现场症状：46 个测试模块 `ImportError`、打包中途死掉，
    且**看不出原因**。约定：**导入期只做纯定义**；恢复状态用显式 `init()`（由 `api.py` lifespan 在
    `gpu.configure()` 之前调用）；建目录用惰性 `ensure_dirs()`；`_apply_persisted_state()` 也在 lifespan 里调。
27. **跑测试不要往仓库根写运行时状态**（2026-09-16）：`config.STATE_ROOT`（`DOCMIND_STATE_ROOT` 优先）
    + `state_path()` 统一派生 10 项状态；**测试进程**默认隔离到 `tempfile.mkdtemp()`。三个易踩点：
    ① 判据**不能**用 `"unittest" in sys.modules`（任何 `import unittest` 的进程都会被误判，把用户状态
    搬到临时目录、看起来像「状态丢了」）——正确判据是 `sys.modules["__main__"].__spec__.name == "unittest.__main__"`
    （仅 `python -m unittest` 命中；`-c`/`script.py` 下 `__spec__` 为 `None`）；
    ② 隔离生效时**相对路径** env 覆盖的解析基准是 `STATE_ROOT` 而非 cwd（否则 `.env` 里
    `CHROMA_DIR=./.chroma` 仍会写仓库根）；③ 逃生阀 `DOCMIND_NO_TEST_ISOLATION=1`。
28. **同一份「状态」不要写两套 schema**（2026-09-16）：GPU 协调器的**租约快照**与**采样样本**曾写同一个
    `STATE_FILE`，5s 一次的采样线程会把租约冲掉 → 崩溃恢复**静默失效**（无报错、无异常，只是恢复永远不生效）。
    现在拆成 `gpu_state.json`（leases/queue）与 `gpu_samples.json`（samples）。凡「后台定时写」与
    「事件驱动写」共用一个文件的地方，都要拆开。
29. **共享会话 Agent 上的可变属性必须按请求注入并还原**（2026-09-16）：`api.py` 每个 session 一个长驻
    `Agent`，逐请求开关（`web_enabled`/`thinking_enabled`/`tool_mode`/`plan_mode`/`llm`）一律经
    `Agent.run(...)` 的**仅关键字参数**传入，由 `run()` 开头快照 + `finally` 还原（正常/异常/`close()`/断连四出口）；
    **不要**在调用方就地改共享单例属性。同理：云端路由**不要**新建并注册 `Agent` 到 `_SESSION_AGENTS`
    —— 会让模块级 `agent` 沦为孤儿（`set_config` 换模型 / `reset_code` / `/api/ingest` 对活跃会话失效）、
    且该会话此后「黏」云端而路由事件仍报 `local`。
30. **失败判定靠文案白名单，新增工具必须同步**（2026-09-16）：`agent._FAILURE_MARKERS` 决定一次工具观察
    是否算失败（进而决定是否触发 Reflection、trace 的 `ok`）。工具返回新的失败文案而没进白名单，就会
    **失败被当成功**（静默）。本轮已补「搜索失败 / 搜索未返回结果 / 网页读取失败 / 读取失败 / 文件不存在 / 拒绝访问」，
    并有表驱动测试 `tests/test_failure_markers.py` 反查漏项——新增失败文案时先跑它。
31. **把「导入期副作用」改成「lifespan 显式调用」时，必须检查顺序反转**（2026-09-16 真踩）：`_apply_persisted_state()`
    原本在 `import config` 时执行——那时 `_RUNTIME` 还空，所以调用方（`verify_scene_canvas.py --serve`、
    `verify_engine_embed.py`、`verify_regions.py`）在其**之后** `set_runtime('code_root', <临时工程>)` 能覆盖；
    移到 lifespan 后调用方变成「先设」、持久化变成「后设」→ **持久化把临时工程顶掉**，画布/场景图全空
    （症状是冒烟**超时**而不是报错）。**约定**：启动期的持久化恢复一律「**显式设置优先**」——
    `if "code_root" not in _RUNTIME: _RUNTIME["code_root"] = root`。`desktop.py`/`run.py` 不预设 code_root，
    所以用户正常启动不受影响——**别以为"用户没报错"就说明这个顺序没问题**。
32. **校验脚本的 code_root 语义 × TestClient 是否触发 lifespan**（2026-09-16）：`verify_scene_canvas.py --serve`
    是「先 `set_runtime('code_root', 临时 Godot 工程)` 再 `uvicorn.run`」，所以它**依赖**上面第 31 条；
    而 `verify_scene_canvas.py` 的非 `--serve` 路径与 `verify_engine_embed.py` 用 `TestClient(app)` 但**不进 `with`**，
    因此**不触发 lifespan**、不受影响——这正是「单跑 54/54 全过、`--serve` 路径却已经坏了」的原因。
    改 lifespan 里任何东西后，**两个路径都要各跑一次**。

---

## 9. 仓库内文档地图

| 文档 | 用途（不要删） |
|---|---|
| `HANDOFF.md`（本文件） | 唯一权威交接：原因/基线/待办/坑，每次里程碑后更新 |
| `DocMind_BUILD.md` | 14 次冻结构建档案；发布 Skill 强制在其中追加，不新建 |
| `分区开发设计.md` | 分区 2.0 架构设计，`regions.py` 注释引用 |
| `game_project_template.md` | 给用户游戏工程（code_root）使用的目录骨架与 DOCMIND_RULES 模板 |
| `README.md` / `README_en.md` / `DEMO.md` | 对外门面/演示（内容偏旧，待按 §5 重写，勿当现状依据） |
| `docs/integrations.md` | MCP 引擎桥接与 Web 试玩导出的产品级使用文档与边界说明（2026-09-14 新增，README/README_en 已引用） |
| `.trae/skills/*/SKILL.md` | 发布、引擎装配/适配、HWND 嵌入的操作规范 |
| `verify_scene_canvas.py` | 场景画布后端自检（50 项，走真实 HTTP 路由）；`--serve` 模式可开一个指向临时 Godot 工程的演示服务 |
| `verify_scene_canvas_ui.mjs` | 场景画布浏览器冒烟（23 项，Playwright + 系统 Edge/Chrome）；截图产物在 `docs/screenshots/` |
| `verify_engine_embed.py` | 引擎嵌入实机自检（60 项）：真 Godot + ctypes 真 Win32 宿主，覆盖置父/几何/resize/真实合成键鼠/解绑还原/无孤儿/DPI |
| `sample_docs/docmind_product.md`、`uploads/*` | 产品资料与上传件（uploads 不进版本控制） |

---

## 10. 文档整理变更记录

**2026-09-13**
- 新建本文件，整合 5 份旧交接/审计文档的全部仍有效信息（待办按 622fdbc 后代码重新核对状态）。
- 删除：旧 `HANDOFF.md`（2026-09-09 版）、`AI_BRIEF.md`、`DEV_WORKBENCH_AUDIT.md`、`HANDOFF_ENGINE_EMBEDDING.md`、`HANDOFF_REMAINING_WORK.md`。
- 同步引用：`.trae/skills/engine-project-setup/SKILL.md` 改指本文件；`分区开发设计.md` 中原指向旧 HANDOFF §6 的引用更新为本文件 §5/§8。

**2026-09-14（P0-2 + P1-1 落地）**
- §2 基线：测试数 162 → 202；前端入口由 MPA 收敛回单入口（spike 移除）；补两条自检脚本作为新验证基线。
- §3：`scene_runtime.py` 标注重写后的真实职责与规模；前端组件表补画布/时间线 4 个组件与 `sceneApi`。
- §4：新增 2026-09-14 时间线行。
- §5：**删除已完成的 P0-2、P1-1**（铁律：完成项移出待办）；补"场景画布刻意未支持的能力"清单，避免下一手误判为 bug。
- §6：由"spike 现状与去留决策"改写为"画布方案决策记录"，说明为什么最终用扁平节点+四类边而不是容器嵌套（spike 4 条结论与 4 个坑保留留档）。
- §8：新增第 11–18 条前端/画布坑（Vue Flow 样式与 manualChunks、事件载荷、tscn 空行归属、undo 同形、fixture 隔离、favicon）。
- §9：补两条验证脚本与 `docs/screenshots/` 的归属说明。

**2026-09-14 追加（P0-1 实机闭环）**
- §2：新增"引擎嵌入"条目，写明实测环境与结论（含 150% DPI 实测数据）。
- §4：新增 P0-1 时间线行。
- §5：**删除已完成的 P0-1**；把"100%/125% 未实测"与"前端自动嵌入开关未接线"作为残留写进其他改进点（铁律：未做实的不写成已完成）。
- §7：补 `verify_engine_embed.py` 运行方式。§8：新增第 18 条引擎嵌入坑（6 个子项）。§9：补脚本归属。

**2026-09-14 追加（Agent 模型路由与权限，多轮累积）**
- ReAct 工具表加入 `dev_mcp_call`：连接器启用检查 + 可选 task_id 绑定 + 参数路径越权（region/allowed_paths）校验，失败转可审计文本不静默执行。
- 云端密钥：`/api/agent/secrets`（GET 仅返回 Provider 名称 / DELETE 撤销轮换，明文不落盘），DPAPI/Fernet 往返有回归测试。
- 外部权限：审批创建/批准、approval_id 精确路径授权、`redact_for_cloud` 上云前脱敏与上下文截断、`/api/agent/external-write` 实际写入执行器（approved + 精确路径 + 写前 `.docmind.bak`）、审批 before/after unified diff 与 `AgentPolicyPanel` 批准/拒绝 UI。
- 上述过程测试数 205 → 207 递增；与 RAG 收紧轮合计后全量 **223 项**。

**2026-09-14 接手校准**
- 基线提交更新为 `eeb73ab`；核对全量测试 **223/223**，修正 §2 旧数字（202/50/23 → 223/54/27，补 68 项引擎实机）。
- Agent 路由与权限段由"待办"改写为"已落地 + 剩余非阻塞项（连接器启停 UI、ReAct 自主切换策略层）"。
- 核证第 17 次冻结构建交付卡点已解除（exe mtime 20:56 已在 `dist/DocMind`，旧实例与临时目录均已清）。

**2026-09-14 追加（P1-2 Unity GUID 引用图，`6e9b95b`）**
- 新增 `unity_graph.py`（meta GUID 索引 + 序列化文本引用解析 + 冲突/孤儿/缺失统计）、`GET /api/unity/guid-graph`（run_in_threadpool）、前端 `UnityGraph.vue` 与顶栏「Unity 图」入口、`tests/test_unity_graph.py`（4 例）。
- 口径决策：工程 Assets 内解析不到的 guid 一律标"缺失/外部"且默认折叠——Packages 包资源与真断裂引用在无 Library/PackageCache 时无法区分，不夸大为错误；GUID 冲突单独红色统计（Unity 中属致命问题）。
- 全量测试 223 → **227**；§3.1 补模块行，§5 P1-2 改为"GUID 图已落地 + 编辑器联机待装 Unity"，§4 补时间线行（含 981b116 校准行）。合成 Unity 工程经真浏览器冒烟：图渲染/缺失展开/详情侧栏出入边/搜索高亮/类型 chips 过滤全过。

**2026-09-14 追加（P2-1 GPU 协调补完，`6e9b95b`）**
- `gpu_coordinator.py` 重写：serial/parallel/multi 三模式、`acquire_lease` 结构化结果、严格 FIFO（修掉 multi 下新请求绕过队列占空闲卡的漏洞）、显存门槛拒绝不排队、`reown/force_release(owner)/cancel_wait`、驱逐钩子（锁外、一次）、采样环、TTL pump、Ollama 空闲卸载；nvidia-smi 探测一律移到锁外，避免 2s 子进程卡住条件变量。
- 接线：`api.py` lifespan 启停后台线程并注册 Ollama 卸载钩子（复用 `_ollama_ps`/`_ollama_keep_alive`），新增 `/api/gpu/cancel|force-release|configure` 与 `/api/comfy/cancel`；`config.py` 新增 `load_state` 并恢复 gpu 两项偏好；`game_workbench.py` ComfyUI 租约改为覆盖完整生成周期；`llm.py`/`embeddings.py` 加 `note_activity("ollama")`。
- 前端：`GpuPanel.vue` + `gpuApi`（含 TS 类型），TaskEnginePanel 加「取消生成」。
- 新增 `tests/test_gpu_coordinator.py` 23 例（双卡假探测/严格 FIFO/取消/驱逐一次/门槛拒绝/reown/定向回收/空闲规则与冷却/采样环+后台线程/parallel/状态结构/假 HTTP ComfyUI 服务的 queue→history→释放与 cancel 生命周期/提交失败释放）；全量 **227 → 250**，`npm run build` 通过；真机 RTX 5070 Ti 浏览器冒烟 9 项全过（控制台仅有既有的导航中断请求噪声，非本次接口）。
- §5 P2-1 改为"主体已落地 + 三条未实测残留"（CUDA 隔离、物理多卡、空闲卸载端到端等待），§3.1/§3.2 补模块与组件行。

**2026-09-14 追加（P2-1 收尾硬化，`82d092c`）**
- 驱逐时机收窄：`acquire_lease` 只在 verdict=mem-deny（显存被无租约的外部驻留吃掉）时跑钩子；verdict=wait（有活跃持有者）时不跑——卸载不释放租约，重试必然失败，旧逻辑会让用户白付一次 22GB 级冷加载。
- `game_workbench.py` 新增 `DOCMIND_COMFY_MIN_FREE_MB`（默认 1024MB）并在 `comfy_queue` 传 `min_free_mb`，显存不足→驱逐 Ollama→重试授予/拒绝，门槛链路从 env 可选项变成默认生效。
- `query_gpus` 真实探测 0.5s TTL 缓存（注入探测永不缓存）；`_try_grant_locked` 返回真实 reentrant。
- **真机端到端抓 bug**：双模型（qwen3.6:35b + bge-m3）同驻时空闲卸载首次真机运行，bge-m3 的 `keep_alive=0` 在紧跟大模型卸载后 HTTP 成功但模型仍驻留；`_gpu_ollama_evict_hook` 改为卸载后等 0.8s 用新 `/api/ps` 对幸存者补一轮（最多两轮）。修复后真机复验 PASS（后台空闲触发→两模型全部清空）。
- 新增 8 例测试（wait 不触发驱逐、mem-deny 才触发、真实探测 TTL 缓存与注入不缓存、reentrant、ComfyUI 低显存拒绝与驱逐后授予、钩子两轮重试/无驻留不调用）；全量 **250 → 258**；§5 P2-1 残留只剩"物理多卡 + CUDA 隔离"两条纯硬件项。

**2026-09-14 追加（文档引用，`60afe5b` → 本次引用补完）**
- 新建 `docs/integrations.md`（MCP 引擎桥接 + Web 试玩导出的产品级使用文档与边界说明，提交 `60afe5b`）。
- 本次在 `README.md` / `README_en.md`（配套能力段 + 目录树 `mcp_client.py`/`web_export.py` 注释）与 `HANDOFF.md` §9 文档地图补 `docs/integrations.md` 引用，使其从"docs/ 下第一份孤立 .md"变为被三处引用。
- 提交：本引用补完（独立提交，仅文档引用变动）。

**2026-09-15 追加（连接器启停 UI，`mcp_client.py`+`api.py`+`ChatDock.vue`+`api.ts`+`tests/test_mcp_connector.py`）**
- 后端新增 `POST /api/mcp/close`（复用 `close_server`，仅关会话保留配置；HTTP 无状态服务无会话可关）与 `GET /api/mcp/status`（`active_servers` 返回当前有长驻会话的 key 列表，供前端反映真实连接态）。`mcp_client` 新增 `active_servers(root)`。
- 前端 `api.ts` 的 `mcpApi` 补 `close/status/save/remove`；`ChatDock.vue`「引擎」弹层每服务器加 **断开** 按钮（仅 stdio 且已连接可点）、连接态经 `status` 回填、并加 **新增/移除连接器** 表单（key/label/transport/command|url/enabled）。
- 修一个旧 bug：原模板用 `resultOf(s.key)`（传字符串）而 `resultOf` 接收 server 对象，导致「已连接 · N 工具」**永不显示**；统一改为传 `resultOf(s)`。
- 新增 `tests/test_mcp_connector.py` 6 例（active 空/有会话+close/未知 key 安全/status 形状/close 形状/save-remove 往返），全绿。**未实机**：`active` 仅在真实引擎（Godot/uvx）建立 stdio 会话后填入，本沙箱无引擎未跑该路径。
- **未跑 `npm run build`**（铁律：会重建 `web/assets` 打断 `:8000` 实例/对面前端写入者），前端编译待你或下个发布构建核对；改完后端用隔离端口 8078 真打 `status`(`active:[]`) 与 `close`(`ok,closed:false`) 验收。

**2026-09-15 追加（B 档浅实现收口，`simulate_growth` 深化）**
- `game_workbench.simulate_growth` 由纯等比数列玩具改为四模型数值曲线模拟器：`geometric`（原行为，向后兼容）/ `linear`（绝对步进）/ `logistic`（S 形，承载上限 K=base*20 或 `k` 入参，速率由 growth 映射）/ `diminishing`（边际递减凹函数，p=clamp((growth-1)*2,0.15,0.95)）；未知 model 兜底 geometric。返回 `{ok,model,values:[{level,value}],summary:{count,start,end,total,peak_increment,peak_increment_level,doubling_level?,inflection_level?}}`。
- `api.py` `/api/simulate_growth` 加 `model`/`k` 查询参数（直接返回函数 dict，不再二次包裹）；`tools.py` `game_simulate` 工具解析 `model`/`k` 并改写描述。无前端消费者，返回结构向后兼容（values 仍为 `{level,value}` 列表）。
- 新增 `tests/test_simulate_growth.py` 10 例（几何向后兼容/翻倍等级/线性/Logistic 有界单调/递减凹性/未知 model 兜底/levels 截断/base<=0 修正/汇总 total/端点形态），全绿；隔离端口 8078 真打三类模型业务结果正确（logistic 拐点 15.7 给出、几何仍可 100/108/116.64、线性 100/200/300 翻倍在 2 级）。
- HANDOFF §5 B 档审计「仍有效」标记校正为已逐项做深、B 档清单清空。

**2026-09-15 追加（连接器自主切换策略层）**
- `mcp_client` 新增连接器能力模型与路由策略：`capabilities_of`（由 `engine` + 显式 `capabilities` 推导能力标签，预设 godot/unity/unreal 注入 scene/editor/run/build/asset/script/export/debug）、`best_for_of`（各引擎适用说明）、`connector_directory(root)`（Agent 面向目录，含能力/适用说明/启用态，**不打开会话**）、`select_connector(root, hint)`（按任务语义打分排序：能力同义词 +2、引擎名 +3、label/help 命中 +1）。两个语义约束：①关键词用**子串扫描**匹配以兼容中文无空格分词；②任务点名某引擎（godot/unity/unreal）时**只在该引擎内选**，绝不把 Unity 任务误路由到 Godot。
- `tools.py` 新增三个只读 Agent 工具：`dev_list_connectors`（目录）、`dev_route_connector`（按 hint 选 top，返回排序候选+理由）、`dev_list_connector_tools`（某连接器工具清单，确定 name/参数）；`dev_mcp_call` 在连接器未启用/异常时追加「可调用 dev_route_connector 重新挑选」回退提示。三者均为只读，不进 `_NO_PARALLEL_TOOLS`。
- `agent.py` `SYSTEM_PROMPT` 改写连接器指引，接入「发现→路由→列工具→调用→切换」闭环（原仅一句 `dev_mcp_call` 提示且让 Agent 自行读工具清单，无运行时发现/路由手段）。
- `api.py` 新增 `GET /api/agent/connector-route`（包装 `select_connector`，供前端复用与真机核对）；`/api/agent/connectors` 返回追加 `capabilities`/`best_for`（additive，向后兼容）。
- 新增 `tests/test_connector_routing.py` 13 例（能力推导/目录启用态/排序/引擎专指过滤/unity 启用场景/`dev_*` 工具/端点形态），全绿；隔离端口 8079 真打 `connector-route`（`Godot 场景`→godot score 9、`Unity 构建`→[]）与 `connectors` 富化字段。**未实机**：真实 stdio 引擎（godot/uvx）的连接/工具调用路径本沙箱无引擎未跑，路由策略纯配置读取已离线覆盖。

**2026-09-15 追加（harness 成本/并行增强）**
- `orchestrator.run_plan` 新增三项能力：① **批次内去重** `dedup_tasks(tasks)` —— 按 `(role, 归一化任务)` 判定重复，保留拓扑波中最靠前的副本、后续副本标记 `deduped` 并把其下游 `depends_on` **传递重路由**到保留副本（发出 `dedup` 事件）；② **成本/显存感知调度** —— 每波前 `pricing.check(budget_session)`（仅 `cost_aware=True` 时）预算用尽则停该波并标记 `budget_blocked`、发 `budget` 事件，`vram_provider()` 探测显存自由量随波透传；③ **replanner 第四参 ctx** —— `inspect` 探测 4 参签名后透传 `{budget, vram_free, attempt, cost_aware}`。`run_plan` 新参数 `budget_session/vram_provider/cost_aware` **默认保持向后兼容**（cost_aware 默认 False，不改动既有调用方行为）。
- `pricing` 强一致 + 每分钟限流：进程内 `threading.Lock` 换成**跨进程文件锁**（`O_EXCL` 原子锁文件 + `os.replace` 临时写，硬上限 5s 防卡死、3s 孤儿锁清理），`charge/set_limit/reset/check/status` 跨进程 RMW 不丢更新；新增 `per_minute` 调用/费用限额（分钟桶 `_minute_key`，跨分钟自动重置）+ `rate_check()` 预检、`set_rate_limit()` 配置、`charge()` 超限拒绝并标记 `rate_limited`；`status()` 暴露 `per_minute_calls_limit/cost_limit/calls_this_minute/rate_limited`。
- `agent.py` `orchestrate` 调用处显式 `cost_aware=True` + `budget_session=self.session_id` + `vram_provider=(lambda: _gpu.memory_info().free_mb)`（防御式 `import gpu_coordinator`）；每回合预算熔断门（既有的 per-turn `pricing.check`）保留，编排层为额外的安全网。
- `api.py` `/api/budget` 增 `per_minute_calls`/`per_minute_cost`（与 `limit_cny`/`reset` 可组合），返回 `rate` 块；`BudgetReq` schema 扩展。新增 `tests/test_harness_cost_parallel.py` 12 例（去重重路由/成本感知停波/replanner ctx/定价强一致并发不丢/每分钟限流）全绿；**全量 491 项 OK**。
- **未实机**：①`/api/budget` 每分钟字段的真机 HTTP 验收因启动服务会触发沙箱对 `.docmind/gpu_state.json` 的批量删除保护（你已拒绝），改用 FastAPI `TestClient` + 临时预算文件的单测覆盖，端点包裹层以路由注册测试 + 代码核查为准；②成本感知停波仅在 `cost_aware=True` 且预算真用尽时触发，本机默认无预算不触发，逻辑由单测覆盖。

**2026-09-15 追加（rel19 冻结换包交付）**
- 第 19 次产物换入完成：用户确认 8000 实例本就不在（探活 HTTP 000）、并清理残留 python 进程后，执行 `robocopy "D:/Temp/docmind_rel19/DocMind" "D:/WorkBuddy/rag-agent/dist/DocMind" /E /XD .docmind`。**改用 `/E /XD .docmind` 而非原计划的 `/MIR`**——目标 `dist/DocMind` 含运行时目录 `.docmind/`（预算/轨迹/gpu_state/chroma 指针），`/MIR` 会误删，故排除保护、仅镜像构建文件。
- 校验：源 exe SHA-256 `7c816242944eb0cbfb7a9c4d486df92bc5a83d1e57af6504d6837cf607c88745`；换后 `dist/DocMind/DocMind.exe` SHA-256 **相同** = 完整性通过；目标 onedir 结构补齐 `DocMind.exe`(19,814,751)+`MinGit`+`_internal`，`.docmind/` 保留。rel19 交付闭环，无需重打包。

**2026-09-15 追加（P2-2 ComfyUI 精确取消修复）**
- `comfy_cancel` 改为 `POST /queue` 带 `{"delete":[prompt_id]}`（ComfyUI 官方精确取消接口：`delete_prompt` 移除队列任务、若正在执行则自动 `interrupt`，不误伤其他任务）。移除旧 `POST /interrupt` + `prompt_id` 体的误用（真实 ComfyUI 忽略该体、只中断当前全局任务，取消不掉指定队列任务——属 bug）。
- 同步：`tests/test_comfy_cancel.py` 改写为断言 `/queue` delete 体与 `deleted` 字段；`tests/test_gpu_coordinator.py` fake server 增 `/queue` 处理、`ComfyLeaseLifecycleTest` 断言 `deletes==1`；`api.py` `/api/comfy/cancel` docstring 与前端 `api.ts` cancel 返回类型补 `deleted` 字段。
- **全量 `discover` 492 项 OK（skip=1 为 ComfyUI 不可达的真机 H3 转换测试）**。
- **真机验收（2026-09-15，本机起 ComfyUI 8188 后）**：直连 `/prompt` 排两条 Z-Image Turbo 任务 → `A=running`、`B=pending`（B 在队列排队，正是原 bug 场景）；`comfy_cancel(B)` 返回 `deleted=True` 且 **B 被精确移除、A 未误伤**（旧 `/interrupt`+prompt_id 实现取消不掉队列任务）；`comfy_cancel(A)` 返回 `deleted=True`、触发 `interrupt`。已知例外（非 DocMind bug）：Z-Image Turbo 执行不即时响应 ComfyUI `interrupt` 标志，被中断任务长期留 `queue_running` 至自然完成，属 ComfyUI/模型层行为，端点契约正确。验收脚本 `D:/Temp/comfy_cancel_accept.py`。
- 校正 §5 P2-2 过期文字（原把已落地的自动轮询/重试/网格/许可证/重复资源分析列成"待做"），并给 §10 两处历史「仍待实现」汇总行的 ComfyUI 部分加【已解决】标记。

**以下为 codex/p1-3-gpu-comfyui 分支原始变更记录（保留存档；其中部分设计在合并集成时被有意调整，以本文件末尾「P1-3 合并集成」段为准）**
- 密钥存储新增 1 项回归测试：往返解密、明文不落盘、Provider 列表和撤销均已验证。全量测试 205 项。

- 外部权限现支持 approval_id 绑定：只有对应审批记录为 approved 且路径精确匹配时，/api/agent/permission 才会记录授权；新增回归测试，测试总数 206。

- 目标核对（本轮）：ReAct MCP 工具、云端密钥 DPAPI/Fernet 往返与撤销、外部审批创建/批准/approval_id 精确授权均有接口；全量测试 206 项通过。仍未完成云端请求脱敏与审批 Diff 驱动的实际外部写入执行器。

- 云端 Agent 路由现接入 redact_for_cloud：发送云端前会脱敏 api_key/token/password/secret/private key 等凭据并截断上下文；新增回归测试，测试总数 207。

- 外部审批已接入实际写入端点 /api/agent/external-write：需 approved approval_id + 精确路径，写入前生成 .docmind.bak，失败拒绝。

- 审批请求支持 before/after 自动生成 unified diff，AgentPolicyPanel 审批列表可展示 Diff 并批准/拒绝；前端构建通过。

- 独立分支 codex/p1-3-gpu-comfyui 新增 /api/comfy/wait/{prompt_id} 有界轮询接口，ComfyUI 可等待完成/失败/超时；测试 213 项通过。

- Unreal inspect 现在额外返回 .uproject 插件/目标平台，并解析 Build.cs 依赖。

- ComfyUI history 输出现在包含 preview_url 与 MIME，导入元数据记录 mime，便于前端多媒体预览。

- GPU 队列新增 /api/gpu/cancel/{owner}，可取消尚未获得租约的等待任务；持有中的任务不强制中断。

- Unreal headless verify 现在自动传入扫描到的 .uproject 路径，避免 -ProjectOnly 校验错误项目。

- GPU status 现返回 devices 全量列表，并支持 DOCMIND_GPU_INDEX 选择显存门控目标卡；保持旧 used/free 字段兼容。

- TaskEnginePanel 现按 MIME 预览 ComfyUI 图片/音频/视频输出，并保留导入操作；独立 worktree 未安装 node_modules，需在主仓/frontend 环境构建验证。

### 本轮新增（独立 worktree）

- Unreal 诊断解析：新增 `parse_unreal_diagnostics()`，支持 MSVC/Unreal 常见 `path(line[,column]): error|warning ...` 格式；`engine_verify()` 在 Unreal 模式返回结构化 diagnostics。
- 新增 `POST /api/engine/diagnostics`，用于前端/Agent 对任意 Unreal 编译日志做结构化解析。
- 补回并验证 `comfy_wait()` 及 `/api/comfy/wait/{prompt_id}` 有界轮询（超时最多 900 秒），避免无限等待。
- 新增 Unreal 诊断单元测试；独立 worktree 全量测试 215 项通过。

仍待实现：Unreal Blueprint/Level 深度桥接、GPU 跨进程真实显存隔离与优先级持久队列、ComfyUI 后台自动轮询 UI/取消任务/许可证与重复资源分析。【ComfyUI 部分已解决：自动轮询/取消/许可证与重复资源分析均已于 2026-09-15 前落地，见 §5 P2-2；Unreal 与 GPU 隔离仍待】
- ComfyUI 新增后台 watcher：`POST /api/comfy/watch/{prompt_id}` 启动有界后台轮询，`GET` 查询状态；前端可持续显示完成结果而不阻塞请求。
- watcher 生命周期已加入单元测试；全量测试基线仍为 215 项通过（另加 watcher 测试通过）。
仍待实现：GPU 跨进程真实显存隔离、多 GPU 任务绑定/优先级持久队列；Unreal Blueprint/Level Editor 插件桥接；ComfyUI 取消任务、结果网格、许可证/来源与重复资源分析。【ComfyUI 部分已解决：取消/结果网格/许可证与重复资源分析均已落地，见 §5 P2-2；GPU 隔离与 Unreal 桥接仍待】
- GPU 协调器新增优先级队列：`acquire(..., priority=N)`，高优先级任务优先获得释放的租约，同优先级保持 FIFO；取消和 TTL 回收会清理优先级元数据。
- 新增优先级交接测试，GPU 队列相关测试通过。
仍待实现：跨进程真实显存隔离和任务进程绑定；GPU 优先级尚未持久化到磁盘队列。
- Unreal `engine_inspect` 现在分类索引 Content 资产：`blueprints`（按 BP_/Blueprint 命名）与 `levels`（.umap），并保留其它 .uasset 为 assets；仅做只读索引，不修改二进制资源。
- 新增资产分类测试。
仍待实现：通过 Unreal Editor Python/HTTP 插件读取 Blueprint 节点、Level Actor 属性并执行安全编辑；当前索引不能替代编辑器级解析。
- ComfyUI 资源管理新增只读重复检测：`comfy_resource_duplicates()` 按 SHA-256 对生成目录分组，并通过 `GET /api/comfy/resources/duplicates` 提供报告；不会自动删除资源。
- 新增哈希分组测试。
仍待实现：ComfyUI 任务取消、许可证/来源元数据完善、未使用资源分析；GPU 跨进程显存隔离与持久队列；Unreal Editor 深度读写桥接。
- ComfyUI 新增未使用资源分析：`GET /api/comfy/resources/unused` 扫描生成目录并与项目文本引用比对，输出疑似未引用文件；仅供审计，不自动删除。
- 新增对应测试。
仍待实现：Unreal Editor Python/HTTP Blueprint/Level 深度桥接；GPU 跨进程显存隔离、设备绑定和持久队列；ComfyUI 任务取消与许可证来源管理。
- GPU 租约现在记录 `device_index`，状态接口暴露当前 owner 的设备绑定；由 `DOCMIND_GPU_INDEX` 选择。该绑定用于后续子进程 CUDA 环境注入，仍不等同于 CUDA 显存隔离。
- 新增设备绑定测试。
仍待实现：将设备绑定实际注入 Ollama/ComfyUI/引擎子进程，以及跨进程显存监控和持久任务队列。
- ComfyUI 新增显式取消：`POST /api/comfy/cancel/{prompt_id}` 调用原生 `/interrupt`，并在 watcher 状态记录 `cancel_requested`；返回值明确表示“已请求中断”，不会伪装成任务完成。
- 新增取消请求测试。
仍待实现：按 prompt_id 的精确取消（ComfyUI 原生 interrupt 是全局当前任务）、Unreal Editor 深度读写桥接、GPU 跨进程显存隔离与持久队列。
- Unreal 新增桥接脚本安装 API：`POST /api/engine/unreal-bridge/install`（需 `confirm=true`，可 `force` 覆盖）。脚本写入 `Content/Python/docmind_bridge.py`，提供 Editor Python 下的 Blueprint 资产枚举和当前 Level Actor 枚举入口；不修改二进制 `.uasset`。
- 已通过 API 路由与资产索引测试。
仍待实现：在 Unreal Editor 中启用并运行桥接脚本的进程通信、Blueprint 节点级读写和 Actor 属性安全编辑；GPU 跨进程显存隔离/持久队列。
- GPU 新增 `GET /api/gpu/environment`，返回启动 Ollama/ComfyUI/引擎子进程时建议注入的 `CUDA_VISIBLE_DEVICES` 与 `DOCMIND_GPU_INDEX`，不修改工作台自身环境。
- 新增环境生成测试。
仍待实现：将该环境实际传入各子进程启动器，以及真实跨进程显存监控/隔离。
- 引擎启动现在会将 GPU 调度器生成的 `CUDA_VISIBLE_DEVICES`/`DOCMIND_GPU_INDEX` 环境注入 Godot、Unity、Unreal 子进程，便于实际设备选择；工作台自身环境不变。
- API 路由与 GPU 环境测试通过。
仍待实现：引擎启动前自动申请/停止时释放 GPU 租约（当前仅注入环境）；跨进程显存监控与持久队列。
- 引擎启动现在先申请 GPU 租约（owner 为 `engine:<project-root>`），启动失败会释放；停止或发现进程已结束也会释放，避免引擎与 Ollama/ComfyUI 抢占。
- 全量测试运行中已通过前段检查；编译通过。
仍待实现：跨进程显存真实监控/隔离、持久任务队列，以及 Unreal Editor Blueprint 节点/Actor 属性的实际通信读写。
- ComfyUI queue 响应新增 `workflow_sha256`，对规范化 workflow 计算稳定哈希，便于任务追踪、缓存和资源来源审计；不保存敏感 workflow 内容。
- ComfyUI 导入元数据现在保留可选 `license`、`source_url`、`author`、`workflow_sha256` 字段（长度受限），便于资源来源和授权审计。
- 新增引擎 GPU 租约护栏测试：验证 GPU 忙时引擎启动被阻止，防止未来改动绕过调度器。
- `engine_verify` 的 Godot/Unity/Unreal headless 校验进程现在同样继承 GPU 设备环境，确保验证阶段与运行阶段使用一致的 CUDA 设备。
- `engine_verify` 现在也申请独立 GPU 租约，并在成功、找不到可执行文件、超时或异常时释放，避免校验任务与运行任务并发争抢显存。
- ComfyUI watcher 现在在轮询生命周期内持有 `comfy:<prompt_id>` GPU 租约，并在完成、失败或超时时释放；重复 watcher 不重复占用租约。
- 这使生成监控阶段与 Ollama/引擎调度互斥，避免轮询期间 GPU 被其它任务抢占。
- ComfyUI workflow 提交成功后会自动启动后台 watcher（当响应包含 `prompt_id`），`/api/comfy/queue` 返回 `watch` 状态；无需前端额外发起轮询请求。

### 本地 ComfyUI 实机联调（2026-09-14）

- 已确认安装目录：`D:\ComfyUI\ComfyUI`，便携 Python 3.13.14。
- 已启动实例 PID 46712：`127.0.0.1:8188`，ComfyUI 0.33.1，PyTorch 2.13.0+cu130。
- 实测 GPU：`cuda:0 NVIDIA GeForce RTX 5070 Ti Laptop GPU`，总显存约 12.82 GB，启动时空闲约 11.58 GB。
- 已发现 Z-Image：`models/unet/z_image_turbo-Q8_0.gguf`、`models/clip/Qwen3-4B-Q8_0.gguf`、`models/vae/ae.safetensors`。
- 已发现 MiniMax H3：`diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors`、`diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors`、对应 LoRA、Qwen3VL 文本编码器及音视频 VAE。
- ComfyUI 日志确认已加载 `TE-Speed-MiniMaxH3-OSS`、`ComfyUI-GGUF`、`comfyui-ollama` 自定义节点。

实机服务已具备，下一步可提交实际 Z-Image/H3 workflow 做端到端生成验证；生成任务会经过工作台 watcher 和 GPU 租约调度。

### Z-Image 实机端到端验证（2026-09-14）

- 使用本地 `UnetLoaderGGUF + CLIPLoaderGGUF + TextEncodeZImageOmni + KSampler + VAEDecode + SaveImage` workflow。
- ComfyUI 返回 prompt：`2a82927b-0468-478e-857c-26a3c1fab143`。
- history 状态：`success/completed=true`，耗时约 25 秒。
- 输出：`docmind_zimage_00001_.png`（ComfyUI output 目录）。
- 证明 Z-Image 模型、GGUF 节点、VAE、GPU 推理和结果查询链路均可用。
- MiniMax H3 实机首次 workflow 已提交并被 ComfyUI 接受，但在 `MiniMaxH3ImageToVideo` 文本编码阶段失败：`mat1 and mat2 shapes cannot be multiplied (8x5120 and 2560x8192)`。
- 诊断表明当前 `CLIPLoaderGGUF(qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors, type=minimax)` 与 H3 节点期望的文本编码维度不匹配；未把失败误报为成功。下一步需读取官方 H3 workflow/正确文本编码器配置后再重试。
### MiniMax H3 实机成功验证（2026-09-14）

- 使用本地官方 `minimax_h3_t2v.json` 展开脚本 `h3_video_gen.py`，避免手写 workflow 的文本编码器维度错误。
- 实际参数：首帧 `starblade_kf01.png`、672x384、约 5 秒、8 steps。
- ComfyUI prompt：`ff95c4f5-e632-4468-9a6c-a0f48dd17df6`，状态 success，耗时约 10 秒。
- 输出：`D:\ComfyUI\ComfyUI\output\docmind_h3_00001_.mp4`，SaveVideo 返回 animated=true。
- 证明 H3 模型、正确 Qwen3VL 配置、I2V workflow、GPU 推理和视频输出链路可用。
- 新增 `/api/comfy/templates` 与前端模板按钮，显示本机 Z-Image Turbo 和 MiniMax H3 参考图视频模型及 workflow 路径，作为工作台模板入口（当前按钮展示元数据，完整 JSON 仍从本地官方 workflow 加载）。
- ComfyUI 模板现在可直接加载 workflow：`GET /api/comfy/templates/{template_id}`；Z-Image 返回 API prompt 骨架，H3 读取本机官方 UI workflow。前端模板按钮会自动填充 JSON 编辑器。
- 新增模板加载测试。
- Ollama 本地聊天与选区 AI 的流式请求现在纳入 GPU 租约：请求开始申请、SSE 完成/异常时释放；GPU 忙时返回清晰提示，避免与 ComfyUI/H3 抢占。
- 前端 ComfyUI 面板新增最近 10 个 prompt 历史（localStorage），支持点击切换并自动查询；提交后每 4 秒自动刷新当前任务结果，显示生成状态和输出数量。
- Unreal 桥接脚本升级为本地 HTTP 服务骨架：在 Editor Python 中运行 `run_server(8765)` 后，`/` 探活、`/assets` 枚举 Blueprint 资产、`/actors` 枚举当前关卡 Actor；工作台新增 `GET /api/engine/unreal-bridge/status` 探测端点。
- 尚需在真实 Unreal Editor 中启用 Python 插件并运行脚本后做通信实测；未宣称节点级编辑已完成。
- Unreal 桥接新增工作台代理接口：`GET /api/engine/unreal-bridge/assets` 与 `/actors`，转发本地 Editor bridge 的 Blueprint 资产和当前关卡 Actor 数据；不可达时返回 `available=false`，不伪造结果。
- 前端引擎面板新增 Unreal 桥接状态、Blueprint 资产数量和 Level Actor 数量展示，并通过 `/api/engine/unreal-bridge/*` 查询。
- ComfyUI history 响应新增 `progress.executed_nodes/total_nodes/percent`，前端自动刷新时显示节点进度百分比。
- Unreal 面板现在展开显示最多 8 个 Blueprint 资产和 8 个 Level Actor（名称/类名），便于快速确认桥接数据。

**2026-09-15（P1-3 合并集成，分支 `merge/p1-3-integration`，已合入 main（merge commit 4617dfc））**
- 合并 `codex/p1-3-gpu-comfyui`（HEAD `226258f`）入 main（基线 `d130646`，merge commit `4617dfc`），不硬合：**保留 main 的新版三模式 GPU 协调器**（serial/parallel/multi、`acquire_lease` 结构化结果、严格 FIFO、显存门槛、`reown/force_release/cancel_wait`、采样环/TTL pump/0.5s 探测缓存/精准驱逐），分支功能在此之上回植。
- 吸收的 P1-3 功能：Z-Image/H3 模板（`comfy_templates/comfy_template_workflow`，模板路径暂硬编码 `D:\ComfyUI\...`，待配置化）；history 的 `progress`/`preview_url`/`mime`；`comfy_wait` 有界轮询；`comfy_watch/watch_status` 后台轮询（`_COMFY_JOBS`）；queue 返回 `workflow_sha256` 并自动起 watch；import 元数据透传 mime/license/source_url/author/workflow_sha256；`comfy_resource_duplicates/unused_resources`；Unreal inspect 扩展（plugins/targets、Build.cs 依赖、.umap levels、Blueprint 分类）、`install_unreal_bridge`、`parse_unreal_diagnostics` 与 `/api/engine/diagnostics`；Unreal bridge 四个 HTTP 路由；`process_environment()` 回植协调器（含 `status().device_index` 兼容键，`DOCMIND_GPU_INDEX`）；`engine_start/engine_verify` 启动前申请租约并向子进程注入 `CUDA_VISIBLE_DEVICES`/`DOCMIND_GPU_INDEX`，`engine_stop` 所有返回路径释放（owner `engine:<root>` / `verify:<root>`）；`GET /api/gpu/environment`。
- **相对分支原设计的有意偏离（合并时必须遵守）**：
  1. **删除两处 SSE 双重租约**：分支在问答/选题两个 event_stream 里以 `ollama:chat`、`ollama:selection` 申请租约；main 的 `llm.py _ollama_chat` 已以 owner `ollama` 持租约，serial 模式不同 owner 不可重入，等 2s 后 100% 自报"GPU 正忙"（自锁）。已删除 SSE 层 acquire/release，租约只在 llm 层持有。
  2. **ComfyUI watch 不持租约**：整作业周期租约由 `comfy_queue` reown 为 `comfyui:<prompt_id>`（main 命名，非分支的 `comfy:` 前缀），终态由 history/cancel 释放、TTL 兜底；watch 仅轮询，避免重复占卡/泄漏。
  3. **不回植 priority 优先级队列**（分支 `acquire(priority=)`/`cancel()`，无生产调用方），维持严格 FIFO；`/api/gpu/cancel` 保留 main 的 body 版（`cancel_wait`），丢弃分支路径参 `/cancel/{owner}`。
  4. ComfyUI 取消以 main 为准：POST /interrupt + `force_release(comfyui:<id>)`，并给 watch job 打 `cancel_requested`；`comfy_wait` 超时键沿用 main 的 `timeout`（分支的 `timed_out` 不采用）。
- 测试：分支 4 个旧 API 测试改写（priority→FIFO、device_binding 按新语义、engine_lease 改 patch `gw._gpu.acquire_lease`、environment 直接通过），6 个 comfy 测试按 main 响应口径调整；另新增 3 个合并护栏用例（SSE 不自锁行为测试 + 静态 owner 护栏、引擎 start 注入 env 副本且 stop 释放、cancel 给 watch job 打标记）；全量实测 **277** 项通过（main 258 + 分支 13 文件 16 例 + 新增 3 例）；`npm run build` **通过**（vite 5.4.21，2026-09-15）；真机验证见 §5 验收项 B（CUDA =0/=99 负对照已在 RTX 5070 Ti 实测通过；问答/ComfyUI 流式真机冒烟因两服务未运行、且按安全策略不由集成方自行启动，待用户启动服务后补验）。

- 合并后前端生产构建已验证：在 `frontend` 目录执行 `npm run build`，Vite 5.4.21 构建成功；输出 `web/workbench.html` 及全部资源 chunk，只有体积提示，无错误。
- 集成分支运行时冒烟（2026-09-15）：临时后端 `127.0.0.1:8899` 启动成功；`/api/gpu/status` 返回 RTX 5070 Ti、空闲约 10.9 GB、无残留 holder；`/api/comfy/templates` 正常返回 Z-Image 与 MiniMax H3 模板；本机 Ollama `127.0.0.1:11434/api/tags` 在线并列出 qwen2.5:7b、qwen3:14b 等模型。
- Unreal bridge 探测按预期返回 `available=false`（Editor bridge 未运行），未将离线状态宣称为通信成功。

### 2026-09-15 GPU/ComfyUI/Unreal 产品化增量

- GPU 协调器新增真实 `nvidia-smi --query-compute-apps=pid,process_name,used_memory` 探测；探测失败明确返回 `available=false`，不以估算值替代。
- 新增进程注册、心跳、注销和 PID 存活回收；异常退出会释放对应租约并记录 `orphan_recovered`。引擎 `Popen` 成功后自动注册真实 PID，停止时注销。
- `/api/gpu/status` 增加 `processes`、`compute_apps`、`process_probe` 和 `recovery_events`；采样曲线写入有界 `.docmind/gpu_state.json`（运行时文件，已忽略）。GPU 前端显示注册进程和 compute-app 显存。
- 新增 GPU 进程专项测试；全量 Python 测试从 277 增至 280 项并通过。前端 `npm run build` 通过。
- ComfyUI 模板支持环境变量 `DOCMIND_COMFY_WORKFLOW_H3` 和 Windows 常见目录探测；模板返回参数 schema。新增 `/api/comfy/templates/apply`，对节点/字段存在性做校验后应用 prompt、尺寸、帧数、steps、seed、输出前缀。
- ComfyUI 作业历史写入 `.docmind/comfy_history.json`，服务启动恢复；新增 `/api/comfy/jobs` 分页接口和 `/api/comfy/retry/{prompt_id}`，失败作业最多重试 2 次并保留 workflow。
- H3 workflow 路径优先级现为项目根 `.docmind_comfy.json` 的 `h3_workflow` → `DOCMIND_COMFY_WORKFLOW_H3` → Windows 常见 D/C 盘目录；相对路径按项目根解析。
- Unreal 工作台代理新增 Blueprint/Actor 查询路由：`/api/engine/unreal-bridge/blueprint/{path}`、`/actor/{name}`。当前本机 Unreal Editor 未运行，真实节点/属性通信仍未验收。
- 当前已验证：Python 281 项、前端构建、Z-Image/H3 历史实机记录、GPU 单卡采样。未验证：物理多 GPU、Unreal Editor 端到端、引擎实际安装路径上的 Godot/Unity/Unreal 启动、最终远端推送。
- GPU 运行时状态现在在启动时读取 `.docmind/gpu_state.json`；上次未完成租约不会复用，而是标记为 `recovered` 事件并清除旧状态，避免重启后永久占卡。新增专项测试通过。
- 2026-09-15 当前机实测：`nvidia-smi --query-gpu` 成功读到单卡 RTX 5070 Ti（12227 MB，总使用约 1717 MB）；`--query-compute-apps` 返回进程但显存均为 `[N/A]`，协调器因此正确报告进程显存 `available=false`，未将其当作真实数值。`CUDA_VISIBLE_DEVICES=0` 正常生成，`=99` 仅作为无效设备负对照生成环境变量，尚未启动 CUDA 子进程验证失败行为。
- ComfyUI 状态探测现在尝试通过监听端口解析外部服务 PID，并将其登记为 `comfyui:service`，因此 GPU 面板可显示常驻服务进程；服务不可达时不登记、不伪造可用状态。当前本次探测 `127.0.0.1:8188` 不可达，未做生成实测。
- Unreal 新增受控写回代理 `POST /api/engine/unreal-bridge/write`：必须提供任务 ID、目标路径和 `confirm=true`，`.uasset/.umap` 直接拒绝；当前 bridge 仍返回 `available=false`，未实现二进制资产写回。
- ComfyUI 取消现在区分“已请求中断”和“已终止”：存在 watcher 时 `/interrupt` 成功只标记 `cancel_state=requested`，等待 history 返回终态后再释放租约；取消专项与 GPU 生命周期测试通过。
- GPU 状态文件现在同时保存等待队列快照；重启恢复时将旧租约标记为 `recovered`、旧等待项标记为 `recovered_waiting`，只记录审计事件，不自动重启任务，避免重复执行。
- 发布前回归（2026-09-15）：Python 全量测试 **282/282 通过**；`frontend/npm run build` 使用 Vite 5.4.21 成功；工作区 clean。当前 `main` 比 `origin/main` 超前 86 个提交，尚未推送。
- 追加实机探测：`D:\Tools\Godot\Godot_v4.7.2-stable_win64_console.exe --version` 返回 `4.7.2.stable.official.ed1daf0bf`；Ollama `127.0.0.1:11434/api/tags` 可用并列出本地模型；本次 ComfyUI `127.0.0.1:8188` 不可用；未发现 Unity Hub 或 UnrealEditor 可执行文件，故 Unity/Unreal 启动和端到端桥接仍未验证。
- 最新回归复核：Python 全量 **282/282 通过**；工作区 clean；当前 `main` 比 `origin/main` 超前 88 个提交。由于 ComfyUI/Unity/Unreal 实机条件当前不可用，暂不推送发布分支。
- 参数修复后的最新回归：Python 全量 **283/283 通过**；前端构建成功。ComfyUI 正/负提示词、尺寸、steps、seed 和输出前缀已分别校验；当前 `main` 比远端超前 91 个提交。
- 工作台前端已接入 ComfyUI “失败重试”操作，调用 `/api/comfy/retry/{prompt_id}` 并切换到新 prompt；重试上限和服务端 workflow 保留逻辑不变。当前工作区保持 clean，最终推送仍待外部服务/引擎实机验证完成后执行。
- 最新全量回归：Python **286/286 通过**，前端 `npm run build` 成功；Unreal bridge 安全写入测试已纳入总数。当前 main 比 origin/main 超前 95 个提交。
- 当前发布状态复核：工作区 clean，`HEAD=5596b82`，`origin/main=d130646`，本地 main 超前 96 个提交；已确认 D 盘存在 `D:\ComfyUI\ComfyUI\main.py` 与 Godot GUI 可执行文件，但本轮未自行启动 ComfyUI 或引擎以避免改变用户 GPU/桌面状态。
- ComfyUI 前端结果区已改为自适应多媒体网格，图片/音频/视频保持原 MIME 预览与导入操作。
- 最新回归（任务历史接入后）：Python 全量 **288/288 通过**；前端 `npm run build` 成功；工作区 clean。当前 main 比 origin/main 超前 103 个提交。
- 来源校验接入导入流程后的最新回归：Python 全量 **291/291 通过**；前端 `npm run build` 成功；工作区 clean。当前 main 比 origin/main 超前 111 个提交。
- API 可变默认值修正后复核：Python 全量 **291/291 通过**，`api.py` 编译通过；ComfyUI 参数请求不会共享跨请求状态。
- 来源校验 API 路由测试加入后的最新回归：Python 全量 **292/292 通过**；来源校验、ComfyUI 参数化、GPU 监控和 Unreal 安全边界均有自动化覆盖。
- ComfyUI 重试边界测试加入后：Python 全量 **293/293 通过**；已覆盖完成任务禁止重试、失败任务最多两次重试和来源校验路由。
- ComfyUI 取消现向 `/interrupt` 发送目标 `prompt_id`，使用 ComfyUI 的定向中断能力；测试确认请求体带目标 ID，避免恢复为全局中断。
- 生成 Unreal bridge 脚本已扩展 `/health`、`/assets`、`/actors`、`/blueprint/{path}`、`/actor/{name}` 路由，并通过 `py_compile`；真实 Editor 节点/属性仍需 Unreal Python 插件运行验证。
- 最新环境复核：Ollama 进程与 API 在线；ComfyUI 端口 8188 当前不可用；未发现 Unity/Unreal 进程。未启动外部服务，避免未经用户操作改变 GPU 负载。
- 2026-09-15 收尾复核：使用项目 `.venv` 执行 Python 全量测试 **293/293 通过**；`frontend\npm run build` 使用 Vite 5.4.21 成功；工作区 clean。新增提交 `65f5fbf` 修复 ComfyUI watcher/history 终态持久化：状态、进度、输出、完成时间和取消终态会写入 `.docmind/comfy_history.json`。当前 `main` 比 `origin/main` 超前 123 个提交。物理多 GPU、Unity/Unreal 编辑器联机及 ComfyUI 当前端口生成闭环仍缺实机条件，不能宣称已验收。
- 2026-09-15 实时环境复核：Ollama `/api/tags` 在线，本机 `qwen2.5:7b` 非流式生成返回 `OK`，随后 `/api/ps` 为空（`keep_alive=0`）；Godot 控制台版返回 `4.7.2.stable.official.ed1daf0bf`。ComfyUI `127.0.0.1:8188` 当前连接被拒绝；未发现 Unity 或 UnrealEditor 可执行文件，因此这三项仍未完成实机验收。
- 追加回归：ComfyUI 项目配置优先级测试通过；项目 `.venv` 全量测试 **294/294 通过**（忽略既存 ResourceWarning），工作区保持 clean。
- ComfyUI 资产体验增量：history 输出按扩展名/MIME 标记 `asset_kind`（media/3d/unknown）和 `preview_supported`；前端媒体卡片对 3D 与未知格式显示安全导入/下载提示，并完成 API 类型约束。模板路径优先级与 3D 分类均有自动化测试；前端 Vite 构建通过。
- 最新验证：在 `frontend` 目录正确执行 `npm run build`（Vite 5.4.21）成功；项目 `.venv` 全量测试 **295/295 通过**；工作区 clean。根目录没有 `package.json`，构建必须从 `frontend` 目录执行。
- 最新 GPU 只读实测：`nvidia-smi` 读取 RTX 5070 Ti Laptop 单卡，12227 MB 总显存、约 2.4 GB 使用、52–53°C；compute-apps 仍返回 `[N/A]`，协调器正确报告 `process_probe.available=false`。本次独立状态读取 `holders=[]`、`queue=[]`、`recovery_events=[]`，没有伪造进程显存或多卡数据。
- ComfyUI 前端任务历史新增“刷新历史”按钮，显式重新请求后端分页接口；构建已通过，避免仅依赖浏览器 localStorage 的旧任务列表。
- 3D 输出能力口径已收紧：`.glb/.gltf/.fbx/.obj/.stl/.ply/.usd/.usdz` 仅标记为 `asset_kind=3d` 并提供导入元数据，`preview_supported=false`，直到接入引擎渲染器；前端显示相应的导入提示。
- ComfyUI 历史分页已接入前端：任务面板支持后端分页的页码、上一页/下一页和刷新，localStorage 仅用于保留近期选择项；`npm run build` 成功。
- 分页改动后的最终自动化回归：项目 `.venv` 全量测试 **295/295 通过**，前端 Vite 构建成功，工作区 clean。
- Godot 原生嵌入复验：`verify_engine_embed.py` 在当前 150% DPI 下通过 59 项（HWND 定位、rect/fill 几何、resize、detach、停止和 HTTP embed 端点）；3 项合成键鼠输入因当前会话拒绝 `SendInput` 而失败，已按工具限制跳过后续输入断言，不影响窗口嵌入契约。
- 发布审计补充：`verify_api.py` 接口冒烟整体返回 OK，但本机已有 Chroma 集合为 1024 维、当前一次 ingest 使用 256 维而被拒绝；该环境数据维度不一致需在发布前重建/迁移索引，不能将该 ingest 错误宣称为通过。
- 已修复 Chroma 维度错误可诊断性：`vectorstore.add_documents` 现在将维度冲突转换为明确中文错误，并指导切回原 embedding 配置或显式 `reset_collection()`；不会自动删除现有索引。全量测试 **295/295 通过**。
- Chroma 维度诊断新增专项回归测试（模拟 1024/256 冲突并断言包含 `reset_collection` 指引）；专项测试通过。
- ComfyUI 重启语义已收紧：加载持久化历史时，`queued/running` 远端任务标记为 `recovered` 并停止显示为 live；保留完成/失败记录，避免服务重启后重复提交或永久占用租约。专项测试通过。
- 前端 `ComfyWatchJob` 类型已覆盖 `recovered`、取消状态、超时和进度字段；Vite 构建通过，UI 可安全消费重启恢复状态。
- 最新完整回归：项目 `.venv` 全量测试 **298/298 通过**（含 Chroma 维度诊断、ComfyUI 重启恢复和 3D 输出分类专项）；工作区 clean。
- 2026-09-15 发布同步：`main` 已成功推送到 `origin/main`（远端从 `d130646` 更新至 `6ac51a9`）；未强推、未改写历史。外部引擎与物理多 GPU 未验收项仍按前述限制保留。
- 发布复核（最新）：`git fetch origin` 后 `HEAD` 与 `origin/main` 均为 `437bd9faf42b979c654988b7bdb3ff165251438c`，分支完全同步、工作区 clean。文档前部旧提交数字属于历史记录，以上述最新复核为准。
- GPU 生命周期专项复验：进程注册/PID 退出与孤儿回收、队列快照恢复、设备环境注入四组测试共 **17/17 通过**；既存 ResourceWarning 仅来自测试夹具未关闭文件，不影响结果。
- 2026-09-15 ComfyUI 实机闭环：通过 `D:\ComfyUI\run_nvidia_gpu.bat` 启动便携版，ComfyUI 0.33.1 / PyTorch 2.13.0+cu130 / RTX 5070 Ti，真实 PID 35400 被登记。Z-Image 以 256×256、4 steps 实际生成成功，输出 `docmind_zimage_00002_.png`，history/preview/import 均成功并写入 `assets/generated`；任务结束后租约释放。H3 官方 UI workflow 可读取但直接提交 `/prompt` 返回 HTTP 500（UI 格式尚未转换为 API 格式），因此 H3 生成、取消/重试闭环仍未验收；服务已停止释放 GPU。**【已解决 `e096834`，2026-09-14】** 该节点已确认安装，`comfy_ui_to_api_workflow` 重写为子图拍平 + `/object_info` 驱动 widget 映射后，H3 官方 UI workflow 可直接提交并实机产出 39 帧视频，生成/取消/重试闭环已验收，详见「2026-09-14 H3 实机验收收尾」条目。
- H3 转换器已落地：`comfy_ui_to_api_workflow` 将官方 UI graph 转为 API graph（实测 11 个 UI 节点中生成 6 个可提交节点），并由 `comfy_queue` 自动转换。真实提交现返回 ComfyUI 400 `missing_node_type`：自定义节点 `4c314f31-ecda-4b08-ae98-faaba1bf613f` 未安装；格式转换链路已验证，H3 仍需安装匹配的 TE-Speed-MiniMaxH3-OSS 节点后再做生成/取消/预览验收。服务已停止。**【已解决 `e096834`，2026-09-14】** `4c314f31-…` 即 `TESpeedMiniMaxH3` 别名，节点已安装；转换器进一步支持子图拍平、`autogrow values.a`、`-10` 接口槽解析与 UUID 别名，实机短生成/取消/重试全部通过，23/23 ComfyUI 用例绿。
- 新增受控 ComfyUI 服务生命周期 API：`POST /api/comfy/start` 按 `DOCMIND_COMFY_ROOT`/D 盘便携目录启动 `python_embeded` + `ComfyUI/main.py`，登记真实 PID；`POST /api/comfy/stop` 终止并注销 PID。默认不自动启动，启动/停止均需用户显式操作；已完成编译与专项回归。
- ComfyUI 任务面板已接入服务控制按钮（启动/停止/刷新状态），调用上述生命周期 API 并显示 PID；前端构建验证通过，服务仍保持用户显式启动策略。
- 服务生命周期改动后的全量回归：项目 `.venv` 测试 **298/298 通过**，分支与 `origin/main` 同步，工作区 clean。
- 新增 ComfyUI 服务生命周期专项测试：缺失便携安装拒绝、启动命令参数/真实 PID 注册、停止注销共 **2/2 通过**；已推送 `origin/main`。


### 2026-09-14 H3 实机验收收尾（`e096834`）

- **前置阻塞解除**：`TE-Speed-MiniMaxH3-OSS` 自定义节点（`4c314f31-ecda-4b08-ae98-faaba1bf613f` = `TESpeedMiniMaxH3` 别名）已确认安装就绪；`comfy_ui_to_api_workflow` 重写为 **子图拍平 + `/object_info` 驱动 widget 映射 + autogrow 子输入（`values.a`）+ `-10` 接口槽解析 + UUID 别名**，彻底修掉 H3 官方 UI workflow 直接提交 ComfyUI 的 400/500。
- **实机验收三项全过**（RTX 5070 Ti / ComfyUI 0.33.1）：①**生成** —— 经 `comfy_queue`→转换器→ComfyUI→`comfy_history` 实机产出 `MiniMax_H3_00008_.mp4`（39 帧，`preview_url`/`mime`/`asset_kind:media` 正确）；②**重试** —— `comfy_retry` 重排失败作业并重新生成成功（`DOCMIND_COMFY_MIN_FREE_MB=0` 下验证了内存门槛放行逻辑，门槛本身由 `82d092c` 硬化，非 bug）；③**取消** —— `comfy_cancel` 经 `/interrupt` 标记 `interrupted=True`、`cancel_state` 由 `requested` 收敛到 `terminated`。
- **帧数控制收口**：编辑 subgraph 接口槽 5（`value_1`，经 instance node 105 `widgets_values[3]`）即改 `PrimitiveFloat.value`→`ComfyMathExpression`→`MiniMaxH3ImageToVideo.length`；直接改 inner node 111 的 widget 因接口 link 206 已接是空操作——该行为已用 `D:/Temp/h3_find_slot5.py` / `h3_validate_fix*.py` 验证。
- **回归护网**：新增 `tests/test_comfy_h3_converter.py`（合成子图 UI + `fake_object_info` mock：子图拍平/接口解析/autogrow `values.a`/UUID 别名，含一个 live 用例断言 `PrimitiveFloat.value==1.0` 与真实 H3 节点类型齐备）；修掉 converter fallback 把无 link 的 widget 输入误当连接丢弃的回归后，**23/23 ComfyUI 用例全绿**。
- **提交**：`game_workbench.py` + `tests/test_comfy_h3_converter.py` 以白名单路径提交 `e096834`（未 `git add -A`、未推送）。§4 时间线已加 `e096834` 行，§5 P2-2 已标注 H3 验收完成，本文件 §10 上两条「H3 仍未验收」旧记已加【已解决】标记。
- **仍不宣称**：ComfyUI `/queue` 不把 DocMind 作业列为 running/pending（可观测性差异，`comfy_history` 才是真值源且正确），故"干净中途打断"未在本次观察；取消机制已有单测且生命周期收敛到 `terminated`。

### 2026-09-15 H3 外部方案检索

检索 GitHub Anil-matcha/minimax-h3-comfyui（MIT）确认：该项目是 Muapi 云端 API 节点，并非本地 MiniMax H3 模型；需要 MUAPI_API_KEY，通过 /api/v1/minimax-h3-* 异步生成。其 workflow 可作为云端连接器参考，但不能替代当前 D:\ComfyUI 本地模型验收。当前本地官方 workflow 的正确链路为 MiniMaxH3ReferenceToVideo → SamplerCustomAdvanced → VAEDecode/VAEDecodeAudio → CreateVideo → SaveVideo；简化 minimax_h3_t2v.json 仍不可提交。

- 2026-09-15：完整 H3 Example_Workflow.json 转换复核 PASS。过滤说明/预览/标签及缺失 LoadImage 分支后，得到 18 个可执行节点，保留完整采样、双 VAE 解码、CreateVideo、SaveVideo 链路；当前无有效参考图片时按 T2V/无参考模式提交仍需真实服务端验收。**【已解决 `e096834`，2026-09-14】** 经 I2V（带参考图）路径的实机短生成已验收通过，T2V/无参考路径的单独验收仍可作为后续专项，但"H3 整体未验收/暂缓"已不成立。

- 2026-09-15：按当前决策暂缓 ComfyUI H3 实机生成验收。已完成路径探测、UI→API 转换、说明节点过滤、模型路径归一化、悬空依赖清理和错误诊断；真实 H3 生成留待后续专用 T2V workflow/有效参考资源准备后再验收。**【已解决 `e096834`，2026-09-14】** 经由 `minimax_h3_t2v.json`（I2V）的实机短生成/取消/重试闭环已验收，见「2026-09-14 H3 实机验收收尾」条目；"暂缓"决策已被实机验收推翻。

- 2026-09-15：PyInstaller 发布目录已成功构建（dist\\DocMind\\DocMind.exe，约 19MB，含 MinGit）。桌面专项测试 8/8；当前系统执行策略对独立 EXE 启动返回 Access is denied，故独立启动健康检查未宣称通过。仓库没有 MSI/NSIS/Inno Setup 卸载器，覆盖升级/卸载需后续增加安装器后验证。

- 2026-09-15：已安装 Inno Setup 6.7.3 并成功生成 dist\\installer\\DocMind-Setup.exe（约 0.85MB，压缩目录包）。安装器脚本包含覆盖安装、快捷方式和标准卸载。当前执行策略拒绝直接运行安装 EXE（Access denied），因此安装/升级/卸载实机结果未宣称通过；需在用户桌面双击或解除策略后执行。

### 2026-09-15 联网搜索能力交接状态

已完成：web_search（DuckDuckGo）、web_fetch（公开 HTML 正文读取）、web_research（搜索+最多 3 个来源抓取）、标题/最终 URL/正文清理、非 HTML 与网络失败降级、回答中的来源 URL 可点击。

未完善：B 站专用搜索与字幕提取、GitHub API 专用搜索、搜索结果缓存、来源可信度评分、多来源冲突检测、前端来源卡片/正文展开、登录/验证码/付费墙处理。当前能力可用于普通教程、GitHub 资料和引擎文档查询，但不可宣称达到 Perplexity/Claude Research 级别。

### 2026-09-16 设计评审 + 缺陷修复（A→B→C→D + R 批）文档同步

- §2：验证基线 450 → **707**；桌面分发版本 第 19 次 → **第 21 次**（exe 20,536,866 B / SHA-256 `f67eac87…`）。
- §4：新增 2026-09-16 时间线行（四批修复 + 独立 QA 复核 + R 批回归修复 + 第 21 次冻结构建）。
- §5：P3 版次推进到第 21 次，并记录本轮**流程偏离**（未重跑浏览器冒烟 `verify_scene_canvas_ui.mjs`(27) 与引擎嵌入 `verify_engine_embed.py`(68)，理由=本轮改动面未触碰 `scene_runtime.py`/`desktop_bridge.py`/`engine_adapters.py`/`game_workbench.py`）；新增「已知限制与后续项」小节共 5 条。
- §8：新增「运行时状态 / 共享对象相关的坑」小节（第 26–30 条）。
- `DocMind_BUILD.md`：顶部指引行与产物段更新为第二十一次；新增「第二十一次重建」章节（14 项改动 / 验证明细 / 已知限制）。
- 本轮是**无新功能**的纯修复发布，流程为「评审 → 四批修复 → 独立 QA 复核 → R 批回归修复」；**未验证项**（同会话真并发、需真实硬件/外网的项）已如实列在 §5 与 BUILD.md，未宣称通过。

### 2026-09-16 续：R5 修复 + code_root 顺序回归（第 22 次构建）文档同步

- §4：新增 2026-09-16 第二行（R5「每标签页 session_id」+ 冒烟抓出的 `code_root` 覆盖回归 + 第 22 次构建）。
- §5「已知限制与后续项」第 1 条：由「未做的并发限制」改写为「**已修复 + 残余（复制标签页会复制 `sessionStorage`）+ 刻意语义变更（各标签页独立会话；旧的 `default` 会话不再被前端使用）**」。
- §8：新增第 31–32 条（lifespan 化改造的**顺序反转**坑；校验脚本的 code_root 语义与 TestClient 是否触发 lifespan——解释了「54/54 全过但 `--serve` 已坏」）。
- `DocMind_BUILD.md`：新增「第二十二次重建」章节，顶部指引行与产物段更新为第 22 次；**第二十一次章节保留**（其 exe 20,536,866 B / SHA `f67eac87…` 是当时真实交付值，已被本次取代但不得改写历史）。
- 本轮**未重跑**：`verify_regions.py`（不在冻结发布流水线内）。`docs/screenshots/*.png` 是校验脚本的副产物、不入库（跑完已 `git checkout --` 还原，避免工作树脏）。

### 2026-09-18 工作台交互回归收尾

- 修复并验证场景画布的真实聚焦/打开链路：回归脚本先确认“聚焦选中”确实把节点置于画布中心，再通过画布自带的适应视图恢复全图，双击 `behaviors/player.gd` 文件卡可打开实际编辑器；没有使用强制点击绕过遮挡。
- 新增 `verify_workbench_races.mjs` 场景交互断言，覆盖延迟恢复不覆盖新输入、项目切换中止旧 SSE、回答中断恢复、首页重复发送防护、节点聚焦和文件卡双击打开。
- 本轮验证：`npm --prefix frontend run typecheck` 通过；`npm --prefix frontend run build` 通过；`verify_workbench_races.mjs` 6/6 通过；`verify_workbench_regressions.mjs` 9/9 通过；`verify_scene_canvas_ui.mjs` 27/27 通过；Python 全量测试 **935/935 通过（1 项跳过）**。
- 已知限制保持不变：真实 Unreal Editor 联机、物理多 GPU 和安装器独立 EXE 启动仍需对应环境实测；本轮未修改用户未跟踪的 Godot 工程文件。

### 2026-09-18 运行时状态项目隔离

- 新增 `project_state.py`：所有按项目归属的内部运行时状态写入
  `<STATE_ROOT>/.docmind/projects/<project_id>/`。`project_id` 由项目根目录规范化后稳定计算，后台任务不再依赖当前 UI 选择来判断归属。
- 已迁移并按项目隔离：ComfyUI 历史/模板配置/任务 watcher、生成任务、引擎配置与日志、运行时事件、任务历史、审批与 Agent 权限日志、密钥、MCP 注册表和语义标签。ComfyUI prompt/job 的 owner 同时包含项目 ID，避免不同项目相同 prompt ID 互相取消或释放租约。
- 旧项目根目录状态采用一次性复制迁移，保留原文件；`.migrated` 标记防止用户清空新状态后旧文件再次回灌。项目代码、资源、Git、`.docmind_backups` 与导出产物仍属于用户项目，未移动或删除。
- 新增 `tests/test_project_runtime_state.py`，覆盖路径隔离、Comfy 历史 A/B 项目隔离、重启恢复和旧状态一次性迁移。最后一轮重点回归 106/106 通过；完整测试与前端构建需以本轮提交前命令结果为准。
- 限制：全局模型/联网配置、GPU 协调器全机租约和 Chroma 索引仍是实例级资源；它们不伪装成项目级隔离。真实多项目并发后台服务尚未进行长时间实机压测。

### 2026-09-18 Godot 热重载

- 新增 `POST /api/engine/reload` 与运行台「↻ 热重载」按钮。桌面原生 Godot 运行实例会保存启动参数、GPU 绑定和嵌入矩形，停止旧进程后快速重启并恢复原嵌入位置；旧进程失败时不会直接启动第二个实例。
- Web 试玩继续使用 `docmind_bridge.gd` 的进程内 `reload_current_scene()`，不刷新整个 iframe。原生桌面端采用进程重启是因为 Godot 没有跨版本、无需插件的统一进程内脚本热替换协议；这样会重新导入脚本/场景，但游戏内存状态会重置。
- 仅允许 Godot 使用该端点；Unity/Unreal 返回明确提示。新增引擎重载回归测试和路由存在性检查。
