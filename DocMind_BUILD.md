# DocMind 分发版构建说明（2026-09-11）

> 最新构建见下方「第七次重建（随包 MinGit + git 脏标记修复 + Godot 索引）」；历史构建清单保留在下文。

## 产物
- 路径：`rag-agent/dist/DocMind/`（onedir 目录分发）
- 入口：`DocMind.exe`（约 19.4 MB，控制台模式，启动时自动开浏览器）
- 整体体积：约 662 MB（chromadb / onnxruntime / webview 运行时 + 随包 MinGit 89.5 MB）
- **当前构建时间：`2026-09-11 18:53:13`（第七次重建，随包 MinGit）**
- 上一版：`2026-09-11 15:45:03`（第六次重建，工作台前端脚手架，P0 任务 1）

---

## 第七次重建：随包 MinGit + git 脏标记修复 + Godot 索引（2026-09-11 18:53）

### 改动
- **MinGit 随包分发**：`dist/DocMind/MinGit/`（Git for Windows 官方便携版 2.55，89.5 MB / 365 文件，含 cmd+mingw64+usr，自足无外部依赖），用户机器无需安装 Git。
  - `docmind.spec`：COLLECT 后把 MinGit 复制到与 exe 同级目录；源目录解析顺序 `MINGIT_DIR` 环境变量 → `vendor/MinGit` → `%USERPROFILE%\.local\bin\MinGit`，都找不到时构建明确报错。换机构建请把 `MinGit-*-64-bit.zip` 解压到 `vendor/MinGit`（vendor/ 已 gitignore，二进制不入库）。
  - `desktop.py`：frozen 启动时把 `<exe目录>/MinGit/cmd` 前置进进程 PATH（`_ensure_bundled_git`），所有裸 `git` 子进程（文件树状态、分区初始化/提交/回滚）自动命中；日志打印「已启用随包 Git（MinGit）」。
  - `regions.py`：缺 git 提示区分 frozen/源码——分发包里若 MinGit 目录被杀软删除，提示恢复目录或重新获取完整分发包，而非让用户去装 Git。
- **git 脏标记修复**（同包带走）：`_git()` 对输出 `.strip()` 会吃掉 `status --porcelain -z` 首条记录的状态列空格，导致最常见的「未暂存修改」永不亮脏点；改为 `.rstrip()`，并显式 UTF-8 解码（中文路径不再错码）。
- **Godot 资产可索引**：`ingest.py` 白名单补 `.gd/.gdshader/.tscn/.tres/.godot`（二进制 .scn/.res 天然排除），lang 元数据 gdscript 等；之前 .gd 不进代码集合，问答看不到游戏脚本。
- 前端无改动，沿用既有 workbench 资源（JS `workbench-CC3MjeAJ.js`，含无 git 中性文案）。

### 验证（冻结包实测）
- 用**不含任何 git 的最小 PATH**（仅 System32）冷启动 `DocMind.exe`（DOCMIND_SERVER_ONLY=1，CODE_ROOT=样例 Godot 项目）：~16s 服务就绪，日志确认「已启用随包 Git（MinGit）」。
- 文件树三态：修改过的 level.gd → tracked=true/dirty=true；新增 boss.gd → tracked=false/dirty=true；干净文件 → tracked=true/dirty=false。浏览器徽标「已修改/未跟踪/已跟踪」全部正确，console 零错误。
- 产物自检：MinGit 365 文件完整、`git --version` 2.55；web/workbench 资源哈希与源码构建一致；无 .env、无 python\*.exe；总体积 661.6 MB。
- 源码侧回归 25/25；GDScript 语义索引 19 切片、4 个中文查询精准命中；端到端问答（暴击公式）Agent 主动 search_code 且回答与 crit.gd 一致。

---

## 第六次重建：工作台前端脚手架（2026-09-11 15:45）

### 改动
- 新增 `frontend/` Node 工程：Vite 5 + Vue 3 + TypeScript 多页（MPA）构建，当前唯一入口 `workbench.html`，产物写入 `web/workbench.html` 与 `web/assets/*`（`emptyOutDir:false`，不清空问答页 index.html）。
- **打包流程变更（重要）**：PyInstaller 前必须先在 `frontend/` 执行 `npm install`（首次）与 `npm run build`；spec 无需改动，`("web","web")` 会原样带走新资源。`web/assets/` 与 `web/workbench.html` 为构建产物，已加入 .gitignore。
- 后端 [api.py](file:///D:/WorkBuddy/rag-agent/api.py)：新增 `GET /workbench`（未构建时 404 明确提示）与 `/assets` 静态挂载（目录不存在时跳过，源码态不崩）；问答页与其余接口零改动。
- dev 联调：`npm run dev`（5173）已配 `/api` 代理到 8000。

### 验证
- `npm install` 32 包；`npm run build` 成功（11 模块，约 1s）；产物 JS 62KB / CSS 0.4KB。
- dev 冒烟：`/workbench` 200、`/assets/*` 200、首页 `/` 回归 200（72548 字节不变）；headless 截图确认 Vue 挂载与中文渲染正常。
- 打包：退出码 0（仅历次同款无害警告）；新 exe 15:45:03；冻结包内 workbench.html 与 JS 的 SHA-256 与源码产物一致；exe 冷启动后冻结环境 `/workbench` 200（412 字节）、`/assets` JS 200（62125 字节）、`build_time=15:45:03`；收尾后 8000 端口释放。

---

## 第五次重建：模型驻留（显存）开关（2026-09-11 14:30）

### 改动
- 新增模型显存开关：侧栏「模型状态」卡片实时显示 Ollama 驻留模型、显存占用与自动卸载倒计时，一键卸载（`keep_alive=0`，立即释放显存）/ 一键预加载（`keep_alive=30m`，免去冷启动等待）；每 20s、窗口重新可见、对话结束自动刷新。
- 后端新增 `GET /api/model_status`、`POST /api/model_power`（[api.py](file:///D:/WorkBuddy/rag-agent/api.py)）：
  - 生成型模型走 `/api/generate`（仅 model + keep_alive，不推理）；**嵌入模型（bge-m3）不支持 generate（400），自动回退 `/api/embeddings`**；
  - 卸载后大模型 runner 释放有数秒 Stopping... 延迟，接口轮询至 `/api/ps` 清空（上限 15s）再返回终态。
- 前端 [web/index.html](file:///D:/WorkBuddy/rag-agent/web/index.html)：卡片驻留状态行 + 红/绿开关按钮，云端/mock 模式自动隐藏。
- 设计原因：22GB 的 qwen3.6:35b-a3b 默认长时间驻留 12GB 显存，不对话时也占满 GPU（与小游戏抢显存）。

### 验证
- dev 实测：预加载（qwen 8.69GB + bge 0.62GB）→ 卸载后显存 11.4GB → 0.95GB；接口终态与 `ollama ps`/`nvidia-smi` 一致。
- 打包：`PyInstaller 6.22.2`，退出码 0；新 exe 19,347,400 字节 / 14:30:40；冻结 index.html 与源码 SHA-256 一致（`ECF5B82A…F84E5`）；包内无 python*.exe、无 .env。
- exe 冷启动冒烟：12s 内服务可用；`/api/health` guidance 空；`build_time=14:30:40`；冻结环境预加载/卸载开关实测正常；浏览器 console 零错误、无 Ollama 误报横幅（headless 截图确认）；结束进程后 8000 端口释放。

---

## 第四次重建：代码审查 11 项问题修复（2026-09-11 13:32）

### 背景
对当日上午（08:21–12:02，由其他 AI 完成）的更新——审批真门禁、`generate_test_scene` 真实化、playtest 接 pytest/unittest、默认分区 builtin 校验、Ollama 健康检查/`/api/health`/横幅、CORS、`dev_http_request` 白名单——做了一轮完整代码审查：逐文件读码 + 运行时实证 + 两路独立子代理交叉验证，共确认 **1 高危 / 1 中高危 / 9 中低危** 问题，全部修复后重建分发版。

### 修复清单

| # | 严重度 | 问题 | 修复（文件） |
|---|--------|------|--------------|
| 1 | 高 | 前端 `$("resetCodeBtn")` 空引用在页面加载时抛 TypeError，中止唯一 `<script>` 块：保存配置、整个开发工作台按钮组、初始 `loadConfig()` 全部失效；`loadPending` 另引用 3 个不存在的 id，每发一条消息报一次错 | 空值守卫 + 缺失卡片早退：[web/index.html L808-821](file:///D:/WorkBuddy/rag-agent/web/index.html#L808-L821)、[L1194-1199](file:///D:/WorkBuddy/rag-agent/web/index.html#L1194-L1199) |
| 2 | 中高 | onedir 包内无 python.exe，frozen 时 `sys.executable` 就是 DocMind.exe：`builtin:py` 校验（compileall）、playtest 的 pytest/unittest 探测、cProfile、`python_exec` 全部变成再启动一个应用实例（挂起/弹浏览器/可能假绿灯），分发版 4 个默认 py 分区校验不可用 | builtin:py 改为进程内 `compile()` 遍历（frozen/dev 行为一致）；pytest/cProfile/python_exec 在 frozen 下直接返回"分发版不含 Python 解释器"的明确提示：[regions.py L97-122](file:///D:/WorkBuddy/rag-agent/regions.py#L97-L122)、[game_workbench.py L323-342](file:///D:/WorkBuddy/rag-agent/game_workbench.py#L323-L342)、[L392-409](file:///D:/WorkBuddy/rag-agent/game_workbench.py#L392-L409)、[tools.py L223-226](file:///D:/WorkBuddy/rag-agent/tools.py#L223-L226) |
| 3 | 低 | Ollama 模型名精确比较：`/api/tags` 返回 `bge-m3:latest` 而配置写 `bge-m3`，已装模型被恒误报缺失 | 双向补 `:latest` 归一化后再比较：[api.py L534-549](file:///D:/WorkBuddy/rag-agent/api.py#L534-L549) |
| 4 | 低 | 服务不可达时无视当前 provider 是否需要 Ollama 都给安装引导（mock/local 零依赖用户被骚扰，引导里还会出现错误模型名） | `needed_models` 为空时不产生 guidance，pull 列表只列真实所需模型：[api.py L591-608](file:///D:/WorkBuddy/rag-agent/api.py#L591-L608) |
| 5 | 低 | 白名单文档教用户写 `*.example.com`，实现却不识别（只支持精确/`.后缀`），按文档配置必静默失效 | 支持 `*.` 前缀（仅子域，不含裸域），三处文档示例同步：[tools.py L123-151](file:///D:/WorkBuddy/rag-agent/tools.py#L123-L151)、[config.py L113-117](file:///D:/WorkBuddy/rag-agent/config.py#L113-L117) |
| 6 | 低 | `run_command` 不拦 `git`：`git -C <分区> commit`/普通 `git push` 可绕过分区审批门禁、变更集台账并外带代码；playtest 自带的子串黑名单过粗（双空格可绕） | 解析 git 子命令（跳过 `-C` 等带值选项），17 个写操作（commit/push/merge/rebase/reset/checkout/stash…）一律拦截并引导走 dev 审批工具，且优先于 `RUN_COMMAND_ALLOW`；playtest 复用同一结构化黑名单：[tools.py L1090-1137](file:///D:/WorkBuddy/rag-agent/tools.py#L1090-L1137)、[game_workbench.py L372-381](file:///D:/WorkBuddy/rag-agent/game_workbench.py#L372-L381) |
| 7 | 低 | `dev_http_request` 纵深缺陷：不校验 scheme（白名单配 `*` 时 `file://` 可读任意本地文件）；urllib 自动跟随 302 且不重过白名单（开放重定向一跳到 169.254.169.254/内网） | 仅允许 http/https；自定义 RedirectHandler 对 301/302/303/307 每一跳重过 scheme + host 白名单：[tools.py L111-187](file:///D:/WorkBuddy/rag-agent/tools.py#L111-L187) |
| 8 | 低 | 鉴权中间件在 CORS 外层短路返回 401，跨域网页端 token 错误时读不到 401 响应体（无 ACAO 头） | CORS 改为后注册（Starlette 后注册者在最外层），401 也带 CORS 头：[api.py L80-104](file:///D:/WorkBuddy/rag-agent/api.py#L80-L104) |
| 9 | 低 | 切换 Ollama 模型强制 12s 真实推理探活，35B 冷加载必超时被拒；`/api/config`、`/api/chat`、`/api/health` 在 async 端点内同步 urllib 阻塞事件循环（最坏 8s） | 探活超时放宽到 60s；三处探活全部移入 `run_in_threadpool`：[api.py L510-515](file:///D:/WorkBuddy/rag-agent/api.py#L510-L515)、[L350-354](file:///D:/WorkBuddy/rag-agent/api.py#L350-L354)、[L502-503](file:///D:/WorkBuddy/rag-agent/api.py#L502-L503)、[L742-748](file:///D:/WorkBuddy/rag-agent/api.py#L742-L748) |
| 10 | 提示 | 分区 Git 功能是隐性外部依赖（机器无 git 即不可用且报错晦涩），`init_regions` 在 git init 失败时残留半成品文件；审批文案"target 默认 *"误导 Agent（`*` 非通配符）；另有空转死代码、health docstring 名不副实 | 写文件前 `_git_available()` 预检（失败零残留 + git-scm 下载提示）：[regions.py L232-264](file:///D:/WorkBuddy/rag-agent/regions.py#L232-L264)、[L333-337](file:///D:/WorkBuddy/rag-agent/regions.py#L333-L337)；审批 target 文案三处澄清：[agent.py L52](file:///D:/WorkBuddy/rag-agent/agent.py#L52)、[tools.py L1707-1713](file:///D:/WorkBuddy/rag-agent/tools.py#L1707-L1713)；清理空转循环：[regions.py L545-547](file:///D:/WorkBuddy/rag-agent/regions.py#L545-L547) |
| 11 | 文档 | BUILD 文档缺少 Git 前置依赖与"分发版不含 Python 解释器"的限制说明 | 补充两条重要说明（见本文末）；README 打包段落由过时的 onefile 单文件描述更正为 onedir 实际流程 |

### 验证记录（修复 → 重建 全程）

1. **编译**：改动涉及的 8 个 .py（api/regions/game_workbench/tools/config/agent/desktop/llm）`py_compile` 全过。
2. **逻辑用例 39/39 全过**（临时验证脚本，验完即删，不入仓库）：
   - 白名单语义 11 例：`*.example.com` 匹配子域/多级子域、不含裸域、不串域；`*` 放行任意 host 但仍拒 `file://`；空白名单禁用工具；
   - git 门禁 11 例：`git -C 区 commit`、多空格变形 `git  -C … push`、push/reset/checkout/stash 全拦，status/log/diff/pytest/npm build 放行；playtest 拦 `powershell`；
   - frozen 守卫 4 例（模拟 `sys.frozen=True`）：测试探测返回 None、playtest auto / cProfile / python_exec 均返回"分发版"明确提示；
   - builtin:py 2 例：正常包计数通过、语法错误精确定位文件与行；
   - SSRF 3 例：本地 302 服务器跳 `169.254.169.254` 被白名单拦截、跳 `file://` 被协议拦截、直连 `file://` 被拦；
   - git 预检 3 例：无 git 时预检失败、`init_regions` 整体失败、写文件前拦截（regions.json 与分区目录零残留）；
   - Ollama 3 例（真实服务）：`bge-m3` 不再误报缺失、模型齐备 guidance 为空、mock/local 组合不产生 guidance。
3. **dev 浏览器实测**（uvicorn 8010）：全新标签页 **console 零错误**；保存配置/初始化/刷新/重建/全部提交/研判/应用/新增分区/数据校验/发布检查/Git 信息/发送全部 onclick 恢复；真实对话返回正常；Ollama 横幅因无缺模型正确隐藏。
4. **接口实测**：`/api/health` 与 `/api/config` 均返回 `missing_models: []`、`guidance: ""`；另起带 `DOCMIND_API_TOKEN=testtoken123` 的实例验证：401 已带 `access-control-allow-origin`、OPTIONS 预检 200、正确 token 200。

### 打包与产物核对

```powershell
.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm --log-level WARN
```

- 退出码 0；产物 [dist\DocMind\DocMind.exe](file:///D:/WorkBuddy/rag-agent/dist/DocMind/DocMind.exe) 19.3 MB，时间戳 **2026-09-11 13:32:04**。
- 冻结前端与源码 SHA-256 **完全一致**（`8392FA03…0B9B41`），确认 #1 修复已进入分发版。
- onedir 内确认无 `python*.exe`（#2 守卫的存在前提成立）；dist 下无 `.env`（拷到干净机器回落 mock/local，且不再有 Ollama 警告骚扰）。
- 打包日志中的 `hnswlib / pycparser.lextab / tzdata / importlib_resources.trees` Hidden Import 警告为 chromadb 可选后端引起，历次打包均存在，实测功能不受影响。

### exe 冷启动冒烟（冻结环境实测）

- 双击启动，约 12s 内 8000 端口服务可用；`/api/health` 返回 200 且 `missing_models: []`、`guidance: ""`。
- 浏览器打开首页：console **零错误**；全部工作台/配置按钮已绑定；状态栏初始化正常、Ollama 横幅正确隐藏。
- 冒烟结束后结束 DocMind 进程，确认 8000 端口释放。

### 标准「修复 → 打包」流程（本次沉淀，后续照做）

1. 修源码（最小改动，不顺手重构无关代码）；
2. `py_compile` 全部受影响 .py；
3. 编写临时逻辑验证脚本放 `D:\Temp`（**不入库**），覆盖安全/边界用例，绿后删除；
4. dev 起 uvicorn（8010 等非交付端口），浏览器实测 console、关键按钮、真实链路，验证完杀进程；前端改动先在 `frontend/` 跑 `npm run build`（产物进 `web/`）再测交付态；
5. 经授权后执行 PyInstaller 重建（**若含前端改动，必须先 `npm run build`**，详见第六次构建记录）；
6. 核对产物时间戳 + 冻结前端与源码哈希一致；
7. **exe 冷启动冒烟**（health/console/按钮/横幅），这是 dev 源码模式测不到的交付态问题（本次 #1、#2 均属此类）；
8. 结束冒烟进程、释放端口，更新本文档。

---

## 附录：11:53 第三次构建（外接 API 能力）包含的改动
### A. 上一版已包含（语义化改进）
1. **`impact_analysis` 语义化**：有代码索引走 bge-m3 向量检索，无索引退化子串 grep。
2. **默认分区配齐 `verify`**：`DEFAULT_REGIONS` 8 区全部带校验命令（`builtin:py` / `builtin:json`），经 `run_verify` 分发、绕开安全黑名单。
3. **`playtest` 接真实测试框架**：自动探测 pytest / unittest，解析 passed/failed/ran；`performance_sample` 支持 `cProfile` 真实剖析。
4. 桌面端（控制台启动器）、分区开发台（契约/依赖图/校验/提交/回滚）、智能研判分区、小游戏索引等。

### B. 本次重建新增（三项缺口修复）
5. **`approval` 真门禁**：`commit_region`/`commit_all`/`rollback_changeset`/`init_regions(apply)` 底层加 `is_approved`（30 分钟 TTL）；未审批返回 `{"blocked":true,"approval_required":true}` 且**不执行**；API 拦截返回 HTTP 403 + 拦截信息；新增 `dev_approve`/`dev_approval_status` 工具与 `/api/approval` 的 `target`/`check` 模式；Agent 系统提示加入"先审批再提交"。
6. **`generate_test_scene` 真实生成**：`ast` 扫描分区真实代码符号 + 读契约字段，产出引用真实符号/字段的 `<region>/tests/<name>.json` 与可运行 `<region>/tests/test_<name>.py`（importlib 加载、断言符号存在，与 `playtest`/`unittest` 衔接）。修了一个生成骨架 bug：`ROOT` 原只往上两级（落到 region 目录），已改为往上三级到 code_root。
7. **Ollama 离线降级与引导**：新增 `check_ollama()`（可达性 + 所需模型就绪探测，结构化返回）；`/api/config` 加 `ollama_status`、新增 `/api/health`、`/api/chat` 在 Ollama 不可达时快速返回友好错误；`desktop.py` 启动与前端顶部横幅在"不可达 **或** 缺模型"时均给出引导。

### C. 本次重建新增（外接 API 能力）
8. **外部系统调用 DocMind API（反向接入）**：`api.py` 新增 CORS 中间件（`DOCMIND_CORS_ORIGINS`，默认 `*`），浏览器/前端可跨域调用；沿用既有 `optional_api_auth` 令牌鉴权（`DOCMIND_API_TOKEN` 启用后对 `/api/*` 校验 `x-docmind-token` 或 `Bearer`，且对 `OPTIONS` 预检放行以配合 CORS）。
9. **Agent 调用你的业务 API（正向接入）**：`tools.py` 新增 `dev_http_request` 工具（多行 `key: value` 解析 `url/method/headers/body/timeout`，用标准库 `urllib` 发请求，返回状态码+响应头+截断响应体）；`config.py` 新增 `EXTERNAL_API_ALLOWLIST` 域名白名单——**空则拒绝**、命中才放行，防 SSRF（含云元数据地址 `169.254.169.254` 亦被拦）；`agent.py` 已在工具清单与"何时用"指引中补充该工具。

### 第三次构建验证结果（11:53）
- 构建：`PyInstaller 6.22.2` 经 `docmind.spec`（onedir，不打包 `.chroma`）成功，输出 `dist_build` 后换入 `dist/DocMind`，`BUILD_EXIT=0`。
- 启动冒烟（端口 8000）：`/api/health` 返回 200 并给出结构化状态；`/api/config` 的 `build_time=2026-09-11 11:53:16` 确认新版；`ollama_status` 字段存在。
- **外接 API 综合验证 `C:/tmp/verify_external_apis.py`：11/11 全过**（含源码层 + exe 实测两端）：
  - 反向接入：无 token→401、正确 `x-docmind-token`→200、正确 `Bearer`→200、错误 token→401；CORS `OPTIONS` 预检→200 且带 `Access-Control-Allow-Origin`。（exe 实测：用 `DOCMIND_API_TOKEN=testtoken123` 启动后，上述四项 + CORS 预检全部符合预期）
  - 正向接入：`dev_http_request` 空白名单拒绝、白名单命中 GET/POST+body 放行、白名单外拒绝、元数据地址 SSRF 拦截。
- 源码层综合验证 `C:/tmp/verify_fixes_123.py`：**21/21 全过**（四路门禁拦截+审批后放行、`generate_test_scene` 引用真实符号且生成测试经 unittest 通过、`check_ollama` 结构化返回、`/api/health`、config 含 `ollama_status`、`/api/approval check`、`/api/dev_commit` 未审批 403）。

## 重要说明
- **未打包 `.chroma`**：不把开发期测试索引烤进分发版。运行时 Chroma 会在 `dist/DocMind/.chroma` 自动建空索引，用户自行对目标项目执行索引即可。
- **旧项目 `regions.json` 残留**：若某项目根目录已有旧版 `regions.json`（早期默认无 `verify`），其优先级高于 `DEFAULT_REGIONS`，会显示 `verify` 为空。解决办法：删除该项目下的 `regions.json` 让其回落到新默认，或在其 `regions.json` 每个分区补 `"verify"` 字段。
- **启用外部调用 DocMind API**：设置环境变量 `DOCMIND_API_TOKEN=<你的令牌>`（启用后 `/api/*` 需带 `x-docmind-token` 或 `Authorization: Bearer`）；如需浏览器跨域调用，设 `DOCMIND_CORS_ORIGINS=https://你的前端域名`（默认 `*` 允许任意来源）。
- **启用 Agent 调外部 API**：必须设置 `EXTERNAL_API_ALLOWLIST`，否则 `dev_http_request` 一律拒绝——这是防 SSRF 的安全闸门，非空不可放行。写法：`api.example.com`（精确匹配且含其子域）、`*.example.com`（仅子域，不含裸域）、`*`（任意 host，协议仍限 http/https）；30x 重定向的每一跳都会重新校验白名单。
- **端口占用提示**：启动器含单实例保护——若 8000 端口已被旧 `DocMind.exe` 占用，新实例会直接打开浏览器而不重复启动；如遇旧进程卡死占用端口，需先结束旧进程再启动。
- **Git 前置依赖**：第七次构建起分发包**自带 MinGit**（`DocMind/MinGit/`，frozen 启动自动加入 PATH），用户机器无需安装 Git；仅源码运行（.venv / dev）仍需系统装有 git。分区工作台的初始化/提交/回滚基于每个分区独立 git 仓库；git 不可用时初始化会在写文件前给出明确提示，不产生半成品。
- **分发版不含 Python 解释器**：分区 `builtin:py` 校验在进程内做语法编译检查（exe/源码均可用）；但 pytest/unittest 试玩（playtest auto）与 cProfile 剖析需要真实 Python 环境，请在源码 `.venv` 中运行，exe 内会直接返回明确提示而不会静默失败。
- 第七次构建起 `docmind.spec` 含 MinGit 随包步骤（见上文）；早期「临时构建 spec 已清理」的说明不再适用。
