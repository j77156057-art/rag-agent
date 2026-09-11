# DocMind — 项目交接 / 审查文档（完整版 · 2026-09-09）

> **用途**：整篇直接交给接手 AI 或审查 AI 即可无缝续做 / 审查。照「1 定位 / 2 当前进度 / 3 运行环境 / 4 文件地图 / 5 桌面端打包 / 6 待办与审查重点 / 7 验证 / 8 坑」阅读。
> 本文件已包含本会话全部进展（T2–T4 实现、桌面端 onedir 控制台模式打包、自包含 dist、纯 Python 手写 .lnk），替代早期旧版。

---

## 1. 项目一句话定位
本地 **RAG + ReAct 工具调用 Agent**（Python）。把「文档问答 + 代码问答」塞进一个会思考、会调工具的 Agent 里，FastAPI + SSE 服务化，单页前端。
**目标场景**：帮开发者在本地读懂/检索自己的工程代码与文档，作为「防 AI 代码堆叠 / 幻觉」的搭档——你问"伤害计算在哪实现"，它用工具定位文件与行号，而不是直接编代码。
**零依赖可跑**：默认 `mock` 模式不需要任何 API Key，全链路能演示。

---

## 2. 当前进度（截至 2026-09-09）
- **28 个工具**（9 基础 + 3 增强写工具 + 1 分区初始化 + 11 分区开发 dev 工具 + 4 Agent 研判分区工具）：
  - 基础 9：`search_knowledge`(文档检索) / `search_assets`(游戏素材筛选) / `calculate` / `python_exec`(Python 沙箱) / `web_search`(DuckDuckGo 免 Key，降级优雅) / `gen_video_prompt`(H3 三段提示词) / `search_code` / `read_file` / `grep`
  - 增强 3（受控写）：`apply_edit`(受控改) / `create_file`(受控建) / `run_command`(受控跑命令)
  - 分区开发 `init_regions`(初始化四+/N 个独立分区 + 每区 git init + 写契约)
  - 分区开发 dev_*（11 个，见下条）：`dev_list_regions` / `dev_region_read` / `dev_region_edit` / `dev_region_verify` / `dev_refactor` / `dev_commit` / `dev_commit_all` / `dev_list_changesets` / `dev_rollback_changeset` / `dev_verify_contracts` / `dev_rebuild_index`
  - **Agent 研判分区（4 个，见 §4/§6）**：`list_dir`(目录勘察) / `dev_propose_regions`(据代码库研判方案) / `dev_apply_regions`(落地自定义方案) / `dev_add_region`(增补单分区)
- **ReAct 推理**：Thought→Action→Observation→Final 流式输出；工具调用失败会 **自我反思换工具重试**（`_MAX_REFLECTIONS=2`）。
- **弱模型兼容**：`calculate`/`python_exec`/`gen_video_prompt` 是「产出即答案」类，执行后直接作 Final（terminal 模式），防止 7B 模型改写导致数字/提示词错乱。
- **多 Provider LLM**：`mock`/`qwen`/`deepseek`/`ollama` + **页面内免重启切换**（运行时覆盖层 `_RUNTIME`）；切 Ollama 前 12s 探活拦截坏模型（显存不足崩溃会被拦）。
- **代码问答模式**：独立代码集合 `docmind_code` + 代码感知分块（Python 走 `ast` 按 def/class 切取符号名，其它语言走正则启发式）+ `search_code`/`read_file`/`grep`（路径沙箱限定 `code_root`）。
- **前端**：左栏双分区（📁 代码库 / 📄 文档库）实时展示切片数，ReAct 轨迹默认折叠可展开，深色主题。
- **T 状态**：**T1 ✅** 网页代码索引入口；**T2 ✅** `apply_edit`；**T3 ✅** `run_command`+`create_file`；**T4 ✅** 代码分块增强；**T5 ⏳** 接真实工程验证（未做，决定 T2/T3 是否对外开放）。
- **桌面端 ✅** onedir 打包 + 程序图标 + 桌面快捷方式（详见 §5）。
- **分区开发（Region-based Dev）2.0 ✅（2026-09-09）**：在 1.0 脚手架之上全面落地。新增/重写 `regions.py`（配置驱动 `DEFAULT_REGIONS` 8 区 + 支持 `regions.json` 覆盖；`depends_on`/`exports`/`verify` 元数据；`verify_contracts` 依赖无环/导出存在/目标存在校验；`commit_all` 跨区变更集 `dev_changesets.jsonl` + `rollback_changeset` 整体回滚）。`tools.py` 实现全部 **11 个 `dev_*` 工具**（分区内受控读写 + 前缀校验、单区/跨区校验、跨区安全搬移 `dev_refactor` 依赖方向校验、提交与变更集回滚）并注册 `TOOLS`。`api.py` 新增 `GET /api/regions`、`GET /api/verify_contracts`、`POST /api/rebuild_index`、`POST /api/region_verify`、`POST /api/commit_all`、`GET /api/changesets`、`POST /api/rollback_changeset`、`POST /api/dev_commit`。前端「🛠 开发模式」工作台升级：依赖图 SVG + 分区卡片（依赖/导出/脏状态 + 校验/提交按钮）+ 变更集列表（回滚）。`agent.py` SYSTEM_PROMPT 已含 `dev_*` 工具清单与选择指引。详见 `分区开发设计.md`。**越区写已被 `dev_region_*` 硬拦截（双层沙箱）；契约软约束仍由 DOCMIND_RULES.md 注入，需跑真实模型才触发**。`dev_asset_get`/`dev_capture_bug` 仍待做（见 §6）。
- **Agent 研判分区 ✅（2026-09-09）**：用户明确「默认 8 区仅作初始建议，真实分区由 Agent 据代码库判断」。落地 `regions.py` 的 `propose_regions`（扫描顶层目录 + 资源类型扩展名，给 8 区 `detected`/`evidence` 证据、核心区恒含、并识别代码库特有的可独立模块 `custom_suggestions`）、`_normalize_regions`（校验/补全自定义方案）、`init_regions(root, regions_list=None)`（`regions_list` 覆盖写 `regions.json` 并初始化）。`tools.py` 新增 `list_dir`/`dev_propose_regions`/`dev_apply_regions`/`dev_add_region`（均注册 `TOOLS`）；`api.py` 新增 `POST /api/propose_regions`/`POST /api/apply_regions`/`POST /api/add_region`；前端工作台新增「🤖 智能研判分区」区块（研判/应用推荐方案/新增分区）；`agent.py` SYSTEM_PROMPT 增加「Agent 研判分区」工作流指引。详见 `分区开发设计.md` §3.1。
- **问答区「增强提示词」✅（2026-09-10）**：footer 加「✨ 增强」按钮（`#enhanceBtn`），调用 `POST /api/enhance_prompt {prompt}`，把 LLM 重写后的提示词替换回输入框供确认后发送。`api.py` 新增 `EnhancePromptReq` + `_local_enhance` + `POST /api/enhance_prompt`：优先用 `LLMClient().chat` 做 LLM 重写（meta-prompt 要求保留原意、明确意图、补约束、不臆造路径）；未配置可用模型或调用异常时降级为本地规则增强，返回 `mode:"local"` 与 `note`。默认 mock 模式走本地兜底。重建 dist 于 14:57。（2026-09-10 续）`config.PROVIDERS` 新增 `llamacpp`(base_url=http://localhost:8080/v1, 免 Key)；`/api/enhance_prompt` 可用性判定改为「有 api_key_env 才要 Key」，使 ollama/llamacpp 免 Key 本地 provider 不再被误判 local 兜底；并修复 llama.cpp 对「纯 system 单消息」报 400 模板解析失败——增强请求改 system(元指令)+user(草稿) 双消息。当前 .env 切 `LLM_PROVIDER=llamacpp`（真模型经 `D:/llama.cpp/cuda131/llama-server.exe` 加载量化 Qwen 35B/nvfp4/8080 验证通过）；embedding 仍 local 占位(检索非语义)。（2026-09-10 15:51）`docmind.spec` 重建 onedir，新 `dist/DocMind/DocMind.exe`(build_time 2026-09-10 15:51:59) 已含上述 llamacpp provider + 增强修复，运行时读 `rag-agent/.env`(=llamacpp)，验证 `/api/enhance_prompt`→mode:llm(真 llama.cpp 重写)。**重建坑**：该版 PyInstaller COLLECT 未把 exe 拷到 `dist/DocMind/` 顶层(只放 `_internal`)，需手动 `cp build/docmind/DocMind.exe dist/DocMind/DocMind.exe` 补全；源模块打包为 .pyc/PKG，验证以运行时行为为准而非 grep 源码。
- 双语文档 `README.md`/`README_en.md`、`DEMO.md`、可视化 ReAct 架构图。
- **代码审查 11 项修复 + 第四次重建 ✅（2026-09-11 13:32）**：对当日上午其他 AI 的更新（审批门禁/真实测试/默认校验/Ollama 引导/CORS/SSRF 白名单）做双路交叉审查，修复 1 高危（index.html 空引用 `resetCodeBtn` 中止整个 script，工作台全瘫）、1 中高危（frozen 下 `sys.executable` 即 DocMind.exe，builtin:py/playtest/cProfile/python_exec 全失效；builtin:py 已改进程内 compile）、9 中低危（Ollama tag 归一化、按需警告、白名单 `*.` 语义、run_command 拦 git 写操作、SSRF 限 scheme+30x 逐跳校验、401 补 CORS、探活 60s+线程池化、git 缺失预检零残留、审批文案/死代码）。39 逻辑用例 + dev 浏览器实测（console 零错误）+ exe 冷启动冒烟全过；新 `dist/DocMind/DocMind.exe` build_time 13:32:04，冻结前端与源码 SHA-256 一致。**完整修复清单、验证记录与标准「修复→打包」流程见 `DocMind_BUILD.md` 第四节**。

---

## 3. 运行环境（硬事实，照搬，别改）
- **绝对路径含单引号用户名** `C:\Users\h'h'h\...`，shell 一律用**双引号**包裹路径（单引号会被吞）。
- **venv**：`<repo>/.venv/Scripts/python.exe`（已装 chromadb / fastapi / pypdf / openai / python-dotenv / uvicorn / pywebview / pyinstaller 等）。
- **启动**：`cd rag-agent && .venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000`（用 127.0.0.1，不是 localhost，避免代理解析失败）。
- **默认** `LLM_PROVIDER=mock`、`EMBEDDING_PROVIDER=local` → 零密钥离线可跑；接真实模型改 `.env`（`LLM_PROVIDER` + 对应 Key）。本机已有本地模型：Ollama 在 11434（qwen2.5:7b / qwen3:14b / qwen3.6:35b-a3b）；llama.cpp 在 `D:/llama.cpp/cuda131/llama-server.exe` 加载量化 Qwen 35B(nvfp4) 于 8080。`.env` 当前用 `LLM_PROVIDER=llamacpp`（免 Key）。
- **embedding 三选一**：`local`(随机256维占位，仅跑通链路) / `qwen`(通义 text-embedding-v3，需 DASHSCOPE_API_KEY) / **`ollama`(本机 Ollama `bge-m3` 多语言，零 Key，已默认启用)**。语义检索零 Key 路径已打通。

---

## 4. 文件地图（改哪改什么）
- `config.py`：`PROVIDERS`（qwen/deepseek/ollama/llamacpp）+ 常量 + 内存运行时 `_RUNTIME`（`get_runtime`/`set_runtime`）+ **onedir/onefile 资源定位**（见 §5.2）。代码模式项：`CODE_COLLECTION_NAME`(docmind_code) / `CODE_ROOT` / `CODE_CHUNK`(1200)。
- `llm.py`：`LLMClient`（多 provider + 流式 + mock）。
- `embeddings.py`：`EmbeddingClient.embed(list[str]) -> list[vec]`。支持 `qwen`(1024)/`ollama`(bge-m3,1024,维度初始化自动探测)/`local`(哈希兜底)；ollama 走 OpenAI 兼容 `http://localhost:11434/v1`，不可用时降级 local 并 warn。
- `vectorstore.py`：多集合封装。`get_collection(name)` / `add_documents(..., collection=)` / `query(emb, k, collection=)` / `reset_collection(name)` / `list_sources(collection=)` / `count(collection=)`。文档集合=`docmind`，代码集合=`docmind_code`。
- `ingest.py`：文档 `ingest_file`；代码 `chunk_code(text, path)`（.py 走 `ast` 按 def/class/方法切取符号名，其它语言走 `_DEF_RE` 正则启发式）、`ingest_code_file`、`ingest_code_directory(root, collection=CODE_COLLECTION_NAME)`（跳过 .git/node_modules/__pycache__/.venv 等、跳 >500KB 文件）。`chunk_code` 已升级为**递归再切**（见 T4）。
- `tools.py`：工具字典 `TOOLS`（28 项：9 基础 + 3 增强写 + `init_regions` + 11 个 `dev_*` + 4 个 Agent 研判分区工具）。`search_code` 查代码集合；`read_file`/`grep` 限定 `code_root` 内（越界拒绝）；`apply_edit`/`create_file`/`run_command` 受控（见 T2/T3）。分区开发新增辅助 `_parse_keyed`/`_require_regions`/`_resolve_region_path`/`_emit_edit_arg`/`_run_region_cmd`；`dev_*` 全部复用 apply_edit/create_file 护栏 + **分区前缀校验**（双层沙箱）。Agent 研判分区新增 `list_dir`（限定 `code_root` 的目录勘察）、`dev_propose_regions`（调用 `regions.propose_regions`）、`dev_apply_regions`（JSON 清单交给 `init_regions` 落地并重注入契约）、`dev_add_region`（追加/覆盖单分区）。
- `regions.py`：分区开发 2.0。`DEFAULT_REGIONS`（8 区，含 `depends_on`/`exports`/`verify`）+ `REGIONS` 兼容别名；`load_region_config`/`get_region_map`（支持 `regions.json` 覆盖）；`init_regions(root, regions_list=None)`（建目录 + 各 git init + **本地提交身份** + 导出桩 + README + regions.json + DEV_INDEX.md + DOCMIND_RULES.md；传 `regions_list` 时归一化并覆盖写 `regions.json` 实现 Agent 自定义方案落地）；`verify_contracts`（依赖无环 DFS / 导出存在 / 目标存在）；`commit_region`/`commit_all`(变更集 `dev_changesets.jsonl`)/`list_changesets`/`rollback_changeset`；`rebuild_dev_index`/`list_regions`；**Agent 研判分区**：`propose_regions`（`_analyze_root` 扫描顶层目录与资源类型、`_detect_signals` 给 8 区 `detected`/`evidence`、`_custom_suggestions` 识别代码库特有模块）、`_normalize_regions`（校验/补全自定义方案）。
- `agent.py`：`SYSTEM_PROMPT`（工具清单 + 选择指引）+ `Agent.run` 流式 yield thought/action/observation/final。`_VERBATIM_TOOLS={calculate,python_exec,gen_video_prompt}`；`_FAILURE_MARKERS`/`_MAX_REFLECTIONS=2`；`MAX_AGENT_STEPS` 来自 config。
- `api.py`：端点 `/api/chat`(SSE) / `/api/ingest` / `/api/ingest_code`(收 root→索引→写运行时 `code_root`) / `/api/config`(GET 返回当前配置不回显 key / POST 切换) / `/`（用 `PROJECT_WEB_DIR` 提供前端与 `/static` 挂载）。`OLLAMA_BASE` 探活拦截坏模型。分区开发新增：`POST /api/init_regions`、`GET /api/regions`、`GET /api/verify_contracts`、`POST /api/rebuild_index`、`POST /api/region_verify`、`POST /api/commit_all`、`GET /api/changesets`、`POST /api/rollback_changeset`、`POST /api/dev_commit`；**Agent 研判分区新增**：`POST /api/propose_regions`、`POST /api/apply_regions`、`POST /api/add_region`。问答区新增：`POST /api/enhance_prompt`（LLM 重写增强提示词，支持 llamacpp 等免 Key 本地 provider，失败降级 local）。
- `web/index.html`：演示前端。⚙ 模型设置面板 + 📁 代码库索引面板（`codeLibBtn` 开面板 → 填 `codeRoot` → `indexCodeBtn` → `fetch('/api/ingest_code',{method:'POST',body:FormData.append('root',path)})`，回显 `已切 N 片 / 共 M 条` 到 `codeMsg`）。
- `run.py`：启动器（先 ingest `sample_docs` 再 uvicorn）。
- `desktop.py`：桌面端控制台启动器（见 §5）。
- `docmind.spec`：PyInstaller **onedir** 配置（见 §5）。
- `make_lnk_pure.py`：纯 Python 手写 `.lnk`（见 §5.3）。
- 其它：`assets_catalog.json`（26 条 CC0/CC-BY 素材清单）、`sample_docs/`（示例知识库）、`README.md`/`DEMO.md`、`docmind.ico`（Pillow 生成，靛蓝→青色渐变 + "D"，多尺寸 16→256）。

---

## 5. 桌面端打包（重点，含全部踩坑）
**目标**：双击桌面 `DocMind.lnk` 即可在本机打开 DocMind（控制台窗口 + 浏览器界面）。
**当前方案** = `onedir` PyInstaller + **控制台模式**启动器 + 自包含 `dist/DocMind/` + 纯 Python 手写 `.lnk`。

### 5.1 架构决策（为什么是控制台模式，而不是原生窗口）
- 早期用 **pywebview 原生窗口**（Windows 为 Edge WebView2）。坑：缺 WebView2 运行时的机器上 `webview.create_window` **既不抛异常也不真正弹窗**，进程还活着 → 用户"双击没反应、任务管理器能看到 DocMind.exe"。
- 改 **tkinter 控制窗**，同样可能在部分机器建不出窗口（且 import tkinter 需 spec 不 exclude tkinter）。
- **最终**：exe 本身 `console=True`（PyInstaller 子系统标志，保证双击必弹黑窗口）。启动后打印本地地址 + `webbrowser.open(URL)` 自动开浏览器；窗口常驻直到用户回车退出。**界面本就是 Web 应用，浏览器即桌面端，跨机器稳定；WebView2 不再是前置依赖。**

### 5.2 关键文件与资源定位（最容易错的点）
`docmind.spec`：
```python
# onedir：EXE(exclude_binaries=True) + COLLECT(name="DocMind") → dist/DocMind/（DocMind.exe + _internal/）
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DocMind",
          console=True, icon="docmind.ico", ...)   # 注意 console=True
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, name="DocMind")
# datas=((".chroma", ".chroma"), ("web", "web"))  → 实际落点 dist/DocMind/_internal/
# hiddenimports 含 chromadb / webview / onnxruntime / hnswlib 等；collect_all("chromadb")/("webview")
```
`desktop.py`（控制台启动器要点）：
- 后台线程 `uvicorn.run(app, host="127.0.0.1", port=8000)`；
- 端口探测 `_already_running()`（GET `/api/config` 200 即视为已运行 → 直接开浏览器并退出，单实例保护）；
  ⚠️ **此机制正是"重建后仍显示旧界面"的根因**：若旧 `DocMind.exe` 仍占着 :8000（哪怕被改名 `DocMind.bak` 也不释放文件句柄、继续服务），双击快捷方式会 re-attach 到那个旧服务 → 看到旧界面。启动前务必先 `taskkill /F /IM DocMind.exe` 清掉旧进程。
- `GET /api/config` 现额外返回 `build_time`（运行中 exe 的 mtime；源码直跑为 `"dev"`）→ 验证"线上跑的是哪次构建"的一键手段：构建后 `curl .../api/config` 看 `build_time` 是否等于最新构建时间。
- `_wait_for_server()` 轮询；`_open_browser()` 用 `webbrowser.open`；
- `_keep_alive()` 用 `input()` 常驻（无 stdin 时退化为保活睡眠）；
- 全程写 `docmind_desktop.log`（与 exe 同目录）便于排查；`main()` 外套 try/except 打印 traceback。

`config.py` **资源定位**（onedir 不设置 `sys._MEIPASS`，必须单独处理）：
```python
if getattr(sys, "frozen", False):
    EXE_DIR = os.path.dirname(sys.executable)
    if hasattr(sys, "_MEIPASS"):                       # 仅 onefile 模式有
        BASE_DIR = sys._MEIPASS
    elif os.path.isdir(os.path.join(EXE_DIR, "_internal", "web")):  # onedir 实际落点
        BASE_DIR = os.path.join(EXE_DIR, "_internal")
    else:
        BASE_DIR = EXE_DIR
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_WEB_DIR = os.path.join(BASE_DIR, "web")
CHROMA_DIR     = os.path.join(BASE_DIR, ".chroma")     # frozen 时强制走打包资源
```
> 若不处理 onedir 分支，会回退去读**源码目录**的 `.chroma`/`web`——本机能跑只是因为 `dist/DocMind` 恰好嵌在项目树里，一旦把 dist 挪走/拷到别的机器就全盘失效。

### 5.3 桌面快捷方式 `.lnk`（本会话最大坑之一）
- **现象**：早期手写 `.lnk` 仅 642 字节、**缺 `LinkTargetIDList`** → Windows Explorer 静默拒绝打开（双击无反应）。
- **约束**：本沙箱 `win32com` 不可用、`WScript.Shell` COM 被安全策略拦截，无法用 Shell API 生成。
- **解法**：`make_lnk_pure.py` 纯 Python 按 **MS-SHLLINK** 规范手写 `.lnk`：`ShellLinkHeader` + `LinkTargetIDList` + `LinkInfo`(VolumeID + LocalBasePath/Unicode) + `StringData`(WorkingDir/IconLocation，Unicode)。关键是 `LinkTargetIDList` 必须有且结构合法——IDList 复用真实 `.lnk` 的「命名空间根 + C: 盘符」标准 PIDL 字节，完整路径交给 `LinkInfo.LocalBasePath`，Explorer 据此实际启动。
- **同时提供保底** `C:\Users\h'h'h\Desktop\DocMind.bat`（`start "" "<exe>"` + exit）：万一某些 Windows 版本对手写 `.lnk` 挑剔，双击 .bat 也能开。
- **成品**：桌面 `DocMind.lnk`（首选）+ `DocMind.bat`（保底）。图标用 exe 内嵌的 `docmind.ico`（spec 已 `icon="docmind.ico"` 打进 exe），自包含。

### 5.4 构建命令（必须用项目 .venv，别用托管 python）
```bash
cd rag-agent
.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm
```
- **必须用 `rag-agent/.venv/Scripts/python.exe`**：用托管 python（`C:\Users\h'h'h\.workbuddy\binaries\python\...`）会 `ModuleNotFoundError: No module named 'webview'`（且缺 chromadb/fastapi 等），构建出坏 exe。
- 构建前**先把旧 `dist/DocMind`、`build/` 改名移走**（见 §8 bulk-delete 守卫），让 PyInstaller 全新生成。
- 产物 `dist/DocMind/DocMind.exe`（约 19MB，含全部依赖），**自包含**：`dist/DocMind/_internal/.chroma`（docmind_code=541 / docmind=24）+ `dist/DocMind/_internal/web`。

---

## 6. 待办与审查重点
### T5 — 接真实工程验证【收尾，决定 T2/T3 是否公开上线】
- 拿一个真实工程（如 `D:\study\AndroidProjects\MusicLayout`，或任意手上的代码库）做端到端验证，评估检索/定位质量，再决定 T2/T3 是否对外开放。
- 命令：
  ```bash
  curl -X POST http://127.0.0.1:8000/api/ingest_code -F "root=<工程绝对路径>"
  curl -X POST http://127.0.0.1:8000/api/chat -F "question=伤害计算在哪实现"
  ```
- 验收：能正确定位函数/类所在**文件与行号**；检索召回相关片段；据此迭代 T4 分块策略与 `SYSTEM_PROMPT` 指引。

### 请审查 AI 重点看（已标红处）
1. **桌面端健壮性**：纯手写 `.lnk` 在某些 Windows 版本可能挑剔（IDList 仅 root+drive，靠 LinkInfo 启动）；控制台模式体验是否可接受；`docmind_desktop.log` 排查信息是否够。
2. **config.py 资源定位**：onedir / onefile / 开发 三分支是否正确，是否真自包含（已用"隐藏项目根 `.chroma` 后启动 exe 仍返回 541"测试通过）。
3. **工具护栏边界**：`apply_edit`/`create_file`/`run_command` 的沙箱、`_DANGEROUS` 黑名单、先读后写、体积上限、`.py` 语法校验是否严密（已有离线单测覆盖，但建议人工审边界）。
4. **embedding 默认已接本地 ollama `bge-m3`**（零 Key 语义检索）；若 Ollama 未运行/未拉 bge-m3，会降级 local 哈希（检索无意义）——UI/文档应提示保持 Ollama 在线。——UI/文档是否已讲清，避免误导。
5. **单实例保护**依赖端口探测，若 8000 被占用会误判；可考虑更稳的锁文件方案。

> **"完善"的定义**：T2–T4 已实现 → T5 接真实工程验证质量 → 据结果微调分块/提示词 → 同步更新 `README.md` 与用户简历的 DocMind 条目。新增工具一律走"工具三步"：①`tools.py` 写函数+加 `TOOLS` 项 ②`agent.py` SYSTEM_PROMPT 加说明与选择指引 ③（如需）`api.py` 加端点。

---

## 7. 验证命令
```bash
cd rag-agent
# 开发模式启动（127.0.0.1，勿 localhost）
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000

# 索引一个代码目录
curl -X POST http://127.0.0.1:8000/api/ingest_code -F "root=C:/abs/path/to/code"
# 看配置（确认 code_root / code_sources）
curl http://127.0.0.1:8000/api/config
# 问代码问题（需 ollama/qwen，mock 不调代码工具）
curl -X POST http://127.0.0.1:8000/api/chat -F "question=xxx 函数/类在哪里实现"

# 打包后自包含测试：把项目根 .chroma 改名，启动 exe 仍应返回 code_sources=541
mv .chroma .chroma.bak
DOCMIND_SERVER_ONLY=1 dist/DocMind/DocMind.exe &   # 后台
curl --noproxy '*' http://127.0.0.1:8000/api/config   # 期望 200 + code_sources=541
mv .chroma.bak .chroma                               # 还原
```
**单元级**：写脚本 `import` `chunk_code` / `ingest_code_directory` / `search_code` / `grep` / `read_file`，临时设 `CHROMA_DIR` + `set_runtime('code_root', tmp)` 跑通（参考此前测试：符号识别、grep 命中、read_file 回内容、未配置 code_root 兜底）。

---

## 8. 坑（必看，照做可省数小时）
- **路径含 `h'h'h` 单引号**：shell 一律双引号包裹路径；Python 字符串里直接写即可（不是转义问题，是 shell 解析问题）。
- **local embedding 随机** → 检索结果无意义；已默认切 `EMBEDDING_PROVIDER=ollama`(本机 bge-m3, 零 Key) 解决；若 Ollama 离线则降级 local，需提示用户启动 Ollama 并 `ollama pull bge-m3`。
- **mock provider 不会有效调用代码工具**（它走固定脚本）；测代码模式用 `ollama` 或 `qwen`。
- **35B ollama 低显存会 CUDA 初始化崩**：已被 `/api/config` 12s 探活拦截，勿绕过。
- **`read_file`/`grep` 限定 `code_root`**：传入越界绝对路径会被拒。
- **分区 git 仓库需本地提交身份**：`init_regions` 已为每个分区 `git init` 后设 `user.email=docmind@local`/`user.name=DocMind`（沙箱常无全局 git 身份，否则 `git commit` 报「Author identity unknown」导致 dirty 不降、commit 失败）。若手工 `git init` 分区，需自行 `git config user.*`，否则提交/变更集会失效。
- **文档/代码是独立集合**：别混；切 `EMBEDDING_PROVIDER`(维度变化 local256→qwen1024/ollama1024) 时**两个集合都要重置**（文档 `reset_collection()` + 代码 `reset_code`/同样 reset），否则旧维度向量与新维度冲突报错。
- **新增工具三步**：①`tools.py` 写函数+加 `TOOLS` 项 ②`agent.py` SYSTEM_PROMPT 加说明与选择指引 ③（如需）`api.py` 加端点。
- **端口 8000 可能被上一会话遗留 uvicorn 占着**（旧代码、缺 `/api/ingest_code` 会 404）。接手先查：`netstat -ano | grep :8000` 找 PID，用 **PowerShell** `Stop-Process -Id <pid> -Force` 杀（Git Bash 下 `taskkill //PID` 会被参数转换报错，别用）。⚠️ 桌面端 `desktop.py` 的单实例保护会"探测到 8000 已活就直接开浏览器连旧服务并退出"——所以**遗留旧 `DocMind.exe` 不死，双击永远看到旧界面**；验证新包是否生效用 `GET /api/config` 的 `build_time` 字段（等于最新构建时间才说明跑的是新包）。
- **后台启动勿加 `&`**：会让 uvicorn 随父 shell 退出被回收（表现为"服务起不来/连接被拒"）。用 `run_in_background` 托管，或直接前台跑 + 另开终端。
- **curl 用 `127.0.0.1` 而非 `localhost`**：本机代理可能让 localhost 解析失败；沙箱内还需 `--noproxy '*'` 否则代理返回 502。
- **PyInstaller 必须用项目 `.venv` 的 python**（`rag-agent/.venv/Scripts/python.exe`），不能用托管 python（缺 webview/chromadb/fastapi）→ `ModuleNotFoundError: No module named 'webview'` 坏 exe。
- **沙箱 bulk-delete 守卫**：PyInstaller COLLECT 删除旧 `dist/DocMind`（690+ 文件）会被 `SAFE_DELETE_BULK_CONFIRM_REQUIRED` 拦截中断。规避：构建前先把旧 `dist/DocMind`、`build/` **改名移走**（rename 非删除），让 PyInstaller 全新生成；清理时 `rm -rf` 各 aside 目录已获沙箱放行。
- **onedir 不设置 `sys._MEIPASS`**（那是 onefile 专属）→ `config.py` 必须单独处理 onedir 落点（`_internal`），否则回退读源码目录 `.chroma`/`web`，dist 挪走即失效。
- **桌面 `.lnk` 必须有 `LinkTargetIDList`**，否则 Windows Explorer 静默拒绝打开（双击无反应）。纯手写 `.lnk` 时务必包含合法 IDList；COM/win32com 在本沙箱不可用，改用 `make_lnk_pure.py`。
- **pywebview/WebView2 静默失败**：缺运行时时既不报错也不弹窗 → 桌面端务必用控制台模式（console=True）而非原生窗口。
