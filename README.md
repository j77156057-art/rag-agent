# DocMind · 本地游戏开发 AI 工作台

一个**本地、单人、面向游戏 / Mod 工程**的轻量研发脚手架。它要解决的是 AI 写代码最常见的两个失败模式：

1. **幻觉**——AI 凭印象说"伤害计算在 player.py"，其实没有这个文件；
2. **代码堆叠**——数值、UI、行为逻辑全塞进少数文件，越改越乱，一改就崩、无法回滚。

DocMind 的应对分两层：

- **本地 RAG + ReAct Agent**：让模型先**检索 / 定位真实代码与文档**（文件 + 行号 + 证据）再回答，而不是直接编代码。零 API Key 可跑（mock / 本地 Ollama / llama.cpp）。
- **仓库级工作流（工作台）**：在 RAG 之上加**任务分区、每区独立 Git、契约方向校验、变更集回滚、选区 AI、符号 / 关系图、引擎嵌入、场景画布、运行时时间线、GPU 协调**。目标形态是能真正拿来改一个 Godot / Unity / Unreal 工程的脚手架，而不是通用 ALM 平台（不做多用户 / 数据库 / 鉴权）。

---

## ✨ 工作台六件事（2026-09 当前形态）

| 能力 | 一句话 |
|---|---|
| **分区开发** | 一个按钮把工程切成 assets / values / behaviors / levels / ui / audio / net / bugs 等分区，每区**独立 git**；Agent 的写操作被约束在对应分区内，越区写直接拒；依赖是单向 DAG，契约校验防循环耦合 |
| **受控改写** | 所有 AI 写操作走 `apply_edit` / `create_file`（先读后写 + 体积上限 + `.py` 语法校验 + 人工确认）；跨区搬移校验依赖方向；改动可单区提交或**跨区变更集整体回滚** |
| **选区 AI** | 编辑器里选中一段代码 → 解释 / Review / 提问（走 Agent，带证据）或**改写**（走直连快通道 + LCS diff 预览，接受才落盘） |
| **符号与关系图** | 多语言符号抽取（Python `ast` / GDScript / Java…）+ 继承 / 场景挂载 / 调用边的关系图；场景画布另有四类边 |
| **场景画布** | Godot `.tscn` 的**可视化 + 可编辑**画布：层级树 / 空间坐标两种布局，节点父子层级、实例（instance）、position / transform、资源引用一目了然；新增 / 删除 / 改名 / 换父 / 复制 / 改属性 / 拖拽写回位置，**全部可撤销，且撤销能逐字节还原文件** |
| **运行时时间线** | 把游戏跑起来产生的事件（掉血 / 死亡 / 生成 / 变量变化）画成多轨道时间轴：类型筛选、时间缩放、会话分组、数值曲线、导出 JSON、点事件跳代码行 |

配套：**引擎嵌入**（Godot / Unity / Unreal 启停 + Win32 HWND 嵌进工作台）、**Web 试玩**（导出 WASM 在画布里边玩边改）、**MCP 桥接**、**GPU 租约队列**、**桌面打包**（PyInstaller onedir，双击即用）。

## 🧱 技术栈

- 后端：Python · FastAPI（HTTP + SSE）· Chroma 双集合（文档 / 代码）· OpenAI 兼容多 Provider（qwen / deepseek / ollama / llamacpp / mock）· PyInstaller + pywebview
- 前端：Vue 3.5 · Vite 5 · TypeScript · CodeMirror 6 · Vue Flow · 手写深色设计系统
- 验证：`unittest` 202 项 · 场景画布自检 54 项 · 浏览器冒烟 27 项（Playwright + 系统 Edge）· 引擎嵌入实机自检 64 项

## 📁 目录结构

```
rag-agent/
├── api.py                 # HTTP / SSE 总入口（105 条路由：chat / ingest / 工作台 fs / regions /
│                          #   engine / desktop-host / selection-ai / scene / runtime / MCP / GPU）
├── agent.py               # ReAct 循环、反思重试、代码优先路由、证据护栏
├── tools.py               # 42 个工具：9 基础 + 受控写 + 24 个分区 / 研发工具
├── regions.py             # 分区 2.0：声明式配置、契约校验（DAG 无环 / 导出存在）、变更集与回滚
├── scene_runtime.py       # 场景画布内核：.tscn 行块解析 → 图模型 → 受控编辑（可回滚 + 可撤销）
├── workbench_fs.py        # 沙箱文件树、读写、git 状态 / 历史 / 回滚、符号地图、关系图
├── symbols.py             # 多语言符号抽取 + 代码感知分块
├── game_workbench.py      # 引擎 catalog / 启停 / 嵌入、任务 / 资产 / bug 工作流
├── engine_adapters.py     # 引擎适配薄封装
├── desktop_bridge.py      # Win32：查找宿主窗口 / SetParent 嵌入 / resize / focus
├── desktop.py             # 桌面启动器（单实例保护 + pywebview 窗口）
├── gpu_coordinator.py     # GPU 租约队列（TTL 过期 / 移交 + 显存阈值）
├── mcp_client.py          # MCP（Model Context Protocol）桥接
├── web_export.py          # Godot Web 导出与本地试玩
├── llm.py / embeddings.py / vectorstore.py / ingest.py / config.py
├── frontend/              # Vue 工作台（构建产物输出到 ../web）
│   └── src/workbench/components/
│       ├── SceneCanvas.vue / SceneNodeCard.vue / SceneFileCard.vue   # 场景画布
│       └── RuntimeTimeline.vue                                       # 运行时时间线
├── tests/                 # unittest 202 项
├── verify_scene_canvas.py / verify_scene_canvas_ui.mjs   # 场景画布自检 + 浏览器冒烟
├── HANDOFF.md             # ★ 唯一权威交接文档（原因 / 基线 / 待办 / 坑，接手先读它）
├── DocMind_BUILD.md       # 冻结构建档案（发布流程强制在其中追加，不新建文件）
└── 分区开发设计.md         # 分区 2.0 架构设计
```

## 🚀 快速开始

```bash
# 1. 建虚拟环境并装依赖（国内建议阿里云镜像）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. 配置（可选，默认 mock 模式无需 key）
cp .env.example .env      # 改 LLM_PROVIDER=qwen 并填 DASHSCOPE_API_KEY

# 3. 启动后端
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000

# 4. 前端：开发用 vite dev，或构建一次产物给后端托管
cd frontend && npm install && npm run build      # 产物 -> ../web

# 5. 打开
#   工作台 : http://127.0.0.1:8000/workbench
#   问答页 : http://127.0.0.1:8000
```

> 服务地址请用 `127.0.0.1` 而不是 `localhost`（本机 IPv6 解析会连不上）；路径含单引号用户名时 shell 一律用双引号包裹。

### 用真实模型（以通义千问为例）

两种方式任选其一：

- **页面内切换（推荐，免重启）**：打开问答页点右上角「⚙ 模型设置」，选 provider、填 Key、点「保存并切换」即时生效（Key 仅存内存）。
- **改 `.env`**：`LLM_PROVIDER=qwen` / `EMBEDDING_PROVIDER=qwen` / `DASHSCOPE_API_KEY=...`，改完重启服务。

```bash
# 不依赖前端直接调 API
curl -X POST http://127.0.0.1:8000/api/chat -F "question=DocMind 支持哪些文件格式？"
```

## 🖥️ 桌面端（原生窗口一键启动）

`desktop.py` 把后端服务与前端组装成原生桌面窗口（Windows 走 Edge WebView2），双击即用：

```bash
.venv\Scripts\python.exe -m pip install pywebview   # 仅需一次
.venv\Scripts\python.exe desktop.py                 # 或双击 run_desktop.bat
```

- **引擎嵌入**：Godot / Unity / Unreal 的窗口按 Win32 HWND 规则嵌进工作台（`desktop_bridge.py`）。
  **已在 Godot 4.7.2 + 真 Win32 宿主下实机验证**（`verify_engine_embed.py` 64 项全绿）：置父与样式摘除、按客户区（或前端指定的"引擎视窗"矩形）铺排、
  宿主 resize 跟随、**真实合成键鼠（SendInput）送达引擎并回显事件**、解除嵌入后窗口原样还原、停止后无孤儿进程/窗口、父子 DPI 一致（本机 150% 缩放实测）。
  嵌入是**可逆**的：`detach` 会恢复原始父窗口、窗口样式与屏幕位置——不保存这些状态直接 `SetParent(NULL)`，窗口会带着 `WS_CHILD` 变成看不见的顶层窗口。
  试玩器里有「嵌入工作台」开关：勾上后点「桌面窗口启动」，游戏画面直接落在弹窗的引擎视窗上，工作台界面照常可用；
  另有「聚焦 / 解除嵌入 / 停止桌面窗口」；关弹窗或切走 tab 会自动解除嵌入。浏览器模式下开关自动禁用并提示需要桌面端。
  未覆盖：100%/125% 缩放的实机数据（本机显示器当前是 150%，脚本会打印 DPI 并按实际坐标断言）。
- **打包成独立 exe（onedir 目录分发）**：`docmind.spec` 一条命令产出 `dist\DocMind\DocMind.exe`，把整个 `dist\DocMind` 目录一起分发即可，目标机器无需安装 Python。完整流程见 [DocMind_BUILD.md](DocMind_BUILD.md) 与 `.trae/skills/docmind-frozen-release/SKILL.md`。
- **分发版能力边界**：分包 `builtin:py` 校验在进程内做语法检查（exe 与源码行为一致）；但 playtest 自动测试、cProfile 剖析、`python_exec` 需要真实 Python 环境，请在源码 `.venv` 里用；分区的 git 操作要求目标机器装有 Git。

## 🧪 测试与自检

```bash
# 全量单元测试（202 项；MinGit 在 PATH 时 git 用例会实际执行）
.venv\Scripts\python.exe -B -m unittest discover -s tests

# 场景画布 —— 后端自检：进程内起 FastAPI + 临时 Godot 工程，走真实路由，不占端口
.venv\Scripts\python.exe verify_scene_canvas.py

# 引擎嵌入 —— 实机自检（真 Godot + 真 Win32 宿主；会短暂弹窗并自动把鼠标移回原处）
.venv\Scripts\python.exe verify_engine_embed.py

# 场景画布 —— 浏览器冒烟：先在另一个终端起演示服务，再用托管 node 执行
.venv\Scripts\python.exe verify_scene_canvas.py --serve 8011
node verify_scene_canvas_ui.mjs http://127.0.0.1:8011
```

浏览器冒烟会产出 `docs/screenshots/scene-canvas.png` 与 `docs/screenshots/runtime-timeline.png`。

---

## 🗺️ 场景画布

把 `.tscn` 画成可操作的节点图。**为什么不是"把节点树画成容器套容器"**：真实场景动辄 4–6 层，容器嵌套表达"归属"还行，但场景开发真正高频的是**空间关系**（position / transform），容器根本表达不了"这几个东西该画在哪"。所以画布用**扁平节点 + 四类边**：

| 边 | 含义 | 样式 |
|---|---|---|
| `hierarchy` | 父子层级（上 → 下） | 灰色实线 |
| `script` | 节点 ↔ 脚本文件 | 紫色虚线 |
| `instance` | 节点 ↔ 被实例化的子场景 | 青色点线 |
| `reference` | 节点 ↔ 贴图 / 材质等资源 | 灰色（默认隐藏，可开） |

两种布局：**层级布局**（DFS 前序的水平树）与**空间布局**（直接按场景坐标落点，拖拽即回写 `position`）。双击文件卡在编辑器打开该脚本 / 场景；右侧检查器列出全部属性。

**安全与可逆性**（这部分才是真功夫，不是画得像就行）：

- 所有写入经双层沙箱（`code_root` 包含性 + 分区 / 契约保护）；属性值含换行会被拒（防注入）；
- 改写走"行块模型"，**写完即自检**（父节点存在 / 同级不重名 / 父块先于子块），任一项不通过就**原样回滚**；
- 每个操作都回传一条与接口请求体**同形**的 `undo`，前端原样回传即可撤销——并有回归用例保证**撤销后文件逐字节还原**（含空行位置与属性顺序）。

## ⏱️ 运行时时间线

游戏跑起来后事件写进 `.docmind_runtime.jsonl`（引擎侧按约定打印 `DOCMIND_EVENT {json}` 即被抓取）。时间线把这些事件画成多轨道时间轴：按类型分轨、按来源 / 会话 / 关键字筛选、时间缩放、数值指标曲线、导出 JSON、点事件跳代码行。清空时用**字节游标**归零，不截断正在被引擎写入的日志。

---

## 🎯 设计要点（面试可讲）

1. **为什么是 Agent 而非朴素 RAG**：简单 RAG 对"需要计算""需要跨文档汇总"的问题力不从心；ReAct 让模型自行规划工具调用，泛化能力更强。
2. **Provider 抽象**：把不同厂商收敛到统一的 OpenAI 协议后，切换模型不改动业务代码——同一种思路也用在作者另一个 Android 项目 `MusicLayout` 的 `AIApiClient` 中。
3. **降级与健壮性**：mock 模式保证无网络 / 无 key 也有可演示产物；向量检索失败有明确兜底提示。
4. **服务化思维**：用 FastAPI 暴露 SSE 流式接口，前端可逐步渲染"思考 / 行动 / 观察"，对应简历中"将 Agent 封装为服务"。
5. **子进程而非进程内执行**：`python_exec` 走 `subprocess` + 12s 超时，LLM 生成的代码永远不会污染主进程状态；
   同理 playtest / 构建验证都跑独立进程，失败只返回文本而不是拖垮服务。
6. **为什么分区用「每区独立 git 仓库」而不是单仓库分支 / tag**：要的是"出错后只回滚该分区"，
   独立仓库语义最干净；跨区功能再用**变更集**把多区的 commit 绑在一起整体 revert，比单仓库更可控。
7. **为什么场景画布用扁平节点 + 四类边，而不是容器嵌套**：见下文「场景画布」一节。
   配套设计的可逆性链路是重点——**每个写操作回传一条与接口请求体同形的 `undo`，前端原样回传即撤销**，
   不在工程里落盘副本；并用回归用例钉死"撤销后文件逐字节还原"。这条不变量反过来约束了后端实现
   （.tscn 里空行归属上一个块，所以任何"顺手规整空行"的清理都会破坏可逆性——代码里有注释守着）。
8. **为什么值得写两个 `verify_*` 脚本**：单元测试量不到"接口字段名漂移"和"CSS 没加载"这类跨层问题。
   实际上"Vue Flow 样式漏 import + `manualChunks` 把它的 CSS 切进独立 chunk 导致永不加载"这个 bug
   就是浏览器冒烟抓到的——它不报错，只是节点在画布里堆成一列，靠数节点/数连线永远发现不了。
   所以自检脚本的断言要盯**几何比例**（落点差值 ÷ 坐标差值必须是常数），不是盯"元素存在"。

## 🎮 素材筛选工具（`search_assets`）

针对"做游戏/需要美术资源"的场景，新增一个可让 Agent 按需筛选素材的工具。

**为什么不直接爬素材库 API？** Kenney / OpenGameArt / itch.io 要么没有稳定的公开 API，要么被 Cloudflare 保护（爬取易失败且不礼貌）。业界做法（如 Arcane Assets MCP）同样是维护一份本地/远程的素材清单 JSON，由 Agent 在其上做检索。因此这里用一份人工精选、可持续扩充的 `assets_catalog.json`（目前含 Kenney CC0 系列 + OpenGameArt 示例共 26 条），配合 `search_assets` 的关键词展开与打分排序实现"按提问筛素材"。

- **能力**：按关键词（支持中文同义词展开，如"角色/精灵→character/sprite"）、类型（`2d-sprites`/`tilesets`/`ui`/`audio`/`fonts`/`3d`）、许可（`CC0`/`CC-BY`/`CC-BY-SA-3.0`）筛选。
- **扩展**：往 `assets_catalog.json` 追加条目即可，工具无需改动；真实接入可在 `tools.py` 中把 `search_assets` 换成对远程素材索引/商店 API 的调用。
- **演示**：
  ```bash
  curl -X POST http://localhost:8000/api/chat -F "question=帮我找一个 CC0 的角色精灵素材"
  ```
  在 mock 模式下，LLM 会自动识别"素材"意图并优先调用 `search_assets`。

## 🔧 代码执行（`python_exec`）

让 Agent 真正"动手"而非只动嘴：把自然语言意图转成可执行 Python 在受限子进程中运行，得到确定性的计算结果或数据变换产物。

- **用途**：数值/统计计算、轻量数据处理、文本变换、为可视化/下游工具生成结构化数据等。相比 `calculate`（只能做算术），`python_exec` 能跑任意 Python。
- **健壮性设计**：
  - 输入来自 LLM，模型常把代码裹在 ```` ```python ```` 围栏或加一句 "python" 提示行——工具会先用正则把这些噪音清理掉，只留可执行代码；清理后为空则直接返回「未提供有效代码」，避免把三引号当代码误执行。
  - 通过 `subprocess.run([sys.executable, "-c", code], ...)` 在当前用户权限下运行，超时 **12s**（死循环会返回超时提示而非卡死），stdout/stderr 截断到 1500 字。
  - 这是本地开发工具，**仅用于可信代码**；若要放开到不可信场景，需加沙箱/资源限额。
- **演示**：
  ```bash
  curl -X POST http://localhost:8000/api/chat -F "question=用代码算一下 1 到 100 所有平方数的和"
  ```
  Agent 会调用 `python_exec` 执行 `sum(i*i for i in range(1,101))`，返回 `338350`。

## 🌐 联网搜索（`web_search`）

当知识库不足，或问题有时效性/外部信息时，Agent 可自主联网补充证据，而不是只会"答不知"。

- **实现**：直接抓取 DuckDuckGo HTML 结果页（`urllib.request` + 正则提取标题/摘要/跳转链接，并还原 `uddg=` 编码后的真实链接），**无需 API Key**。
- **降级**：若运行环境访问不了外网，或 DDG 改版导致未解析到结果，工具会返回明确原因（如"搜索失败: …（请确认运行环境能访问外网，或换用带 Key 的搜索引擎）"），Agent 据此改用知识库或坦诚告知，不会假装搜索成功。
- **可替换**：只需改 `tools.py` 里的 `web_search` 一个函数，即可换成 SerpAPI / Bing 等带 Key 的方案，Agent 侧零改动。
- **演示**：
  ```bash
  curl -X POST http://localhost:8000/api/chat -F "question=查一下 MiniMax H3 模型支持的最大视频时长"
  ```

## 🎬 视频提示词生成（`gen_video_prompt`）

把一句中文创意，转成符合 **MiniMax H3** 三段结构、可直接粘贴进 ComfyUI `MiniMaxH3ImageToVideo` 节点的视频提示词——打通"文字创意 → 可生成视频"的链路。

- **为什么是确定性拼装 + LLM 润色**：早期让模型直接写，弱模型（如 7B）会把"三段结构"理解成"三幕结构"而输出错误格式。改为**三段骨架（integrated_multimodal_description / overall_soundscape / non_diegetic_music）由工具确定性拼装**，保证永远合规；LLM 只负责润色 Shot 1 的英文分镜描述，失败时回退到确定性模板句。无论模型强弱都产出可用提示词。
- **声音设计自动贴合场景**：根据关键词（雨/夜/风/海/城/森/雪…）自动填充画内音（diegetic），并附非画内配乐模板，输出带约束说明（无负面词、FPS=24、时长 4–15s 等）。
- **演示**：
  ```bash
  curl -X POST http://localhost:8000/api/chat -F "question=给我生成一个雨夜城市的视频提示词"
  ```
  Agent 调用 `gen_video_prompt`，输出含 `integrated_multimodal_description` / `overall_soundscape` / `non_diegetic_music` 三段结构，画内音自动带 "steady rain" + "distant night traffic" + "low city hum"。

## 💻 代码问答模式（`search_code` / `read_file` / `grep`）

把 Agent 从"只能读文档"升级为"也懂你的工程代码"：索引一份源代码目录后，它能检索函数/类、打开具体文件、按正则定位符号或报错——这正是做游戏/大项目时防止 AI「代码堆叠、凭空幻觉」的关键：让 AI 先检索到已有结构，再精准改某一处，而不是往一个文件里硬塞。

- **怎么用**：把代码根目录交给 DocMind 索引一次即可（之后所有代码问答都基于这份索引）：
  ```bash
  curl -X POST http://localhost:8000/api/ingest_code -F "root=C:/path/to/your/game-project"
  ```
  也可在 `.env` 里固定 `CODE_ROOT`，启动后自动可用。
- **三个工具**：
  - `search_code(query)`：在已索引源码/配置中检索相关函数、类、配置片段（按符号名+路径呈现），回答"某功能在哪实现/某函数做什么"。
  - `read_file(path)`：读取代码库某个文件（path 相对 `code_root`），需要看完整实现时用。
  - `grep(pattern)`：按正则在代码库里搜文本/符号，返回 `文件:行号: 内容`，定位某变量/某报错位置时用。
- **设计要点（面试可讲）**：
  - **代码感知分块**：散文按段落切即可，代码必须按「文件 + 函数/类」切——否则一个函数被腰斩、两个不相关的函数被粘成一段，检索质量会崩。Python 用 `ast` 精确拿到每个 `def/class`（含方法）的起止行与名字；其它语言用正则启发式识别定义行。切片过大时再按行兜底切。
  - **独立集合**：代码存进单独的 Chroma 集合（`docmind_code`），与文档集合互不污染；`search_knowledge` 管文档，`search_code` 管代码。
  - **路径沙箱**：`read_file` / `grep` 只允许访问 `code_root` 之内，越界访问会被拒绝，不会误读本机其它文件。
  - **跳过噪音**：索引时自动跳过 `.git` / `node_modules` / `__pycache__` / `.venv` 等目录，并跳过超大文件（>500KB），避免把构建产物/压缩包塞进索引。
- **演示**：
  ```bash
  # 索引后，问 Agent：
  curl -X POST http://localhost:8000/api/chat -F "question=伤害计算是在哪个函数里实现的？把那段代码给我看看"
  # Agent 会先 search_code 定位 DamageCalculator.compute，再用 read_file 打开具体文件。
  ```
- **注意**：代码问答依赖语义检索，建议使用真实 embedding（`.env` 设 `EMBEDDING_PROVIDER=qwen`）；离线 `local` 模式向量为随机占位，仅用于跑通链路、检索结果不具语义意义。切换 embedding 维度时只需重建文档集合，代码集合可独立保留。
