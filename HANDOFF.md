# DocMind 项目交接清单（给接手 AI）

> **更新时间**：2026-09-13 ｜ **基线提交**：`622fdbc`（feat(engine): MCP bridge, web player, GPU queue, native embedding）
> **全量测试**：162 项全部通过 ｜ **前端构建**：`npm run build` 通过
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

## 2. 当前基线（2026-09-13）

- **后端**：Python + FastAPI（`api.py`，SSE），入口 `api:app`；Chroma 双集合（文档 `docmind` / 代码 `docmind_code`）。
- **前端**：Vue 3.5 + Vite 5 **多页应用（MPA）**，目录 `frontend/`，构建产物输出到 `../web/`。两个入口：
  - `frontend/workbench.html` → 正式工作台（`src/workbench/`，CodeMirror 编辑器、文件树、任务/场景/引擎面板、ChatDock）；
  - `frontend/spike-canvas.html` → **Vue Flow 画布技术验证（spike，未转正，见 §6）**。
- **桌面分发**：PyInstaller **onedir** 控制台模式 `dist/DocMind/DocMind.exe`（当前第 14 次冻结构建，2026-09-12 18:26；**622fdbc 之后尚未重新打包**）；随包 MinGit；pywebview 原生窗口 + Win32 HWND 嵌入。
- **LLM/Embedding**：mock / qwen / deepseek / ollama / llamacpp 多 Provider，页面内免重启切换；本机 Ollama(`11434`, bge-m3) 与 llama.cpp(`8080`, Qwen 35B) 免 Key；622fdbc 新增 native embedding。
- **验证基线**：后端 `unittest discover` **162/162 通过**（14 个测试文件，MinGit 在 PATH 时 git 用例实际执行）；前端 build 通过；spike 4 项交互验证通过（§6）。

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
| `scene_runtime.py`（7KB） | 场景树解析、单属性写回、运行时事件 JSONL（**基础能力，画布化未做**） |
| `desktop_bridge.py`（6.4KB） | Win32：find_host/find_window/embed/resize/**focus（新增）** |
| `gpu_coordinator.py`（5.2KB） | GPU lease：acquire/release/status + TTL 过期/移交 + nvidia-smi 显存阈值（**单 GPU 串行起步**） |
| `desktop.py` | 控制台启动器、单实例保护、pywebview 窗口与宿主 HWND 注册 |
| `llm.py` / `embeddings.py` / `vectorstore.py` / `config.py` | 多 Provider LLM、嵌入、Chroma 封装、配置与 frozen 资源定位 |
| `engine_adapters.py` | 引擎适配薄封装；`verify*.py` 是独立校验脚本 |
| `docmind.spec` / `make_lnk_pure.py` / `make_shortcut.py` | PyInstaller 配置、纯 Python 手写 .lnk、快捷方式 |

### 3.2 前端 `frontend/src/workbench/`

- `App.vue` 总装；`api.ts` 类型化接口（注意：verify_contracts 等业务"失败"是 HTTP 200 + `{ok:false}`，须独立 fetch，不能走抛错的通用 request）；`composables/workbench.ts` 共享状态。
- 组件：`FileTree.vue`/`FileTreeNode.vue`（树+dirty/tracked）、CodeMirror 编辑器、`SelectionAiPanel.vue`（P2 选区 AI + diff 接受）、`GitHistoryDialog.vue`（P3 历史/回滚）、`RewriteDiffDialog.vue`、`RegionMapDialog.vue`（分区 DAG 治理）、`SceneRuntimePanel.vue`（场景树+属性，622fdbc 大改）、`TaskEnginePanel.vue`、`ChatDock.vue`（622fdbc 新增，markdown 渲染在 `markdown.ts`）。

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
| 2026-09-13 | `c543047` Agent 代码优先路由+健壮动作解析+证据护栏；**`622fdbc` MCP bridge、Web player/导出、GPU lease 队列、native embedding、desktop_bridge focus、场景面板大改、ChatDock**（+4638 行，6 个新测试文件）；Vue Flow 区域画布 **spike 验证通过（未提交）** |

> 逐次构建的改动/验证/哈希核对明细见 `DocMind_BUILD.md`（14 次完整记录，继续追加不要新建文件）。

---

## 5. 待办清单（规划项，未完成；按优先级）

> 每项含【要做什么】【原因】【方案】【验收】。状态以 622fdbc 之后的代码为准，已核对。

### P0-1　Godot HWND 嵌入实机闭环（最高优先）

- 【现状】接口链已通且有单测：pywebview loaded → `desktop_bridge.find_host` → `POST /api/desktop/host` → `engine/start {embed:true}` → `find_window(pid)` → `SetParent + MoveWindow`；`focus()` 已新增（622fdbc）。**但从未在真实 Godot 4.7.2 + 实机窗口下验证输入/焦点/DPI/退出清理。**
- 【原因】嵌入是桌面工作台区别于网页玩具的核心卖点；浏览器标签页不能当 Win32 宿主，未实测不能宣称可用。
- 【方案】① 保存宿主/子窗口状态，停止引擎时先解除父子关系再结束进程，防孤儿窗口；② 接 pywebview `resized/shown/closed` 事件（不同版本事件签名需实测分支）；③ focus 接口在前端引擎面板接线（自动嵌入/独立窗口模式切换与 embedded 状态展示）；④ Godot 4.7.2 下验证 100/125/150% DPI 缩放与键鼠输入。
- 【验收】返回 `embedded:true`；引擎窗口内可正常键鼠操作；宿主 resize 子窗口同步；退出后无孤儿进程；3 档 DPI 不错位。
- 【依据】`.trae/skills/desktop-engine-embedding/SKILL.md`。

### P0-2　场景画布（Vue Flow 转正）

- 【现状】`SceneRuntimePanel.vue` 现为场景树卡片+属性显示；`scene_runtime.py` 仅 `scene_tree / set_scene_property / runtime_events`。**Vue Flow 四项核心交互已通过 spike 硬验证（2026-09-13，§6）。**
- 【原因】.tscn/.unity 场景必须可视化：节点父子层级、实例（instance）、position/transform、资源引用（ExtResource 解析）是游戏开发高频操作；纯列表无法表达层级与连线。
- 【方案】把 spike 的 group+子节点模型迁进正式工作台：① 节点 parent/instance/position/transform/资源引用解析（GDScript/Godot 先行，tscn 文本解析）；② 节点连线、层级树、多选框选、复制、新增、删除；③ 2D/3D 变换写回复用 `set_scene_property`，**所有写入过任务范围校验 + Git 快照**；④ Undo/Redo 与 Diff（可复用 P3 的 LCS/git 基础）。
- 【验收】重开场景位置保持；撤销重做可靠；越权写入被拒；改后 Godot 能正常打开工程（以 Godot 启动无解析错误为准）。

### P1-1　运行时时间线

- 【现状】运行时事件写 JSONL（`scene_runtime.runtime_events`），前端仅列表展示。
- 【方案】多轨道 + 时间缩放 + 类型筛选（方法/信号/变量着色）+ 变量曲线 + 会话分组 + 导出 + 代码跳转；接口建议 `GET /api/runtime/events?from=&to=&type=`、`GET /api/runtime/sessions`、`POST /api/runtime/clear`；后续接 Godot 调试协议。

### P1-2　Unity 深度适配

解析 `.unity/.prefab/.meta` 建 GUID 引用图；Console、PlayMode 验证、Editor HTTP 插件；资产操作必须同步 `.meta`。

### P1-3　Unreal 深度适配

扫描 `.uproject/.uplugin/Source/Build.cs` 建 C++ 符号图；Editor Python/HTTP 插件查 Level Actor/Blueprint；AutomationTool 编译验证；**禁止直接改 `.uasset`**。

### P2-1　GPU 协调补完

- 【现状】lease/acquire/release/status + TTL 过期与移交 + nvidia-smi 阈值已有实现与 `test_gpu_queue.py`（622fdbc）。
- 【待做】显存占用轮询、Ollama `keep_alive=0` 空闲释放、ComfyUI 完成后释放、排队取消/超时、**多 GPU 选择**。**没有实测 CUDA 隔离前，任何文档/UI 不得宣称已隔离。**

### P2-2　ComfyUI 流水线

自动轮询、失败重试、缩略图网格、音频/3D 预览、Prompt/许可证元数据、重复资源分析。

### P3　第 15 次冻结发布

按 `docmind-frozen-release` Skill：py_compile → 162+ 测试 → `npm run build` → PyInstaller（项目 .venv）→ 最小 PATH 冷启动冒烟 → Godot 实机验证 → 前端 6 文件 SHA-256 核对 → **在 `DocMind_BUILD.md` 追加第十五次记录（不新建文件）**；只白名单提交，`agent-golden-eval/` 不提交。

### 其他已记录的改进点

- **B 档浅实现**（2026-09-11 审计结论，仍有效）：`impact_analysis` 是子串 grep、`generate_test_scene` 写死空壳、`simulate_growth` 等比数列玩具、`performance_sample` 仅计时、`approval` 只追加日志不拦截、默认分区 verify 空转。当演示可以，当真工具需要逐个做深或在 UI 标注能力边界。
- **门面文档待更新**：`README.md`/`README_en.md`/`DEMO.md` 仍停留在 9 工具+单页演示时代，未反映工作台/引擎/MCP，择期重写。
- 新落地的 MCP bridge 与 Web player 目前缺产品级使用文档与边界说明。

---

## 6. Vue Flow 画布 Spike 现状与去留决策（2026-09-13）

独立技术验证入口，**零侵入工作台，git 未提交**。涉及：`frontend/spike-canvas.html`、`frontend/src/spike/`（main.ts、RegionCanvasSpike.vue、RegionNode.vue、FileCardNode.vue、spike.css）、`vite.config.ts` 两处（input + vendor-vueflow chunk，均带注释）、5 个依赖 `@vue-flow/{core,background,controls,minimap,node-resizer}`。

**验证结论（4/4 通过，有硬数字与截图）**：
1. `extent:'parent'`：文件卡左拖 420px 被钳在区域左缘（store position x=0）；
2. 父子跟随：拖区域标题栏，区内三卡 deltaX 全部 105，与区域严格同步；
3. 连线：拖 handle 连到另一卡，handle 出现 valid 态、边数 3→4、跨区虚线渲染正确；
4. 滚轮缩放（打到 maxZoom 1.8）、空白平移（+130/+90 零误差）、MiniMap 正常。
构建隔离：vendor-vueflow 独立 chunk（gzip 78.7KB），工作台 chunk 零影响。

**接手者注意的坑（已固化）**：① 节点尺寸必须用顶层 `width/height` 属性，放 `style` 会被 NodeResizer 按 min-* 重置；② 子节点归属字段是 `parentNode`（不是 parentId），group 必须排在子节点之前；③ 附属件从独立包导入；④ v1 交互层是 d3，**合成自动化事件只认 mousedown/mousemove/wheel，pointer 事件无效**。

**决策**：默认按 P0-2 转正（骨架可直接复用）；若决定放弃，删除上述 6 个文件、撤 vite.config.ts 两处改动、`npm uninstall` 5 个包即可整块移除。

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

# 冻结打包（仓库根，发布前先读 .trae/skills/docmind-frozen-release/SKILL.md）
.\.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm
```

---

## 8. 必看的坑（2026-09-13 仍有效）

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
| `sample_docs/docmind_product.md`、`uploads/*` | 产品资料与上传件（uploads 不进版本控制） |

---

## 10. 本次文档整理变更记录（2026-09-13）

- 新建本文件，整合 5 份旧交接/审计文档的全部仍有效信息（待办按 622fdbc 后代码重新核对状态）。
- 删除：旧 `HANDOFF.md`（2026-09-09 版）、`AI_BRIEF.md`、`DEV_WORKBENCH_AUDIT.md`、`HANDOFF_ENGINE_EMBEDDING.md`、`HANDOFF_REMAINING_WORK.md`。
- 同步引用：`.trae/skills/engine-project-setup/SKILL.md` 改指本文件；`分区开发设计.md` 中原指向旧 HANDOFF §6 的引用更新为本文件 §5/§8。
