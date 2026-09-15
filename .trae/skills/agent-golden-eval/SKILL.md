---
name: "agent-golden-eval"
description: "Golden-question SSE evaluation for the local DocMind agent: run batches, score answers against source truth, compare tuning rounds. Invoke when baseline-testing agent quality on a real codebase."
---

# Agent 黄金题评测与微调闭环

对本地 DocMind Agent（FastAPI + Ollama）在**真实工程代码库**上做端到端评测：采集基线 → 逐题核对源码事实 → 区分框架缺陷与模型方差 → 修复 → 复测对比。

## 何时使用

- 用户要求用一批真实工程问题评测/基线化代码问答 Agent 的效果
- Agent 框架（agent.py/tools.py）改动后需要真实模型回归
- 需要对比"微调前后 / 不同模型 / 不同轮次"的答题质量

## 环境约定（本机 Windows / PowerShell）

- venv 解释器：`.\.venv\Scripts\python.exe`（工程根 `d:\WorkBuddy\rag-agent`）
- 所有 shell 调用必须 `dangerouslyDisableSandbox: true`
- 全量单测需把 MinGit 加 PATH：`$env:Path = "C:\Users\h'h'h\.local\bin\MinGit\cmd;" + $env:Path`
- dev 服务：`$env:DOCMIND_SERVER_ONLY="1"; .\.venv\Scripts\python.exe desktop.py`（后台运行，:8000，健康检查 `GET /api/health`）
- **改了 agent.py/tools.py 必须停掉并重启服务**——进程启动时加载模块，热跑的旧进程不会拾取改动
- 切换 Ollama 模型前确认 `GET /api/ps` 已热载；**不要同时开 ComfyUI**（抢 CUDA 会崩 ollama serve）
- PowerShell 不支持 bash heredoc：git 多行提交信息写到临时文件用 `git commit -F <file>`，别用 `<<'EOF'`

## 跑一批黄金题

题库为 JSON 文件（UTF-8），数组形式 `[{"id": "Q1", "question": "..."}, ...]`，参见 `assets/questions.example.json`。

```powershell
$env:GOLDEN_QUESTIONS = "D:\eval\questions_music.json"
$env:GOLDEN_OUT       = "D:\eval\results_35b_baseline.jsonl"
$env:GOLDEN_MODEL     = "qwen3.6:35b-a3b"   # 可选，默认此值
$env:GOLDEN_QIDS      = "Q2,Q5"             # 可选：只跑部分题（复测用）
.\.venv\Scripts\python.exe ".trae\skills\agent-golden-eval\assets\run_golden.py"
```

Runner 行为（SSE 真实请求，不用 mock）：

- 每题用独立 `session_id`（`golden-<GOLDEN_RUN_TAG>-<qid>`）走服务端**会话隔离**，天然互不串台 —— 旧版"先 POST `/api/config` 重设模型以清空 `agent.history`"的 hack **已移除**，不必也不应再依赖它
- POST `/api/chat` 是 **multipart/form-data**，字段 `question` 与 `session_id`；响应按 `data: {json}\n\n` 解析 SSE
- 每题一行 JSON 落盘（边跑边 flush）：`id/question/model/session_id/expect/elapsed_s/n_actions/actions/thoughts/observations(各截200字)/reflections/final`；请求异常记 `error` 字段不中断批次
- `GOLDEN_TOKEN`：服务端设了 `DOCMIND_API_TOKEN` 时填令牌（runner 会带 `x-docmind-token`）

### 输出文件命名（强制）

`results_<模型标签>_<轮次>.jsonl`，如 `results_35b_baseline.jsonl`、`results_35b_repeatguard_r1.jsonl`。
**基线文件永不覆盖**；每轮修复/换模型用新文件名，保证可回溯对比。

## 评分口径（结论必须绑定证据）

1. **逐事实核对，不凭印象**：把 final 中每个带 file:line 的断言拿到真实源码 grep 验证（行号、枚举值、参数、方法名、常量都要对得上）。
2. 三档：**对**（关键事实全部命中且无编造）/ **部分**（主结论对但漏要点或有非关键幻觉）/ **错**（关键事实错误、把异常当结论、或交白卷）。
3. 评分只看真实 SSE 落盘记录；"代码看起来已生效"不算数，必须端点实测。
4. **失败归因二分法**：
   - **框架事故**：空参空转、步数/重复护栏沉默终止、工具异常文本被当成"查无此物"的证据、参数归一化未接线（假"文件不存在"）、grep 未限定路径被噪声淹没、误路由到通用知识。→ 必须修框架 + 加测试。
   - **模型检索方差**：同题历史轮次跑对过、工具与观察均正常、仅模型选择的检索路径差。→ 记录，不改框架（至少两次复现再考虑 prompt 调整）。

## 自动打分与回归门（CI 用）

`agent_eval.py`（仓库根）给 results.jsonl 打分：默认**纯规则、离线可跑**，适合接 CI；可选叠加 LLM-judge。
题库每题可带 `expect`（runner 会原样写进结果行，结果文件自包含）：

```json
{"id": "Q1", "question": "...", "expect": {
  "must_include": ["player.gd:"], "any_of": [".gd:", "behaviors/"],
  "must_not_include": ["player.py"], "regex": "\\w+\\.gd:\\d+",
  "must_call": ["search_code"], "min_actions": 1, "max_actions": 8,
  "no_error": true, "judge_criteria": "是否给出具体文件与行号且未编造（仅 --judge 时生效）"
}}
```

```powershell
# 只打分
.\.venv\Scripts\python.exe agent_eval.py --results D:\eval\results_r1.jsonl
# 回归门：与 baseline 对比，pass→fail 或通过率下滑 → 退出码 1
.\.venv\Scripts\python.exe agent_eval.py --results D:\eval\results_r1.jsonl --baseline D:\eval\results_baseline.jsonl
# 叠加 LLM-judge（需本地模型）
.\.venv\Scripts\python.exe agent_eval.py --results r1.jsonl --baseline baseline.jsonl --judge --provider ollama
# 机器可读（CI 解析）
.\.venv\Scripts\python.exe agent_eval.py --results r1.jsonl --baseline baseline.jsonl --json
```

CI 串法（本地模型）：`run_golden.py` → `agent_eval.py --baseline`。规则打分逻辑本身由
`tests/test_agent_eval.py` 常驻单测守着（离线、进全量回归），无需模型即可门禁。

## 定位框架缺陷的标准动作

1. dump 该题记录：actions / observations[:200] / thoughts / reflections / final 全量打印。
2. 怀疑解析问题时，对记录中的**原始 Action Input 字符串**直接复现 `parse_response()` 与 `_normalize_tool_arg()`（PowerShell 跑含引号的 `python -c` 会被拆词，写成临时 .py 探针，用完删除）。
3. 怀疑接线问题：确认事件展示、evidence、实际派发三处用的是否都是归一化后的入参。
4. 修复必须配测试（`tests/test_agent.py` 风格 `_ScriptedLLM`，**必须实现 `count_tokens()` 返回 0**）：
   - 纯函数测试（parse/normalize）**不够**，还要有走 `Agent.run()` 的端到端接线测试；
   - 护栏类行为断言 final/reflection 文案关键词、工具真实调用次数、evidence 内容。
5. 验证顺序：单测 → 全量回归（基线 111 tests，新增用例数相应增加）→ **重启服务** → `GOLDEN_QIDS` 只重跑受影响题 → 必要时全量对比。

## 代码库切换

- 索引新工程：POST `/api/ingest_code`（multipart `root=<绝对路径>`）会自动 reset 代码集合、持久化 code_root（`.docmind_state.json`）、清空历史。
- 解除配置：POST `/api/reset_code`。
- 当前状态查 `GET /api/config`：`llm_model / code_root / code_sources`。
- 评测结束按用户要求切回原 code_root 并重新索引；勿擅自进入下一路线图阶段。

## 铁律

- 只做用户批准的事；未明确批准**不 commit、不 push**。
- 每轮结论附证据：jsonl 文件路径 + 源码 file:line + 测试结果。
- 真实模型输出存在方差：单次跑对/跑错都要结合多轮记录下判断。
