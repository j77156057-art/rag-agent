# DocMind 开发台功能审计：真跑 vs 占位

> 审计日期：2026-09-11 ｜ 审计对象：`api.py` 开发台路由 + `regions.py` + `game_workbench.py` + `tools.py` dev_*
> 方法：逐一阅读 API 路由的底层实现（非只看签名）。

## 总结论

**没有空壳（无 `return {"ok":True,"data":[]}` 式占位）。开发台每一层都是真·落地的代码，但分两档深度：**

- **A 档（真·扎实，生产级逻辑）**：分区/Git/契约/Bug/资产/任务 这一整套"仓库级工作流"。
- **B 档（真·但浅，能跑但逻辑简陋）**：游戏内容流水线那几个（测试场景/成长模拟/性能/审批/影响面）。

---

## A 档：真·扎实（已验证为实实现）

### 分区 + Git 工作流（regions.py）—— 最强部分
| 函数 | 实现实质 |
|------|----------|
| `init_regions` | 给每个分区建目录 + **`git init` 独立仓库** + 写 README/导出桩 + 基线提交 ✅ |
| `propose_regions` | 真扫顶层目录，关键词命中 + 自定义模块建议（启发式）✅ |
| `verify_contracts` | 依赖存在性 + **环检测（拓扑排序）** + 导出文件存在性 ✅ |
| `commit_all` / `commit_region` | 真跑 `git add/commit`，写变更集 + 工作区快照（`.docmind_backups/`）✅ |
| `rollback_changeset` | 真跑 `git revert --no-edit`，防重复回滚 + 防脏树 ✅ |
| `list_changesets` / `list_regions` / `region_git_info` | 真读 jsonl / `git log` / `git diff --stat` ✅ |
| `_run_region_cmd` | 真在分区内执行 verify 命令 ✅ |

### 本地存储型（tools.py / game_workbench.py）
- **任务**：`list_tasks` / `upsert_task` — jsonl 文件存储 ✅
- **Bug**：`dev_capture_bug` / `dev_list_bugs` / `dev_update_bug` — 按 Bug 写 JSON 文件 ✅
- **资产**：`dev_asset_get` / `dev_asset_register` — `manifest.json` 注册表 ✅
- **待确认编辑**：`list_pending_edits` / `confirm_edit`（create_file/apply_edit + 失效保护）/ `reject_edit` — 带锁的内存暂存编辑 ✅
- **项目记忆**：`project_memory` — 读写 `DOCMIND_MEMORY.md` ✅
- **数据校验**：`validate_data`（JSON/YAML/CSV/TOML 解析校验）、`localization_check`（i18n key 差异）、`release_check`（组合 + .env 告警）— 真扫文件 ✅

---

## B 档：真·但浅（能跑，逻辑简陋）

| 函数 | 实际行为 | 评价 |
|------|----------|------|
| `impact_analysis` | **纯大小写不敏感子串 `query in file` grep** | 非语义检索，弱 ⚠️ |
| `generate_test_scene` | 写死模板 `{"steps":[{"action":"load"},{"action":"assert","condition":"no_error"}]}` | 只生成空壳测试 ⚠️ |
| `simulate_growth` | `base*(growth**(i-1))` 等比数列公式 | 玩具，无真实模拟 ⚠️ |
| `performance_sample` | 包一下 `playtest` 并标 `wall_time_seconds` | 无真实性能剖析 ⚠️ |
| `approval` | 往 jsonl **追加一行日志** | 无实际门禁/鉴权 ⚠️ |
| `asset_dependencies` | 正则扫 `asset/sprite/texture/sound` 等词 | 粗糙 ⚠️ |
| `playtest` | 真 `subprocess` 执行命令，但黑名单粗糙（rm -rf/git reset…） | 真执行但策略简陋 ⚠️ |
| `preview_resource` / `create_placeholder` | 取文件元信息 / 建空占位文件 | 琐碎 |

---

## 跨切面共性短板（即便"真"也受限）

1. **单用户 / 纯本地**：全部基于 jsonl / 本地 git，无数据库、无鉴权、无多用户协作。
2. **领域偏游戏/内容开发**：默认分区是 `assets/behaviors/levels/audio/values/bugs/ui/net`——通用软件工程用不上大半。
3. **无真实 CI**：`playtest`/`performance` 只是跑 shell 命令；`approval` 不实际拦截。
4. **verify 默认空转**：默认分区 `verify` 命令为空 → `region_verify` 直接返回"跳过"，契约校验只验导出文件存在性。
5. **Agent 行为毛刺**：开放问题会触发"防重复工具循环守卫"提前停（已实测）。

## 给用户的一句话

> 开发台的**「分区 + 每区独立 Git + 契约校验 + 变更集回滚 + Bug/资产/任务本地库」这套仓库级工作流是认真写出来的、能真用**；但**「测试场景/成长模拟/性能采样/审批/影响面」这几个游戏流水线辅助是凑数的浅实现**，当演示可以，当工具不够。整体定位是"本地单人的、面向游戏/Mod 项目的轻量研发脚手架"，不是通用的 ALM/DevOps 平台。
