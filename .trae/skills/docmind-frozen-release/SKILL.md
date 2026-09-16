---
name: "docmind-frozen-release"
description: "Builds, verifies, smoke-tests and commits a DocMind frozen (PyInstaller) release. Invoke when user asks to package/rebuild the distributable desktop app (打包/重建/分发版/发布)."
---

# DocMind 冻结版发布流程（rag-agent / Windows + PowerShell 5.1）

把一轮已完成的功能从源码推进为「可分发的第十 N 版」：构建 → 核对 → 冻结冒烟 → 文档 → 提交 → 记忆 → 恢复 dev。**所有命令在项目根 `d:\WorkBuddy\rag-agent` 下执行。**

## 环境约定（每个新 Shell 必做）

```powershell
$env:PATH = "C:\Users\<you>\.local\bin\MinGit\cmd;" + $env:PATH   # 用户名含单引号，必须双引号
```

- Python：`.\.venv\Scripts\python.exe`（3.13，无 pytest，测试用 unittest）
- 测试：`.\.venv\Scripts\python.exe -B -m unittest discover -s tests`
- `cmd /c` 被安全策略禁用；一律用纯 PowerShell / `Start-Process`
- 样例仓（godot_sample 等外部仓）写 `.git/index.lock` 被沙箱硬拦：**样例仓提交只能请用户在系统 PowerShell 手动做**，不要自己尝试

> **本机自动化环境的实操修正（2026-09-14 实测）**：本会话里 PowerShell 工具的输出捕获会失效（拿不到 stdout），且在 Bash 内嵌 `powershell`/`powershell.exe` 会被安全策略判为"从 Bash 调 PowerShell"而拦截。等效做法是 **Bash + 托管 venv Python** 复刻规范里的 PowerShell 步骤（停端口、起冻结 exe、轮询、curl 用 `curl --noproxy '*'`、提交信息用 Python 写无 BOM 文件再 `git commit -F`）。`git`/`netstat`/`tasklist` 等可直接在 Bash 跑。
> **`$TEMP` 大坑**：Git Bash 里 `$TEMP` 解析成 `/tmp`，而 Python 的 `os.environ['TEMP']` 是 `D:\Temp`，两者不一致。写提交信息/日志文件务必用 **Windows 绝对路径**（如 `D:/Temp/dm_commit.txt`）交给 `git commit -F`，否则报"找不到 /tmp/dm_commit.txt"。

## 阶段 0：发布前检查

1. 全量测试通过（基线以 `HANDOFF.md` 头部为准，当前 **387 tests**）。
2. 前端已 `npm run build`（cwd=`frontend`）并在浏览器实测过；`emptyOutDir:false` 会累积旧 hash，**构建前/后手动删除 `web/assets/` 上一版 `workbench-*` 旧文件**；vendor 三个分包哈希应保持不变（业务改动不该使 vendor 失效）。
3. GetDiagnostics 零问题；`git status` 明确本轮白名单（web/assets、dist 均 gitignore，不入库）。
4. **Agent 黄金题回归门**（打包含 agent/工具/提示词改动时必跑；纯前端或文档改动可跳）：
   ```powershell
   # 题库已入仓（golden/questions.json，8 题，expect 已用真实模型校准）；baseline 同目录
   $env:GOLDEN_QUESTIONS = "D:\WorkBuddy\rag-agent\golden\questions.json"   # 含 expect 字段
   $env:GOLDEN_BASELINE  = "D:\WorkBuddy\rag-agent\golden\results_baseline.jsonl"
   .\.venv\Scripts\python.exe ".trae\skills\agent-golden-eval\assets\gate.py"
   ```
   - 退出码 **0 = 通过或跳过**（无题库 / 服务不可达 / 无本地模型时自动 SKIP）；**1 = 检出回归**（pass→fail 或通过率下滑）→ **本版不得发布**，先修再重跑；2 = 执行失败。
   - 需要本地模型 + 已索引的「本仓库」代码：源码类题（G2/G3/G4/G6/G7）依赖 `search_code` 检索 DocMind 自身源码，必须在 `code_root` 指向本仓库且已 `ingest_code` 的 dev 服务上跑，否则退化读文件、判不过。推荐起一个**隔离 chroma** 的专用评测服务（不污染你 `:8000` 的游戏代码索引）：
     ```powershell
     $env:CHROMA_DIR = 'D:\Temp\docmind_chroma_eval'   # 复制自 .chroma 以保留知识库集合
     $env:CODE_ROOT  = 'D:\WorkBuddy\rag-agent'
     $env:DOCMIND_SERVER_ONLY = '1'; $env:DOCMIND_PORT = '8078'
     .\.venv\Scripts\python.exe desktop.py            # 起服务后 POST /api/ingest_code root=D:\WorkBuddy\rag-agent
     ```
     再设 `GOLDEN_BASE=http://127.0.0.1:8078` 跑门。**切勿对 :8000 直接 ingest**（会清空你的游戏代码索引）。`golden/results_baseline.jsonl` 即按此配置产出（8/8 通过）。
   - gate 会打印一行 `摘要（贴 BUILD.md）`，**原样记入 `DocMind_BUILD.md` 本轮「验证」小节**；若为 SKIP，也必须把跳过原因写进构建档案（不得留空、不得假装跑过）。
   - 规则打分逻辑本身由 `tests/test_agent_eval.py` 常驻单测守着——**即使本轮 SKIP，离线部分也已被全量测试覆盖**。

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

> **⚠️ 例外：:8000 是「用户自己正在用的实例」时，绝不要杀它。**
> 本机常态是用户挂着一个 DocMind 桌面实例在 :8000（它同时锁着 `dist/DocMind/DocMind.exe`）。
> 正确做法（第 17–19 次构建实际采用）：**不碰 :8000**，把产物打到临时目录再延后换入——
> ```powershell
> .\.venv\Scripts\python.exe -m PyInstaller docmind.spec --noconfirm --distpath D:\Temp\docmind_relNN
> ```
> 冒烟改用别的端口（`$env:DOCMIND_PORT = '8044'`），交付说明里写明
> 「用户关闭该实例后 `robocopy /MIR D:\Temp\docmind_relNN\DocMind dist\DocMind` 即完成，无需重打包」。
> 这样既不打断用户，也避免在锁定目录上做半截替换。**换入前务必确认目标 exe 未被占用。**

## 阶段 2：产物核对

- `dist\DocMind\DocMind.exe`：大小（约 18.5–19.4 MB）与 mtime=本轮构建时间；整包约 663 MB。
- **前端哈希一致性**：`web\` 与 `dist\DocMind\_internal\web\` 的 assets（5 个左右）+ `workbench.html` 逐个 SHA-256 比对，全 match。
- 卫生扫描 `dist\DocMind\`：无 `python*.exe`、无 `.env`、无 `.docmind_state.json`、无 `.chroma`（或仅空库 `chroma.sqlite3`，**绝不可含开发者已索引的代码向量**——`docmind.spec` 的 datas 不得写 `(".chroma", ".chroma")`，否则会烤进每版分发版，泄漏本地索引并徒增约 374 MB）；`MinGit\` 随包存在（约 89.5 MB）。

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
  1. `Stop-Process` 杀冻结进程，确认端口释放；
  2. 删除**冒烟写出的全部运行时态**（都不能随包分发，逐个复核为 0）：
     `_internal\.docmind_state.json`（本机 code_root 选择）、
     `_internal\.docmind_traces.jsonl`（+`.1`）、`_internal\.docmind_sessions\`、
     `_internal\.docmind_budget.json`、`_internal\.docmind\`（含 `gpu_state.json`）、
     `_internal\.chroma\`（走一遍问答就会自建空库）、以及 exe 同级可能出现的 `docmind_desktop.log`；
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

- **`/tmp` 在 Git Bash 里不可写**（第十九版）：`curl -o /tmp/x.bin` 直接 `exit 23` 且文件不存在，后续 `sha256sum` 报 "No such file"。**一律用 Windows 绝对路径**（`D:/Temp/...`）落盘——`$TEMP`（= `/tmp`）与 Python 的 `os.environ['TEMP']`（= `D:\Temp`）本来就不一致。
- **`curl -o /dev/null -w "%{size_download}"` 对这些 chunked 响应会报 0**（第十九版）：不能拿它判断字节数和"有没有内容"。要看字节就 `-o <file>` 后 `stat -c%s`，要比对就 SHA-256。
- **冻结冒烟后要清的运行时态清单会随功能增长**（第十九版起）：除 `.docmind_state.json` 外还有 `.docmind_traces.jsonl` / `.docmind_sessions/` / `.docmind_budget.json` / `.docmind/` / `.chroma/`；**新增功能若在 `BASE_DIR` 下落新文件，记得同步更新本清单**。
- **:8000 是用户实例时不要杀**（第十七版起）：打 `--distpath D:\Temp\docmind_relNN`、冒烟换端口、延后 `robocopy` 换入（详见阶段 1 的例外块）。
- SQLite 锁：不停 :8000 打包必失败（第八版；前提是那个 :8000 是**你**起的 dev 实例）。
- commit BOM：禁用 `Set-Content -Encoding UTF8` 写信息文件（第九版 e825b26）。
- 状态泄漏：冻结冒烟后忘删 `_internal/.docmind_state.json` 会把本机 code_root 分发给用户（第八版起纳入清单）。
- 前端旧 hash：`emptyOutDir:false` → 每次手动清 `web/assets/workbench-*`（第八版起）。
- 冻结冒烟 PATH 必须真的只剩 System32，否则无法证明随包 MinGit 自足。
- `.chroma` 索引库泄漏：若 `docmind.spec` 的 datas 写成 `(".chroma", ".chroma")`，开发者本机已索引的代码向量（约 377 MB）会被烤进每个分发版——既泄漏本地索引、又徒增约 374 MB 体积，且对用户自己的代码库毫无用处。第十六次重建已删除该项；冒烟后 `_internal/.chroma` 应为空（chromadb 首次启动自建），分发前务必确认包内无 `.chroma` 或仅含空库。
