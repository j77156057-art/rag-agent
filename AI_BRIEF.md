# DocMind 项目 AI 交接摘要（截至 2026-09-10 16:53）

> 用途：让接手本项目的 AI 在 3 分钟内理解主旨、当前状态与关键坑，直接继续后续任务。
> 详细运维细节见同目录 `HANDOFF.md`；今日逐条工作日志见 Workspace 记忆 `2026-09-10.md`。

## 1. 项目是什么
**DocMind** —— 一个**本地优先**的 RAG 编码助手，帮开发者读懂/检索**自己的工程代码与文档**，定位函数/类/文件与行号，而不是直接替你写代码（定位搭档，防 AI 幻觉式代码堆叠）。
- 技术栈：FastAPI 后端 + Chroma 向量库 + 自研 Agent（工具调用）+ 纯前端（`web/index.html`）。
- 分发形态：PyInstaller onedir 桌面 exe（`dist/DocMind/DocMind.exe`），双击即用的窗口应用，也支持 `DOCMIND_SERVER_ONLY=1` 无头服务模式。

## 2. 当前模型配置（零 Key，全本地）
| 能力 | Provider | 端点 | 模型 | 维度 |
|---|---|---|---|---|
| 生成 / 增强 / Agent 推理 | `llamacpp` | `http://localhost:8080/v1` | `qwen3.6-35b-a3b`（nvfp4 GGUF，约 17GB） | — |
| 语义检索 embedding | `ollama` | `http://localhost:11434/v1` | `bge-m3`（多语言） | 1024 |

- 二者**全程免 API Key**。配置读自项目根 `.env`（`LLM_PROVIDER=llamacpp`、`EMBEDDING_PROVIDER=ollama`、`EMBEDDING_MODEL=bge-m3`）。
- 此前 embedding 是 `local`（随机 256 维占位，检索无意义）；本次会话已替换为真·语义向量。

## 3. 已交付的核心能力
- **多 Provider LLM**：`mock`/`qwen`/`deepseek`/`ollama`/`llamacpp`，页面内免重启切换（运行时覆盖层 `_RUNTIME`）。
- **智能研判分区**（regions）：Agent 依据代码库结构建议分区，工作台可应用/增补。
- **增强提示词**（✨按钮）：调用 LLM 重写用户草稿，走 llama.cpp 真 35B。
- **代码问答模式**：`search_code` / `read_file` / `grep` 工具 + `ingest_code_directory` 索引代码库。
- **文档问答**：`search_knowledge` + `ingest_file`。
- **单实例保护**桌面端：端口 8000 被占则复用已有服务。

## 4. 关键结论与选型理由（重要）
- **embedding 选 `bge-m3` 而非 `nomic-embed-text`**：nomic 是英文为主模型，中文查询做跨语言（中文问→英文代码）检索会失败（player.py 排不进前排）；bge-m3（多语言）中文「碰撞检测」能正确命中 `player.py`。→ **面向中文用户索引代码，bge-m3 优于 nomic**。
- **切换 embedding provider 必须重置两个集合**（文档 + 代码），否则旧维度（256）与新维度（1024）冲突报错。文档用 `reset_collection()`，代码用 `reset_code` / 同样 reset。

## 5. 后续 AI 必看的坑（按踩坑频率排序）
1. **PyInstaller onedir 漏拷 exe**：COLLECT 不会把 exe 放到 `dist/DocMind/` 顶层（只放 `_internal`）。构建后必须 `cp build/docmind/DocMind.exe dist/DocMind/DocMind.exe`。**验证以运行时行为为准，勿 grep 打包后的 `_internal` 归档源码。**
2. **单实例保护会 re-attach 旧进程**：测新包前务必先杀旧 `DocMind.exe`，否则连到的还是旧服务。强杀走沙箱旁路：`MSYS_NO_PATHCONV=1 taskkill /F /IM DocMind.exe`（普通 `tasklist` 在沙箱里看不到宿主进程，会误报干净）。
3. **索引代码会污染**：`ingest_code_directory(cwd)` 若不跳过，会把 `.chroma`、旧打包 `_archived_builds/`、上传 `uploads/` 当代码索引（曾误出 34792 块）。`ingest.py` 的 `_SKIP_DIRS` 已含 `.chroma`/`_archived_builds`/`uploads`/`.venv`/`dist` 等，新增跳过目录时同步更新此处。
4. **`/api/chat` 的 `question` 是表单字段**（`Form(...)`），curl 用 `-F "question=..."`，不是 JSON body。
5. **增强接口用 system+user 双消息**：llama.cpp 对纯 system 单消息报 `400 Unable to generate parser for this template`；agent 的 `/api/chat` 本就是双消息，正常。
6. **可用模型判定**：`ollama`/`llamacpp` 等免 Key 本地 provider 的 `api_key_env` 为空，判定逻辑是「有 `api_key_env` 才要求 Key」，否则会误判为 local 兜底。
7. **构建/重索引耗时**：PyInstaller 约 2 分钟；全量重索引 574 块经代理会超过 Bash 默认 120s 上限 → 显式给 600000ms 超时。
8. **代理环境变量**：本机存在 `HTTP(S)_PROXY=127.0.0.1:60243`，但 localhost 的 openai/ollama 调用正常，无需处理。
9. **llama.cpp 默认上下文仅 4096**：当前 `llama-server.exe` 启动未带 `--ctx-size`，默认 4096。RAG 注入检索片段+历史后，长问题会触发 `exceed_context_size_error`(400) 导致 chat 失败。修复：重启 llama-server 加 `--ctx-size 8192`（或 16384，视显存），如 `"D:/llama.cpp/cuda131/llama-server.exe" --model <gguf> --host 127.0.0.1 --port 8080 --ctx-size 8192`。
10. **后台启动任务"failed"常是假警报**：启动 llama-server / exe 的后台 Bash 任务外壳常被回收并标记 failed，但**子进程会脱离外壳继续运行**（如 llama-server.exe、DocMind.exe 仍在线服务）。判断真实状态只看 `curl 127.0.0.1:8000/api/config` 与 `:8080/v1/models` 是否响应，勿因任务 failed 就误以为服务挂了。

## 6. 当前运行状态（2026-09-10 16:33 后）
- exe 运行于 `http://127.0.0.1:8000`，`build_time=2026-09-10 16:33:48`，`/api/config` 显示 `embedding_provider=ollama` / `llm_provider=llamacpp`。
- `llama.cpp(8080)` 与 `Ollama(11434)` 均在线。
- **前提**：这两个本地服务必须保持运行，否则检索降级为 local 占位、生成接口不可用。
- 代码集合 `docmind_code` 已用 bge-m3 重索引 574 块（项目自身源码）；文档集合 `docmind` 51 块。

## 7. 如何索引用户的游戏代码（下一步最可能的任务）
- 在界面用「代码索引」或 `POST /api/ingest_code -F "root=<游戏仓库绝对路径>"` 指认游戏仓库根目录。
- 端点会先 `reset_code` 清旧向量（旧维度冲突），再用当前 ollama/bge-m3 重索引；切到新仓库前务必 reset。
- 验收标准：问「XX 功能在哪实现」，应能定位到文件与行号；召回相关片段。

## 8. 待办 / 可选
- 用真实游戏代码库做端到端问答验收，据结果微调 `CODE_CHUNK`(1200)/分块策略/`SYSTEM_PROMPT`。
- 可把「本地零 Key 接入（llama.cpp + Ollama bge-m3）+ 重建 exe」这套流程存成 skill 复用。
