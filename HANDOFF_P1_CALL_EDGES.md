# DocMind 交接文档：P1 调用边（Call Edges）

> 生成时间：2026-09-11 22:12 · 工作目录：`d:\WorkBuddy\rag-agent`
> 上一轮上下文用尽，本文件用于移交给下一个 AI 继续开发。
> **注意**：根目录的 `HANDOFF.md` 是更早的 T1–T5 体系，已过时，**不要**以它为准；以本文件为准。

---

## 0. 30 秒速览

| 项 | 值 |
|---|---|
| 当前 HEAD | `322602c`（chore: 发布流程 skill），工作区 **clean** |
| 测试基线 | **49 tests OK（4 skipped）** |
| 当前任务 | P1 **调用边**（谁调用了谁）——**只完成了方案设计，一行代码未写** |
| 已确认的范围决策 | ① GDScript + Python 都做；② 样例仓补一条真实调用链用于演示 |
| 下一步 | 从第 5 节 TODO 的 `c-1` 开始：改 `workbench_fs.py` |
| dev 实例 | 已在 :8000 运行（`run_desktop.bat`），code_root 自动恢复为 `D:\WorkBuddy\godot_sample` |

---

## 1. 环境与硬性约定（每个新 Shell 必做）

```powershell
$env:PATH = "C:\Users\h'h'h\.local\bin\MinGit\cmd;" + $env:PATH   # 用户名含单引号，必须双引号包裹
Set-Location 'd:\WorkBuddy\rag-agent'
```

- Python 解释器：`.\.venv\Scripts\python.exe`（3.13；**没有 pytest**）
- 跑测试：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests`
- 前端：`cd frontend` 后 `npm run build`（Vite + Vue 3 + TypeScript；**未安装 vue-tsc**，靠 `npm run build` + IDE 诊断把关）
- 系统：Windows + **PowerShell 5.1**；
  - `cmd /c` 被安全策略**禁用** → 一律用纯 PowerShell / `Start-Process`
  - 写外部仓的 `.git/index.lock` 被沙箱硬拦 → **外部样例仓的提交只能请用户在系统 PowerShell 手动做**
- 读 `.git` 内容（如 `git cat-file`）需在 RunCommand 里加 `requires_approval:false` 且可能仍需用户放行；`git log` 用 `--no-pager`（MinGit 不带 less）

---

## 2. 项目架构地图（改代码前必读）

### 后端（项目根，平铺 .py）
| 文件 | 职责 | 与本任务相关的锚点 |
|---|---|---|
| [workbench_fs.py](file:///d:/WorkBuddy/rag-agent/workbench_fs.py) | 工作台文件系统 API（FastAPI router，前缀 `/api/fs`） | P1 关系图代码在 **718–1002 行**；`_split_top_commas` 737、`_scene_script_refs` 756、**`build_relation_graph` 797**、端点 `GET /api/fs/relation-graph` 1122 |
| [symbols.py](file:///d:/WorkBuddy/rag-agent/symbols.py) | 零依赖符号提取器（导入别名 `symlib`） | `_extract_gdscript` 194、`_extract_python` 415、`extract` 624、`file_symbols` 648（`(mtime_ns,size)` 缓存） |
| [ingest.py](file:///d:/WorkBuddy/rag-agent/ingest.py) | 符号感知切片 + 向量入库 | 本轮**不需要动** |
| [api.py](file:///d:/WorkBuddy/rag-agent/api.py) | 其它 HTTP 端点（`/api/ingest_code` 等） | 本轮**不需要动** |
| [config.py](file:///d:/WorkBuddy/rag-agent/config.py) | `CODE_ROOT` / `STATE_FILE` / `get_runtime` | 本轮不需要动 |

### 前端（`frontend/src/workbench/`）
| 文件 | 职责 |
|---|---|
| [RelationGraph.vue](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/components/RelationGraph.vue) | **本轮主战场**：关系图全屏 overlay，零依赖手写力导向 + SVG |
| [api.ts](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/api.ts) | 接口类型与 client。`RelationNode` 122、`RelationEdge` 135、`RelationGraphResp` 144、`fsApi.relationGraph()` |
| [theme.ts](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/theme.ts) | `graphNodeStyle(kind)` 节点配色（class 蓝 / script 青 / scene 橙 / engine、external 灰虚线） |
| [composables/workbench.ts](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/composables/workbench.ts) | `relationGraphOpen` / `openRelationGraph` / `closeRelationGraph` / `jumpToLine` |
| [App.vue](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/App.vue) | 顶栏「关系图」按钮 + 挂载 `<RelationGraph />` |

### 构建产物
- 前端产物 `frontend/dist` → 复制到 `web/`（Vite `emptyOutDir:false`，**每次构建前后要手动删 `web/assets/workbench-*` 旧 hash**）
- 打包：PyInstaller `docmind.spec` → `dist/DocMind/DocMind.exe`
- 发布全流程已固化为工作区 skill：[.trae/skills/docmind-frozen-release/SKILL.md](file:///d:/WorkBuddy/rag-agent/.trae/skills/docmind-frozen-release/SKILL.md) —— **要打包时先读它**

---

## 3. 已完成进度（上下文）

### 提交链
```
77dcf31  第八版：code_root 持久化 + 前端 vendor 分包
6397045  第九版：P1 符号语义地图（symbols.py / 大纲 / 全局地图 / 符号级检索）
6031542  第十版：P1 关系图（继承边 + 场景挂载边 + RelationGraph.vue 力导向）
322602c  发布流程 skill 入库
```
- 第十版 exe：19,397,582 字节 / 构建时间 `2026-09-11 21:08:54` / 整包 663.3 MB
- 第十版文档记录见 [DocMind_BUILD.md](file:///d:/WorkBuddy/rag-agent/DocMind_BUILD.md) 顶部「第十次重建」

### 现有关系图能力（本轮要在其上扩展）
`build_relation_graph(root)` 输出 `{ok, code_root, regions_enabled, nodes[], edges[], stats}`：

- **节点**：`{id,label,sub,kind,rel,line,region,region_name,external,doc}`
  - `kind` ∈ `class`（.gd 有 class_name / .py 顶层类）| `script`（.gd 无 class_name）| `scene`（.tscn）| `engine` | `external`
  - id 规则：`gd:{rel}` / `py:{rel}:{ClassName}` / `scene:{rel}` / `ext:engine:{Name}` / `ext:external:{Name}`
- **边**：`{source,target,kind,label,line}`，现有 `kind` ∈ `inherits`（子→父，label `extends`/`bases`）、`mounts`（场景→脚本，label `挂载`）
- **护栏**：复用 `_SKIP_DIRS` / `_CODE_EXT` / `_MAX_CODE_FILE`（500KB）/ `SYMBOL_MAP_MAX_FILES`（1000）；遍历结果 `walked` 为 `[(rel, ext, abs_path)]`
- 继承解析：`gd_by_rel`（路径精确匹配）、`user_by_name`（简单名匹配）、`py_classes`（`[(node_id, bases_detail)]`）

### 样例仓（独立 git 仓）
- 路径：`D:\WorkBuddy\godot_sample`，基线 `3a260e4`，工作区 clean
- 结构：`behaviors/{enemy,player}.gd`、`ui/hud.gd`、`values/{crit,level}.gd`、`regions.json`、`project.godot`
- **无 .tscn**；5 个 .gd 均为 **UTF-8 带 BOM**
- 当前真实图：7 节点（5 用户 + 2 外部）/ 3 继承边 / **0 调用边**（脚本内全是引擎 API 调用，无项目内互调）

---

## 4. 本轮任务：P1 调用边 —— 已确认的设计方案

### 4.1 核心原则（防噪声，务必遵守）
> **只连「能解析到项目内已定义类/方法」的调用；引擎 API、裸函数调用、无法确定接收者类型的一律不出边。**

这条原则是对上一轮「调用边正则噪声大所以暂缓」的直接回应，也是本方案能落地的前提。

### 4.2 后端：两类高置信规则

**规则 A — 静态类名调用**
```gdscript
CritConfig.crit_damage(0.2, 1.5)   # 上层项目类 → 边
```
- 接收者标识符精确命中项目 `class_name`（`user_by_name`）
- 且该方法确实定义在目标类的符号表中（`symlib` 的 `kind == "function"`）

**规则 B — 类型化变量/参数调用**
```gdscript
@onready var hud: HUD = $HUD
var cfg: CritConfig
func take_damage(self, cfg: CritConfig) -> void:
    hud.show_hp(max_hp, 100)       # hud 的声明类型 HUD 是项目类 → 边
    cfg.crit_damage(...)           # 参数类型注解同样是项目类 → 边
```
- 从本文件符号信封的 `detail`（类型注解）构建「变量名 → 项目类」映射
- 覆盖三种来源：`var x: T`、`@onready var x: T = ...`、函数参数 `p: T`
- 目标类中必须存在同名方法，否则不出边

**必须排除（否则噪声爆炸）**
- 裸调用：`clampf(...)`、`move_and_slide()`、`pow(...)`、`print(...)`
- 同文件自调用（节点粒度下会形成自环）
- `.new()` / 引擎单例（`Input`、`Engine`、`OS`…）
- 字符串字面量与注释中的括号 —— **扫描前先掩码字符串**（如把 `"..."` 内容替换为空白，保留长度以便行号/列位稳定）
- 接收者类型无法解析（如 `player.global_position` 中 `player` 是 `Node3D`，引擎类型 → 不出边）

**Python（用 `ast`，比正则精确）**
- 走 `ast.Call` + `ast.Name/Attribute`，结合 `import` / `import as` 别名与类型注解解析接收者
- 只连项目内类（`py:{rel}:{Class}`）中已定义的方法；`self.` 自调用不出边
- 引擎/第三方模块（`os`、`re`、`chromadb`…）不出边

### 4.3 边形态（节点级聚合，不建函数节点）
现有图是「文件/类/场景」粒度，调用边沿用同粒度，避免图爆炸：

- `kind`：**`calls`**
- 方向：`source` = 调用方节点，`target` = 被调类节点
- `label`：**`调用`**（与 `extends`/`bases`/`挂载` 风格一致）
- `line`：**首个调用点的行号**（用于点击跳转）
- 去重：同一 `(source, target, "calls")` 只留一条；多个不同方法调用合并为一条边
- 建议在边对象上附加 `methods: [...]`（方法名去重列表），供 tooltip 展示；**若加字段需同步 `api.ts` 的 `RelationEdge` 类型**

### 4.4 前端
- [api.ts](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/api.ts#L135-L142)：`RelationEdge.kind` 联合类型加 `'calls'`；如有 `methods` 字段一并加
- [RelationGraph.vue](file:///d:/WorkBuddy/rag-agent/frontend/src/workbench/components/RelationGraph.vue)：参照现有 `mounts` 的接入方式，共 7 处要动：
  1. 新增 `const showCalls = ref(true)`（第 18–20 行那组）
  2. `watch([showExternal, showInherits, showMounts])` → 加 `showCalls`（第 100 行）
  3. `viewEdges` 过滤条件加 `(e.kind !== 'calls' || showCalls.value)`（第 114–125 行）
  4. 弹簧理想长度 `ideal`：给 calls 一个值（第 273 行 `e.kind === 'mounts' ? IDEAL_MNT : IDEAL_INH`）
  5. 统计文案（第 520 行）加「N 调用」；`edges_by_kind.calls` 由后端返回
  6. 开关按钮区（第 537–545 行）加第 4 个 `.rg-toggle`
  7. 边渲染（588–602 行）与 CSS（799–806 行）：新增 `.rg-line.calls`（建议绿色实线，如 `#4f9e6a`）、`rg-arrow-call` marker、`.rg-elabel-*-call`
- 图例、节点 tooltip（可选：把 `methods` 拼进 `<title>`）
- 点边/点节点跳转沿用现有 `jumpToLine`（`window.__docmind_cm` 单例桥）
- 完成后 `npm run build`，**先删 `web/assets/workbench-*` 旧 hash**；vendor 三个分包哈希应保持不变（预计业务 chunk 从 57.45KB 略增）

### 4.5 样例仓改动（用户已同意；需用户手动提交）
> 目的：让真实图里出现一条有意义的调用边（`player → HUD`），覆盖最有价值的「类型化接收者」规则。

**`ui/hud.gd`** —— 新增 `class_name HUD`（放在首行，`extends` 顺延）：
```gdscript
class_name HUD
extends CanvasLayer
## 战斗 HUD（界面区）

@onready var hp_label: Label = $Margin/VBox/HPLabel

func show_hp(hp: int, max_hp: int) -> void:
    hp_label.text = "HP %d / %d" % [hp, max_hp]
```

**`behaviors/player.gd`** —— 加生命值与受击方法：
```gdscript
extends CharacterBody3D
## 玩家控制器（行为区）

@export var speed: float = 6.0
@export var jump_velocity: float = 4.5
@export var max_hp: int = 100

@onready var hud: HUD = $HUD

func take_damage(amount: int) -> void:
    max_hp -= amount
    hud.show_hp(max_hp, 100)

func _physics_process(delta: float) -> void:
    var direction := Input.get_vector("move_left", "move_right", "move_forward", "move_back")
    velocity.x = direction.x * speed
    velocity.z = direction.y * speed
    move_and_slide()
```

注意事项：
- 写文件时保持 **UTF-8 带 BOM**（与仓内既有 .gd 一致）、缩进用 **Tab**
- `$HUD` 在无 .tscn 的仓里运行时不成立，但本仓只做静态解析/演示，可接受
- 预期效果：新增 `gd:ui/hud.gd` 变为 `kind=class, label=HUD`；出现 1 条 `calls` 边 `gd:behaviors/player.gd → gd:ui/hud.gd`
- **用户在系统 PowerShell 手动提交**（沙箱写不了样例仓的 .git）；提交前提醒用户 `git add behaviors/player.gd ui/hud.gd` 并写清提交信息

---

## 5. 剩余 TODO（按序执行）

| id | 任务 | 关键点 / 验收 |
|---|---|---|
| `c-1` | **后端**：在 `workbench_fs.py` 的 `build_relation_graph` 内新增第四遍「调用边」扫描 | 加字符串掩码 helper；GDScript 规则 A/B；Python `ast` 解析；聚合去重成 `calls` 边；`stats.edges_by_kind.calls` 自动包含（现有代码按 kind 汇总，无需改） |
| `c-2` | **单测**：扩 [tests/test_relation_graph.py](file:///d:/WorkBuddy/rag-agent/tests/test_relation_graph.py) | 复用 `_write` / `_node` / `_edges` 三个 helper（18–34 行）。至少覆盖：静态类调用命中、类型化变量调用命中、参数类型调用命中、引擎 API 不出边、字符串/注释内伪调用不出边、同文件自调用不出边、多方法合并去重 + `line` 取首个、Python 项目内类→类、Python 第三方模块不出边、目标类无该方法不出边 |
| `c-3` | **样例仓**：按 4.5 修改 `hud.gd` / `player.gd` | 改完立即用 `build_relation_graph` 验证出现 1 条 calls 边；**提醒用户在系统 PowerShell 手动提交** |
| `c-4` | **前端**：按 4.4 改 `api.ts` + `RelationGraph.vue`，`npm run build` | 顶栏「关系图」→ 出现第 4 个开关；统计含「调用」；绿色实线箭头；点节点仍能跳转 |
| `c-5` | **dev 实测**（浏览器） | 重启 `run_desktop.bat`；确认 player→HUD 调用边渲染、开关联动（关调用边后边消失）、搜索/适应/Esc 正常、**console 零错误**、`/api/fs/relation-graph` 200 |
| `c-6` | **全量测试 + 汇报** | `python -B -m unittest discover -s tests` 应全绿（49 + 新增） |
| 后续 | 打包 & 提交 | **等用户下指令**，然后走 [docmind-frozen-release skill](file:///d:/WorkBuddy/rag-agent/.trae/skills/docmind-frozen-release/SKILL.md)（它会覆盖构建/冒烟/文档/提交全流程） |

**不要在用户没明确要求时打包或提交。**

---

## 6. 验证命令速查

```powershell
# 环境
$env:PATH = "C:\Users\h'h'h\.local\bin\MinGit\cmd;" + $env:PATH
Set-Location 'd:\WorkBuddy\rag-agent'

# 全量测试
.\.venv\Scripts\python.exe -B -m unittest discover -s tests

# 只跑关系图测试
.\.venv\Scripts\python.exe -B -m unittest tests.test_relation_graph -v

# 快速看真实图（dev 实例需在运行）
Invoke-RestMethod 'http://127.0.0.1:8000/api/fs/relation-graph' |
  Select-Object -ExpandProperty edges | Format-Table source, target, kind, label, line

# dev 实例
Start-Process -FilePath '.\run_desktop.bat' -WindowStyle Minimized
# 打包前务必停 :8000（SQLite 锁会让 PyInstaller 失败）
```

---

## 7. 坑与教训（血泪，务必遵守）

1. **SQLite 锁**：源码实例占用 :8000 时打包会失败（COLLECT 先清空旧 dist，表现为 exe 时间戳不变 + web/assets 被清空）。**打包前先停服务**。
2. **commit 信息 BOM**：PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会加 BOM，污染 commit 标题（曾产生 `e825b26`）。**必须**用：
   ```powershell
   [System.IO.File]::WriteAllText($tmp, $text, (New-Object System.Text.UTF8Encoding($false)))
   git commit -F $tmp
   ```
   提交后可用 `git cat-file commit HEAD`（经 `Start-Process -RedirectStandardOutput`）字节复验首个标题字节非 `EF BB BF`。
3. **冻结包状态泄漏**：冒烟后必须删 `dist/DocMind/_internal/.docmind_state.json`，否则把本机 code_root 分发给用户。
4. **前端旧 hash 累积**：Vite `emptyOutDir:false`，每次构建前后手动删 `web/assets/workbench-*`。
5. **Vue 编辑注意**：`RelationGraph.vue` 里**只有 viewport transform 是响应式**，节点/边坐标全部是命令式 `setAttribute`（性能设计）。改动时不要破坏这个约定，否则布局会卡。
6. **BOM 影响解析**：所有读源码都用 `encoding="utf-8-sig"`（`symbols.file_symbols` 已处理），新写的解析代码也要注意。
7. **样例仓/外部仓**：沙箱写不了 `.git`，提交只能用户手动做。
8. **测试基线**：4 个 skip 是「无 git 环境」用例，属正常，不要试图消除。

---

## 8. 文档与记忆位置

| 内容 | 位置 |
|---|---|
| 分发包构建历史（含第十版详情） | [DocMind_BUILD.md](file:///d:/WorkBuddy/rag-agent/DocMind_BUILD.md) |
| 发布流程（7 阶段 runbook） | [.trae/skills/docmind-frozen-release/SKILL.md](file:///d:/WorkBuddy/rag-agent/.trae/skills/docmind-frozen-release/SKILL.md) |
| 分区开发总体设计 | [分区开发设计.md](file:///d:/WorkBuddy/rag-agent/分区开发设计.md) |
| 项目长期记忆（Trae 侧的 project_memory.md） | `C:\Users\h'h'h\.trae-cn\memory\projects\-d-WorkBuddy-rag-agent--p2-77f9a787836ed5f14695\project_memory.md` |
| 过时文档（勿用） | `HANDOFF.md`（旧 T1–T5 体系） |

---

## 9. 给下一个 AI 的第一句话建议

> 先读 `workbench_fs.py` 第 718–1002 行（P1 关系图全部实现）和 `tests/test_relation_graph.py` 的测试写法，
> 然后按本文第 4 节方案实现调用边，从 TODO `c-1` 开始。改完先跑 `python -B -m unittest discover -s tests`，
> 再启动 dev 实例用浏览器验证，**不要在用户没要求时打包或提交**。
