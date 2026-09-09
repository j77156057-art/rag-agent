# DocMind 演示说明（Demo Guide）

本文档说明如何在本地把 DocMind 跑起来、预期看到什么，以及它的推理流程长什么样——相当于一份"截图说明"的文字版。

## 一、两种运行方式

### 方式 A：零配置（mock 模式，无需任何 key）
项目默认 `LLM_PROVIDER=mock`、`EMBEDDING_PROVIDER=local`，开箱即跑：

```bash
.venv\Scripts\activate
python run.py
```

终端会先打印 `[ DocMind ] 已摄取示例文档，共 N 个切片`，随后启动服务。
浏览器打开 **http://localhost:8000** 即可对话。

### 方式 B：接真实大模型（以通义千问为例）
```bash
cp .env.example .env
# 编辑 .env：
#   LLM_PROVIDER=qwen
#   EMBEDDING_PROVIDER=qwen
#   DASHSCOPE_API_KEY=你的key
python run.py
```

## 二、前端界面长什么样

```
┌──────────────────────────────────────────────┐
│ DocMind  RAG 问答 Agent · 支持工具调用的 ReAct 推理   [上传文档] │
├──────────────────────────────────────────────┤
│  你：DocMind 支持哪些文件格式？                  │
│  助手：                                        │
│   [思考] 我需要先检索知识库来获取准确信息。      │
│   [行动] search_knowledge(...)                  │
│   [观察] [docmind_product.md] 支持 PDF/Markdown/TXT… │
│   [回答] 根据知识库内容：DocMind 支持 PDF、Markdown│
│          (.md) 和纯文本(.txt) 三种格式……         │
├──────────────────────────────────────────────┤
│  [ 输入问题，回车发送 ]            [发送]       │
└──────────────────────────────────────────────┘
```

- 右侧「上传文档」可上传你自己的 PDF/MD/TXT，入库后即可基于它问答。
- 每一步「思考 / 行动 / 观察 / 反思 / 回答」会实时渲染，推理过程完全透明。

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
               │ Action: search_knowledge(问题)
               ▼
        ┌─────────────┐
        │ 工具执行      │──► Chroma 向量检索 Top-K
        └──────┬──────┘
               │ Observation: 检索到的片段
               ▼
        ┌─────────────┐
        │ 有用? ──否──►┐
        └─────┬───────┘ └──► [反思] 换用 web_search 再试
              │是             └──────────────┐
              ▼                            │
        ┌─────────────┐                    │
        │ Final Answer │◄───────────────────┘
        └─────────────┘
```

- **朴素 RAG**：检索结果直接拼给 LLM。
- **DocMind（Agent）**：LLM 自决定「检索 / 计算 / 联网」与「何时停止」，并能在工具失效时自我反思换工具——这正是项目与"直接套 LangChain"的区别。

## 四、命令行 / API 验证（不依赖前端）

```bash
# 问答
curl -X POST http://localhost:8000/api/chat -F "question=DocMind 的计费方式是怎样的？"

# 上传文档
curl -X POST http://localhost:8000/api/ingest -F "file=@你的文档.pdf"
```

返回为 SSE 流，事件类型依次为：`token → thought → action → observation → [reflection] → token → final → done`。
当知识库为空时，`search_knowledge` 返回"未找到"，Agent 会输出 `reflection` 并改调 `web_search`，演示"自我反思 + 换工具重试"。

## 五、用真实模型后预期提升

- 回答由真实大模型生成，引用检索片段后给出自然语言结论；
- `embedding` 切换为通义千问 `text-embedding-v3`，检索为真实语义相似度（不再是本地哈希兜底）；
- 支持更复杂的多跳追问与计算类问题（如"近一年文档里提到多少次'安全'？"可结合 calculate）。
