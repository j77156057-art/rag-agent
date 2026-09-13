# DocMind 项目交接清单（给接手 AI）

> **更新时间**：2026-09-14（含 P0-1 实机闭环）｜ **基线提交**：`809a3b9` + 本次嵌入加固
> **全量测试**：**202 项全部通过** ｜ **场景画布自检**：`verify_scene_canvas.py` 54/54
> **引擎嵌入实机自检**：`verify_engine_embed.py` **64/64**（真 Godot 4.7.2 + 真 Win32 宿主，含真实合成键鼠与 UI 调用路径）｜ **浏览器冒烟**：`verify_scene_canvas_ui.mjs` **27/27** ｜ **前端构建**：`npm run build` 通过
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
- **前端**：Vue 3.5 + Vite 5，目录 `frontend/`，构建产物输出到 `../web/`。**单入口** `frontend/workbench.html`
  → 工作台（`src/workbench/`）：CodeMirror 编辑器、文件树、符号/关系图、任务引擎面板、ChatDock，
  以及 2026-09-14 转正的 **场景画布** 与 **运行时时间线**（试玩器弹窗的第 2/3 个 tab）。
  两者都是 `defineAsyncComponent` 异步分块，首屏 JS 体积不受影响（工作台 130KB / gzip 49KB 不变）。
- **桌面分发**：PyInstaller **onedir** 控制台模式 `dist/DocMind/DocMind.exe`（当前第 14 次冻结构建，2026-09-12 18:26；**仍未重新打包**）；随包 MinGit。
- **引擎嵌入（P0-1 已实机闭环，且 UI 可用）**：Godot 4.7.2（`D://Tools//Godot//Godot_v4.7.2-stable_win64.exe`）+ 真 Win32 宿主窗口下实测通过——
  置父/样式摘除、按客户区（或前端指定矩形）对齐、宿主 resize 跟随、**真实合成键鼠（SendInput）送达引擎并回显**、
  解除嵌入后窗口原样还原、停止后无孤儿进程/窗口、父子 DPI 一致（本机 **150% 缩放 = 144 DPI** 实测）。
  UI 侧试玩器有「嵌入工作台」开关：勾上后点「桌面窗口启动」，游戏画面直接落在弹窗里那块引擎视窗上，
  界面照常可用；另有「聚焦 / 解除嵌入 / 停止桌面窗口」。关弹窗或切走 tab 会自动解除嵌入（视窗元素没了，
  继续嵌着只会让引擎画到别处）。浏览器模式下开关自动禁用并提示需要桌面端。
- **LLM/Embedding**：mock / qwen / deepseek / ollama / llamacpp 多 Provider，页面内免重启切换；本机 Ollama(`11434`, bge-m3) 与 llama.cpp(`8080`, Qwen 35B) 免 Key；622fdbc 新增 native embedding。
- **验证基线**：后端 `unittest discover` **202/202 通过**（14 个测试文件，MinGit 在 PATH 时 git 用例实际执行）；
  `verify_scene_canvas.py` 走真实 HTTP 路由 **50/50**（含"每个 op 的 undo 逐字节还原"）；
  `verify_scene_canvas_ui.mjs` 真浏览器 **23/23**（含"空间布局落点与场景坐标严格成比例"）；前端 build 通过。

---

## 3. 架构与模块地图

### 3.1 后端（仓库根的 Python 模块）

| 模块 | 职责 |
|---|---|
| `api.py`（78KB） | HTTP/SSE 总入口：chat、ingest、工作台 fs、regions、engine/*、desktop/host、selection_ai、MCP、GPU 等全部路由 |
| `agent.py` | ReAct 循环（Thought→Action→Observation）、反思重试、弱模型 terminal 工具、代码优先路由（c543047） |
| `tools.py`（105KB） | 工具注册表 `TOOLS`：9 基础 + 受控写（apply_edit/create_file/run_command）+ 11 个 dev_* 分区工具 + 研判分区工具 |
| `regions.py`（47KB） | 分区 2.0：DEFAULT_REGIONS 8 区、regions.json 覆盖、契约校验（DAG 无环/导出存在）、init/scaffold/fill_exports、变更集与回滚 |
| `game_workbench.py`（60KB） | Godot/Unity/Unreal catalog、引擎启停与自动嵌入、运行时事件、任务/资产/bug 工作流 |
| `workbench_fs.py`（56KB） | 沙箱文件树、读写、git 状态/历史/回滚/恢复、符号地图、关系图（继承/挂载/调用边） |
| `symbols.py`（40KB） | 多语言符号抽取（Python ast / GDScript / Java 等），代码感知分块 |
| `ingest.py`（22KB） | 文档/代码摄取与递归分块；跳过 .chroma/dist/.venv/_archived_builds/uploads 等 |
| `mcp_client.py`（20KB，新） | MCP（Model Context Protocol）桥接客户端，168 行测试 |
| `web_export.py`（18KB，新） | 游戏 Web 导出与本地预览（Web player） |
| `scene_runtime.py`（1198 行） | **场景画布内核**：.tscn 行块解析 → 图模型（节点/外部引用/四类边/几何量）＋受控编辑（add/delete/rename/reparent/duplicate/set_props/move/restore，写完自检失败自动回滚，每个 op 回传可原样回放的 undo）＋运行时事件检索（筛选/统计/会话切分/游标清空） |
| `desktop_bridge.py`（6.4KB） | Win32：find_host/find_window/embed/resize/**focus（新增）** |
| `gpu_coordinator.py`（5.2KB） | GPU lease：acquire/release/status + TTL 过期/移交 + nvidia-smi 显存阈值（**单 GPU 串行起步**） |
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

> 逐次构建的改动/验证/哈希核对明细见 `DocMind_BUILD.md`（14 次完整记录，继续追加不要新建文件）。

---

## 5. 待办清单（规划项，未完成；按优先级）

> 每项含【要做什么】【原因】【方案】【验收】。状态以 `809a3b9` 的代码为准，已核对。

### Agent 模型路由与权限（基础层已落地）

新增 `agent_policy.py`、`/api/agent/route`、`/api/agent/routing`、`/api/agent/connectors`、`/api/agent/permission` 和 Skill `agent-model-routing`。`/api/chat` 已在 SSE 首事件返回路由建议并注入 Agent 上下文；开启 `AGENT_AUTO_CLOUD=1` 且配置 `AGENT_CLOUD_PROVIDER` 对应密钥后，复杂请求会临时使用云端 Agent，缺少密钥自动回退本地。连接器清单可供 Agent 选择但不会自动启动。外部授权现写入项目内 `.docmind_permissions.jsonl` 审计日志。连接器 UI、云端密钥管理和 ReAct 内部连接器选择仍待完成。外部路径仅在显式授权下允许进入审批流程，Agent 自身项目始终拒绝写入。

### P1-2　Unity 深度适配

已新增 `/api/engine/inspect?engine=unity`：读取 Unity 版本、`.unity/.prefab` 清单和 `.meta` GUID；Unreal 同接口读取 `.uproject/.uplugin` 与 Source 文件清单。仍待 GUID 引用图、Console/PlayMode、Editor 插件和 Unreal Blueprint/AutomationTool。

### P1-3　Unreal 深度适配

扫描 `.uproject/.uplugin/Source/Build.cs` 建 C++ 符号图；Editor Python/HTTP 插件查 Level Actor/Blueprint；AutomationTool 编译验证；**禁止直接改 `.uasset`**。

### P2-1　GPU 协调补完

- 【现状】lease/acquire/release/status + TTL 过期与移交 + nvidia-smi 阈值已有实现与 `test_gpu_queue.py`（622fdbc）。
- 【待做】显存占用轮询、Ollama `keep_alive=0` 空闲释放、ComfyUI 完成后释放、排队取消/超时、**多 GPU 选择**。**没有实测 CUDA 隔离前，任何文档/UI 不得宣称已隔离。**

### P2-2　ComfyUI 流水线

自动轮询、失败重试、缩略图网格、音频/3D 预览、Prompt/许可证元数据、重复资源分析。

### P3　第 15 次冻结发布

按 `docmind-frozen-release` Skill：py_compile → **202 项测试** → `verify_scene_canvas.py`(54) → `verify_scene_canvas_ui.mjs`(27) → `verify_engine_embed.py`(64) → `npm run build` → PyInstaller（项目 .venv）→ 最小 PATH 冷启动冒烟 → Godot 实机验证 → 前端 6 文件 SHA-256 核对 → **在 `DocMind_BUILD.md` 追加第十五次记录（不新建文件）**；只白名单提交，`agent-golden-eval/` 不提交。

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
- **场景画布尚未支持的能力**（刻意留给后续，不是 bug）：Unity `.unity/.prefab` 场景图（P1-2）、节点属性引用边（`node_paths=PackedStringArray`）的自动跟随改写（改名/换父时只改 `parent` 前缀，NodePath 属性需人工核对）、多场景同时打开、画布上的 Undo/Redo 跨会话持久化。

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
20. **浏览器默认会请求 `/favicon.ico`**：不接这条路由，每个页面都留一条 404，浏览器冒烟的
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

- Agent 连接器：ReAct 工具表已加入 dev_mcp_call，可在确认连接器启用并读取工具清单后调用 MCP 工具；调用失败会转为可审计文本，不会静默执行。

- 云端密钥管理新增 /api/agent/secrets（仅返回 Provider 名称）及 DELETE /api/agent/secrets/{provider}，支持撤销/轮换，密钥内容不回传。

- ReAct MCP 调用已增加连接器启用检查与可选 task_id 绑定，未启用连接器或不存在任务会拒绝调用。

- dev_mcp_call 现在会检查 MCP 参数中的 path/file/scene/asset/script 是否落在 task_id 的 region/allowed_paths 内，越权参数直接拒绝。

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
