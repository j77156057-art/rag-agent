# DocMind · 支持工具调用的 RAG 问答 Agent

一个面向简历作品集的轻量级 RAG Agent 项目：把文档变成可对话的知识库，并让大语言模型**自主决定调用工具**来回答问题（ReAct 范式），而不是朴素地"检索完直接喂给 LLM"。

## ✨ 核心特性

- **真正的 Agent 循环**：Thought → Action → Observation 多轮推理，LLM 自主决定何时检索 / 计算 / 联网、何时给出最终答案，过程完全可解释、可观测。
- **工具集（9 个，易扩展）**：`search_knowledge`（知识库检索）、`search_assets`（游戏素材筛选）、`calculate`（表达式求值）、`python_exec`（受限沙箱执行 Python，用于计算/数据处理/文本变换）、`web_search`（DuckDuckGo 联网，无需 Key）、`gen_video_prompt`（按 MiniMax H3 三段结构生成视频提示词）；以及**代码问答三件套** `search_code` / `read_file` / `grep`——可索引你的工程源码与配置，回答"某功能在哪实现 / 某函数做什么 / 某配置怎么写 / 某报错在哪"类问题。加一个工具只需在 `tools.py` 的 `TOOLS` 字典里追加一项。
- **多模型 Provider 抽象**：通过 OpenAI 兼容协议统一封装 **通义千问 / DeepSeek / Ollama 本地 / mock**，配置文件一键切换，业务代码零改动。
- **服务化**：FastAPI 把 Agent 封装为 HTTP 服务，SSE 流式返回推理过程与答案；附带单页演示前端。
- **零依赖可跑**：内置 `mock` 模式，无需任何 API Key 即可本地跑通「摄取 → 检索 → Agent 问答」全链路。

## 🧱 技术栈

Python · OpenAI 兼容 SDK（通义千问/DeepSeek/Ollama）· Chroma 向量库 · FastAPI · pypdf

## 📁 目录结构

```
rag-agent/
├── config.py          # 配置中心（provider / 路径 / 参数）
├── llm.py             # LLM 客户端：多 provider 抽象 + 流式 + mock
├── embeddings.py      # 嵌入：云端(通义千问) + 本地兜底
├── vectorstore.py     # Chroma 封装
├── ingest.py          # 文档加载 / 切分 / 入库
├── tools.py           # Agent 工具集
├── agent.py           # ReAct Agent 核心循环 + 记忆
├── api.py             # FastAPI 服务（SSE 流式问答 + 上传）
├── run.py             # 一键启动
├── web/index.html     # 演示前端
├── sample_docs/       # 示例知识库
└── requirements.txt
```

## 🚀 快速开始

```bash
# 1. 建虚拟环境并装依赖（国内建议使用阿里云镜像）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. 配置（可选，默认 mock 模式无需 key）
cp .env.example .env
#   修改 LLM_PROVIDER=qwen 并填入 DASHSCOPE_API_KEY

# 3. 启动（会自动摄取 sample_docs 并启动服务）
python run.py
#   打开 http://localhost:8000
```

### 用真实模型（以通义千问为例）
两种方式任选其一：

**方式 A · 页面内切换（推荐，免重启）**：打开 http://localhost:8000，点右上角「⚙ 模型设置」，选 provider（mock / qwen / deepseek / ollama）、填 Key、可选填模型名与 Embedding 方式，点「保存并切换」即时生效。Key 仅存内存，重启服务后恢复 `.env` 默认值。

**方式 B · 改 `.env`**：
```
LLM_PROVIDER=qwen
EMBEDDING_PROVIDER=qwen
DASHSCOPE_API_KEY=你的key
```
改完需重启服务（`uvicorn` 未开热加载）。

### 调 API（不依赖前端）
```bash
curl -X POST http://localhost:8000/api/chat -F "question=DocMind 支持哪些文件格式？"
```

## 🖥️ 桌面端（原生窗口一键启动）

不想手动起服务、开浏览器？`desktop.py` 把后端服务与单页前端打包成一个原生桌面窗口（Windows 端基于 Edge WebView2），双击即用。

```bash
# 安装桌面窗口依赖（仅需一次）
.venv\Scripts\python.exe -m pip install pywebview

# 一键启动（也可直接双击 run_desktop.bat）
.venv\Scripts\python.exe desktop.py
```

启动后会在后台拉起 FastAPI 服务（127.0.0.1:8000），原生窗口自动加载前端页面；关闭窗口即退出。

- **前置**：Windows 需已安装 Microsoft Edge WebView2 运行时（Win10/11 通常自带）。若环境缺失 WebView2，`desktop.py` 会自动回退用默认浏览器打开页面。
- **与浏览器模式完全一致**：工具链（search_code / read_file / apply_edit / create_file / run_command 等）与 API 全部复用，无功能差异。
- **打包成独立 exe（onedir 目录分发）**：已提供 `docmind.spec`，一条命令产出目录版 `dist\DocMind\DocMind.exe`，双击即用（内部含 FastAPI 服务 + 前端，无需安装 Python；首次构建约几分钟，chroma/onnxruntime 较大，整体约 572 MB）。

  ```powershell
  # 1) 安装打包器（仅需一次）
  .venv\Scripts\python.exe -m pip install pyinstaller

  # 2) 按 docmind.spec 构建（onedir）
  .venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm --log-level WARN

  # 3) 产物：dist\DocMind\DocMind.exe —— 整个 dist\DocMind 目录一起分发，直接双击
  ```

  - 打包要点：`config.py` 已做 frozen 适配——onedir 下按 `sys.executable` 同级的 `_internal/` 定位 `web` 等随包资源（onefile 才走 `sys._MEIPASS`，两种形态都兼容）；运行时索引目录 `dist\DocMind\.chroma` 首次启动自动创建，**开发期 `.chroma` 不打进包**，避免把测试索引带给用户。`docmind.spec` 用 `collect_all("chromadb")`/`collect_all("webview")` 兜底运行时动态子模块，并把 `web` 目录 add-data 进 `_internal`。
  - **分发版不含独立 Python 解释器**：分区 `builtin:py` 校验在进程内做语法编译检查（exe/源码行为一致）；但 playtest 自动测试（pytest/unittest）、cProfile 剖析、`python_exec` 需要真实 Python 环境，请在源码 `.venv` 中使用，exe 内会直接给出明确提示。分区工作台的 git 初始化/提交/回滚还要求目标机器安装 Git 并加入 PATH。详见 [DocMind_BUILD.md](DocMind_BUILD.md)。
  - 前置：Windows 需 Edge WebView2 运行时（Win10/11 通常自带）；缺失时 `desktop.py` 会回退用默认浏览器打开。
  - **每次修复后重新打包的标准验证流程**（py_compile → 逻辑用例 → dev 浏览器实测 → 重建 → exe 冷启动冒烟 → 哈希核对）已沉淀在 [DocMind_BUILD.md](DocMind_BUILD.md) 文末，照做可避免"源码已修但分发版仍旧"。

- **前端"分区"设计**：`web/index.html` 已重做为**左分区侧栏 + 主聊天区**的双栏布局，直观呼应"防 AI 改代码混乱"的核心诉求——
  - 左侧「知识库·文档分区」与「代码库·代码分区」两个独立卡片，实时展示已索引的文档与代码切片数；
  - 代码索引说明里明确写出"分区让 Agent 精准定位、按需修改，避免堆叠与幻觉"，把工具链（search_code→read_file→apply_edit/create_file→run_command 验证）的闭环在界面层可视化；
  - ReAct 推理轨迹（思考/行动/观察/反思）默认折叠、可一键展开，信息密度可控；模型状态、Embedding 选择、上传/索引入口都收敛在侧栏与弹窗中，操作路径更短。

## 🎯 设计要点（面试可讲）

1. **为什么是 Agent 而非朴素 RAG**：简单 RAG 对"需要计算""需要跨文档汇总"的问题力不从心；ReAct 让模型自行规划工具调用，泛化能力更强。
2. **Provider 抽象**：把不同厂商收敛到统一的 OpenAI 协议后，切换模型不改动业务代码——同一种思路也用在作者另一个 Android 项目 `MusicLayout` 的 `AIApiClient` 中。
3. **降级与健壮性**：mock 模式保证无网络/无 key 也有可演示产物；向量检索失败有明确兜底提示。
4. **服务化思维**：用 FastAPI 暴露 SSE 流式接口，前端可逐步渲染"思考/行动/观察"，对应简历中"将 Agent 封装为服务"。

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
