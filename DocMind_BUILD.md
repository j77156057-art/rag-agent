# DocMind 分发版构建说明（2026-09-11）

> 最新构建见下方「第十一次重建（P1 调用边：项目内高置信调用关系）」；历史构建清单保留在下文。

## 产物
- 路径：`rag-agent/dist/DocMind/`（onedir 目录分发）
- 入口：`DocMind.exe`（约 18.5 MB，控制台模式，启动时自动开浏览器）
- 整体体积：约 663.3 MB（chromadb / onnxruntime / webview 运行时 + 随包 MinGit 89.5 MB）
- **当前构建时间：`2026-09-11 23:17:51`（第十一次重建，P1 调用边，exe 19,401,970 字节）**
- 上一版：`2026-09-11 21:08:54`（第十次重建，P1 关系图，exe 19,397,582 字节）

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
- **未打包 `.chroma`**：不把开发期测试索引烤进分发版。运行时 Chroma 会在 `dist/DocMind/.chroma` 自动建空索引，用户自行对目标项目执行索引即可。
- **旧项目 `regions.json` 残留**：若某项目根目录已有旧版 `regions.json`（早期默认无 `verify`），其优先级高于 `DEFAULT_REGIONS`，会显示 `verify` 为空。解决办法：删除该项目下的 `regions.json` 让其回落到新默认，或在其 `regions.json` 每个分区补 `"verify"` 字段。
- **启用外部调用 DocMind API**：设置环境变量 `DOCMIND_API_TOKEN=<你的令牌>`（启用后 `/api/*` 需带 `x-docmind-token` 或 `Authorization: Bearer`）；如需浏览器跨域调用，设 `DOCMIND_CORS_ORIGINS=https://你的前端域名`（默认 `*` 允许任意来源）。
- **启用 Agent 调外部 API**：必须设置 `EXTERNAL_API_ALLOWLIST`，否则 `dev_http_request` 一律拒绝——这是防 SSRF 的安全闸门，非空不可放行。写法：`api.example.com`（精确匹配且含其子域）、`*.example.com`（仅子域，不含裸域）、`*`（任意 host，协议仍限 http/https）；30x 重定向的每一跳都会重新校验白名单。
- **端口占用提示**：启动器含单实例保护——若 8000 端口已被旧 `DocMind.exe` 占用，新实例会直接打开浏览器而不重复启动；如遇旧进程卡死占用端口，需先结束旧进程再启动。
- **Git 前置依赖**：第七次构建起分发包**自带 MinGit**（`DocMind/MinGit/`，frozen 启动自动加入 PATH），用户机器无需安装 Git；仅源码运行（.venv / dev）仍需系统装有 git。分区工作台的初始化/提交/回滚基于每个分区独立 git 仓库；git 不可用时初始化会在写文件前给出明确提示，不产生半成品。
- **分发版不含 Python 解释器**：分区 `builtin:py` 校验在进程内做语法编译检查（exe/源码均可用）；但 pytest/unittest 试玩（playtest auto）与 cProfile 剖析需要真实 Python 环境，请在源码 `.venv` 中运行，exe 内会直接返回明确提示而不会静默失败。
- 第七次构建起 `docmind.spec` 含 MinGit 随包步骤（见上文）；早期「临时构建 spec 已清理」的说明不再适用。
