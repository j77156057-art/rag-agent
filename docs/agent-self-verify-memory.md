# DocMind Agent 改造方案：写后自验证 + 跨会话经验记忆

> 适用对象：Agent 内核维护者、架构评审。
> 本文档是**设计提案**（Phase 0）。确认实施后再把任务项搬进 `HANDOFF.md §5`。
> 定位：深化 DocMind 既有「代码 + 资产代码化协作 + AI 研发脚手架」切片，**不扩范围、不越界**（与 `docs/capability-boundary.md` 一致）。

---

## 1. 目标与定位

把 DocMind 从「会写代码的助手」拉到「会交付可运行结果的 agent」。只补两块工程可控的能力：

1. **写后自验证闭环**：Agent 每次写操作后，自动跑对应校验（单测 / 类型检查 / 域自检），失败则折回 ReAct 循环自修。
2. **跨会话经验记忆**：把「操作 → 决策 → 结果」沉淀成可检索的 episodic 记忆，让 Agent 从过往成败里学习，而非每次从零开始。

**为什么现在做、且符合宪章**：上一轮对照里，这两条是 DocMind 离「真正 agent」最近、且最该补的缺口；二者都在本地单人、代码协作层内，不触碰多用户/DB/鉴权、不引入美术/音频、不碰发布运营。

---

## 2. 总体设计原则

复用 DocMind 已有的三条护栏取向（见 README「Harness 能力」）：

- **护栏复用**：自验证与经验召回都走既有工具 / 守卫路径，不新开执行分支。
- **有界**：经验库有容量上限 + 置信衰减；自验证受步数预算与失败上限约束，杜绝自修死循环。
- **不越权**：经验是**建议性 RAG 上下文**，绝不自动变成规则；证据优先原则始终压过陈旧经验。

---

## 3. 能力一：写后自验证闭环

### 3.1 设计

在 `agent.py` 的 ReAct 回合中，当一个回合产生了**写操作**（`apply_edit` / `create_file` / `generate_test_scene` 等），回合收尾前触发一次验证：

1. **判定验证范围**：依据 `git diff` 改动文件 + 文件 glob，选校验策略：
   - 后端 Python 改动 → 跑受影响单测（`unittest discover` 或 `pytest`）；改动面大时跑全量 `discover`（耗时 53s~236s，**后台跑**，不阻塞回合）。
   - 前端改动 → `npm run typecheck`（`vue-tsc --noEmit`）。**严禁 `npm run build`**：铁律规定对方改前端时 build 会重建 `web/assets` 打断并发写入者；深度校验（真构建）仅作可选开关，且必须确认无并发写入者才启用。
   - 场景画布 / 引擎模块改动 → 调 `verify_scene_canvas.py` / `verify_engine_embed.py` 域自检（后者需真 Godot，按环境能力降级为 skip）。
2. **执行校验**：新增工具 `self_verify(scope)` 封装上述策略，内部用 `.venv\Scripts\python.exe -B -m unittest ...` 或 `typecheck`（托管 python 缺 webview/chromadb，须用 `.venv`）。
3. **折叠失败回循环**：校验结果与失败项作为 observation 注入 ReAct；复用既有 `_FAILURE_MARKERS`（`agent.py:257`）+ `_TOOL_FAIL_LIMIT=3` / `_TOTAL_FAIL_LIMIT=5`（`agent.py:247/249`）与 `streak>=_TOOL_FAIL_LIMIT or fail_total>=_TOTAL_FAIL_LIMIT` 的强制收尾（`agent.py:1444`），自然封顶自修次数。
4. **区分「声明完成」与「验证完成」**：在 `agent_trace.py` 的回合记录里加 `verified: bool` 字段；前端 `/trace` 页可看到哪些回合是验证通过的。

### 3.2 接口（新增）

- 工具 `self_verify(arg)`：`arg = {"scope": "auto|backend|frontend|scene|engine"}`，返回 `{ok, passed, failed, log_path}`。
- `agent.py` 回合收尾守卫：写回合 → 注入「需验证」observation → Agent 调 `self_verify` → 结果折回。

### 3.3 风险与护栏

- **并发写入者**：前端校验只用 `typecheck`，绝不在对方编辑期 build。
- **校验范围误判**：先按文件 glob 粗选，跑全量 `discover` 时后台执行并 polling（`TaskOutput`），超时改后台不卡回合。
- **自修死循环**：完全复用既有 fail-limit 守卫；超过上限直接收尾并标注 `verified:false`，交人工。

---

## 4. 能力二：跨会话经验记忆

### 4.1 设计

一个本地、单用户、向量化的 episodic 记忆，沉淀「这次怎么干的、成了没、为什么」。

1. **存储**：复用 `vectorstore.py` 的 `get_collection(name=)` 单集合抽象，新建集合 `docmind_experience`（与文档 / 代码集合并列，不污染）。元数据条目：
   - `action_summary`（做了什么，自然语言）、`decision`（关键选择）、`outcome`（`success`/`fail-then-fixed`/`repeated-fail`）、`lesson`（从失败提炼的教训，可为空）、`project_id`、`ts`。
2. **脱敏**：写入前用 `flows._classify_error`（`flows.py:688`）同类思路脱敏——**只存元数据和教训文本，绝不存代码片段 / 凭据 / 正文**（与 trace 隐私原则一致）。
3. **召回**：
   - 新增工具 `recall_experience(query)`：Agent 可主动查「这类改动我以前踩过什么坑」。
   - 回合开始时**自动**按 `project_id` 检索 top-k 相似经验，作为**建议性**上下文注入（不强制覆盖证据）。
4. **记录**：回合结束，若 `outcome ∈ {fail-then-fixed, repeated-fail}`，用 LLM（受护栏与 token 预算约束）提炼一条 `lesson` 并 upsert；成功但「新颖」的也可记，失败优先以控制噪声。
5. **有界与防漂移**：集合容量上限 + 去重 + `valid_until` 置信衰减；召回结果显式标注置信度与时效，低置信一律不参与决策；证据优先原则兜底。

### 4.2 接口（新增）

- `experience.py`：`record_episode(...)`、`recall_similar(project_id, query, k=5)`、`_extract_lesson(...)`（复用 LLM 弹性 / 成本熔断）。
- 工具 `recall_experience(query)`。
- 回合收尾钩子（挂在 `agent.py` / `agent_trace.py` 上）调用 `record_episode`。

### 4.3 风险与护栏

- **陈旧经验压过现证**：经验永远是 advisory；当前证据（检索到的真实代码 / 校验结果）优先级最高。
- **成本**：只对「有趣」回合（失败 / 新颖成功）嵌入，避免全量噪声与开销。
- **隐私**：复用 `_classify_error` 脱敏，写入前二次校验无 secret / 代码体。

---

## 5. 与现有底座的挂载点（具体）

| 挂载点 | 文件:行 / 符号 | 用途 |
|---|---|---|
| 失败护栏常量 | `agent.py:247/249/257/477` `_TOOL_FAIL_LIMIT` / `_TOTAL_FAIL_LIMIT` / `_FAILURE_MARKERS` / `_is_failure` | 自验证自修封顶 |
| 强制收尾 | `agent.py:1444` streak/fail_total 判定 | 防止自修死循环 |
| 写工具 | `tools.py:2140` `apply_edit` / `:2251` `create_file` / `:1192` `python_exec` | 自验证触发与执行 |
| 向量库 | `vectorstore.py:33` `get_collection(name=)` | 新建 `docmind_experience` 集合 |
| 脱敏 | `flows.py:688` `_classify_error` | 经验条目脱敏 |
| 回合记录 | `agent_trace.py` JSONL 逐轮 | 加 `verified` 字段 + 收尾钩子 |

---

## 6. 分阶段路线图

- **Phase 1（最低风险）**：`self_verify` 工具 + 后端单测 / 类型检查收尾门。复用 `python_exec` 与 fail-limit 守卫。
- **Phase 2**：前端 `typecheck` 校验（严守 build 铁律）+ 场景/引擎域自检，按改动类型自动选策略。
- **Phase 3**：`experience.py` + `recall_experience` 工具 + 回合收尾记录，按 `project_id` 隔离。
- **Phase 4**：评测门扩展——golden 题加「改出 break → 自修通过」用例 + 反陈旧经验用例；接入冻结发布流程。

**验收门槛（每阶段）**：新增单测全绿；完整 `discover`（当前 1065）不回退；前端 `npm run typecheck` 干净；自验证不得引入写工具的新执行路径（护栏复用）。

**实施铁律**：动手前 `git status`；只 `git add` 明确路径、禁 `git add -A`；对方改前端时不跑 build；提交前 `git diff --stat` 核实无 stat-dirty 误判。

---

## 7. 边界声明

本方案**仅深化 Agent 内核**，与 `docs/capability-boundary.md` 一致：不涉及多用户 / DB / 鉴权、不补美术/音频创作、不碰资产管理(Perforce) / 发布运营 / 联机后端。经验记忆与自验证都是「代码协作层」内的能力增强，不是平台化扩张。
