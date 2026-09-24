---
name: mcp-tool-discovery
version: 1.0.0
description: 从自然语言需求发现并装配合适的 MCP 连接器，离线优先、联网仅走 GitHub 域，全程用户审批。
when_to_use: 用户表达"找个能做 X 的 MCP/工具/连接器"或代理自检发现能力缺口时
---

# MCP 工具发现（mcp-tool-discovery）

把"我想要一个能 XX 的 MCP"变成经过信任闸门、用户批准的真实连接器。

## 触发
- 用户说"帮我找个能做 <能力> 的 MCP / 工具 / 连接器"；
- 或代理在回答中发现本地没有某能力，且判断 MCP 比裸 shell 更可控 / 可审计。

## 流程（严格顺序）
1. 调用 `dev_mcp_discover_from_need(need)`：
   - 默认仅离线精选索引（秒级、可信）；
   - 命中不足时，用户可附 `web_enabled: true`，此时**仅**走 GitHub 域限定搜索
     （等价于 `web_search("platform:github <need> MCP server")`）→ 取仓库 README
     → 解析官方安装命令。绝不走泛搜索（质量差、易带偏）。
2. 逐个候选：
   - 向用户展示：来源域 / transport / command+args 或 url / 会新增哪些能力 / 凭证需求；
   - 说明风险（未知来源、需注册、需存密钥等）。
3. 用户确认某候选后，调用 `dev_mcp_add`（key/command/args 或 url）→ 触发审批闸门
   → `dev_approve(action: mcp_server, target: <key>)` → 用相同参数重试 `dev_mcp_add`。
4. `dev_mcp_probe` 探活（initialize + tools/list）；失败按 error 修正参数。
5. `dev_mcp_discover` 生成能力候选 → 向用户说明能力 → `dev_approve(action: mcp_capability, target: <key>)` → `dev_mcp_decide(decision: approve)` 才允许路由调用。

## 护栏（不可违反）
- 绝不静默写入：任何连接器落盘前必须有用户明确确认（dev_mcp_add 的审批闸门）。
- 凭证只走 `@secret:<provider>`：需要 token 的 server，引导用户存入 secrets_store，
  命令 / env 里只留 `@secret:xxx`，绝不在配置里写明文密钥。
- 信任闸门 R1-R9 是权威裁决：即便来自 GitHub README，命令也必须过结构 / 启动器 / URL /
  来源域校验；`command_unresolved=True` 或 `source_untrusted` 的候选只能走手动确认，不得自动放行。
- 联网仅 GitHub 域：不要为"找 MCP"发起泛搜索（弱、易带偏、返回无关百科 / 政策页）。
- 不重复造轮子：先 `dev_mcp_discover_from_need` 离线索引，再考虑联网；已知热门 server 已收录。
