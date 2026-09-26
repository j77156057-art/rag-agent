# DocMind · MCP 自动连接模块 接手 handoff

## 2026-09-27 项目级工具环境锁定

- `ToolInstallManager` 在项目 `.docmind/tool_envs` 内安装后写入 `.docmind/tool-lock.json`，记录 PyPI/npm 来源、精确 spec、隔离目录、启动方式、安装时间和内容 SHA-256/文件统计。
- 安装审计记录同步包含来源和启动方式；哈希计算有文件数和 50MB 上限，避免大型环境阻塞工作流。
- `tests/test_tool_install.py` 已覆盖 lock manifest、来源、启动方式和 fingerprint；定向工具测试 **6 passed**。

## 2026-09-27 并行代理文件冲突保护

- `tools.py` 为每个绝对文件路径增加进程内写锁，`apply_edit` 在最终落盘前重新读取并比对基线；并行代理已修改文件时直接返回“并行修改冲突”，不会覆盖新内容。
- `create_file` 和人工确认后的落盘也在同一文件锁下再次检查目标，避免两个代理同时创建或确认过期修改。
- 新增 `tests/test_file_conflict.py` 覆盖落盘前文件变化的 fail-closed 行为；定向工具/并行测试 **11 passed**。

## 2026-09-27 任务时间线与安全续跑

- `WorkflowState` 新增持久化 `timeline`，最多保留 500 条事件；原有 `events` 继续作为 100 条 SSE 重放窗口，旧状态读取时自动迁移。
- 开发舱改为展示可展开的任务时间线，记录计划、审批、工具/MCP、快照、视觉证据、验收和暂停/恢复事件；失败或阻塞任务可从该任务继续，复用已有幂等重试和重试上限。
- 新增 `tests/test_workflow_timeline.py` 验证长时间线、SSE 窗口和进程重启持久化；完整回归：**1638 passed / 6 skipped / 60 subtests passed**。前端构建通过。

## 2026-09-27 工作流质量评估接入开发舱

- 开发舱读取既有 `/api/agent/workflow/{id}/evaluation` 评估接口，在工作流完成、失败或暂停后展示评分、任务/步骤/重规划次数、失败/阻塞/不确定副作用数量及逐项检查结果。
- 评估请求属于增强信息；接口暂时不可用时不影响工作流状态、恢复方案和预览展示。
- 前端 typecheck 与 `npm run build` 通过，仅保留既有非 module 脚本和大 chunk 警告。

## 2026-09-27 失败恢复方案接入自主开发舱

- 工作流失败、子代理重试失败或进程恢复失败时，会从持久化结果生成脱敏的 `recovery` 计划，只暴露任务 ID、数量和错误类别，不暴露工具参数、文件内容或模型输出。
- 计划最多提供三类可审核动作：先确认不确定副作用、按任务重试失败/阻塞任务、恢复执行前项目快照；没有安全动作时引导查看失败证据并重新规划。
- `AutonomousCockpit.vue` 右侧新增“失败后的下一步”卡片，重试按顺序执行并保留确认框，快照恢复复用既有回滚接口，查看证据复用对话审核卡片。
- 新增 `tests/test_recovery_plan.py` 覆盖失败任务、不确定副作用、快照和无证据场景；完整回归：**1637 passed / 6 skipped / 60 subtests passed**。前端 typecheck 与 `npm run build` 通过，仅保留既有非 module 脚本和大 chunk 警告。

## 2026-09-27 工具权限租约与自主开发舱能力范围

- 工作流新增持久化 `capability_lease`：执行阶段默认临时授予完整的 `read_local`、`read_external`、`write_local`、`write_external`、`exec`、`network`、`admin` 能力，默认 30 分钟到期（可用 `DOCMIND_CAPABILITY_LEASE_S` 调整，范围 5 分钟至 2 小时）。
- `Agent` 在串行和并行工具执行边界检查租约；过期或不包含工具能力时只回填权限租约观察，不执行工具。子代理继承父 Agent 租约，现有用户审批、白名单、幂等和副作用保护仍是最终边界。
- 工作流终态（完成、失败、中断）自动标记租约为 `released`；进程重启后的工作流回调按工作流 ID 重新读取租约。开发舱右侧显示能力、状态和到期时间。
- 新增 `tests/test_capability_lease.py`；完整回归：**1632 passed / 6 skipped / 60 subtests passed**。前端 `npm run build` 通过，仅保留既有非 module 脚本和大 chunk 警告。

## 2026-09-26 非流式取消与预览重启持久化补充

- `llm.py` 的云端 OpenAI 兼容调用在收到 `cancel_event` 时，会临时使用流式响应并在客户端聚合为原有字符串；取消会关闭 SDK 流、终止当前回合，不再受非流式 `resp` 阻塞限制。未提供取消事件的普通非流式调用保持原路径。
- Ollama 工作流子代理的非流式调用同样在有取消事件时使用可关闭的 NDJSON 流聚合；GPU lease 覆盖整个生成和收尾，取消后由流适配器释放。
- 新增 `tests/test_llm_resilience.py` 的云端非流式正常返回与取消回归；`tests/test_game_workflow.py` 验证包含截图 artifact 的工作流保存后，经新 `GameWorkflowManager` 实例恢复仍可读取预览证据。定向测试：**84 passed**；`py_compile` 通过。
- 之前本节中“非流式云端请求仍依赖 SDK timeout”的描述属于更新前状态，以本补充为准。
## 2026-09-26 生产视觉验收与云端取消补充

- 正式工作流已接入 `agent_runtime/visual_acceptance.py` 与 `tools.preview_project`：网页项目的 tester 子代理可以启动临时 Edge、捕获真实截图、把截图送入视觉通道，并通过 `artifacts` 写入工作流 `preview` 证据。开发舱现有通用预览会直接显示该截图。
- `self_verify` 支持显式 `scope: visual`，会调用同一浏览器适配器并返回截图路径与检查结果；跨域、原生窗口、视频和非网页项目仍需领域专用适配器。
- `StreamChat` 与 `_OllamaStream` 都监听 `cancel_event`，停止/断连时关闭底层响应；Ollama 和 OpenAI 兼容云端流均覆盖。非流式云端请求仍依赖 SDK timeout，不能宣称任意时刻强制中断。
- 定向回归：87 passed / 4 subtests；正式视觉适配器已在沙箱外用隔离网页实际启动 Edge 并捕获截图。真实 `qwen3.6:35b-a3b-agent256k` 生产链路报告为 `.docmind/docmind-production-visual-9h2yej50/evidence/report.json`：文件确有修改、模型调用了生产 `preview_project`、截图 artifact 已登记、`verified=true`、`passed=true`。前端 typecheck 通过。

> 生成时间：2026-09-25 16:55 GMT+8（接手状态与验证结果于 2026-09-25 更新）
> 冻结基线 commit：**`80b8a90`**（= origin/main，验证基线）
> 当前 HEAD：**`ecea19a`**（N3 已合入，未推送）
> 适用范围：DocMind 后端 `mcp_*.py` + 前端 `SettingsView.vue` / `api.ts` 的 MCP 自动连接（auto-connect）链路
> 形态：单人桌面 GUI（Python FastAPI + Vue3 + PyInstaller/pywebview），非 SaaS

---

## 最新接手点 · 2026-09-26 真实模型视觉修改验收

### 继续推进 · 通用网页截图与自动复验已接入开发舱

- 新增 `frontend/src/workbench/previewCapture.ts`，同源 iframe 现在可以截取普通 DOM、文字、图片和 canvas 的完整视口，不再要求页面必须有 canvas。截图前检查跨域、未加载图片、嵌套页面、视频和污染 canvas；无法完整证明时返回明确原因，不向模型发送假截图。
- 视觉反馈发送前仍保存修改前截图；AI 回复结束后自动刷新同一预览 iframe，等待加载完成，再保存修改后截图。自动截图失败会把反馈留在「等待检查效果」，显示失败原因，用户可以手动重试或记录；自动截图不会替用户点击「效果满意」。
- 自动复验绑定发起反馈的工作流和项目，切换项目或画面后不会把另一项目的截图写入旧反馈；原始截图和复验截图分开保存。
- `html2canvas` 已加入前端依赖，生产构建通过。`tests/ui/cockpit_feedback_smoke.py` 现有隔离浏览器检查扩展到 **28 项通过**：普通网页+画布同图、普通 DOM 无 canvas、滚动视口、刷新后的新画面、截图失败、跨域/视频拒绝和错误状态均覆盖；项目绑定也有检查。
- 慢响应体验已补强：聊天回合记录最后事件时间，连续15秒没有事件时显示模型/工具可能仍在处理的提示；服务端进入 Agent 前发送“已进入模型处理”通知；用户主动停止前保存可继续现场，恢复入口复用既有“继续上次任务”。这只改善反馈和恢复，不改变模型/GPU参数。
- `/api/chat` 已把同步 Agent 消费放入后台队列，SSE 每15秒发送一次存活提示，即使 Ollama 在首个 token 前长时间预填充，连接也不会看起来像断开。后台线程只传递已产生的 Agent 事件；客户端断开会设置停止标记，已产生的工具副作用仍按现有审计和恢复规则处理。
- 相关后端回归：`tests/test_visual_feedback.py tests/test_preview_adapters.py tests/test_runtime_contracts.py tests/test_workflow_chat_stream.py`：**48 passed，4 subtests passed**；另有聊天 SSE 心跳专测通过。前端 `npm run build` 通过，仅保留原有非 module 脚本和大 chunk 提示。

范围限制：截图依赖同源 DOM。跨域页面、嵌套页面、视频和被污染的 canvas 仍需要领域专用适配器；系统会回退为文字反馈并明确告诉模型没有收到画面。`html2canvas` 只负责可见网页效果，不验证源码、功能或模型声明；最终验收仍由用户完成。

本轮新增真实模型验收脚本 `tests/manual/cockpit_live_validation.py` 和普通 DOM 网页示例 `tests/fixtures/cockpit-live/index.html`。使用本机 Ollama 已加载的 `qwen3.6:35b-a3b-agent256k`，运行真实 `Agent(tool_mode="native")` 和生产 `read_file` / `apply_edit`，在独立示例目录实际修改文件。模型/GPU 全局配置未改动，不访问用户真实项目。

- 写后自动校验现在优先使用当前 Agent 实例注册的 `self_verify`；未注册时仍使用原有校验器。项目可提供真正适用的校验，失败仍阻止模型宣称完成。
- 验收适配器通过隔离 Edge 返回真实截图，并检查按钮位置、尺寸、字号、文字对比度和两次真实点击行为。随机六位编号只出现在截图里，用于验证模型确实接收视觉输入。
- 增加源码检查，拦截已发现的 `Observation: [apply_edit result]` 工具结果占位文字、`<reset_dir_prompt>` 对话标记，并检查状态文字样式，避免浏览器静默忽略非法 CSS 或陌生 HTML 标签后产生假通过。这个约束只属于示例验收，生产文件工具没有全局禁止或删除用户内容。
- `tests/test_runtime_contracts.py tests/test_self_verify.py tests/test_tool_vision_channel.py tests/test_vision_workflow_e2e.py`：**45 passed / 4 subtests passed**；生产修改与手动脚本编译通过。
- 复现：`.venv\Scripts\python.exe tests/manual/cockpit_live_validation.py --model qwen3.6:35b-a3b-agent256k`。每次保存在 `.docmind/cockpit-validation/<时间与随机后缀>/`，含示例源码、前后截图、工具事件和 `evidence/report.json`；隔离状态不会覆盖用户设置。当前环境浏览器需沙箱外运行。
- 支持 `--resume-report <旧报告>` 从失败的第二轮继续：只允许独立验收目录里的文件，复制失败源码到新的运行目录，保留旧证据和已通过的第一轮。验收器返回具体失败值，例如实际对比度及4.5阈值。示例 Agent 允许24次工具调用，以容纳修复与预览；此设置不改变产品的连续执行策略。

真实现场结果（以报告链为准，不是一次无中断运行）：

- `20260926-172226-8231/evidence/report.json` 第一轮 **44.13秒通过**：按钮由越界150×24、字号11、对比度1.13，改为居中200×44、字号16、蓝底白字、对比度5.17。模型正确读出仅存在于截图中的编号 **139607**。第二轮未达标且后续响应超过十分钟，已停止；`interruption.json` 单独记录中断，原报告保留为未整体通过。
- `20260926-173701-cf86/evidence/report.json` 第一次续修改善对比度，但残留对话标记、未实际调用预览，**不通过**。这轮暴露了源码质量与默认工具预算不足的问题。
- `20260926-173951-a337/evidence/report.json` 最终续修 **100.23秒通过 / passed=true**，链接前两份报告。模型自己删除残留标记，真实调用 `preview_project({})` 取得 `preview-3.png`。最终为深绿色按钮「再次运行」、居中200×44、字号16、对比度 **8.29**；两次浏览器点击显示「运行次数：1」「运行次数：2」，源码、样式和所有浏览器检查通过。验收脚本现在按实际成功执行的预览计数，缺参拦截事件不算已预览。
- 最终源码：`.docmind/cockpit-validation/20260926-173951-a337/project/index.html`；真实前后拼图：同运行 `evidence/comparison.png`。所有失败证据均保留，无人工替模型修复示例源码。
- 较早的 `20260926-171555-586c` 报告只覆盖旧版功能检查，后续发现样式污染；它不是完整源码质量通过的证据。

范围（本节记录的是接入前的历史状态）：这是**真实 Agent + 文件工具 + 浏览器验收适配器**的现场验证。当时适配器尚未注册到正式开发舱，普通 DOM 截图也尚未接入正式预览服务。后续第 14 节已完成通用网页截图、自动复验和生产 `preview_project` 接入；当前仍需在实际项目上联验正式 UI、真实 MCP、联网下载和高风险审批，非网页领域仍需专用适配器。上一节所述“真实模型修改待验”以本节后续更新为准；其他产品限制仍有效。

## 上轮接手点 · 2026-09-26 自主开发舱视觉反馈闭环

当前重点已从 MCP 搜索扩展为通用自主开发舱。用户要求能力覆盖任意领域，EDA、游戏只是例子；只服务当前项目，可连续执行，用户审核计划、需要确认的操作与最终效果。既有 MCP 连接与能力审批仍必须由用户完成。用户已暂停 Ollama/GPU 性能优化，后续不要重新改动这部分配置。

本轮实现（未提交）：

- 预览可拖动调整宽高、刷新、全屏、框选区域并打开反馈浮窗；修复 `/play/...` 等同源相对预览地址。
- 工作流保存最多 20 条视觉反馈，状态区分待发送、AI 处理中、等待检查效果、失败、效果已确认和需要继续修改。
- 修改前/复验 PNG 截图保存在 `.docmind/workflows/visual_feedback/{workflow_id}/{feedback_id}/`，JSON 仅存尺寸、大小、时间等信息。原始截图不可覆盖，复验截图允许更新；实际图片内容和路径经过校验，读写接口检查当前请求项目。
- 对话组件明确确认接收反馈；忙碌或等待审批时保留反馈并说明原因，用户可重新发送。重试读取已保存的原始截图，提醒模型先核对当前状态以免重复副作用。画面反馈不清空聊天草稿或用户附件。
- 反馈状态按顺序保存，避免慢响应覆盖完成状态；保存请求绑定发起反馈的项目。查看/评价历史反馈不会重新启动历史工作流的项目互斥。
- 用户刷新预览后选择「记录当前效果」，并排查看修改前/复验画面，再选择「效果满意」或「继续修改」。AI 回复结束只表示等待用户检查；反馈评价不替代整个工作流的最终验收。

验证证据：

- `.venv\Scripts\python.exe -m pytest tests/test_visual_feedback.py tests/test_game_workflow.py tests/test_preview_adapters.py tests/test_cockpit_policy.py -q`：**77 passed**，仅现有 Starlette 测试客户端弃用提示。
- 前端 `npm run build`（含类型检查）：通过；仅既有非 module session 脚本和大 chunk 提示。
- `tests/ui/cockpit_feedback_smoke.py`：隔离 Edge 中实际挂载开发舱和对话组件，**15 项交互检查通过**，包括尺寸调整、框选、接收图片、草稿保留、忙碌反馈、重试、乱序响应、截图展示、组件重新打开和页面无运行异常。使用受控模型/存储服务，不能等同于真实模型修改项目已验收。检查截图位于 `.docmind/cockpit-feedback-smoke.png`（忽略文件）。
- UI 复现：先在 `frontend` 启动 `npm run dev -- --host 127.0.0.1 --port 5179 --strictPort`，再于仓库根运行 `.venv\Scripts\python.exe tests/ui/cockpit_feedback_smoke.py`。测试使用临时浏览器配置和临时测试页，不访问真实项目服务；当前受限环境中浏览器须在沙箱外运行。

实际限制和下一步：

1. 自动截图支持同源 iframe 中可读取的普通 DOM、文字、图片和 canvas；跨域画面、嵌套页面、视频、原生软件窗口以及被污染的 canvas 不能由通用链路直接截图。无法完整截图时明确显示文字反馈；WebGL 缓冲内容是否可读取由项目渲染方式决定。
2. 真实模型结合截图修改通用示例项目的生产链路已有现场通过证据；仍需在正式桌面 UI 和实际用户项目上复验，尤其是不支持原生视觉的模型所使用的视觉辅助链路。受控 UI 测试不能替代这类验收。
3. 接下来完善通用预览服务的截图适配能力、失败/中断后的恢复与不确定副作用处理；沿用设计文档 Phase 4，保留现有审批门。
4. 大量既有 MCP/工作流改动仍未提交；不要 reset、clean 或擅自提交/推送。保留后来出现的 `.docmind_model_context.json`。

原 MCP 交接与历史测试记录保留如下，其结果属于对应历史状态。

## 0. 一句话背景

「设置 → MCP → 搜索连接方式」链路：让大模型调用工具自己查文档、自己连，遇需注册的弹窗说明「只需帮忙注册」。N3 已在 `ecea19a` 完成；当前工作树中的可选密钥语义、领域目录候选、模块拆分、Agent 自然语言调度和用户审批门已通过测试，尚未提交。2026-09-26 已用真实 EasyEDA MCP 完成 Registry 发现、握手、19 项工具发现、临时项目能力审批及只读状态调用；编辑器扩展未安装，实际图纸操作待验。后端搜索接口及 Agent 的 `dev_mcp_search` 工具均已在空临时项目中真实查到 Registry 候选，搜索不写连接配置；受控模型测试已证明调度链路，真实模型自然语言会话仍待可用模型验收。MCP 连接器安装和能力路由的审批现在只能由设置页用户操作写入，Agent 的 `dev_approve` 对这两类操作会拒绝。验证基线、源码快照和真机结果记录在 `docs/mcp-autoconnect/verification-2026-09-25.md`。工作树还包含自主开发舱设计文档及一个本地模型上下文文件，均为既有未跟踪项。

---

## 1. 当前状态（接手必核）

```bash
git log --oneline -6
git status --short
```

- **HEAD = `ecea19a`**；`origin/main` 仍为 `80b8a90`。5 个历史 junk 文件已于 2026-09-25 删除（`.docmind_model_context.json` / `.docmind_state.json.bak-qa-cleanup` / `api.py.bak-pre-autoconnect` / `frontend/tsc-check.txt` / `mcp_client.py.bak-pre-autoconnect`）。当前新增的 `.docmind_model_context.json` 是后来出现的未跟踪文件，保留原样。
- **本轮已交付 6 笔提交（全在 main）**：

| commit | 内容 |
|---|---|
| `df49458` | 适配器误路由修复：`provenance`（来源仓库）不再参与 provider 匹配；未收录 provider 的 `register_url` 置空（堵误跳 GitHub PAT 页） |
| `2d098a9` | Registry 检索瞬时超时重试（attempts=2）+ 网络错误中文友好化（不再裸透 `TimeoutError`） |
| `d2fce2e` | 收录 smithery 适配器（`register_url=https://smithery.ai/account/api-keys`）+ 未收录分支文案增强 + 回归测试改写 |
| `29f03b9` | smithery L2 文案点明「需先登录」（未登录点深链回落首页） |
| `b2f04a8` | 候选卡片三项修复：标题显服务名 / 信任徽章分 official·community·unknown / 试连 404 中文友好化 |
| `80b8a90` | `McpProvenance` 类型放宽（`server_name`/`namespace`/`registry`/`curated` 可选），修 `vue-tsc` TS2339 |

- **历史测试基线**：`tests/test_mcp_autoconnect.py` **84 passed**；全量 `tests/` 基线 **232 passed / 4 skipped**（来自旧交接记录）。当前工作树全量测试 **1582 passed / 5 skipped / 60 subtests passed**（临时使用 `LLM_PROVIDER=mock`、`EMBEDDING_PROVIDER=local`，避免本机 Ollama 不可达）。

### 三条安全不变量（改任何 MCP 代码都不得破）
1. `mcp_client.py:405` 是 `subprocess.Popen([command, *args])`：写进 `.docmind_mcp.json` 的 `command`/`args` 会被**真实执行**。凡「让模型自动填连接参数」的设计 = 开放本机任意代码执行入口 -> R8 自动填参闸门只允许**外部副作用自动、本地副作用人工确认**。
2. **df49458 不变量**：未收录 provider 的 `register_url` **恒为空**，绝不回退 `provenance.url`（那是源码仓库，会跳错站）。
3. `trust` 字段仍供 R8 闸门使用；`trust_tier` 仅 UI 分档，勿混用。

---

## 2. 接手 backlog 状态

### ✅ N2 · 验证期仓库移动靶冻结 【已记录】
- `docs/mcp-autoconnect/verification-2026-09-25.md` 固定了对比基线 `80b8a90`、本地 HEAD `ecea19a`、未提交源码的 SHA-256、测试结果及 2026-09-26 真实 EasyEDA MCP 的现场探测结果。真实图纸读写与需账号注册流程仍待具备对应软件和账号的环境验收。

### ✅ N3 · `acNeedRegister` 死标志清理 【已完成 · 2026-09-25 · commit `ecea19a`】
- **落点**：`frontend/src/workbench/components/SettingsView.vue`
  - `:164` `const acNeedRegister = ref(false)`（声明）
  - `:596`、`:721` 两处 `acNeedRegister.value = false`（置位）
  - `:1039` `<button v-if="c.config.command_unresolved || acNeedRegister || acNeedsSecret(c)" ...>`（仅此引用，且恒 false）
- **根因**：L2 改造前的遗留标志，从未被置为 `true`。
- **动作**：删声明 + 两处置位行 + `:1039` 去掉 `|| acNeedRegister`。纯删除，无行为变化。
- **验证**：`vue-tsc --noEmit` 退出码 0；全仓 grep `acNeedRegister` 代码引用 = 0。接手 AI **跳过此项**。

### ✅ (b) · Registry 可选密钥语义 【已实现，验证完成】
- Registry stdio 环境变量与 remote headers 输出 `secret_specs: [{name, required}]`，候选视图传给前端 `secrets`；旧候选没有元数据时仍按 `@secret:` 视为必填，保持兼容。
- 注册表单按必填/可选显示；只有必填项阻止提交。只有可选密钥时不启动自动注册流程，用户可主动打开表单填写；全部留空直接关闭，不发送空提交。后端解析缺失的可选密钥为空串，HTTP 空请求头会被移除。
- **验证**：最新 MCP 相关定向测试 **136 passed**；接通 Agent 搜索工具后的全量测试（临时使用 mock/local provider，避免本机 Ollama 不可达造成两项无关早退）**1579 passed, 5 skipped**。问答首页静态工作台链接已补齐，桌面入口测试通过。前端 `npm run build`（含类型检查）通过，仍有既存非 module 脚本与大 chunk 提示。

### ✅ `headers_sent` 字段 【已核实，无需处理】
- 全仓 `rg` 搜索（排除依赖目录）无匹配；当前源码没有该响应字段或对应实现，故从 backlog 移除。

### ✅ 模块抽取 · `mcp_autoconnect.py` 【已完成】
- 主模块从本轮开始时的 1559 行降到 997 行。候选构造和诊断图迁至 `mcp_candidates.py`，服务适配器表与匹配规则迁至 `mcp_providers.py`，R1–R9 校验迁至 `mcp_validation.py`，Registry 接入边界迁至 `mcp_registry_bridge.py`。
- 浏览器会话与注册状态机仍留在主模块；原有导入入口兼容，相关测试及全量测试通过。

### ✅ Agent MCP 调度与用户审批门 【已完成 · 2026-09-26 · 尚未提交】
- `agent_runtime/context_router.py` 在 MCP 连接意图出现时开放 `developer` 工具组；`dev_mcp_search` 会继承会话联网开关并查询真实 MCP Registry。
- `tests/test_mcp_self_assembly_tools.py` 的受控模型测试证明自然语言请求会收到并调用 `dev_mcp_search`，且搜索不会写入 `.docmind_mcp.json`。这验证调度链路，不等同于真实模型已验收。
- `tools.dev_approve` 不再允许 Agent 写入 `mcp_server` / `mcp_capability` 审批；设置页保存、移除连接器及批准能力时记录 `workbench-user` 审批，绑定具体连接参数或 key，用户确认后 Agent 重试才会放行。
- 全量测试最新结果为 `1582 passed / 5 skipped / 60 subtests passed`；前端 `npm run build`（含 `vue-tsc`）通过，只有既有脚本类型和大 chunk 提示。

---

## 3. 接手环境坑（必读，避免重踩）

1. **真实代码位置**：`D:\WorkBuddy\rag-agent`。沙箱 worktree（`C:\Users\h'h'h\WorkBuddy\Worktrees\rag-agent\main-b8047d2e`）是镜像，**改真实仓库**，不要写 worktree。
2. **沙箱 Bash PATH shim 坏**：须手动 `export PATH=".../PortableGit/.../usr/bin:.../bin:$PATH"`；且 python/node 用**绝对全路径**（单引号用户名会破坏 Git Bash 引号，无 `ls`/`tail`/`cat`/`wc`）。
3. **沙箱 managed python 缺依赖**：缺 `openai`/`chromadb` -> 全量 `pytest tests/` 冒大量 collection ERROR（`ImportError: from openai import OpenAI`），**与本仓库改动无关**。自测须用项目 venv：`D:\WorkBuddy\rag-agent\.venv\Scripts\python.exe`（CI 同理）。
4. **提交/推送**：沙箱 MITM 代理破坏 `api.github.com` 鉴权 -> 提交须用户本机 PowerShell `git push origin main`。
5. **前端构建产物**：`web/workbench.html` 与 `web/assets` 被忽略；`web/index.html` 是已跟踪文件。本轮修改 `frontend/index.html` 后已运行 `npm run build`，因此 `web/index.html` 也更新。不要把被忽略的资源目录误加入 Git。

---

## 4. 建议接手节奏

1. 已完成本交接列出的代码与验证记录工作；本地代码改动尚未提交。
2. 真实第三方 EasyEDA MCP 已完成发现、试连、工具发现、临时项目能力审批及只读状态调用，详见验证记录。当前机器未装 EasyEDA Pro 扩展，实际设计图纸操作需在有编辑器的机器验收；需要账号的注册流程仍需真实账号验收。

---

## 5. 关键文件速查

| 文件 | 职责 |
|---|---|
| `mcp_autoconnect.py` | auto-connect 主模块（pipeline 编排、候选视图 `_candidate_view`、provider 适配器表 `PROVIDER_ADAPTERS`、误路由修复 `select_provider_adapter`、L2 桩 `browser_register`） |
| `mcp_registry.py` | 官方 MCP Registry 检索（`search_registry` 有界重试、`parse_registry_response`、`server_to_candidates`、`rank_candidates`、`RegistryCache`）；`variables[].isRequired` 在 `:180` |
| `mcp_server_index.py` | 离线精选索引（`curated_entry_to_config` 补 `server_name`） |
| `mcp_client.py` | `_http_post` / `_http_status_message`（:600 附近，404 提示已加「服务可能已下线」）/`probe_server` |
| `api.py` | `/api/mcp/autoconnect/*` 端点（`:3368` probe 端点捕获 404 回中文友好文案） |
| `frontend/src/workbench/api.ts` | `McpAutoConnectCandidate` / `McpProvenance` / `AcSource` 类型 |
| `frontend/src/workbench/components/SettingsView.vue` | 候选卡 UI：`acCardTitle`(标题取 server_name)、`acTrustTag`(徽章分档)、`acSecretProviders`(:373)、`regFields`(:644) |

> 更细的真机复现证据、匹配逻辑、commit diff 见 `.workbuddy/memory/2026-09-25.md`「接手 handoff」节。
