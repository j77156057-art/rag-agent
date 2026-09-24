# MCP 自动连接 · 主流 provider 凭证获取流程（L0/L1/L2 选型依据）

> 来源：PM 调研（各官方文档 + 佐证页）。**未真机登录验证**，不确定处标「未核实」。
> 档位定义：**L0**=全自动填表取凭据（无人工）；**L1**=遇验证码/2FA/邮箱验证/授权同意时暂停、保持会话、用户点一下继续；**L2**=只能人工，仅给直达链接 + 指引。

按「需求频次 × 自动化可行性」排序：

| # | Provider | 凭证（env） | 页面 / 关键步骤 | 登录 | 验证码 | 2FA | 邮箱验证 | 付款 | 可达档位 | 官方预填深链 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | **GitHub** | `GITHUB_PERSONAL_ACCESS_TOKEN`（`github_pat_…`/`ghp_…`） | `https://github.com/settings/personal-access-tokens/new` → Token name/Expiration → Resource owner → Repository access → Permissions(Contents/Pull requests/Issues) → Generate → 复制(仅一次) | 是(已登录免) | 未核实(通常无) | 账号开启则需 | 否 | 否 | **L0**(已登录)；未登录=**L1**；组织级=**L1** | **有(文档化预填)**：`…/personal-access-tokens/new?name=…&description=…&target_name=…&expires_in=45&contents=read&pull_requests=write&issues=write`；classic：`…/tokens/new?scopes=repo,workflow&description=…` |
| 2 | **Figma** | `FIGMA_API_KEY`（`figd_…`） | `https://www.figma.com/settings` → Security → Personal access tokens → Generate new token → 名称+scopes+有效期 → Generate → 复制(仅一次) | 是 | 未核实 | 支持 TOTP | 否 | 否 | **L0**(已登录)；未登录=**L1** | 页面深链有，**无预填参数** |
| 3 | **Stripe** | `STRIPE_SECRET_KEY`（`sk_test_`/`sk_live_`；`rk_…`） | `https://dashboard.stripe.com/apikeys`（测试 `/test/apikeys`）→ Developers → API keys → Reveal；或 Restricted keys → Create → 权限矩阵 → Create → 复制(仅一次) | 是 | **创建 live 秘密键需邮件/短信验证码(必须人工)** | 可开启 | 注册时 | 否(测试键无需) | **测试键 L0**；**live 键 L1**；受限键 L0 | 页面深链有，**无预填** |
| 4 | **Notion** | `NOTION_TOKEN`/`NOTION_API_KEY`（`ntn_…`新 / `secret_…`旧） | `https://www.notion.so/my-integrations` → New integration → 名称+Workspace → Submit → 配置页 "Internal Integration Secret" → Show → Copy | 是 | 未核实 | 可开启 | 注册时 | 否 | **L1**(需 workspace owner；内容授权需逐页操作) | `…/my-integrations`（无预填） |
| 5 | **Slack** | `SLACK_BOT_TOKEN`(xoxb-)（搜索需 `SLACK_USER_TOKEN` xoxp-） | `https://api.slack.com/apps` → Create New App → From scratch → 命名+选 workspace → OAuth & Permissions → 加 Bot Token Scopes → Install to Workspace → Allow → 复制 Bot User OAuth Token | 是(且需在该 workspace) | 未核实 | 可开启 | 注册时 | 否 | **L1**(「Install→Allow」是 OAuth 授权同意，建议用户点) | `…/apps`（无预填）；可用 App Manifest 预填 scope 但不是 URL 预填 |
| 6 | **Brave Search** | `BRAVE_API_KEY`（`BSA…`，头 `X-Subscription-Token`） | `https://api-dashboard.search.brave.com/` → 注册(邮箱+密码) → **邮箱验证(点确认链接)** → 订阅计划(**免费版也需绑卡**) → API Keys → Add API Key → 复制 | 是 | 未核实 | 未核实 | **是** | **是(免费也需信用卡在档)** | **L1 偏 L2**(邮箱验证+绑卡属硬人工，绑卡不宜自动化) | **无**预填 |
| 7 | **Google Drive**（官方 remote MCP） | `OAUTH_CLIENT_ID`/`OAUTH_CLIENT_SECRET`（token 走 OAuth；Server `https://drivemcp.googleapis.com/mcp/v1`） | `https://console.cloud.google.com` → 建项目 → 启 Drive API + Drive MCP API → 配 OAuth 同意屏 → 建 OAuth 客户端(Web) → 取 Client ID/Secret → MCP 配置 → 浏览器 OAuth 登录 | 是 | 未核实 | 可开启 | 注册时 | 否(配额内免费) | **L2**(多页面控制台，易碎，L1 风险仍高) | 多个页面深链有，但**需先有 project**，无端到端预填 |

## 对选型的结论
1. **可做 L0 集合（首版主战场）**：**GitHub**（官方预填深链，已登录即全自动）、**Figma**、**Stripe 测试键** —— 表单简单、无邮箱验证/绑卡/外部授权。
2. **L1 集合（必须实现「暂停-续跑」会话保持）**：Notion（需 owner）、Slack（OAuth 授权同意）、Stripe live（邮件/短信验证码）。
3. **L2 集合（只给直达链接 + 指引，首版不做自动）**：Brave（邮箱验证 + 绑卡）、Google Drive（Cloud Console 多步）。**首版强烈建议不做这两个的自动** —— 绑卡与云控制台自动化合规/稳定性双差。
4. 普适前提：**L0/L1 都依赖用户已在浏览器 profile 里登录对应站点**；「首次注册账号」整体只能 L1/L2。登录页/注册页可能触发风控验证码。

## 未核实项（不编造）
- 各站是否在**已登录流程**中出现 CAPTCHA：未核实（文档未提及，通常出现在注册/风控时）。
- Figma scopes 完整取值、Brave 2FA 支持：未核实。
- Notion token 前缀 `ntn_`(新)/`secret_`(旧) 并存，MCP server 侧 env 名可能是 `NOTION_TOKEN` 或 `NOTION_API_KEY`，需按具体 server 定：未核实到唯一标准。
- 「直达深链」仅 **GitHub** 明确文档化支持**预填参数**，其余仅为页面深链（无预填）。
