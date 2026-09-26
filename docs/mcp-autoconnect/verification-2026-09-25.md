# MCP 自动连接验证记录（2026-09-25）

## 固定基线

- 验证对比基线：`80b8a90975a5a72144fd47afbd96589b62c7c88b`（验证开始时的 `origin/main`）。后续验证应始终与此 commit 比较，不以移动中的 `main` 代替。
- 本次验证时本地 HEAD：`ecea19a9ea0974f40e91173e95e8686511b0a4f5`。下述改动仍在工作树中，尚无对应提交；“已通过”仅指本节记录的源码快照。真实第三方连接补测于 2026-09-26（北京时间）完成。
- 基线测试数 `232 passed / 4 skipped` 来自此前的 `HANDOFF.md`，本次没有重跑基线提交。

## 本次范围与结果

- 领域名称搜索没有直接候选时，目录会用领域关键词查询官方 MCP Registry，把通过 R1–R9 校验的具体连接方式交给用户选择。目录不会执行命令或写入连接配置。
- 目录候选显示其实际的官方或社区分档，不会仅因来自 Registry 就显示为官方发布。
- Registry 的 `isRequired:false` 会作为可选凭证传到界面。用户可主动填写；留空不启动注册、不提交空凭证。HTTP 的可选凭证缺失时，包含该引用的请求头整体省略。
- `mcp_autoconnect.py` 的候选、服务适配器、配置校验和 Registry 边界迁至独立模块，主文件由 1559 行降至 997 行；旧调用入口仍保留。
- 问答首页的静态工作台链接已补齐，构建后的 `web/index.html` 能通过桌面入口契约测试。

验证命令与结果：

- 初始源码快照全量 **1572 passed，5 skipped，60 subtests passed**；接通 Agent 搜索工具后的全量（临时将 `LLM_PROVIDER=mock`、`EMBEDDING_PROVIDER=local`）**1582 passed，5 skipped，60 subtests passed**。新增测试覆盖受控模型调用 `dev_mcp_search`、Agent 无法自批 MCP，以及用户侧能力审批台账。本机默认 Ollama 不可达；不覆盖这两个测试环境变量时，只有 `test_cloud_agent_registration.py` 的 2 项早退失败。
- 最终 MCP 定向测试：**136 passed**。
- 在 `frontend` 目录执行 `npm run build`：类型检查与 Vite 构建通过。仍有现存的 `/static/session.js` 非 module 提示和大资源块提示。
- `git diff --check`：通过。

## Agent 自助装配与用户确认门（2026-09-26）

- MCP 连接意图会让 Agent 获得 `dev_mcp_search`；受控模型会话收到该工具并调用它。联网打开时使用同一真实 Registry 链路，联网关闭时保持离线目录行为。该测试验证工具调度，不冒充真实模型自主决策验收。
- `dev_approve` 对 `mcp_server` 和 `mcp_capability` 永久拒绝 Agent 自批。`dev_mcp_add`、`dev_mcp_decide`、`dev_mcp_remove` 被阻断时返回用户确认提示。
- 设置页保存/移除连接器及批准能力的 API 以 `workbench-user` 写入审批台账；连接器审批 target 绑定 transport、command、args 或 URL，能力审批 target 绑定 key。用户点击确认后，Agent 才能重试并继续探活、能力路由和调用。
- `frontend` 的 MCP 手工添加表单现在显式提交 `transport` 和 `enabled`，与后端配置校验一致。

## 真实第三方现场验收（2026-09-26）

- 固定源码基线仍为上述 HEAD 加本报告 SHA-256 所列工作树改动。官方 MCP Registry 实时查询：`EDA` 返回 `ai.jeda/jeda-ai`（思维导图，属于无关子串命中）；`kicad` 本次无候选；`easyeda` 返回 `io.github.VLab-Software/easyeda-pro-mcp` 和 `io.github.biosshot/easyeda-copilot` 两个社区发布的 stdio 候选。`pcb` 等部分查询遇到 Registry 瞬时超时，不能据此判定无服务。
- 真实 `search_directory("EDA", web_enabled=True)` 已返回上述两个 EasyEDA 连接选项，均显示为“社区”，无 `jeda` 误命中。关闭联网时不查询 Registry。自动连接主链路也过滤 EDA 子串误命中。
- 后续针对“候选是不是临时配置”的复核：用空临时项目直接请求 `/api/mcp/catalog/search`，得到 HTTP 200、`source_status=registry` 和 `io.github.VLab-Software/easyeda-pro-mcp`，请求前后均无 `.docmind_mcp.json`；测试只替换了无关的通用网页搜索，Registry 查询仍为真实网络请求。原先模型工具 `dev_mcp_search` 只读离线目录，本轮已改为继承 Agent 会话联网开关并调用同一 Registry 发现链路。再用空临时项目真实调用 `dev_mcp_search("EDA")`，返回 Registry 候选，请求前后同样没有连接配置文件。这证明候选不是预置项；实际模型在自然语言会话中自行决定调用该工具尚未实测。
- 两个项目的公开说明均要求 EasyEDA Pro 编辑器及各自扩展；本机未发现 EasyEDA 安装。`easyeda-copilot-mcp` npm 包版本可查询到 1.2.0，但首次试连在 MCP `initialize` 阶段等待 180 秒后超时；该项目文档说明首次运行可能下载额外组件，未将此项记为连接成功或服务故障。
- `@vlabsoft/easyeda-pro-mcp` 在临时项目中完成真实 `initialize` 和 `tools/list`，返回 **19 个工具**。再以项目配置、能力发现、审批、`easyeda_live_status` 调用走完整链路：待审批时未激活；审批后状态为 `active`；只读工具调用成功返回 `connected: false` 和“EasyEDA Pro extension is not connected”。这证明 MCP 层可用，但当前机器还不能读取或修改实际图纸。
- `probe_candidate` 现在会调用这一已知只读状态工具，返回 `probe_ok: true, ready: false` 和中文扩展安装提示；前端分开显示“MCP 服务已连接”和编辑器未就绪。需要在安装 EasyEDA Pro 与扩展、打开真实图纸后复验设计工具。需要注册的第三方服务及账号流程本次没有真实账号可验收。

本轮修正还包括：目录严格遵守联网开关；EDA 关键词查询能覆盖 EasyEDA；`npx` 首次试连默认使用 `-y` 避免交互提示；GitHub 命名空间不再被误标为目标厂商官方；试连 `initialize` 遵守传入超时；Windows 下关闭经 `npx.CMD` 启动的 MCP 时会尝试终止子进程树。

## 源码快照 SHA-256

以下哈希记录了本次验证时尚未提交的源码，便于在生成提交后确认内容一致：

```text
README.md                                        9A53B045388CB4757EFB464CB4BFF1C61328AA771D63E8AA8C5DF4C5F937E690
HANDOFF.md                                       D986BAB98FA60ECE4184424C4D315F6BA9469F3FF039BEE2FB069B0225BD3BF2
agent.py                                         80837CBD66AF0CE3F0EBCB7A47D644C2CB0E01BC2ED91667340C9F36B5F90CAD
agent_runtime/context_router.py                  0297673A411083DB0F5F9C52FBDB416E5761EFF6CCBF749B28B6425622BFBD6B
agent_runtime/tools.py                           D06B75120ABCD9F0C17FE0E8A9913C9A974BD41A809823154F829A7D7F47BB12
api.py                                          24FB8AC343E26D129DB5A1E1039E7F34534D190C23245C27BA821A9200023741
docs/integrations.md                            6F60C5D74E2D1BFB2A21C8406607A3AF2FE51A34124166B6EC7AFBC3147E82A8
frontend/index.html                              FA8D2C09BFCBB4B8E41217A310936F0321E72216DB5566204DDEBFB5D4E1028C
frontend/src/workbench/api.ts                    0275EAC2D44FE6290EFD934816593095FE15A62A1A0159966ED3DFA9FFE032F7
frontend/src/workbench/components/SettingsView.vue A01A17EEC5B1DE1A582123B264B53797D36A0AF93D6388833D3A01AE5B749282
mcp_autoconnect.py                               5F9C3984D234BE8682955C443E905E1B4C7C6C0A1D6AD7761E93F1BAABE1430E
mcp_candidates.py                                6E08C54B335770F24BE41888C646C0A9F1229FF9E8F9FD788CFB4AF4407A2284
mcp_client.py                                    C4E6B6076D0DD0DAB5CB7484B5EB9D453C05EACB129CFA929DB16F0DB9B036EC
mcp_providers.py                                 85B11C119FA09C8CA7AEE4FF81ACE20B1A16B705C3BFAC3F79AA53209B1C4258
mcp_registry_bridge.py                           313354BEB73ED4DFEF2252BE94E73F0923EC5885740E84AE5EA86B00523F90B8
mcp_validation.py                                D7B741FD0C1A5F9941B12FC6197AE6CFAB5A40179A5C7AFB73224D0834BDC561
mcp_capabilities.py                              F7A81A637847EE0C0957C01A4D202713E9EE19DE1C32735600A7B67DD7EB55C3
mcp_registry.py                                  23E52076AB85AD9DE1C9D29A36C3086C439B47C93FCF2C48D8BC9365281BFB6F
tests/test_mcp_autoconnect.py                    29D1B5FD3A4CC3BDA32F11D9135124D3D8009B0E5041752EAD6904F70FA7D0CF
tests/test_mcp_capabilities.py                   67FBA7347F499F85B8CDDE3FD3ACA48A6DE08E64404ED3E160D8D42A6658B468
tests/test_mcp_registry.py                       9348CF2BD44F26E86E12C5F53EA93F750A91D821828397E40F428E0220DE7420
tests/test_mcp_connector.py                      331BC96B089D8D51B4117C832F2D0DE58352C4B251E6136FAF19FC8160F85165
tests/test_mcp_self_assembly_tools.py            D899B703C4A6784F39891C336B7A3B3C36A574624B51DD5C0ECE2383BD57EF09
tools.py                                        9DED26E227315D1823FE204929F0CAFDCEA444E183DF93DF8358CD410AF604AA
web/index.html                                   6938AE66E1B2F27763552522148E26E5988DDFD8E7346CD30FC9E88C27160342
```
