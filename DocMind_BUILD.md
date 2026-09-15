# DocMind 分发版构建说明（2026-09-11）

> 最新构建见下方「第十八次重建（ComfyUI 受管生命周期 + H3 实机验收 + 网页检索工具落定，并修回 17 次构建引入的 TaskEnginePanel 崩溃回归）」；历史构建清单保留在下文。

## 产物
- 路径：`rag-agent/dist/DocMind/`（onedir 目录分发）
- 入口：`DocMind.exe`（约 19.7 MB，控制台模式，启动时自动开浏览器）
- 整体体积：约 309 MB（chromadb / onnxruntime / webview 运行时 + 随包 MinGit 91 MB/365 文件；**第十六次起不再打包开发者 `.chroma` 索引库，较第十五次 682.5 MB 降约 374 MB**）
- **当前构建时间：`2026-09-15 13:09:19`（第十八次重建，ComfyUI 受管生命周期 + H3 实机验收 + 网页检索工具 + TaskEnginePanel 崩溃回归修复，exe 19,737,852 字节，SHA-256 1772415d76788920d5b33b57b0253863903cba081804f3894e2cdcda591a17a1）**
- 上一版：`2026-09-14 17:14:57`（第十五次重建，桌面入口改 RAG 问答页 + 引擎嵌入 + 场景画布 + Agent 路由收敛，exe 19,670,124 字节）

---

## 第十八次重建：ComfyUI 受管生命周期 + H3 实机验收 + 网页检索工具落定，并修回 17 次构建引入的 TaskEnginePanel 崩溃回归（2026-09-15 13:09）

### 改动
- **ComfyUI 受管生命周期完整落地**（17 次构建后并入的大量提交，09-15 全量）：启停控制（`start`/`stop`/`status`）、历史分页（`/api/comfy/jobs?page&page_size`，前端 `TaskEnginePanel` 每 4s 轮询释放租约）、精确取消（按 prompt_id 调 `/interrupt` 并置 `cancel_requested`/`cancel_state`）、有界重试（`retryComfy`）、来源校验（provenance：`validateProvenance` → 受保护 `import`，需人工审核标记）、模板参数应用（`applyComfyParams` 节点校验）、3D/媒体网格输出分类（`classifyComfyUIOutputs`）、Unreal 桥接（Blueprint/Actor 查询 + 受保护写端点）、GPU 协调增强（显存门槛/探测缓存/空闲卸载竞态/孤儿恢复/遥测）。
- **H3 子图 UI→API 转换器重写 + 实机短生成/取消/重试验收**（`e096834`，HANDOFF 收尾 `9b97ead`）：`feat: H3 子图 UI->API 转换器重写` 将 ComfyUI UI workflow 转 API prompt（映射 widget 字段、归一化 H3 模型路径、裁剪 editor-only/预览/标注节点、支持 union 输入、跳过缺失引用图、保留 SaveVideo API 字段、校验 link 类型、映射 legacy UUID）；实机跑通 39 帧（≈1.6s）短生成 + 取消 + 重试端到端，HANDOFF §5 P2-2 标【已解决 `e096834`】。
- **网页可审计抓取 + 答案来源可点击**（09-15 全量）：`feat: add auditable web page fetch tool` + 答案中 `相对路径`/URL 引用变蓝链；research 组合工作流（`web_research`）。
- **修复两处构建前发现的回归（随本构建同次提交，尚未独立 commit）**：
  - **`game_workbench.py` `comfy_history` 租约释放竞态**：后台 `comfy_watch` 线程轮询 `comfy_history`（带释放）会与前端每 4s 轮询（API `GET /api/comfy/history`）抢释放，导致 `lease_released` 非确定性（单测 `test_cancel_requests_interrupt_then_releases_on_terminal_history` 偶发失败）。改为 `comfy_watch` 调 `comfy_history(..., _release=False)` 仅更新状态，真实释放只走用户侧轮询；`comfy_history` 加 `_release` 开关。修复后 `tests/test_gpu_coordinator.py` 31/31、全量 313/313。
  - **`TaskEnginePanel.vue` `comfyPage`/`comfyTotal` 未声明即用**：17 次构建引入的前端回归——`loadComfyHistory`/`comfyPage` pager 使用这两个 `ref` 但第 7 行声明链漏掉，浏览器加载即抛 `ReferenceError: comfyPage is not defined`，面板崩溃。补 `comfyPage = ref(1), comfyTotal = ref(0)` 后浏览器冒烟从 26/27 回升至 27/27（此前唯一失败即此项）。

### 验证
- 单测：全量 `unittest discover` **313/313 通过**（含 ComfyUI 生命周期 + H3 转换器 + GPU 协调回归；`skipped=1`）。
- 场景画布：后端 `verify_scene_canvas.py` **54/54**；浏览器冒烟 `verify_scene_canvas_ui.mjs`（serve 8011）**27/27**（修复 comfyPage 崩溃后从 26/27 回升）。
- 引擎嵌入：真机 `verify_engine_embed.py` **68/68 沿用第十七次**——本轮未改动引擎嵌入代码路径（`game_workbench.py` 仅动了 `comfy_history`/`comfy_watch`，未触碰嵌入逻辑），故直接继承 17 次实机结论，未重复弹窗。
- 前端：`npm run build` 成功；vendor 三分包（vue/codemirror/misc）哈希与 17 次一致，业务改动未使其失效；`web/assets/workbench-BBWdzMae.js` 182,945 字节、`SceneCanvas-BCwZps71.js` 240,029 字节。
- 冻结态（最小 PATH 仅 `System32`，`DOCMIND_SERVER_ONLY=1`，PyInstaller 退出码 0，端口 8022 冷冒烟）：
  - 冷启动 ~1s 服务就绪；build_time（exe mtime）`2026-09-15 13:09:19`；exe 19,737,852 字节；SHA-256 `1772415d76788920d5b33b57b0253863903cba081804f3894e2cdcda591a17a1`；
  - `/` 200（RAG 问答页，响应体字节与 `web/index.html` SHA-256 一致 `18dda125…`）；`/workbench/` 307→`workbench.html` 且字节与 `web/workbench.html` 一致 `f11e211…`；`/assets/*` 9 个 JS+CSS 逐个 SHA-256 与源一致；favicon 200；
  - `/api/health` 200（`provider=mock`/`embedding_provider=local`，Ollama 本机 `present_models` 含 `bge-m3:latest` 等）；`/api/config` 200；
  - **12 个前端产物经 HTTP 返回体与 `web/` 源逐字节一致**（SHA-256 全 match：index.html / workbench.html / favicon.ico / 9 个 JS+CSS）；
  - 包内**无 `.chroma` / `.env` / `.docmind_state.json`** 泄漏（冒烟 exe 惰性初始化的 `_internal/.chroma` 与 `.docmind` 运行时态已清出）；无 `python*.exe`；MinGit 随包（`cmd/git.exe` 校验通过）；冒烟后已杀进程、端口释放、bundle 清理。
- 源码对应提交（本轮修复）：`game_workbench.py` comfy_history 竞态修复、`TaskEnginePanel.vue` comfyPage/comfyTotal 补声明（二者随本构建同次提交，见下方「第十八次构建提交」）；H3 与 ComfyUI 生命周期主体见 `e096834` / `9b97ead` 及 09-15 全量提交。

> **交付说明**：同第十七次——用户正运行的 8000 端口桌面实例锁定 `dist/DocMind/DocMind.exe`，暂未换入冻结产物。待用户关闭该实例后，将 `D:/Temp/docmind_rel18/DocMind/` 经 `robocopy /MIR` 换入 `rag-agent/dist/DocMind/` 即完成交付，无需重打包。

---

## 第十七次重建：RAG 问答质量收紧 — 提示词纪律 + 深度思考标注 + 文件链接看源码 + 思考泄漏修复（2026-09-14 20:56）

### 改动
- **Agent 提示词五项硬纪律**（d4d8820）：SYSTEM_PROMPT 收紧 ReAct 行为——① 文档/提示词/教程/规范类问题【第一个 Action 必须是 search_knowledge】，严禁先 search_code（避免命中 EXT_blend_minmax 等无关串误判"没有该文档"）；② 收敛硬护栏：同一检索词连续 2 次无新命中即停检、一轮检索步数 ≤4；③ 兄弟仓库指路：文档点名的实现文件名用 search_code/grep 确认（禁 read_file），查不到必须指引 /api/ingest_code 索引对应仓库；④ 用户给编号清单时 Final Answer 逐项答、不可答项标 N/A+原因；⑤ Thought 只写决策、Final Answer 答完即停。
- **问答页「深度思考」标注 + 文件路径可点击看源码**（11d7d91，纯前端 `web/index.html` 改动，刷新即生效）：推理轨迹头部「推理轨迹（ReAct）」改为「深度思考」，流式时带脉冲指示点（深度思考中）、答完熄灭，默认折叠；答案与轨迹里的 `相对路径:行号` 引用自动变蓝色可点击链接（.fileref），点击经 `GET /api/fs/file?path=` 拉取源码、弹窗带行号并定位高亮目标行；文件不在当前索引库时给友好提示并指引 /api/ingest_code 索引兄弟仓库。
- **修复 ReAct 思考过程泄漏到最终答案**（82ac6e4）：SYSTEM_PROMPT 加格式硬边界——不调用工具时必须直接以 Final Answer: 开头，禁止以 Thought: 冒充答案；新增 `_clean_fallback_answer()` 兜底残句优先提取 Final Answer、否则剥掉 Thought/Action/Action Input/Observation 标记、仍无实质内容返回友好提示，不再把原始 ReAct 标记抛给用户。
- 本轮构建还并入此前已提交、但尚未进冻结产物的问答页视觉改造：深色设计语言统一（b28a495）、底部输入区放大重构（a00194f）、滚动条浅色突兀修复（5cf4c1b）、检索去重 + 提示词收紧初版（ec4936f）。

### 验证
- 单测：全量 `unittest discover` **223/223 通过**（与第十六次持平）。
- 场景画布：后端 `verify_scene_canvas.py` **54/54**；浏览器冒烟 `verify_scene_canvas_ui.mjs`（serve 8011）**27/27**。
- 引擎嵌入：真机 `verify_engine_embed.py` **68/68**（本机 150% DPI 下按实际坐标断言；注：HANDOFF §5 旧记 64 项，实际 68）。
- 前端：`npm run build` 成功；vendor 三分包哈希与第十六次完全一致（业务改动未使 vendor 失效）；`web/assets/workbench-BOV3qPvm.js` 145,833 字节。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：
  - 冷启动 ~1s 服务就绪；`build_time=2026-09-14 20:56:00`（exe mtime，确认本轮，进程 PID 32316）；
  - `/` 200（响应体 80,310 字节 = RAG 问答页）；`/workbench/` 200；`/assets/workbench-BOV3qPvm.js` 200 且 content-length 145,833 与源一致；favicon 200；
  - `web/index.html` 含 `think-dot` / 「深度思考」标记、`composer-bar` 输入区；`/api/health` 200；
  - **前端产物经 HTTP 返回体与 `web/` 源逐字节一致**（`index.html` / `workbench.html` / `web/assets` 下 9 个 JS+CSS 逐个 SHA-256 match）；
  - 包内**无 `.chroma` / `.docmind_state.json` / `.env`** 泄漏（冒烟 exe 惰性初始化的 `_internal/.chroma` 已移出）；无 `python*.exe`；MinGit 随包；冒烟后已杀进程、端口释放。
- 真机 E2E（playwright + 系统 Edge）：问答「在 godot_sample 找定义 `_ready()` 的 .gd 文件，用 文件名:行号 格式回答」→ 答案中 `behaviors/player.gd:18` / `behaviors/enemy.gd:16` / `addons/docmind_bridge/docmind_bridge.gd:11` 全部变蓝链；点击 `behaviors/player.gd:18` 弹窗正确拉取源码并高亮第 18 行 `func _ready() -> void:`；最终答案不再以 Thought: 开头（思考泄漏修复生效）。
- 源码对应提交：`d4d8820 agent: 收紧 ReAct SYSTEM_PROMPT 五项纪律`、`11d7d91 UI: 问答页思考轨迹标注「深度思考」+ 文件路径可点击查看源码`、`82ac6e4 agent: 修复 ReAct 思考过程泄漏到最终答案`。

> **交付说明**：本轮新构建已生成并通过全部验证，产物位于 `D:/Temp/docmind_rel15/DocMind/`（因用户正运行的第十六次桌面实例 PID 15520 锁定 `dist/DocMind` 目录，暂未能换入 `rag-agent/dist/DocMind/`；待用户关闭该实例后，将临时构建 `robocopy /MIR` 换入 `dist/DocMind` 即完成交付，无需重打包）。

---

## 第十六次重建：模型预加载驻留修复 + 索引库 .chroma 泄漏修复（2026-09-14 18:16）

### 改动
- **模型预加载驻留时长修复**（62c0b9b，用户反馈「刚预加载就自动卸载」）：`api.py` 的 `model_power_ep` 预加载分支硬编码 `_ollama_keep_alive(name, "30m")`，覆盖了用户 `OLLAMA_KEEP_ALIVE=24h` 的服务端默认，导致预加载后约 30 分钟即被自动卸载。新增 `_preload_keep_alive()`——预加载只负责把模型加载进显存，驻留时长一律跟随 `OLLAMA_KEEP_ALIVE`（为空或 `0/-1/inf/infinite/none` 时回退 24h），不再写死。dev 实测：`/api/ps` 的 `expires_at` 从 30m 回到 24h（`expires_minutes` 29→1439）。
- **分发版索引库（`.chroma`）泄漏修复**（本次构建发现并修）：`docmind.spec` 的 datas 含 `(".chroma", ".chroma")`，会把**开发者本机已索引的代码向量（约 377 MB）原样烤进每个分发版**——既泄漏开发者本地索引、又徒增约 374 MB 体积，且对用户自己的代码库毫无用处（用户须自行 `/api/ingest_code` 重建索引）。该行为与 `DocMind_BUILD.md`「重要说明」里「未打包 .chroma」的既定约定相悖（疑为后续某次误加回 spec 导致回潮，第三/七/八次构建均明确不打包）。修复：`docmind.spec` 删除该 datas 项；`config.py` 在 `CHROMA_DIR` 解析后补 `os.makedirs(CHROMA_DIR, exist_ok=True)`，由 chromadb 首次启动自动建空索引目录。整包体积从约 682.5 MB 降至 **309 MB**。

### 验证
- 单测：全量 `unittest discover` **223/223 通过**（与第十五次持平，含 config.py 改动回归）。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：
  - 冷启动 ~1s 服务就绪；`build_time=2026-09-14 18:16:52`（exe mtime，确认本轮）；
  - 默认 `/` 是 RAG 问答页且返回体含 `toplink`（指向 `/workbench`）；`/workbench` 200；`/api/health` 200；`/api/model_status` 200；
  - **12 个前端产物经 HTTP 返回体字节与 `web/` 源逐字节一致**（SHA-256 全部 match）；`web/assets/workbench-BOV3qPvm.js` 字节数 145,833 与源一致；
  - **包内不再含 `.chroma`**（本次修复核心证据：此前每版都烤进 377 MB 开发者索引）；包内无 `python*.exe`/`.env`/`.docmind_state.json` 泄漏；MinGit 91 MB/365 文件；冒烟后已删状态文件、进程结束、端口释放。
- 阶段 2 哈希一致性：12 个前端产物逐个 SHA-256 与 `web/` 源一致；卫生扫描无状态/索引/密钥泄漏（含 `.chroma` 不复存在）。
- 模型预加载修复：dev 生产态实测 `expires_minutes` 29→1439（24h 驻留恢复）；冻结冒烟仅确认 `/api/model_status` 端点可达、相关逻辑已编入 exe。
- 源码对应提交：`62c0b9b fix(api): 模型预加载驻留时长跟随 OLLAMA_KEEP_ALIVE（不再写死 30m）`；`.chroma` 泄漏修复见 `docmind.spec` / `config.py`（与本文档同次提交）。

---

## 第十五次重建：桌面入口改 RAG 问答页 + 引擎嵌入实机闭环 + 场景画布/运行时时间线 + Agent 路由收敛（2026-09-14 17:14）

### 改动
- **桌面壳默认入口改为 RAG 问答页，工作台做第二入口**（d8e21c2，用户拍板）：`desktop.py` 新增 `HOME_PATH = os.getenv("DOCMIND_HOME", "/")`，`build_host_window` 用 `page = base + HOME_PATH` 取代硬编码的 `/workbench/`；可用环境变量覆盖不改代码。问答页顶栏加「开发工作台 →」（`web/index.html` + `.toplink` 样式），工作台顶栏加「问答」回链（`App.vue` + `style.css` 的 `.wb-question-link`）。新增 `tests/test_desktop_entry.py`（5 例）把「默认入口必须是 `/`、可被 `DOCMIND_HOME` 覆盖、宿主窗口不得再硬编码页面路径、问答页必须有 `/workbench` 链接、工作台必须有 `/` 回链」钉住，避免再退化成「两条路进不同页面」（含源码版/打包版状态隔离的说明）。
- **引擎嵌入实机闭环（Godot HWND）**：e250160（P0-1）/ 622fdbc（MCP bridge、web player、GPU queue、native embedding）/ 511bc12（试玩器接「嵌入工作台」开关）。后端契约 `POST /api/engine/{start,embed,detach,focus,resize}` + `GET /api/engine/inspect`；引擎视窗比例用「宿主客户区宽 / 页面视口宽」（非 `devicePixelRatio`，多屏/DPI 下才准），`detach` 必须可逆（保存原始 parent/style/屏幕矩形），二次嵌入复用保存句柄；本机 150% DPI 下 `verify_engine_embed.py` 按实际坐标断言。
- **场景画布 + 运行时时间线**（809a3b9，P0-2/P1-1）：试玩器弹窗第 2/3 个 tab——Vue Flow 场景画布（扁平节点 + 四类边 hierarchy/script/instance/reference）、运行时时间线；配套 `verify_scene_canvas.py`（54 项）/ `verify_scene_canvas_ui.mjs`（23 项）。
- **Agent 路由与审批工作流收敛**（a753614）：新增 `GET /api/agent/routing`、`GET /api/agent/connectors`；新增 `agent_policy.py`、`secrets_store.py` 两模块。
- **启动修复**（adfb542）：修复被吞掉的换行 + bat 编码导致的启动失败。
- 次要：c543047 agent 代码优先路由/健壮动作解析、5c5be2b 符号提取补 Java、b8e869c Vue Flow region canvas spike，以及 handoff/技能同步若干文档提交。

### 验证
- 单测：全量 `unittest discover` **223/223 通过**（较第十四次 88 例 +135，含 `test_desktop_entry` 5 例、场景画布/引擎嵌入回归）。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：
  - 冷启动 **1.6s** 服务就绪；`build_time=2026-09-14 17:14:57` 确认本轮；
  - 默认 `/` 是问答页且返回体含 `class="toplink" href="/workbench"`（默认入口已改）；`/workbench` 200；
  - **9 个前端产物经 HTTP 返回体字节与源逐字节一致**；`web/assets/workbench-*.js` 含 `wb-question-link`（问答回链在包内 JS 分包），`web/index.html` 含 `toplink`；
  - 无代码库时 `/api/fs/tree` 返 **400**（不是 500）；新端点 `/api/agent/routing`、`/api/agent/connectors`、`/api/engine/inspect` 均 **200**；`/api/engine/embed` 单处理器优雅返回 **200**；
  - 包内无 `python*.exe`/`.env`/`.docmind_state.json`/`.chroma` 泄漏；MinGit 89.5 MB/365 文件；冒烟后已删状态文件、进程结束、端口释放。
- 阶段 2 哈希一致性：9 个前端产物逐个 SHA-256 与 `web/` 源一致；卫生扫描无状态/索引/密钥泄漏。
- 引擎嵌入 / 场景画布 / 运行时时间线属需真机（Godot 进程 + 真实 HWND）才能端到端验证的能力，本轮冻结冒烟仅确认其端点可达、相关模块已编入 exe（`agent_policy`/`secrets_store` 在 PYZ 内）；真机闭环由既有 `verify_engine_embed.py`(60)/`verify_scene_canvas.py`(54)/`verify_scene_canvas_ui.mjs`(23) 守护，不在本机打包环境复跑。
- 源码对应提交：`d8e21c2 feat(desktop): 默认入口改为 RAG 问答页，工作台做第二入口` 为本轮入口改造主提交；a753614 / adfb542 / e250160 / 809a3b9 / 511bc12 / 622fdbc 为同期并入功能。

---

## 第十四次重建：分区可视化增强 — 一键创建 + 补齐导出桩 + 排序收紧（2026-09-12 18:26）

### 改动
- **missing 分区一键创建**：`regions.py` 新增 `scaffold_region(root, key)`——为单个缺失分区建目录、声明的导出接口桩（JSON 写 `{}`，与全量 init 一致）与 README；不重写 regions.json/DEV_INDEX/RULES；含目录根逃逸与「同名非目录」防护；项目其它已存在分区均为独立 git 仓库时才沿用 `git init` + 基线提交，否则纳入主仓库（`_siblings_use_git` 判定）。新端点 `POST /api/regions/create`。
- **已存在分区补齐导出桩**：新增 `fill_region_exports(root, key)`——只补写缺失导出文件，不动 README、不自动提交，含导出路径逃逸防护；新端点 `POST /api/regions/fill_exports`。`list_regions` 输出新增 `missing_exports`（目录存在时统计缺失导出）驱动按钮显隐。
- **前端（`RegionMapDialog.vue`）**：卡片顺序从配置声明序改为「依赖深度优先、同列 key 字典序」（与 DAG 分列同一语义，layout 暴露 `order: cols.flat()`）；DAG 列间距 76→40、去掉 `min-width:100%` 改 `margin:0 auto` 居中（溢出退化横滚）；missing 卡右下角蓝色「创建目录与文件」、缺导出的已存在卡琥珀色「补齐导出桩（N）」+「缺 N 个导出」徽章 + 红色虚线文件名；两类操作均有确认弹窗（列明将生成文件）、互斥 busy 态、成功后面板不关就地刷新分区/契约横幅/文件树。
- `api.ts`：`RegionInfo.missing_exports`、`createRegion`/`fillExports` 与响应类型；`workbench.ts` 抽出 `refreshContracts()`，busy 态统一为 `busyRegionKey`。
- 业务 chunk 100.02 KB（gzip 37.14K，105,997 字节）、CSS 54.13 KB；vendor 三分包哈希全部不变；构建前已清空 `web/assets/` 历史 workbench hash。

### 验证
- 单测：新增 `tests/test_regions_scaffold.py` **15 例**（桩生成、幂等、已有内容不覆盖、无导出区仅 README、未知 key、缺目录、非 git 兄弟不 init、git 兄弟 init+基线提交、非法 `../` 目录在归一化层被丢弃、missing_exports 三态、scaffold+fill 后契约转绿）；全量 `unittest discover` **88/88 通过**（MinGit 在 PATH，git 用例实际执行）。
- dev 生产态浏览器实测：初始仅数值区有补齐按钮（红标 balance.schema.json），取消无副作用；确认后 2→3 文件、按钮消失、文件树出现桩文件、契约 2→1；再建素材区后横幅转绿「契约校验通过」、DAG 节点虚线转实；创建素材区/bug 区流程同上轮（5→4→3 按钮）；控制台无应用错误，测试产物已删、样例仓 git 干净。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：冷启动 0.7s `/workbench/` 200；`build_time=2026-09-12 18:26:30` 确认新版；空 code_root 时 `/api/regions` 返 code_root 空串+默认 8 区；新业务 JS `/assets/workbench-BlWw82QY.js` 200（105,997 字节）与源一致；表单 ingest 样例 **19 切片**；**本轮新端点回归**——fill_exports(values) 200 生成 balance.schema.json（files 2→3）、verify_contracts 1 错误，create(assets) 200（manifest.json+README.md）、verify_contracts ok=true/0 错误；随后经 fs/delete 清理样例仓，契约恢复 2 错误。前端 6 文件 SHA-256 全一致；包内无 python*.exe/.env/状态文件；MinGit 89.5 MB/365 文件。
- **冒烟后清理**：ingest 在冻结默认 local 嵌入下改写包内 `_internal/.chroma`（mtime 18:29，新增 1 个 collection），已用源 `.chroma` `robocopy /MIR` 镜像还原（148=148 文件、逐一 SHA-256 零不一致，无 dist 根级 .chroma），已删 `_internal/.docmind_state.json`；进程结束、端口释放。
- 源码对应提交：`4000fd2 feat(workbench): region one-click scaffold and export stub repair`（6 文件 +579−16）。

---

## 第十三次重建：P3 — Git 历史/回滚 + 改写 diff 预览 + 分区可视化（2026-09-12 03:24）

### 改动
- **Git 历史版本与一键回滚**：
  - 后端 `workbench_fs.py` 新增 4 端点：`GET /api/fs/gitlog`（文件级提交历史）、`POST /api/fs/revert`（放弃该文件全部未提交改动，含已暂存，`reverted=false/reason=clean` 幂等）、`GET /api/fs/git-show`（任意 ref 文件内容）、`POST /api/fs/restore-at`（恢复历史版本并按既有保存链重索引）；文件树节点补充 `tracked`/`dirty` 字段。
  - 前端新增 `GitHistoryDialog.vue`：提交列表（哈希/作者/相对时间/消息）、历史版本与当前对照预览、恢复任意版本；顶栏「历史」「回滚」按钮与右键菜单均按 tracked/dirty 状态禁用；回滚有二次确认。
- **AI 改写字级 diff 预览**：新增零依赖行级 LCS `diff.ts`（返回 same/del/add 行 + 统计，边界用例已验证）；「替换选区」改为先弹 `RewriteDiffDialog.vue`——双行号（旧/新，按选区起始行偏移）、+N/−N/未变统计、完全一致时禁用接受、Esc/遮罩/取消均可放弃；**接受才写编辑器**，复用 P2 陈旧坐标/只读护栏，仍不自动保存。
- **分区治理可视化**：顶栏「分区治理」药丸（span→button）打开 `RegionMapDialog.vue`：① 依赖方向 DAG（dep→依赖方）按依赖深度分列的 SVG 布局，7 条贝塞尔箭头带 marker；② 每区状态卡（文件数/独立 git/分支/脏标记/依赖 chip/导出/校验器，missing 区虚线不可点）；③ 契约校验横幅（`/api/verify_contracts` 的 ok/errors）；点卡片在文件树定位该分区，点 DAG 节点高亮并滚动到卡片。
  - `api.ts` 新增 `regionsApi`；契约失败是 HTTP 200 + `{ok:false}` 的正常业务结果，**独立 fetch 实现**，不走会把 `body.ok===false` 抛错的通用 `request()`。
  - **修复实测抓出的 DAG 分列 bug**：初始调用误把节点自身放入环检测栈（`depthOf(k, new Set([k]))`），入口守卫立即命中导致所有节点深度恒 0、全挤第一列；改为初始空栈（环检测只在递归下钻时由祖先触发），Node 复现确认后修复。
- 规范化 `SelectionAiPanel.vue` 中 P2 遗留的 2 个**字面 NUL 字节**（markdown 代码块占位符本意是 `\0` 转义），git 不再把该文件识别为二进制；渲染行为不变。
- 业务 chunk 97.27 KB（gzip 36.24K，102,873 字节）、CSS 52.83 KB；vendor 三分包哈希全部不变（vendor-codemirror-B2ERf4YJ / vendor-vue-2seagieN / vendor-misc-CEfHfS6C）；构建前已手动清空 `web/assets/` 全部历史 workbench hash。

### 验证
- 单测：全量 `unittest discover` **73/73 通过**（MinGit 在 PATH，git 用例无跳过）；其中 `tests/test_workbench_fs.py` 31 例覆盖沙箱/树/回滚/恢复与分区元数据。
- dev 生产态浏览器实测（:8000/workbench，qwen3.6:35b-a3b 真实 SSE 两轮）：① 改写 diff——5 删 5 增双行号、统计栏、取消后编辑器不动、真实 Esc 关闭、接受后选区写为 AI 结果且 turn 状态「已替换」、Ctrl+S 才落盘，随后用本版「回滚」按钮恢复 HEAD，regions 复查 dirty=false；② 分区面板——8 节点正确分列（values/assets/bugs 在 x=10，behaviors/levels/ui/audio/net 在 x=254），7 箭头从被依赖区跨列指向依赖方，契约横幅报 2 个问题（缺 manifest.json/balance.schema.json），卡片点击定位文件树、DAG 节点点击高亮卡片；console 无应用错误，测试后样例仓 git 干净。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：冷启动 1s `/workbench/` 200；`build_time=2026-09-12 03:24:52` 确认新版；空 code_root 时 `/api/fs/tree` 返 400；新业务 JS 200（102,873 字节）与源一致；表单 POST `/api/ingest_code` 索引样例 **19 切片**；symbol-map 7 文件/21 符号、relation-graph 7 节点/4 边（3 inherits+1 calls）；**P3 回归**——regions 8 区/3 存在、verify_contracts ok=false/2 错误、最小 PATH 下 gitlog 200 返回 3 条提交（随包 MinGit 自足）。前端 6 文件 SHA-256 全一致；包内无 python*.exe/.env/状态文件；MinGit 89.5 MB/365 文件。
- **冒烟后新增清理动作**：冒烟的 ingest 在冻结默认 local 嵌入下改写了包内 `_internal/.chroma`（mtime 03:26），已用源 `.chroma` `robocopy /MIR` 镜像还原（148 文件逐一 SHA-256 一致），并删除 `_internal/.docmind_state.json`；进程结束、端口释放。
- 源码对应提交：`4889dcd feat(workbench): P3 git history/rollback, rewrite diff preview, region map`（12 文件 +1849−9）。

---

## 第十二次重建：P2 选区 AI（2026-09-12 00:32）

### 改动
- **编辑器选中代码即弹出 AI 工具条**（fixed 浮条锚定选区末端，滚动/缩放 RAF 刷新坐标，贴近底沿自动上翻）：四个动作——解释 / Review / 提问 / 改写；只读文件（契约名单）不显示「改写」。
- **双通道设计**（用户拍板）：
  - 解释 / Review / 自由提问 → 复用 `POST /api/chat` ReAct agent，把文件、语言、起止行、选区全文与任务指令拼成上下文，可 search_code / read_file / grep 检索并引用文件:行号；右侧 392px 面板渲染流式 markdown（标题/列表/行内与围栏代码），检索思考过程折叠在「检索 / 思考过程」里。
  - 改写 → **新增 `POST /api/selection_ai`**：绕过 agent 单轮直连 `LLMClient`，system 强约束「只输出替换选区的纯代码、无围栏无解释、不改对外签名」，SSE 逐 token；答案可一键**替换原选区**（不自动保存，仍走 Ctrl+S 与 409/422 护栏），另有复制、停止（AbortController）、清空。
- **替换安全护栏**：异步返回后必须当前标签仍是目标文件、文件可写、且原 `from/to` 范围文本与发起时逐字一致（陈旧坐标检测，防覆盖往返期间的手动修改）。
- **实测抓出并修的两个真问题**：① agent 每轮 ReAct 都转发 token（含 `Thought:/Action:` 草稿），面板最初把草稿当答案累加——改为 agent 通道只在 `final` 渲染干净答案，仅直连快通道逐字显示；② 选区已附完整代码，agent 仍逐文件浏览耗尽 29 次工具步数——提示词收紧为「优先基于已贴代码作答、工具调用≤2 次、拿到必要信息立即收尾」，复测 Review 8 次检索正常收尾。
- 长度护栏：选区 40,000 字符、文件上下文 60,000、指令 4,000；空选区 400、超长 413、mock/无 key 409、ollama 不可达走既有引导流。
- 业务 chunk 73.93 KB（gzip 28.77K），CSS 38.10 KB；vendor-vue 因引入 nextTick 等哈希变为 `vendor-vue-2seagieN`，codemirror/misc 不变。

### 验证
- 单测：新增 `tests/test_selection_ai.py` 10 例（强约束 system/定位信息/自定义指令/文件上下文/空语言回退/未知行号 + 400/413/422 护栏），全量 `unittest discover` **67/67 通过（4 skip）**。
- dev 生产态浏览器实测（:8000/workbench，qwen3.6:35b-a3b 驻留）：浮条元信息（行号/行数/字符数）与 4 按钮；改写 8s 返回纯 GDScript 并替换选区（doc 变更、dirty 角标、「已替换·Ctrl+S 保存」、未自动落盘）；只读 regions.json 选中后无改写按钮；Review 返回 1409 字报告（4 个代码块、精确引用 player.gd:11/12 与 hud.gd:6、按严重级排序、无 ReAct 草稿污染）；提问输入框 Enter 发送/问题透传/生成中可停止（状态「已停止」）；console 零错误。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0，构建约 75s）：冷启动 0.5s `/workbench/` 200；`build_time=2026-09-12 00:32:23` 确认新版；新业务 JS 200（78,134 字节）与源一致；`/api/selection_ai` 空选区 400 / 超长 413 / 缺字段 422；**真实改写 SSE 端到端**（最小 PATH、随包环境，3.2s，36 token + final + done）——指令「amount 改名 damage 并加生命值下限」产出纯代码 `func take_damage(damage: int)` + `max_hp = max(0, max_hp)`，无 markdown 围栏；ingest 19 切片、symbol-map 7 文件/21 符号、relation-graph 7 节点/4 边（3 inherits+1 calls）；最小 PATH 下 gitlog 200（样例仓 head 已为用户提交的 90db41b）。前端 6 文件 SHA-256 全一致；包内无 python*.exe/.env；MinGit 89.5 MB/365 文件；冒烟后已删 `_internal/.docmind_state.json`，进程结束端口释放。
- 源码对应提交：`da70800 feat(ai): P2 selection AI — grounded explain/review/ask + direct rewrite channel`（8 文件 +801−2，无 BOM 提交信息字节复验通过）。

---

## 第十一次重建：P1 调用边（2026-09-11 23:17）

### 改动
- **后端第四遍扫描：高置信调用边 calls（`workbench_fs.build_relation_graph`）**，只连「能解析到项目内已定义类、且目标类确实定义了该方法」的调用，从源头压制噪声；裸调用、引擎 API、`.new()`、自调用、字符串/注释中的伪调用一律不出边。
  - GDScript：扫描前用 `_mask_gdscript` 状态机把字符串与注释逐字符掩码（保长、保行号，支持引号转义）；规则 A=接收者标识符命中项目 `class_name`（`CritConfig.method(`），规则 B=类型化类成员/局部变量/函数参数（类型来自 `var x: T`、`@onready var x: T`、参数注解）。
  - Python：`ast.Call`+`Attribute`，结合 `import`/`from ... import ... as` 别名、`import m; m.Cls.x()` 模块限定、字段/局部/参数类型注解解析接收者；第三方模块不出边。
  - 边形态：同 source/target 聚合成一条 calls 边（label「调用」），`methods` 为按首次出现序去重的方法名列表，`line` 取首个调用点，自环丢弃；自动计入 `stats.edges_by_kind.calls`。
- **前端 `RelationGraph.vue`**：新增第 4 个开关「调用」（绿色实线箭头 `#4f9e6a`，弹簧理想长 170），统计文案增加「N 调用」，边悬停 `<title>` 显示「调用：method()…（首个调用点第 N 行）」；`api.ts` 的 `RelationEdge` 增加 `'calls'` 与可选 `methods`。业务 chunk 58.41 KB（gzip 22.05K），vendor 三哈希不变。
- **审查阶段修复的两个真 bug**（原实现未构建未实测）：
  1. GD 类成员类型预扫描用 `^\s*var` 误收函数体内缩进局部变量，导致局部类型跨函数泄漏、给其他函数的未定义接收者造假边；改为预扫描只收列 0 声明，局部 var 在逐行扫描进入函数后注册。
  2. calls 过滤条件被错放在 `Array.filter` 的第二参数（thisArg 位），生产构建里自由变量求值抛异常，**全部边静默不渲染**；已并入回调条件链。另删除死代码 `file_envs`。
- 样例仓新增真实演示链（样例仓独立提交）：`ui/hud.gd` 加 `class_name HUD`，`behaviors/player.gd` 加 `@onready var hud: HUD` 与 `take_damage()` 调 `hud.show_hp()`；图上呈现 player → HUD 一条调用边。

### 验证
- 单测：`tests/test_relation_graph.py` 新增 8 例（静态类调用+同方法去重、类型化字段/参数调用、引擎 API/字符串/注释/裸自调用排除、Python 项目内调用+第三方/缺失方法排除、局部类型跨函数泄漏回归、self/同类名自环、多方法合并保首行、Python module 限定调用）；全量 `unittest discover` **57/57 通过（4 skip）**。
- dev 生产态浏览器实测（:8000/workbench）：关系图 4 边全渲染（3 继承 + 1 绿色调用边 player→HUD），边 tooltip「调用：show_hp()（首个调用点第 12 行）」，调用开关关闭后边数 4→3、重开恢复，调用开关图标为绿色，console 零错误，tree/relation-graph 接口 200。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，PyInstaller 退出码 0）：冷启动 0.5s `/workbench/` 200；`build_time=2026-09-11 23:17:51` 确认新版；空 code_root 时 relation-graph 返 400；新业务 JS 200（60,965 字节）、CSS 200（30,082 字节）与源文件字节一致；表单 POST `/api/ingest_code` 索引样例 **19 切片**（新增调用链代码后较第十版的 18 增 1）；symbol-map 7 文件/**21 符号**；relation-graph **7 节点/4 边 = 3 inherits + 1 calls**（`gd:behaviors/player.gd → gd:ui/hud.gd`，line=12，methods=show_hp）；最小 PATH 下 gitlog 200（2 条提交）、tree 中 player.gd/hud.gd `tracked=true dirty=true`（随包 MinGit 自足）。前端 6 文件与源码 SHA-256 全一致；包内无 python*.exe/.env；MinGit 89.5 MB/365 文件；冒烟后已删 `_internal/.docmind_state.json`，进程结束端口释放。
- 源码对应提交：`a28d69d feat(graph): P1 call edges — high-confidence project-internal calls`（4 文件 +367−14，无 BOM 提交信息字节复验通过）。

---

## 第十次重建：P1 关系图（2026-09-11 21:08）

### 改动
- **后端关系图构建（`workbench_fs.py`）**：新增纯函数 `build_relation_graph(root)` 与 `GET /api/fs/relation-graph`，复用符号信封与遍历护栏（1000 文件上限、500KB 单文件、mtime 缓存）。
  - **继承边 inherits**（子 → 父）：GDScript `extends`（含 `extends "res://x.gd"` 路径形式与 `class_name` 解析）、Python class bases（`ast` detail 串按顶层逗号切分，`Dict[str, int]` 泛型内层逗号不产生噪声；`object`/隐式 RefCounted 不出边）。项目内类直连，引擎/第三方基类聚合为虚线外部节点（同名只建一个）。
  - **挂载边 mounts**（场景 → 脚本，虚线橙色）：解析 .tscn 的 `[ext_resource type="Script" id=...]` 与节点段 `script = ExtResource("id")`；同脚本挂多节点去重；PackedScene 资源与缺失脚本目标不出边。
  - 节点 schema：`{id,label,sub,kind,rel,line,region,region_name,external,doc}`，kind∈class/script/scene/engine/external；stats 含 user/external 节点数与 edges_by_kind。
- **前端 `RelationGraph.vue`（新）**：零第三方依赖手写力导向（斥力 + 弹簧 + 矩形碰撞 + 中心引力，260 次预迭代收敛、rAF 余温），SVG 渲染；滚轮以指针为焦点缩放、背景拖拽平移、节点可拖动、矩形边界收进的箭头端点；继承/挂载/外部基类三个开关、搜索高亮节点及其直接邻居、适应窗口；点用户节点关闭图并 jumpToLine 到定义行；Esc/遮罩关闭。顶栏新增「关系图」按钮；`api.ts` 类型与 client、`theme.ts` graphNodeStyle、composable 的 relationGraphOpen 开关配套。
- 业务包 workbench chunk 57.45 KB（gzip 21.8K），vendor 分包哈希全部不变；构建后手动清理 `web/assets/` 上一版 workbench 旧 hash（emptyOutDir:false 会累积）。
- **范围决策**：调用边（谁调用了谁）因正则噪声大本轮不做；样例仓不新增演示场景（用户选择），样例数据为 5 用户节点 + 2 外部节点 + 3 继承边，挂载边由单测覆盖。

### 验证
- 单测：新增 `tests/test_relation_graph.py` 9 例（空项目、项目内继承、引擎基类聚合、无 extends 不出边、res:// 继承、Python 多继承/object 省略/泛型逗号、场景挂载去重、缺失目标跳过、节点 schema/stats）；全量 `unittest discover` **49/49 通过（4 skip）**，py_compile 与 IDE 诊断干净。
- dev 浏览器实测（两轮自动化）：统计文案「5 个类/脚本/场景 · 2 个外部基类 · 3 继承 · 0 挂载」、7 节点 3 边与虚线外部节点、三开关联动（隐藏外部基类时相关边一并消失）、搜索 crit 高亮 CritConfig 直接关系其余 dim、适应按钮、点 enemy 节点关闭图并把光标定位到 behaviors/enemy.gd 第 1 行、Esc 关闭；**console 零错误**，接口 200。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1，53s 构建）：冷启动 code_root 空时 relation-graph 返 400；`build_time=2026-09-11 21:08:54` 确认新版；POST `/api/ingest_code` 重建样例 **18 切片**；冻结环境 relation-graph 返回 7 节点/5 用户/2 外部/3 继承，symbol-map 7 文件/18 符号/by_kind 正确；`/workbench` 200（657 字节）、新业务 JS 200（57447 字节）。冻结前端 6 个文件与源码 SHA-256 全一致；包内无 python*.exe/.env/状态文件；MinGit 随包；冒烟后已删 `_internal/.docmind_state.json`，进程结束端口释放。

---

## 第九次重建：P1 符号语义地图（2026-09-11 20:05）

### 改动
- **零依赖符号提取器 `symbols.py`（新增，约 640 行）**：离线环境装不了 tree-sitter，采用手写行级/ast 解析。
  - GDScript：行级状态机（func/static func/inner class/class_name/extends/signal/跨行 enum/const/var/@export/@onready/@export_group/`##` 文档注释归属/块结束行），踩平 UTF-8 BOM、`@export var` 纯注解误跳过、`var x := {}` 推断语法（占位符归一化）等坑。
  - Python 走标准库 `ast`；`.tscn/.tres` 解析 node/ext_resource/sub_resource/section（属性顺序不定，改为整段属性分别 search）；其它语言（JS/TS/Java/C/Lua…）正则 + 大括号配平 + Lua `end`。
  - 统一信封 `{lang, class_name, extends, doc, symbols[]}`，符号含 name/kind/start/end（1 基含端点）/parent/signature/doc/detail；`file_symbols()` 用 utf-8-sig 读盘 + `(mtime_ns, size)` 缓存；`.json/.yaml/.toml/.godot` 等数据文件返回空信封。
- **符号级语义检索（`ingest.py`）**：切片从「启发式等长块」升级为符号感知——class/function 独立成段，叶子声明（const/var/signal/enum/group）按间隔/行数/数量合并为 decl 批，未覆盖间隙补 file/code 段；异常自动回退旧切块。入库文档仍为原文（行号锚点不变），但 **embedding 输入前置符号 doc**，中文 `##` 文档注释参与语义向量。样例库重建后 19 → 18 切片，中文查询函数块可独立命中。`load_code_file` 同步剥 BOM。
- **搜索结果标签（`tools.py` search_code）**：按符号 kind 显示 func/def/class/const/var/signal/enum，并以 `# 文档:` 展示 doc 首行。
- **新 API（`workbench_fs.py`）**：`GET /api/fs/symbols?path=`（单文件符号信封）、`GET /api/fs/symbol-map`（全库遍历，上限 1000 文件，附 region/region_name/class_name/extends/doc 与 stats by_kind）；沙箱解析、路径安全不变。
- **前端导航三件套**：
  - `SymbolOutline.vue`（新）：编辑器右侧 208px 大纲，类卡（class_name/extends/文档）+ 符号列表（kind 色标/parent 缩进/行号），可折叠；切标签与保存后自动重拉。
  - `SymbolMap.vue`（新）：全屏符号地图，多词 AND 搜索（名称/签名/文档/细节/路径/类名/继承/文件级文档）+ kind 动态 chips + 按分区→文件分组，点击符号跨文件打开并居中定位，Esc/遮罩关闭。
  - 顶栏新增「符号地图」按钮；`composables/workbench.ts` 新增 `jumpToLine()`（经 `window.__docmind_cm` 单例桥，等 view 就绪后选区 + scrollIntoView y:center）；`api.ts`/`theme.ts`（kind→色标/字母）配套；App.vue 布局在主区加一行 flex 容纳编辑器与大纲。
  - 业务包 workbench chunk 29.9 → 41.9 KB（gzip 12 → 16K），vendor 分包哈希不受影响。
- 样例仓 `godot_sample` 三个脚本在 `## 类说明` 与首个 `@export` 间补空行（Godot 约定：注释紧贴声明=成员文档），让类文档正确归属文件头（样例仓独立提交）。

### 验证
- 单测：新增 `tests/test_symbols.py` 15 例（BOM、头部文档归属、inner class 父级、局部变量排除、跨行 enum/dict、字符串内 #、tscn、JS、Lua、mtime 缓存等）；全量 `unittest discover` **40/40 通过（4 skip）**，py_compile 全过。
- dev 浏览器实测（http://127.0.0.1:8000/workbench）：crit.gd 大纲类卡 CritConfig + 3 常量 + crit_damage，点击光标定位 L8；地图 7 文件 18 符号、4 个分区组（未分区/角色行为/数值/UI·HUD）、kind chips（函数 6/常量 6/变量 6）、搜索 crit 与中文「暴击」均正确收敛、函数过滤 6 项；点 enemy.gd `_physics_process` 跨文件打开并定位 L9（`func _physics_process(_delta: float)`）；Esc/遮罩关闭、保存后大纲刷新；**console 零错误**。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1）：冷启动首启 code_root 空（400 提示正确）→ 表单方式 POST `/api/ingest_code` 索引样例 **18 切片** → symbol-map 返回 7 文件/18 符号/by_kind 正确，crit.gd 信封 class=CritConfig、doc=「暴击数值表（数值区）」、4 符号行号正确；`/workbench` HTTP 200。冒烟后已删 `_internal/.docmind_state.json`。
- 产物 663.7 MB；打包前已停 :8000（第八版教训：SQLite 锁致 COLLECT 失败）。

---

## 第八次重建：code_root 持久化 + 前端 vendor 分包（2026-09-11 19:11）

### 改动
- **代码库选择跨重启保持**：此前 code_root 只是内存态，每次重启应用都要重新索引选择。
  - `config.py`：新增 `STATE_FILE`（`<BASE_DIR>/.docmind_state.json`，开发=源码根、冻结=`_internal`）与 `save_state()`；import config 时 `_apply_persisted_state()` 自动恢复，**目标目录不存在则静默忽略**（U盘/网盘卸载不会报错）；写入失败静默回落内存态。
  - `api.py`：`/api/ingest_code` 成功后 `save_state("code_root", abs_root)`；`/api/reset_code` 同步写空串，避免重置后重启又被恢复。
  - `.docmind_state.json` 已加 .gitignore；spec 不打包该文件，**冻结包冒烟后必须从 `dist/DocMind/_internal/` 删掉本机选择再分发**。
- **前端 manualChunks 分包**（`frontend/vite.config.ts`）：`vendor-codemirror`（652KB/gzip 228K）/ `vendor-vue`（66KB）/ `vendor-misc`（6.6KB）/ 业务 `workbench`（29.9KB/gzip 12K）。业务代码高频改动不再使 vendor 哈希失效；总体积与单块时持平。构建前先手动删 `web/assets/` 旧 workbench-*（emptyOutDir:false 会累积旧 hash）。
- 后端无其它改动。

### 验证
- 源码态：删状态文件冷启动 code_root 为空 → 选库后状态文件落盘 → **再次重启自动恢复** code_root 与 code_sources=19（向量库本就持久化，二者各自恢复，无需重索引）；失效路径单测不恢复；reset_code 后状态清空。
- 冻结态（最小 PATH 仅 System32，DOCMIND_SERVER_ONLY=1）：首启 code_root 空 → 包内选库写入 `_internal/.docmind_state.json` → 二次冷启动自动恢复、文件树正常、MinGit 两次启动均生效；浏览器分包页面挂载正常、徽标正确、console 零错误；冒烟后已删包内状态文件。
- 回归 25/25；产物 662.9 MB，无 .env / python\*.exe / 本机状态文件泄漏。
- **构建教训**：源码实例正在运行时 PyInstaller 会因源 `.chroma` 的 SQLite 文件被占用而中途失败（COLLECT 已清空旧 dist 才报错，表现为 exe 时间戳不变、web/assets 被清空）。**打包前必须先停掉 :8000 实例**。

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
- **不打包 `.chroma`（第十六次起重申并落实）**：分发版不含开发者本机已索引的代码向量（早期某次误把 `(".chroma", ".chroma")` 加回 `docmind.spec` datas，导致 377 MB 开发者索引被烤进每版；第十六次已删除该项）。`config.py` 在 `CHROMA_DIR` 解析后 `os.makedirs(..., exist_ok=True)`，运行时 chromadb 在 `dist/DocMind/_internal/.chroma` 自动建空索引，用户自行 `/api/ingest_code` 索引自己的代码库即可。
- **旧项目 `regions.json` 残留**：若某项目根目录已有旧版 `regions.json`（早期默认无 `verify`），其优先级高于 `DEFAULT_REGIONS`，会显示 `verify` 为空。解决办法：删除该项目下的 `regions.json` 让其回落到新默认，或在其 `regions.json` 每个分区补 `"verify"` 字段。
- **启用外部调用 DocMind API**：设置环境变量 `DOCMIND_API_TOKEN=<你的令牌>`（启用后 `/api/*` 需带 `x-docmind-token` 或 `Authorization: Bearer`）；如需浏览器跨域调用，设 `DOCMIND_CORS_ORIGINS=https://你的前端域名`（默认 `*` 允许任意来源）。
- **启用 Agent 调外部 API**：必须设置 `EXTERNAL_API_ALLOWLIST`，否则 `dev_http_request` 一律拒绝——这是防 SSRF 的安全闸门，非空不可放行。写法：`api.example.com`（精确匹配且含其子域）、`*.example.com`（仅子域，不含裸域）、`*`（任意 host，协议仍限 http/https）；30x 重定向的每一跳都会重新校验白名单。
- **端口占用提示**：启动器含单实例保护——若 8000 端口已被旧 `DocMind.exe` 占用，新实例会直接打开浏览器而不重复启动；如遇旧进程卡死占用端口，需先结束旧进程再启动。
- **Git 前置依赖**：第七次构建起分发包**自带 MinGit**（`DocMind/MinGit/`，frozen 启动自动加入 PATH），用户机器无需安装 Git；仅源码运行（.venv / dev）仍需系统装有 git。分区工作台的初始化/提交/回滚基于每个分区独立 git 仓库；git 不可用时初始化会在写文件前给出明确提示，不产生半成品。
- **分发版不含 Python 解释器**：分区 `builtin:py` 校验在进程内做语法编译检查（exe/源码均可用）；但 pytest/unittest 试玩（playtest auto）与 cProfile 剖析需要真实 Python 环境，请在源码 `.venv` 中运行，exe 内会直接返回明确提示而不会静默失败。
- 第七次构建起 `docmind.spec` 含 MinGit 随包步骤（见上文）；早期「临时构建 spec 已清理」的说明不再适用。

### Inno Setup 安装器

可使用 installer\\DocMind.iss 构建安装器。安装器支持覆盖升级、开始菜单/桌面快捷方式和标准卸载；当前机器未安装 ISCC，因此尚未生成 Setup.exe。

### 便携版生命周期脚本

- installer\\upgrade_portable.ps1：停止旧进程后增量覆盖升级，保留日志。
- installer\\uninstall_portable.ps1：明确输入 YES 后删除便携版目录。

这两个脚本不依赖 Inno Setup，适合当前机器做升级/卸载演练。
