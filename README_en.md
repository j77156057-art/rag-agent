# DocMind · A RAG Q&A Agent with Tool-Use

A lightweight RAG Agent project built as a resume portfolio piece: turn your documents into a chat-able knowledge base, and let the LLM **decide on its own which tools to call** to answer questions (ReAct paradigm) — instead of naively "retrieve-then-dump-into-LLM".

## ✨ Features

- **Real Agent loop**: a multi-step Thought → Action → Observation reasoning cycle. The LLM decides when to retrieve / calculate / search the web, and when to give the final answer. Fully explainable and observable.
- **Self-reflection & retry**: when a tool returns no useful result, the agent reflects and switches tools (e.g. fall back to web search) rather than giving up.
- **Extensible tools**: `search_knowledge` (vector retrieval), `calculate` (expression eval), `web_search` (optional). Add a tool by appending one entry to the `TOOLS` dict in `tools.py`.
- **Multi-model provider abstraction**: OpenAI-compatible protocol unifies **Qwen / DeepSeek / Ollama (local) / mock**; switch via a config flag with zero business-code changes.
- **Service-ized**: FastAPI exposes the Agent as an HTTP service with SSE streaming; a single-page frontend renders the reasoning steps live.
- **Zero-dependency demo**: a built-in `mock` mode runs the full ingest → retrieve → Agent pipeline with no API key.

## 🧱 Tech Stack

Python · OpenAI-compatible SDK (Qwen/DeepSeek/Ollama) · Chroma vector store · FastAPI · pypdf

## 📁 Structure

```
rag-agent/
├── config.py          # config center (provider / paths / params)
├── llm.py             # LLM client: provider abstraction + streaming + mock
├── embeddings.py      # embeddings: cloud (Qwen) + local fallback
├── vectorstore.py     # Chroma wrapper
├── ingest.py          # doc load / chunk / index
├── tools.py           # Agent tools
├── agent.py           # ReAct Agent core loop + memory
├── api.py             # FastAPI service (SSE chat + upload)
├── run.py             # one-click launcher
├── web/index.html     # demo frontend
├── sample_docs/       # sample knowledge base
└── requirements.txt
```

## 🚀 Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

cp .env.example .env          # optional; mock mode needs no key
python run.py                 # ingests sample_docs, starts server
# open http://localhost:8000
```

### Use a real model (Qwen example)
In `.env`:
```
LLM_PROVIDER=qwen
EMBEDDING_PROVIDER=qwen
DASHSCOPE_API_KEY=your_key
```

### Call the API
```bash
curl -X POST http://localhost:8000/api/chat -F "question=What file formats does DocMind support?"
```

## 🎯 Talking Points (for interviews)

1. **Why an Agent, not naive RAG**: simple RAG struggles with "needs calculation" or "cross-document synthesis"; ReAct lets the model plan tool calls, improving generalization.
2. **Provider abstraction**: converging vendors behind one OpenAI-compatible interface means switching models touches no business code — the same idea is used in the author's Android project `MusicLayout` (`AIApiClient`).
3. **Robustness & degradation**: mock mode guarantees a demonstrable artifact even without network/key; vector retrieval has clear fallbacks.
4. **Service mindset**: FastAPI + SSE streams the reasoning process step-by-step, matching "wrap the Agent as a service" on the resume.
