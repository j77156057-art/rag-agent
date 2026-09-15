# DocMind 演示说明（Demo Guide）

本文档说明如何在本地把 **当前形态** 的 DocMind 跑起来、预期看到什么，以及它的推理流程长什么样——相当于一份"截图说明"的文字版。

> 当前 DocMind 已不是单页 RAG 问答，而是「本地 RAG + ReAct Agent」叠加「游戏开发工作台」的双入口应用：问答页负责检索 / 问答 / 索引代码，工作台负责分区、场景画布、运行时时间线、引擎嵌入、GPU 协调、ComfyUI 等仓库级工作流。两个入口可互跳。

## 一、两种运行方式

### 方式 A：零配置（mock 模式，无需任何 key）
项目默认 `LLM_PROVIDER=mock`、`EMBEDDING_PROVIDER=local`，开箱即跑：

```bash
.venv\Scripts\activate
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

浏览器打开：
- **问答页**：**http://127.0.0.1:8000**
- **工作台**：**http://127.0.0.1:8000/workbench**

（桌面端用 `desktop.py` 启动原生窗口，默认也开问答页；`DOCMIND_HOME=/workbench` 可改默认入口。）

### 方式 B：接真实大模型（以通义千问为例）
```bash
cp .env.example .env
# 编辑 .env：
#   LLM_PROVIDER=qwen
#   EMBEDDING_PROVIDER=qwen
#   DASHSCOPE_API_KEY=你的key
.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000
```

> 也可在问答页点右上角「⚙ 模型设置」免重启切换（Key 仅存内存）。代码问答建议用真实 embedding，否则检索无语义意义。

## 二、前端界面长什么样

### 2.1 问答页（`/`）
```
┌──────────────────────────────────────────────────────────┐
│ DocMind  RAG 问答 · 支持工具调用的 ReAct 推理   [上传] [⚙模型] │
├──────────────────────────────────────────────────────────┤
│  你：伤害计算是在哪个函数里实现的？把那段代码给我看看        │
│  助手：                                                    │
│   [思考] 我需要先检索代码库定位实现。                      │
│   [行动] search_code("damage calculation")                 │
│   [观察] [game/combat.py:42] class DamageCalculator        │
│   [思考] 命中 DamageCalculator.compute，读取完整实现。      │
│   [行动] read_file("game/combat.py")                       │
│   [回答] 伤害计算在 DamageCalculator.compute()（combat.py:42）：│
│          …（引用片段，文件名可点击跳转到编辑器对应行）…      │
├──────────────────────────────────────────────────────────┤
│  [ 输入问题，回车发送 ]                        [发送]       │
└──────────────────────────────────────────────────────────┘
```
- 右上「上传文档」可上传 PDF/MD/TXT 入库问答；「模型设置」切 provider / 填 Key。
- 代码问答的回答里**文件名:行号是可点击的 fileref**，点一下在工作台编辑器里直接跳到对应行并高亮（需先打开工作台并加载该工程）。
- 长回答带「深度思考」标签，区分模型直答与经过工具检索的作答。

### 2.2 工作台（`/workbench`）
顶栏一排入口：文件树 / 选区 AI / 分区治理（RegionMap）/ 任务引擎（含 ComfyUI）/ GPU / Unity 图 / 试玩器（场景画布 + 运行时时间线 三 tab）。左侧文件树带 dirty / tracked 标记，中间 CodeMirror 编辑器，右侧检查器 / 详情侧栏。

## 三、ReAct 推理流程（核心看点）

```
        ┌─────────────┐
        │  用户提问    │
        └──────┬──────┘
               │
               ▼
        ┌─────────────┐    Thought
        │  LLM 思考    │──────────► 决定调用哪个工具
        └──────┬──────┘
               │ Action: search_knowledge / search_code / python_exec / web_search …
               ▼
        ┌─────────────┐
        │ 工具执行      │──► 向量检索 / 跑代码 / 抓网页 …
        └──────┬──────┘
               │ Observation: 检索到的片段 / 计算结果
               ▼
        ┌─────────────┐
        │ 有用? ──否──►┐
        └─────┬───────┘ └──► [反思] 换用 web_search 再试
              │是             └──────────────┐
              ▼                            │
        ┌─────────────┐                    │
        │ Final Answer │◄───────────────────┘
        └──────────────┘
```

- **朴素 RAG**：检索结果直接拼给 LLM。
- **DocMind（Agent）**：LLM 自决定「检索 / 计算 / 联网 / 读代码」与「何时停止」，工具失效时自我反思换工具——这是项目与"直接套 LangChain"的区别。
- 知识库为空时 `search_knowledge` 返回"未找到"，Agent 输出 `reflection` 并改调 `web_search`，演示"自我反思 + 换工具重试"。

## 四、工作台里值得现场点的几样

1. **分区开发**：顶栏「分区」一键把工程切成 assets / values / behaviors / levels / ui / audio / net / bugs，每区独立 git；在文件树里对越区文件做 AI 写操作会被直接拒绝。
2. **场景画布**（试玩器 → 场景画布 tab）：打开一个 Godot `.tscn`，看到层级 / 空间两种布局的节点图；双击文件卡在编辑器打开脚本；在检查器改属性、拖节点写回 `position`；每一步都可撤销，且撤销后 `.tscn` **逐字节还原**（含空行位置）。
3. **运行时时间线**（试玩器 → 运行时时间线 tab）：游戏按约定打印 `DOCMIND_EVENT {json}` 后，事件画成多轨道时间轴，可筛选 / 缩放 / 点事件跳代码行。
4. **引擎嵌入**（桌面端试玩器「嵌入工作台」开关）：勾上后点「桌面窗口启动」，Godot 画面直接落在弹窗的引擎视窗上，工作台照常可用；「聚焦 / 解除嵌入 / 停止」齐备。浏览器模式开关自动禁用并提示需桌面端。
5. **GPU 面板**（顶栏 GPU）：实时每卡显存 / 温度 / 迷你曲线 / 持租约者 / 排队项；可设 Ollama 空闲卸载秒数（持久化）。当前本机单卡 RTX 5070 Ti 已真机冒烟通过；多卡调度与 CUDA 隔离仅做完软件侧，物理多卡仍待验收。
6. **ComfyUI 任务引擎**（任务引擎面板）：本机 ComfyUI 服务可达时显示 Z-Image / MiniMax H3 模板、最近任务、进度百分比、失败重试、取消生成；服务需用户显式启动（`POST /api/comfy/start`）。Z-Image 与 MiniMax H3 均已真机生成成功——H3 在「子图拍平 + `/object_info` 驱动 widget 映射」的转换器重写后，经 DocMind 管线完成 **39 帧短生成**并跑通取消 / 重试端到端。
7. **Unity 图**（顶栏「Unity 图」）：纯文本静态分析 `.meta` GUID 引用图（无需启动编辑器）；力导向布局、类型 chips 过滤、搜索邻接高亮、缺失红虚线、详情侧栏含出入边与 GUID 复制。
8. **联网研究**：问时效性 / 外部问题时 Agent 自主 `web_search` / `web_fetch` / `web_research`，回答里的来源 URL 可点击。
> 下面 9–11 三项属于本轮新增的 **Agent 运行时（Harness）**；完整清单与能力边界见 [README.md](README.md) 的「Harness 能力」一节。

9. **Trace 账本**（浏览器开 `/trace`）：问一轮之后刷新，能看到这一回合结构化记录——调了哪些工具（含每步延迟）、tokens in/out、总耗时、结局（completed / evidence_fallback / 断连中止…）、以及**折算花费**。只记元数据、不存 prompt 与回答正文。截图见 `docs/screenshots/trace-ledger.png`。
10. **多代理编排**（`POST /api/orchestrate`，或让 Agent 自己用 `orchestrate` 工具）：给一张任务图，无依赖的并行跑、有依赖的等上游结论；某任务失败会**自动补一张补救任务**继续（可在返回里看到 `revisions` 审计与每个任务的 `trace`）。
11. **成本熔断**（`GET/POST /api/budget`）：按 provider 计价并累计；先设一个很小的额度，再问一轮，就能看到"回合前拒绝"的效果。本地模型（ollama / llamacpp / mock）花费恒为 0，不会误伤离线演示。

## 五、命令行 / API 验证（不依赖前端）

```bash
# 问答（RAG 页，multipart 字段是 question）
curl -X POST http://127.0.0.1:8000/api/chat -F "question=DocMind 支持哪些文件格式？"

# 上传文档
curl -X POST http://127.0.0.1:8000/api/ingest -F "file=@你的文档.pdf"

# 索引代码目录（之后所有代码问答基于这份索引）
curl -X POST http://127.0.0.1:8000/api/ingest_code -F "root=C:/path/to/your/game-project"

# 指定会话（不同 session_id 的历史互不可见；不传则默认会话）
curl -X POST http://127.0.0.1:8000/api/chat -F "question=你好" -F "session_id=demo1"

# 逐轮 trace 账本 / 会话列表 / 成本账本
curl --noproxy '*' "http://127.0.0.1:8000/api/trace?limit=10"
curl --noproxy '*' http://127.0.0.1:8000/api/sessions
curl --noproxy '*' http://127.0.0.1:8000/api/budget

# 多代理编排：任务图（b 依赖 a，会拿到 a 的结论；失败自动补图）
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/orchestrate -H "Content-Type: application/json" \
  -d '{"tasks":[{"id":"a","role":"researcher","task":"查某功能在哪实现"},
                {"id":"b","role":"reviewer","task":"评审上一结论","depends_on":["a"]}],"synth":true}'

# 热插拔：改完 .docmind/hooks/*.py 或 .docmind/skills/*.md 后重载，无需重启
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/hooks/reload
curl --noproxy '*' -X POST http://127.0.0.1:8000/api/skills/reload
```

返回为 SSE 流，事件类型依次为：`token → thought → action → observation → [reflection] → token → final → done`。
开**计划模式**（`plan_mode=1`）时会先上抛一个 `plan` 事件；原生 function-calling（`tool_mode=native`）事件类型不变。

## 六、自检脚本（发布 / 回归用）

```bash
# 全量单元测试（450 项；MinGit 在 PATH 时 git 用例实际执行）
.venv\Scripts\python.exe -B -m unittest discover -s tests

# 场景画布后端自检：进程内起 FastAPI + 临时 Godot 工程，走真实路由，不占端口
.venv\Scripts\python.exe verify_scene_canvas.py

# 引擎嵌入实机自检（真 Godot + 真 Win32 宿主；会短暂弹窗并自动把鼠标移回原处）
.venv\Scripts\python.exe verify_engine_embed.py

# 场景画布浏览器冒烟：先在另一终端起演示服务，再用托管 node 执行
.venv\Scripts\python.exe verify_scene_canvas.py --serve 8011
node verify_scene_canvas_ui.mjs http://127.0.0.1:8011
```

浏览器冒烟产出 `docs/screenshots/scene-canvas.png` 与 `docs/screenshots/runtime-timeline.png`。

## 七、用真实模型后预期提升

- 回答由真实大模型生成，引用检索片段后给出自然语言结论；
- `embedding` 切换为通义千问 `text-embedding-v3`，检索为真实语义相似度（不再是本地哈希兜底）；
- 支持更复杂的多跳追问与计算类问题（如"近一年文档里提到多少次'安全'？"可结合 `python_exec`）；
- 代码问答能真正定位你工程里的函数 / 类 / 报错行，而不是凭印象编造。

> 已知边界（诚实标注）：本机显示器当前 150% 缩放，引擎嵌入 100%/125% 未实测；物理多 GPU 调度与 CUDA 隔离、Unity/Unreal 编辑器端到端联机仍待对应硬件 / 引擎到位后验收（本机未装编辑器）。H3 已完成 **39 帧短生成**验收，长时长 / 多镜头编排未做。Agent 运行时侧：子代理不共享父上下文、重规划不回滚已执行任务、hooks 无沙箱、技能仅为提示词注入。详见 [HANDOFF.md](HANDOFF.md)。
