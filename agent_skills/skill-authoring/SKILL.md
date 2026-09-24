---
name: skill-authoring
version: 1.0.0
description: 把可复用的工作流沉淀为用户技能，经草稿→展示→用户批准→激活的人工复核闸门写入技能库。
when_to_use: 用户确认要把某段成功的工作流"存成 skill"或代理识别出可复用模式需落库时
---

# 技能编写与入库（skill-authoring）

skill 是**可执行行为**，写入技能库后会被代理在未来的对话中加载执行，因此必须比 MCP 工具更谨慎。

## 流程（严格顺序）
1. 识别可复用模式：一段成功、通用、低风险的流程（非一次性任务）。
2. 起草：调用 `dev_skill_create(name, description, body)`，其中 body 是完整 SKILL.md 正文
   （含 frontmatter 之外的操作指引、触发条件、示例）。该调用把草稿写入
   `SKILLS_DIR/.pending/<name>/SKILL.md`，**不会立即生效**（reload 扫描排除 .pending）。
3. 展示：把 `dev_skill_create` 返回的正文**完整**呈现给用户，说明它会做什么、何时触发。
4. 用户明确同意 → 调用 `dev_skill_approve(name: <name>)`：草稿移到 `SKILLS_DIR/<name>/`
   并 reload，之后可被 `dev_use_skill` 加载。
5. 用户拒绝或犹豫 → `dev_skill_reject(name: <name>)` 丢弃草稿。

## 护栏（不可违反）
- 绝不自动激活：没有用户明确确认，绝不调用 `dev_skill_approve`。`dev_skill_create` 本身不激活。
- 不覆盖：已存在的活动技能不能经此流程覆盖；如需迭代，先 `dev_skill_reject` 旧草稿再新建。
- 正文要可审计：body 应写明触发条件、步骤、边界与风险，避免模糊指令（模糊指令易被误用）。
- 不写危险操作：技能正文不得包含未经确认的文件删除、外部写、凭证明文等；这些应留在人工流程。
- 区分两类入库：工具类能力缺口优先走 `mcp-tool-discovery`（MCP 连接器）；
  仅当缺口是"一段工作流 / 方法论"时才走本技能（skill 库）。
