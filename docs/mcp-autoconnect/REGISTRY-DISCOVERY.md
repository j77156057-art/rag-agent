# MCP 自主发现：接官方 Registry（把手工目录降级为离线兜底）

> 状态：设计已定（基于官方 Registry API **实测**），进入实现。
> 动机：现有 `mcp_server_index.py` 是**手工维护的 20 条精选索引**，违背「agent 自主发现」目标。改为以**官方 MCP Registry** 为主路径，精选索引**降级为离线兜底**（用户已拍板保留）。

## 1. 数据源（实测确认）
- Base：`https://registry.modelcontextprotocol.io`（**无鉴权、只读 REST**）。
- 检索：`GET /v0.1/servers?search=<need>&limit=N`；可选 `cursor`（分页）、`updated_since`（增量）。
- 条目结构：`server.{name, description, version, repository.url, packages[], remotes[]}` + `_meta["io.modelcontextprotocol.registry/official"]{status, publishedAt, updatedAt, isLatest}`。
- ⚠️ **同一 server 会返回多个版本** → 只取 `isLatest == true`，再按 `name` 去重。
- ⚠️ Registry 是 **preview、官方不保证 uptime** → **必须缓存**（见 §4）。

## 2. 两种连接形态 → 配置推导
### A) `packages[]`（本地 stdio）
字段：`registryType`(npm/pypi/oci/nuget…)、`identifier`、`version`、`runtimeHint`(如 `"npx"`)、`transport.type`(`"stdio"`)、`runtimeArguments[]{value,type}`、`packageArguments[]`、`environmentVariables[]{name,description,isRequired,isSecret,default}`。

推导：
- **启动器**：优先 `runtimeHint`；否则按 `registryType` 映射：`npm→npx`、`pypi→uvx`、`oci→docker`、`nuget→dnx`（**必须在 `ALLOWED_LAUNCHERS` 白名单内**，否则降级/跳过）。
- **args**：`[runtimeArguments.value…]` + `[identifier]` + `[packageArguments 按 type 展开]`（位置参数在前，如 `-y`）。
- **env**：`{name: "@secret:<name>" if isSecret else ""}`；`isRequired` 决定是否必须（缺则在前端提示）。
- 产出：`{transport:"stdio", command:<launcher>, args:[…], env:{…}, provenance:{url:<repository.url>, domain:…, registry:true}}`。

### B) `remotes[]`（远程托管 http）—— **本增量纳入**
字段：`type`(`"streamable-http"`)、`url`、`headers[]{name,value,isRequired,isSecret}`。

推导：
- `transport:"http"`，`url`（须过 R6：https + 受信域）。
- **header 值处理（修正版，2026-09-25 真机暴露后）**：
  1. 若 `value` 含 `{placeholder}`（如 `Bearer {smithery_api_key}`）→ **provider = 占位名（`smithery_api_key`）**，且**保留模板前后缀**：
     `Authorization: "Bearer @secret:smithery_api_key"`（**绝不落明文**）。
  2. 否则若 `isSecret` → 回退 provider = 表头名：`@secret:<header_name>`。
  3. 否则原样 `value`。
  > 旧口径 `@secret:<header_name>`（丢模板、provider 取表头名）**是错的**：`Bearer ` 前缀丢失 → 即便填了凭证，发出去也只是裸 token → 鉴权必失败。
- `resolve_secret_refs` 相应支持**值内嵌 `@secret:`**（`re.sub`，不再要求整值匹配）——整值 `@secret:NAME` 仍等价，向后兼容。
- 前端 `acSecretProviders`/`acHeadersMasked` 也须按**内嵌**规则提取 provider 与脱敏（不再用 `^...$` 锚定）。

## 3. 排序 / 去重 / 过滤（**算法，不靠人工条目**）
1. 只留 `isLatest`；按 `server.name` 去重。
2. 丢弃 `_meta.status != "active"`（跳过 deprecated/deleted）。
3. 排序（降序）：
   - 有 `packages`（本地 stdio，最易连）**优先于**仅 `remotes`；
   - 官方命名空间（`io.modelcontextprotocol/*`、`io.github.*/*`）**优先于**第三方托管（`ai.smithery/*`）；
   - 描述与 `need` 的词面相关度；
   - 同 `repository.url` 只留最高分一条。
4. 取 top N（默认 5）。

## 4. 缓存（必须）
- 落 `project_state`（`<STATE_ROOT>`）下，如 `mcp_registry_cache.json`；键 = `search 词`；值 = `{fetched_at, servers:[…]}`；**TTL 默认 24h**。
- 命中 TTL → 直接用缓存（离线可用、提速）；未命中 → 拉 Registry 并写缓存。
- Registry 不可达/超时 → 用**过期缓存**；再无 → 回落**精选索引（离线兜底）**。

## 5. 命中顺序（改后）
1. **Registry API（自主）** ← 主路径
2. **精选索引**（离线兜底，保留）
3. 现有 GitHub 域检索 + readme 兜底（长尾）
4. `llm_fn` 解析兜底（可选，当前 api 未接）

## 6. 安全（沿用 R1–R9，**不放松**）
- 注册表数据仍**强制过 `validate_extracted_config`**（R1–R9）：启动器白名单、args 形状、url https + 受信域。
- **密钥一律 `@secret:`**，绝不留明文（`isSecret` 字段直接映射）；写盘仍走 `confirm`（`user_ack`）。
- `remotes` 的 url 主机须过受信域（`smithery.ai` 已在 `TRUSTED_SOURCE_DOMAINS`；其余如需再加白名单）。
- 页面/注册表**正文不喂模型**。

## 7. 落地清单
- **新增 `mcp_registry.py`**：
  - `registry_search_url(need, limit)` → URL（纯函数，可测）。
  - `parse_registry_response(payload) -> list[server]`（纯函数：过滤 isLatest/status、去重）。
  - `server_to_candidates(server) -> list[cand]`（packages/remotes → 配置；**纯函数**）。
  - `search_registry(need, *, limit=5, http_get=None, cache=None) -> dict`（编排；`http_get` 注入便于测试）。
  - `rank_candidates(cands, need)`（§3 排序）。
- **`mcp_autoconnect.py`**：`discover_from_need` / `auto_connect_pipeline` 命中顺序改为 **registry → 精选 → web/LLM**；registry 结果也过 `validate_extracted_config`。
- **缓存**：`project_state` 读写（复用现有 tool-result 无关的纯读写）。
- **测试**（mock registry JSON，不联网）：packages 版推导（`npx -y <id>` + env `@secret:`）、remotes 版推导（url+headers）、多版本只取 `isLatest`、deprecated 被过滤、同 repo 去重、registry 不可达 → 回落精选、缓存命中不重复拉取。

## 8. 对外行为变化
- 用户输入任意需求（如 `stripe` / `postgres` / `filesystem`）→ **不再依赖是否收录**，直接查 Registry 出候选。
- 精选索引仍在，但只在**离线/Registry 不可达**时兜底。
