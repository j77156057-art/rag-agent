# DocMind 项目交接清单（给接手 AI）

> **更新时间**：2026-09-14（P2-1 GPU 协调收尾硬化 + 真机空闲卸载验证，`82d092c`；P2-1 主体与 P1-2 在 `6e9b95b`，均尚未推送）｜ **基线提交**：`eeb73ab`（本地另有校准提交 `981b116` 未推送）
> **全量测试**：**258 项全部通过** ｜ **场景画布自检**：`verify_scene_canvas.py` 54/54
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
  工作台的重组件用 `defineAsyncComponent` 异步分块，首屏 JS 体积不受影响（工作台 137KB / gzip 52KB）。
- **桌面分发**：PyInstaller **onedir** 控制台模式 `dist/DocMind/DocMind.exe`（当前**第 17 次**冻结构建，2026-09-14 20:56；已重新打包，含本轮 RAG 问答质量收紧）；随包 MinGit。
- **引擎嵌入（P0-1 已实机闭环，且 UI 可用）**：Godot 4.7.2（`D://Tools//Godot//Godot_v4.7.2-stable_win64.exe`）+ 真 Win32 宿主窗口下实测通过——
  置父/样式摘除、按客户区（或前端指定矩形）对齐、宿主 resize 跟随、**真实合成键鼠（SendInput）送达引擎并回显**、
  解除嵌入后窗口原样还原、停止后无孤儿进程/窗口、父子 DPI 一致（本机 **150% 缩放 = 144 DPI** 实测）。
  UI 侧试玩器有「嵌入工作台」开关：勾上后点「桌面窗口启动」，游戏画面直接落在弹窗里那块引擎视窗上，
  界面照常可用；另有「聚焦 / 解除嵌入 / 停止桌面窗口」。关弹窗或切走 tab 会自动解除嵌入（视窗元素没了，
  继续嵌着只会让引擎画到别处）。浏览器模式下开关自动禁用并提示需要桌面端。
- **LLM/Embedding**：mock / qwen / deepseek / ollama / llamacpp 多 Provider，页面内免重启切换；本机 Ollama(`11434`, bge-m3) 与 llama.cpp(`8080`, Qwen 35B) 免 Key；622fdbc 新增 native embedding。
- **验证基线**：后端 `unittest discover` **258/258 通过**（含 `test_unity_graph.py` 4 例、`test_gpu_coordinator.py` 31 例）；
  `verify_scene_canvas.py` 走真实 HTTP 路由 **54/54**（含"每个 op 的 undo 逐字节还原"）；
  `verify_engine_embed.py` 真 Godot + 真 Win32 宿主 **68/68**；
  `verify_scene_canvas_ui.mjs` 真浏览器 **27/27**（含"空间布局落点与场景坐标严格成比例"）；前端 build 通过。

---

## 3. 架构与模块地图

### 3.1 后端（仓库根的 Python 模块）

| 模块 | 职责 |
|---|---|
| `api.py`（78KB） | HTTP/SSE 总入口：chat、ingest、工作台 fs、regions、engine/*、desktop/host、selection_ai、MCP、GPU 等全部路由 |
| `agent.py` | ReAct 循环（Thought→Action→Observation）、反思重试、弱模型 terminal 工具、代码优先路由（c543047） |
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

> 逐次构建的改动/验证/哈希核对明细见 `DocMind_BUILD.md`（17 次完整记录，继续追加不要新建文件）。

---

## 5. 待办清单（规划项，未完成；按优先级）

> 每项含【要做什么】【原因】【方案】【验收】。状态以 `eeb73ab` 的代码为准，2026-09-14 接手时逐项核对。

### Agent 模型路由与权限（基础层 + UI 已落地，2026-09-14 接手核对）

`agent_policy.py`、`/api/agent/route`、`/api/agent/routing`、`/api/agent/connectors`、`/api/agent/permission`、`/api/agent/secrets`(GET/DELETE)、`/api/agent/approvals`(含 decide 与 before/after unified diff)、`/api/agent/external-write` 和 Skill `agent-model-routing` 均已落地；前端 `AgentPolicyPanel.vue` 已在工作台顶栏接线（路由状态/连接器清单/外部路径审批/Diff 批准拒绝）。`/api/chat` SSE 首事件返回路由建议并注入上下文；`AGENT_AUTO_CLOUD=1` + 云端密钥时复杂请求走云端 Agent，缺密钥自动回退本地；云端发送前经 `redact_for_cloud` 脱敏并截断上下文；`dev_mcp_call` 校验连接器启用状态、可选 task_id 绑定与参数路径越权；外部写入需 approved approval_id + 精确路径，写前生成 `.docmind.bak`；密钥 DPAPI/Fernet 往返、撤销、授权均有回归测试。外部授权写项目内 `.docmind_permissions.jsonl` 审计日志，Agent 自身项目始终拒绝写入。
**仍待做（非阻塞）**：连接器 UI 目前只展示清单与启用状态，启停/配置管理界面未做；ReAct 内部连接器选择依赖工具清单读取，尚无"Agent 自主切换连接器"的策略层。

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

自动轮询、失败重试、缩略图网格、音频/3D 预览、Prompt/许可证元数据、重复资源分析。

### P3　冻结发布（标准流程，已执行至第 17 次）

按 `docmind-frozen-release` Skill：py_compile → **250 项测试** → `verify_scene_canvas.py`(54) → `verify_scene_canvas_ui.mjs`(27) → `verify_engine_embed.py`(68) → `npm run build` → PyInstaller（项目 .venv）→ 最小 PATH 冷启动冒烟 → 前端产物 SHA-256 核对 → **在 `DocMind_BUILD.md` 追加记录（不新建文件）**；只白名单提交，`agent-golden-eval/` 不提交。
第 15/16/17 次均已执行（最新 `344007e`=第 16 次、`DocMind_BUILD.md` 第 17 次章节=20:56 构建）。
**第 17 次交付卡点已解除（2026-09-14 接手核证）**：第 16 次旧实例已关闭、无 DocMind 进程残留；第 17 次产物（`dist/DocMind/DocMind.exe`，mtime 2026-09-14 20:56，19,673,210 字节）已换入 `dist/DocMind`，临时目录 `D:/Temp/docmind_rel15` 已清理。下次发布直接从第 18 次流程开始。

### 其他已记录的改进点

- **B 档浅实现**（2026-09-11 审计结论，仍有效）：`impact_analysis` 是子串 grep、`generate_test_scene` 写死空壳、`simulate_growth` 等比数列玩具、`performance_sample` 仅计时、`approval` 只追加日志不拦截、默认分区 verify 空转。当演示可以，当真工具需要逐个做深或在 UI 标注能力边界。
- **门面文档**：`README.md` 已于 2026-09-14 刷新（反映工作台/分区/引擎/画布现状）；`README_en.md` 与 `DEMO.md` **仍是 9 工具+单页演示时代的内容，择期重写**。
- 新落地的 MCP bridge 与 Web player 目前缺产品级使用文档与边界说明。
- **引擎嵌入的残留**（不影响"已可用"）：本机显示器当前是 **150% 缩放**，100%/125% 未实测——
  `verify_engine_embed.py` 会打印当前 DPI 并按实际坐标断言，改了缩放直接重跑即可补档。
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

- 路径含单引号用户名 `C:\Users\h'h'h\...`：shell 一律**双引号**包裹；服务地址用 `127.0.0.1` 不用 localhost；沙箱内 curl 加 `--noproxy '*'`。
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

---

## 9. 仓库内文档地图

| 文档 | 用途（不要删） |
|---|---|
| `HANDOFF.md`（本文件） | 唯一权威交接：原因/基线/待办/坑，每次里程碑后更新 |
| `DocMind_BUILD.md` | 14 次冻结构建档案；发布 Skill 强制在其中追加，不新建 |
| `分区开发设计.md` | 分区 2.0 架构设计，`regions.py` 注释引用 |
| `game_project_template.md` | 给用户游戏工程（code_root）使用的目录骨架与 DOCMIND_RULES 模板 |
| `README.md` / `README_en.md` / `DEMO.md` | 对外门面/演示（内容偏旧，待按 §5 重写，勿当现状依据） |
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

仍待实现：Unreal Blueprint/Level 深度桥接、GPU 跨进程真实显存隔离与优先级持久队列、ComfyUI 后台自动轮询 UI/取消任务/许可证与重复资源分析。
- ComfyUI 新增后台 watcher：`POST /api/comfy/watch/{prompt_id}` 启动有界后台轮询，`GET` 查询状态；前端可持续显示完成结果而不阻塞请求。
- watcher 生命周期已加入单元测试；全量测试基线仍为 215 项通过（另加 watcher 测试通过）。
仍待实现：GPU 跨进程真实显存隔离、多 GPU 任务绑定/优先级持久队列；Unreal Blueprint/Level Editor 插件桥接；ComfyUI 取消任务、结果网格、许可证/来源与重复资源分析。
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
- 新增受控 ComfyUI 服务生命周期 API：`POST /api/comfy/start` 按 `DOCMIND_COMFY_ROOT`/D 盘便携目录启动 `python_embeded` + `ComfyUI/main.py`，登记真实 PID；`POST /api/comfy/stop` 终止并注销 PID。默认不自动启动，启动/停止均需用户显式操作；已完成编译与专项回归。
- ComfyUI 任务面板已接入服务控制按钮（启动/停止/刷新状态），调用上述生命周期 API 并显示 PID；前端构建验证通过，服务仍保持用户显式启动策略。

