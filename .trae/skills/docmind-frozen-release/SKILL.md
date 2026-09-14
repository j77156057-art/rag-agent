---
name: "docmind-frozen-release"
description: "Builds, verifies, smoke-tests and commits a DocMind frozen (PyInstaller) release. Invoke when user asks to package/rebuild the distributable desktop app (打包/重建/分发版/发布)."
---

# DocMind 冻结版发布流程（rag-agent / Windows + PowerShell 5.1）

把一轮已完成的功能从源码推进为「可分发的第十 N 版」：构建 → 核对 → 冻结冒烟 → 文档 → 提交 → 记忆 → 恢复 dev。**所有命令在项目根 `d:\WorkBuddy\rag-agent` 下执行。**

## 环境约定（每个新 Shell 必做）

```powershell
$env:PATH = "C:\Users\h'h'h\.local\bin\MinGit\cmd;" + $env:PATH   # 用户名含单引号，必须双引号
```

- Python：`.\.venv\Scripts\python.exe`（3.13，无 pytest，测试用 unittest）
- 测试：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests`
- `cmd /c` 被安全策略禁用；一律用纯 PowerShell / `Start-Process`
- 样例仓（godot_sample 等外部仓）写 `.git/index.lock` 被沙箱硬拦：**样例仓提交只能请用户在系统 PowerShell 手动做**，不要自己尝试

> **本机自动化环境的实操修正（2026-09-14 实测）**：本会话里 PowerShell 工具的输出捕获会失效（拿不到 stdout），且在 Bash 内嵌 `powershell`/`powershell.exe` 会被安全策略判为"从 Bash 调 PowerShell"而拦截。等效做法是 **Bash + 托管 venv Python** 复刻规范里的 PowerShell 步骤（停端口、起冻结 exe、轮询、curl 用 `curl --noproxy '*'`、提交信息用 Python 写无 BOM 文件再 `git commit -F`）。`git`/`netstat`/`tasklist` 等可直接在 Bash 跑。
> **`$TEMP` 大坑**：Git Bash 里 `$TEMP` 解析成 `/tmp`，而 Python 的 `os.environ['TEMP']` 是 `D:\Temp`，两者不一致。写提交信息/日志文件务必用 **Windows 绝对路径**（如 `D:/Temp/dm_commit.txt`）交给 `git commit -F`，否则报"找不到 /tmp/dm_commit.txt"。

## 阶段 0：发布前检查

1. 全量测试通过（当前基线 **49 tests，4 skipped**；4 skip 为无 git 环境用例，属正常）。
2. 前端已 `npm run build`（cwd=`frontend`）并在浏览器实测过；`emptyOutDir:false` 会累积旧 hash，**构建前/后手动删除 `web/assets/` 上一版 `workbench-*` 旧文件**；vendor 三个分包哈希应保持不变（业务改动不该使 vendor 失效）。
3. GetDiagnostics 零问题；`git status` 明确本轮白名单（web/assets、dist 均 gitignore，不入库）。

## 阶段 1：停服务 → PyInstaller（SQLite 锁是头号坑）

```powershell
# 找到并杀掉占用 :8000 的进程
$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conns) { $conns | Select-Object -Expand OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force } }
Start-Sleep -Seconds 2   # 确认端口已释放再继续
```

- **必须确认 :8000 释放**：源 `.chroma` 的 SQLite 被占用时 COLLECT 会在清空旧 dist 后中途失败（症状：exe 时间戳不变、web/assets 被清空）。
- 构建：`.\.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm --log-level WARN`
- 用 `$LASTEXITCODE` 判定（约 50–60s），必须为 0。

## 阶段 2：产物核对

- `dist\DocMind\DocMind.exe`：大小（约 18.5–19.4 MB）与 mtime=本轮构建时间；整包约 663 MB。
- **前端哈希一致性**：`web\` 与 `dist\DocMind\_internal\web\` 的 assets（5 个左右）+ `workbench.html` 逐个 SHA-256 比对，全 match。
- 卫生扫描 `dist\DocMind\`：无 `python*.exe`、无 `.env`、无 `.docmind_state.json`；`MinGit\` 随包存在（约 89.5 MB）。

## 阶段 3：冻结态冷启动冒烟（关键：最小 PATH）

目的：证明分发包在没装 Git/Python 的干净机器上能跑。

```powershell
# 用仅含 System32 的 PATH + SERVER_ONLY 启动冻结 exe（后台、重定向日志）
$env:PATH = 'C:\Windows\System32'
$env:DOCMIND_SERVER_ONLY = '1'
$p = Start-Process -FilePath '.\dist\DocMind\DocMind.exe' -PassThru `
     -RedirectStandardOutput $env:TEMP\dm_out.log -RedirectStandardError $env:TEMP\dm_err.log
```

- 轮询 `http://127.0.0.1:8000/workbench/` 直到 200（通常 1–2s）。windowed exe 日志 0 字节是正常的，**以 HTTP 证据为准**。
- 必查端点：
  - `GET /api/config`：`build_time` 必须是本轮时间（确认跑的是新版）；
  - code_root 首启应为空 → 新端点预期 **HTTP 400**；
  - `GET /workbench/` 200、新业务 JS 200 且字节数与源一致；
  - **索引必须用 multipart 表单**（`curl.exe -F "root=D:\WorkBuddy\godot_sample" .../api/ingest_code`），**字段是 form 的 `root`，不是 JSON**；样例基线 18 切片；
  - 功能端点回归（如 symbol-map=7 文件/18 符号、relation-graph=7 节点/3 继承边——数值随功能演进而变，以当轮设计为准）。
- **收尾三件事，缺一不可**：
  1. `Stop-Process` 杀冻结进程，确认 :8000 释放；
  2. 删除 `dist\DocMind\_internal\.docmind_state.json`（冒烟写入的本机选择，绝不能随包分发）并复核不存在；
  3. 清理临时日志/环境变量。

## 阶段 4：更新构建文档

编辑 `DocMind_BUILD.md`（不要新建文件）：
- 顶部「最新构建」指引行、产物段的当前构建时间/上一版、整包体积；
- 按既有格式在第九/十版记录上方插入新版章节：改动（后端/前端/范围决策）+ 验证（单测数、dev 实测、冻结冒烟数值、哈希一致性、状态文件已删）。

## 阶段 5：白名单提交（BOM 是二号坑）

```powershell
git add <逐个白名单文件>          # 禁止 git add -A
git diff --cached --name-only     # 与清单逐一核对
git status --porcelain            # 确认无遗漏/误入
```

提交信息必须写**无 BOM UTF-8 文件**再 `-F`（PS5.1 的 `Set-Content -Encoding UTF8` 带 BOM，曾污染 commit e825b26 标题）：

```powershell
$tmp = Join-Path $env:TEMP 'dm_commit.txt'
[System.IO.File]::WriteAllText($tmp, ($msg -replace "`r`n","`n") + "`n",
    (New-Object System.Text.UTF8Encoding($false)))
git commit -F $tmp; Remove-Item -Force $tmp
```

提交后字节级复验标题无 BOM：`git cat-file commit HEAD` 经 `Start-Process -RedirectStandardOutput` 落盘，读原始字节，定位 `\n\n` 后首字节应是标题首字母（如 `feat` = 66 65 61 74），不能是 `EF BB BF`。记录短哈希。

## 阶段 6：记忆 + 恢复 dev 实例

1. 在项目记忆 `project_memory.md` 追加：新版次、commit 哈希、本版设计要点/新端点、冒烟数值、新踩的坑。
2. 重启 dev：`Start-Process -FilePath '.\run_desktop.bat' -WindowStyle Minimized`，轮询 :8000；code_root 持久化会自动恢复（`.docmind_state.json` + 向量库各自持久化，无需重索引），用一个当轮新端点确认恢复正常。
3. 向用户汇报：构建时间/大小、冒烟表、commit 哈希与文件数、文档与记忆已更新、dev 已恢复；提醒桌面快捷方式 DocMind.lnk 即指向新版 exe。

## 历史教训速查

- SQLite 锁：不停 :8000 打包必失败（第八版）。
- commit BOM：禁用 `Set-Content -Encoding UTF8` 写信息文件（第九版 e825b26）。
- 状态泄漏：冻结冒烟后忘删 `_internal/.docmind_state.json` 会把本机 code_root 分发给用户（第八版起纳入清单）。
- 前端旧 hash：`emptyOutDir:false` → 每次手动清 `web/assets/workbench-*`（第八版起）。
- 冻结冒烟 PATH 必须真的只剩 System32，否则无法证明随包 MinGit 自足。
