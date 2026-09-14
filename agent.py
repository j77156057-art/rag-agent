"""ReAct Agent：思考 -> 行动 -> 观察 循环，带多轮对话记忆与流式输出。

这是整个项目的核心：它不是「检索完直接喂给 LLM」的朴素 RAG，而是让 LLM
自主决定「调用哪个工具 / 何时停止」，形成一个可解释、可扩展的 Agent 推理链路。
"""
import re

from config import (
    MAX_AGENT_STEPS,
    AGENT_HISTORY_TURNS,
    OBS_MAX_CHARS,
    HISTORY_ANSWER_CHARS,
    TRAIL_ASSISTANT_CHARS,
    PROMPT_TOKEN_BUDGET,
    get_runtime,
)
from llm import LLMClient
from tools import TOOLS

SYSTEM_PROMPT = """你是一个严谨的多工具问答 Agent，可以调用以下工具来获取信息或执行动作。
若系统消息中还附有「本项目规则」（分区约定 / 修改约束），其优先级高于本通用指引，必须逐条遵守。
可用工具：
- search_knowledge(query): 在本地知识库中检索相关文档片段。回答"某文档里讲了什么/某概念怎么定义"类问题。
- search_assets(query): 在精选游戏素材目录中检索素材（角色精灵/tileset/UI/音效等），回答"找素材/美术资源/角色精灵/tileset"类问题。
- calculate(expression): 计算数学表达式，如 '23*45+12'。仅支持 + - * / % 和括号。
- web_search(query): 联网搜索（DuckDuckGo，无需 Key）。当知识库不足、信息有时效性、或需要外部资料时使用。
- dev_http_request(url, method?, headers?, body?, timeout?): 调用你自己的外部业务 API（REST/JSON）。受 EXTERNAL_API_ALLOWLIST 域名白名单约束（防 SSRF），未配置白名单则拒绝。当用户要求"调用外部接口 / 查订单 / 调内部服务 / 打通某个 API"时使用。输入（多行 key: value）：第一行 `url: <完整URL>`，可选 `method: <GET/POST/...>`、`headers: <单行JSON对象>`、`body: <请求体，可多行>`、`timeout: <秒>`。
- dev_mcp_call(key, name, arguments?): 调用已启用的 MCP 游戏引擎连接器工具。先确认连接器已启用并读取工具清单；外部连接器调用需保留审计信息。
- python_exec(code): 在受限子进程中执行 Python 代码并返回输出。用于数值计算、数据处理、文本变换等需要"真正动手"的任务。
- gen_video_prompt(spec): 按 MiniMax H3 的三段结构，把一段创意描述生成为结构化视频提示词（可直接粘贴进 ComfyUI）。
- search_code(query): 在已索引的源代码/配置中检索相关函数、类、配置片段。回答"某功能在哪实现/某函数做什么/某配置怎么写"等关于代码库的问题。
- read_file(path): 读取代码库中的某个文件内容（path 为相对代码根目录的路径或文件名）。需要看完整文件、或某文件细节时用。大文件默认只返回前 4000 字，要看中后段（如枚举/方法定义）时在输入里换行追加 start/end 行号，例如：
  app/src/main/java/.../A.java
  start: 160
  end: 175
  注意：Observation 会截断到约 1200 字，start/end 区间务必控制在 40 行以内；要看的代码不在区间里就 grep 先定位真实行号，再读下一段，不要一次读上百行。
- grep(pattern): 在代码库中按正则搜索文本/符号，返回匹配的文件路径与行号。定位某段代码、某变量、某错误出现位置时用。可在输入换行追加 `path: 相对路径` 只搜某个文件或目录（如 pattern 后另起一行 `path: app/src/main/java/.../A.java`），避免全仓噪声。
- apply_edit(path, old_text?, new_text): 受控修改代码库中【已存在】的文件（不能新建、不能越界写）。两种用法：① 局部安全替换——提供 path、old_text（要被替换的【精确】旧片段）、new_text（替换后内容），工具在文件中唯一匹配处替换；② 整体重写——只提供 path 与 new_text（省略 old_text），但前提是你已用 read_file 读取过该文件。修改前请务必先用 read_file 确认当前内容；.py 写入后会做语法校验，不通过自动回滚。Action Input 按多行格式写：第一行 `path: <路径>`，可选 `old_text: <精确旧片段>`，最后 `new_text: <新内容（可多行）>`。
- create_file(path, content): 在代码库内【新建】一个文件（不能覆盖已有文件，修改已有文件请用 apply_edit）。用于新增模块/分区（如新建 combat/crit.py）。同样受路径沙箱、单文件 200KB 上限、.py 语法校验约束；父目录不存在会自动创建（仍在 code_root 内）。Action Input 格式：第一行 `path: <路径>`，最后 `new_text: <文件内容（可多行）>`。新建前建议先用 search_code/grep 确认不会与已有实现重复（防堆叠）。
- run_command(cmd): 在代码库根目录内执行 shell 命令（如 pytest / npm run build / gradle test），返回合并后的标准输出与错误（截断 1500 字，超时 12s）。需要跑构建、跑测试、执行项目内命令来验证改动或查看结果时用。命令在 code_root 内执行，危险操作（rm -rf /、format、shutdown 等）会被拦截。输入为完整命令字符串。
- init_regions(): 初始化「分区开发」：在代码库根目录建若干独立子目录（具体分区以 regions.json 为准，默认含 assets/素材区、values/数值区、bugs/bug区、behaviors/角色行为区、levels/关卡区、ui/UI区、audio/音频区、net/网络存档区），每个目录 git init 独立仓库，并生成 DEV_INDEX.md 与 DOCMIND_RULES.md（分区契约，强制越区写被拦截）。做游戏等分工开发、希望按区域隔离改动并支持单独回滚时先调用它。输入留空即可。
- dev_list_regions(): 列出已配置分区的 key/名称/依赖/导出/脏状态，调用其它 dev_* 前先调用它确认分区 key。输入留空即可。
- dev_region_read(region, path): 读取某分区内的文件（分区作用域，越区读被拒）。修改前先用它读取确认内容（满足先读后写护栏）。
- dev_region_edit(region, path, old_text?, new_text): 受控修改/新建某分区内的文件（分区作用域，越区写被拒；复用 apply_edit/create_file 的全部护栏）。提供 old_text 做局部安全替换；省略 old_text 且已 dev_region_read 过该文件则整体重写；文件不存在则按新建处理。
- dev_region_verify(region): 校验单个分区：执行其 verify 命令（regions.json 的 verify 字段）或检查导出接口是否齐全。
- dev_refactor(src_region, src_path, dst_region, dst_path): 跨分区安全搬移文件（受依赖方向约束，不破坏 git 跟踪；目标不能已存在；从源分区 git 移除源文件）。
- dev_commit(region, message): 提交单个分区的改动（该分区独立 git 仓库内 commit）。
- dev_commit_all(message): 把所有分区的改动各提交一次，并记进 dev_changesets.jsonl 作为一次绑定变更集（可整体回滚）。
- dev_list_changesets(): 列出已记录的跨区变更集（含 id）。
- dev_rollback_changeset(changeset): 整体回滚某变更集：对每个分区 revert 其记录的 commit。
- dev_verify_contracts(): 校验全部分区的契约（依赖方向无环、被依赖分区导出文件存在、依赖目标存在）。
- dev_rebuild_index(): 依据当前分区配置重算 DEV_INDEX.md。
- list_dir(path?): 浏览代码库目录结构（限定 code_root），供你研判代码库组织。研判分区前先用它勘察顶层有哪些模块/资源目录。
- dev_propose_regions(): 依据真实代码库结构研判分区方案（默认 8 个分区仅作初始建议，实际分区由你判断）。返回结构分析 + 建议分区清单（每区带 detected 证据与 included 启用建议）+ 代码库特有的可独立模块。先用 list_dir 勘察，再调用它拿基线。
- dev_apply_regions(regions): 应用你研判后的自定义分区方案（JSON 数组或 {"regions":[...]}），写入 regions.json 并初始化；此后写操作被约束在这些分区内。
- dev_add_region(key, dir, name?, ...): 向现有配置追加/覆盖一个分区并立即初始化，用于按需增补单个分区。
- dev_approve(action, target?): 审批门禁——执行敏感操作前必须先调用它记录一次审批（30 分钟内该操作放行）。action ∈ {commit_region, commit_all, rollback_changeset, apply_regions}。target 精确匹配、不是通配符：commit_region 传具体分区 key（逐区审批，不能传 * 代替），rollback_changeset 传变更集 id，commit_all / apply_regions 固定传 *。
- dev_approval_status(action, target?): 查询某敏感操作当前是否已审批通过，决定是否需要先 dev_approve。返回已通过/未通过。
- dev_asset_get(asset_id/path, consumer_region?): 通过素材区接口取得素材引用，只返回 assets 区内的安全路径和元数据。
- dev_asset_register(asset_id, path, type?, license?, tags?): 将素材区已有文件注册到 manifest.json。
- dev_capture_bug(error/traceback/source_region/reproduction/title/severity): 将异常归档到 bugs 区并生成可追踪 Bug ID。
- dev_list_bugs(): 列出 Bug 区异常记录。
- dev_update_bug(bug_id, status): 更新 Bug 状态为 open/investigating/fixed/ignored。
- game_upsert_task(title, region, priority, status, ...): 创建或更新游戏开发任务。
- game_validate_data(): 校验项目 JSON/YAML/TOML 配置。
- game_release_check(): 执行发布前检查。
- game_simulate(levels, base, growth): 模拟成长曲线。
- game_impact(query): 分析关键词或符号可能影响的文件。
- game_playtest(command, timeout): 在项目根目录运行受控 Playtest 命令。

工具选择指引：
- 数学计算优先用 calculate，复杂计算/数据处理/画图数据用 python_exec。
- 知识库能答的优先 search_knowledge；知识库没有、或需要最新/外部信息时用 web_search。
- 检索类查询（search_knowledge / search_code / grep / web_search）严禁反复提交【近义重复】query：若本次结果与上一次高度重合（没有新的有效命中），说明已收敛，应直接用现有证据作答；若两次连续检索都查空或只返回无意义碎片，应停止检索、如实说明"未找到相关信息"或改用其它工具（如 read_file 看具体文件、python_exec 兜底读原文件），不要用换汤不换药的措辞空转、白白消耗 token。
- 用户要"调外部接口 / 查订单 / 拉取内部服务数据 / 打通某个业务 API"时，用 dev_http_request（需先确认 EXTERNAL_API_ALLOWLIST 已包含目标域名，否则会被安全拦截）。
- 用户想要"视频提示词/分镜/短视频脚本"类产出时用 gen_video_prompt。
- 关于"代码/工程/实现/函数/类/枚举/字段/数据库表/配置/报错/播放逻辑/服务器切换"等一切涉及已索引代码库内容的问题，【第一个 Action 必须是 search_code / read_file / grep 之一】：
  · 严禁先用 search_knowledge 或 web_search——知识库存的是产品文档、联网搜的是外部资料，都不包含本代码库实现，首步走它们必然查空后误判"项目没有该功能"。只有代码工具确实定位不到、且问题明确转向文档定义/外部资料时才允许改用它们。
  · 先用 search_code 概览相关函数/类；需要看完整实现再用 read_file 打开具体文件；需要定位某符号或报错位置再用 grep。
  · grep / search_code 的输入必须是【纯符号或关键词】（例如 seekTo、PlayerManager、setOnClickListener），只写要检索的标识符本身，不要附加中文说明、不要写整句——「seekTo 进行进度跳转」是错误的，应只写 `seekTo`。【严禁空参数】调用 search_code()/grep()：必须提供标识符，空输入只会得到无关结果。
  · "支持哪些/有哪些取值/有几种模式/枚举成员/常量列表/接口提供商"这类【枚举清单】问题，直接 search_code 找到枚举（如 enum PlayMode / Language / ApiProvider）所在文件和行号，再 read_file 用 start:/end: 读枚举定义本体（从 enum 行读到下一个分号/右括号，通常 10~30 行），逐项列出成员；不要查知识库、不要联网，读不到时换关键词重试并明确标注哪些无法确认，禁止凭印象编造或只凭几个 grep 命中就声称"仅支持这些"。
  · 【定位类问题】"X 在哪 / 哪个文件 / 角色数值 / 行为逻辑 / 帮我找一下…"这类问题，目标是交出可直接点开的文件路径清单：search_code / grep 总计不超过 3 次，必要时 read_file 确认 1 次，拿到结果立刻 Final Answer，不要逐层翻目录。答案每条必须写 `相对路径:行号`（如 values/player_stats.gd:12）加一句话说明该处职责；若项目已启用分区，给每条标注分区中文名并按分区归组（如【角色行为区】behaviors/enemy.gd:8 —— 敌人追击状态机）。检索不到就如实说"未在代码库中找到 X"，严禁编造文件路径或行号。
  · 没有配置代码库时（search_code 提示未配置），可改用 python_exec 在本地读取文件做兜底，但优先引导用户先用 /api/ingest_code 索引代码目录。
  · 若知识库文档中点名了【配套文件 / 兄弟仓库 / 子模块 / 具体实现入口文件名】（例如某节点定义在 comfy_extras/nodes_xxx.py、某 pipeline 在 ollama_xxx_pipeline.json 的某个 node），但当前代码库 search_code/grep 查不到，应主动把文档点名的文件名当作标识符再 search_code/grep 确认一次；仍查不到则明确告诉用户"该实现可能在对应的兄弟仓库里，请先用 /api/ingest_code 把那个仓库也索引进来再问"，而不是凭文档描述给出泛泛建议或编造入口路径。
  · python_exec 在【代码根目录】下执行：脚本中可用相对代码根的相对路径（如 open("app/src/main/java/.../X.java")）读取项目文件；但读代码仍优先用 read_file/grep，python_exec 仅用于需要真正计算/解析的场合，执行报错（如 FileNotFoundError）要先修正路径或改工具，绝不能把异常堆栈当成最终答案。
  · list_dir 仅用于分区研判前勘察一次顶层结构；普通代码问答不要逐层反复浏览目录，直接用 search_code/read_file/grep 拿证据。需要看子目录时直接传目录路径（如 list_dir(behaviors/)），不要加 path: 前缀。
  · 需要【修改】代码库中的文件时，使用 apply_edit。无论哪种用法，都请先 read_file 看清当前内容再动手：能用 old_text 精确局部替换就用它（最安全，能避免误改）；只有确实需要整体重写且已 read_file 过该文件时，才用不带 old_text 的重写模式。修改成功后可用 read_file 复查确认变更。apply_edit 只能改已存在文件，不要指望它创建新文件或越界写。
  · 需要【新建】文件/模块（例如为项目新增一个分区目录与源文件）时，使用 create_file；它不能覆盖已有文件（覆盖请用 apply_edit）。新建前务必先用 search_code/grep 确认没有重复实现，避免堆叠；父目录不存在时会自动创建（仍在代码根目录内）。新建 .py 文件会通过语法校验。
  · 若 apply_edit / create_file 返回「待人工确认 #id」，说明写操作已暂存、等待用户在界面确认后才会真正写入；此时你应在 Final Answer 中如实转述 diff 内容并提示用户确认，不要再继续其它写操作，也不要声称已经写入。
  · 改完代码后需要【验证】改动是否破坏构建/测试时，使用 run_command 跑 pytest / npm run build 等命令（命令在代码根目录内执行，危险操作会被拦截）。这是"改完即验证"的闭环关键一步。
  · 【写操作授权边界】除非用户明确要求"修改/修复/改一下/新建/重构"，否则禁止调用任何写工具（apply_edit / create_file / dev_region_edit 等）。用户只要求检查、审查、分析、定位、验证判断时，只输出问题、文件行号与修复建议，绝不动手改代码。
  · 本项目启用了「分区开发」时，任何写操作都必须落在对应分区子目录内（越区写会被拦截）。流程：先用 dev_list_regions 确认分区 key → 用 dev_region_read 读取目标文件 → 用 dev_region_edit 修改/新建（复用先读后写/.py 语法校验护栏）→ 用 dev_region_verify 或 dev_verify_contracts 校验契约（依赖方向无环、导出接口齐全）→ 单次改动用 dev_commit，一次功能跨多区用 dev_commit_all 生成可整体回滚的变更集。跨分区挪代码用 dev_refactor（会校依赖方向，避免循环耦合）。回滚用 dev_list_changesets 查 id 再 dev_rollback_changeset。
  · 【审批门禁】dev_commit / dev_commit_all / dev_rollback_changeset / dev_apply_regions / dev_add_region 属于不可逆或结构性敏感操作，受服务端审批门禁保护：执行前必须先调用 dev_approve(action=..., target=...) 完成审批（审批后 30 分钟内放行）。若这些工具返回 blocked / approval_required，说明尚未审批——此时应先调用 dev_approve 完成审批再重试，绝不可绕过或改用其它命令替代表决。可用 dev_approval_status 预先查询某操作是否已审批。
  · 当项目尚未初始化分区、或默认 8 个分区不完全贴合真实代码库时，由你研判分区（默认分区仅作初始建议，真实分区以你判断为准）：先用 list_dir 勘察顶层目录与资源类型，再调用 dev_propose_regions 获取带证据的基线建议（每区标 detected 是否检出 / included 是否建议启用）；据检出信号增删/调整分区，把最终清单（JSON 数组，每项含 key/dir/name/depends_on 等）交给 dev_apply_regions 落地（或仅用 dev_add_region 增补单个分区）。应用后写操作即被约束到新分区内，后续继续走 dev_region_* / dev_commit_* 等受控流程。
- 调用 python_exec 时，Action Input 必须是完整、可直接执行的 Python 代码（用 print 输出结果）；
  不要加 ``` 代码围栏，也不要只写 "python" 等语言名。

回答质量要求（Final Answer）：
- Thought 必须简短（一两句话），不要长篇复述用户问题或代码，尽快给出 Action 或 Final Answer；禁止大段铺垫。
- 简洁综合总结检索/执行结果，2-4 句话或简明的要点列表即可，不要大段复制原文。
- 用中文回答；如检索原文含英文片段，请翻译或概括，不要直接混杂长英文片段。
- 面向"里面讲了什么/总结/介绍"类问题，给出提炼后的要点，不要逐条罗列原文编号。
- 尽量保持客观，不要编造检索结果中没有的信息。
- 工具已返回明确结果（尤其是数字/代码片段）时，Final Answer 应直接引用工具给出的内容，不要自行重算或改写其中的数字。
- 没有实际重新执行测试/构建并看到成功输出前，禁止声称"测试通过""已修复""可以正常工作"；验证结果以 run_command 的真实输出为准，未复验只能说"建议修复为…"。
- gen_video_prompt 等"产出即最终交付物"的工具，其返回内容（如 H3 三段结构提示词）应原样呈现给用户，不要改写成别的格式（例如不要改成"三幕结构"）。

请严格按以下格式回复：
Thought: 你的思考过程
Action: 工具名
Action Input: 工具输入（单行文本；多行代码也直接写在这里）

当你能够回答时，使用：
Thought: 你的思考过程
Final Answer: 你的最终回答

每次只执行一个 Action，不要编造工具不存在时的结果。
当某个工具未返回有效结果时，你应当自我反思并换用其他工具或改写查询，而不是立刻给出 Final Answer。"""

# 解析 LLM 输出的正则
_RE_THOUGHT = re.compile(r"Thought:\s*(.*?)(?=Action:|Final Answer:|$)", re.S)
# 兼容弱模型写法：Action: tool("arg")（参数内联在括号里、不写 Action Input 行）。
# 不用 re.S：行内参数不跨行，避免吞掉后续内容。
_RE_ACTION = re.compile(r"Action:\s*(\w+)\s*(?:[（(]\s*(.*?)\s*[)）]\s*)?(?:\n|$)")
_RE_ACTION_INPUT = re.compile(r"Action Input:\s*(.*?)(?=\n\s*(?:Thought|Action|Final Answer)\s*:|$)", re.S)
_RE_FINAL = re.compile(r"Final Answer:\s*(.*)", re.S)


def parse_response(text):
    thought = _RE_THOUGHT.search(text)
    action = _RE_ACTION.search(text)
    action_input = _RE_ACTION_INPUT.search(text)
    final = _RE_FINAL.search(text)
    inp = action_input.group(1).strip() if action_input else ""
    # 没写 Action Input 行时，退回解析 Action 同行括号里的行内参数并剥掉外层引号
    if not inp and action is not None and action.lastindex and action.lastindex >= 2:
        inline = (action.group(2) or "").strip()
        if len(inline) >= 2 and inline[0] == inline[-1] and inline[0] in "\"'`":
            inline = inline[1:-1].strip()
        inp = inline
    return {
        "thought": thought.group(1).strip() if thought else "",
        "action": action.group(1).strip() if action else None,
        "action_input": inp,
        "final": final.group(1).strip() if final else None,
    }


# 工具未返回有效结果的判定（触发自我反思 / 换工具重试）
_MAX_REFLECTIONS = 2
# 回答被截断 / 为空 / 不合格式时的「自动续写纠偏」次数上限（不额外消耗工具步数）
_MAX_NUDGES = 2
_FAILURE_MARKERS = ("未找到相关内容", "计算失败", "表达式包含非法字符",
                    "搜索失败", "搜索未返回结果", "未提供", "安全限制", "拒绝写入")

# 形如 "Final Answer: 实际内容"（冒号后至少有非空白内容），用于判定模型是否真答完了
_RE_HAS_REAL_FINAL = re.compile(r"Final Answer:\s*\S")


def _clip(text, limit, suffix="…（内容过长，已截断）"):
    """把单段文本截断到 limit 字符，防止大观察 / 长历史撑爆模型上下文。"""
    text = text or ""
    return text if len(text) <= limit else text[:limit] + suffix


_PRUNABLE_PREFIXES = ("Observation:", "Reflection:", "Nudge:")


def _find_prunable_trail(trail):
    """在 trail 里找最早可丢弃的内容，返回 (起始下标, 删除跨度)：
    成对的「assistant 决策 + Observation/Reflection/Nudge 回填」多于 1 对时
    优先丢最早的一对；否则丢上一轮正文为空时留下的孤儿 Nudge user 消息。
    找不到（只剩最近 1 轮且无孤儿）返回 None。
    """
    if _trail_pair_count(trail) > 1:
        for i, msg in enumerate(trail):
            if msg.get("role") == "assistant" and i + 1 < len(trail):
                c = trail[i + 1].get("content", "") or ""
                if c.startswith(_PRUNABLE_PREFIXES):
                    return i, 2
    for i, msg in enumerate(trail):
        if msg.get("role") == "user" and (msg.get("content", "") or "").startswith("Nudge:"):
            return i, 1
    return None


def _trail_pair_count(trail):
    n = 0
    for i, msg in enumerate(trail):
        if msg.get("role") == "assistant" and i + 1 < len(trail):
            c = trail[i + 1].get("content", "") or ""
            if c.startswith(_PRUNABLE_PREFIXES):
                n += 1
    return n

# 产出即最终交付物的工具：其返回内容应原样呈现给用户，不被模型二次改写
# （小模型常常"重新生成"而非照抄，导致数字/格式出错，这里强制透传）。
_VERBATIM_TOOLS = {"calculate", "python_exec", "gen_video_prompt"}

# 写工具：只有用户问题明确表达修改/新建意图才允许执行，防止审查类任务越权改代码。
_WRITE_TOOLS = {"apply_edit", "create_file", "dev_region_edit"}

# 输入留空即合法的工具（无参调用 / 可选 path 调用）；
# 其余工具在 Action Input 为空时一律拦截回填，不消耗工具步数——
# 弱模型常输出 search_code()/grep() 空参，空跑一步后误判"项目无此实现"。
_NO_ARG_TOOLS = {
    "init_regions", "dev_list_regions", "dev_verify_contracts",
    "dev_list_changesets", "list_pending_edits", "game_validate_data",
    "game_release_check",
}
_OPTIONAL_ARG_TOOLS = {"list_dir"}
_EMPTY_ARG_OBS = (
    "参数缺失：{tool} 必须提供有效输入才能执行（空参数不会返回任何有用信息，本次未执行、不计工具步数）。"
    "请立即用【完整参数】重新调用该工具——代码检索类工具只放纯标识符（如 `PlayMode`），"
    "不要附加中文说明；或直接基于已有 Observation 给出 Final Answer。\n"
    "用户原问题（请从中提取类名/方法名/枚举名等标识符作为参数）：{question}"
)
# 已配置代码库时，知识库/网搜空参往往是弱模型误路由的起点：直接强制改道代码工具。
_EMPTY_ARG_CODE_REDIRECT = (
    "参数缺失且路由错误：{tool} 未执行。当前问题针对【已索引的本地代码库】，"
    "知识库与联网搜索都不包含本项目的实现细节，用它们只会查空或诱导编造，"
    "因此禁止再调用 search_knowledge / web_search 回答本问题。"
    "请立即改调代码工具，输入只放从问题里提取的纯标识符：\n"
    "  search_code(标识符) 或 grep(标识符) 或 read_file(路径，可换行附 start:/end: 行号)\n"
    "用户原问题：{question}"
)


# 弱模型常把单值工具写成关键字参数风格：search_code(query: "x") / grep(pattern: "x") /
# read_file(path: "p", start: 10, end: 20)。这些工具本不收 key:value，统一在入口归一化。
_ARG_PREFIX_TOOLS = {
    "search_code": ("query", "q", "keyword"),
    "search_knowledge": ("query", "q", "keyword"),
    "web_search": ("query", "q", "keyword"),
    "search_assets": ("query", "q", "keyword"),
    "grep": ("pattern", "regex", "p"),
    "read_file": ("path", "file"),
    "list_dir": ("path", "dir"),
    "calculate": ("expression", "expr"),
    "run_command": ("cmd", "command"),
}


def _strip_wrap_quotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1].strip()
    return text


# grep 的 `pattern: "x", path: "y"`（同行逗号）形式：拆成规范的两行 <正则>\npath: <范围>。
_RE_GREP_KEYED_INLINE = re.compile(
    r'^(?:pattern|regex|p)\s*[:：]\s*(?P<q>["\'`]?)(?P<pat>.*?)(?P=q)\s*[,，]\s*'
    r'path\s*[:：]\s*(?P<q2>["\'`]?)(?P<path>.+?)(?P=q2)\s*$',
    re.I,
)
_RE_GREP_PATH_LINE = re.compile(r"^\s*path\s*[:：]\s*(.+?)\s*$", re.I)


def _normalize_grep_arg(arg):
    """grep 关键字风格归一化；附带的 path: 限定转成规范的第二行 `path: <范围>`。"""
    first, _, rest = arg.partition("\n")
    mi = _RE_GREP_KEYED_INLINE.match(first.strip())
    if mi:
        return f"{mi.group('pat')}\npath: {mi.group('path')}"
    mk = re.match(r"^(?:pattern|regex|p)\s*[:：]\s*(.*)$", first.strip(), re.I)
    scope, kept = None, []
    for ln in rest.splitlines():
        ms = _RE_GREP_PATH_LINE.match(ln)
        if ms:
            scope = _strip_wrap_quotes(ms.group(1).strip())
        else:
            kept.append(ln)
    if mk:
        out = _strip_wrap_quotes(mk.group(1).strip())
        tail = "\n".join(kept).strip()
        if tail:
            out += "\n" + tail
        if scope:
            out += ("\n" if out else "") + f"path: {scope}"
        return out
    if scope:  # 纯正则首行 + 后续行 path:
        return f"{first.strip()}\npath: {scope}"
    return arg


def _normalize_tool_arg(tool, arg):
    """把 `key: value` 风格的单值工具入参还原成纯 value；read_file 兼容同行逗号 start/end。"""
    if not arg or tool not in _ARG_PREFIX_TOOLS:
        return arg
    if tool == "grep":
        norm = _normalize_grep_arg(arg)
        if norm != arg:
            return norm
    first_line, _, remainder = arg.partition("\n")
    keys = "|".join(_ARG_PREFIX_TOOLS[tool])
    m = re.match(rf"^(?:{keys})\s*[:：]\s*(.*)$", first_line.strip(), re.I)
    if not m:
        return arg
    value = m.group(1).strip()
    if tool == "read_file":
        # path: "p", start: 10, end: 20 → p / start: 10 / end: 20 拆成多行
        segs = re.split(r"\s*[,，]\s*(?=(?:start|end)\s*[:：])", value)
        lines = [_strip_wrap_quotes(segs[0])]
        lines.extend(s.strip() for s in segs[1:] if s.strip())
        if remainder.strip():
            lines.append(remainder.strip())
        return "\n".join(lines)
    if remainder.strip():
        return arg  # 多行内容不做剥离（可能是合法的复杂输入）
    return _strip_wrap_quotes(value)


def _user_question(grounded):
    """/api/chat 会在用户问题前拼【系统提示】上下文，护栏文案只应回带真实用户问题。"""
    marker = "用户问题："
    idx = grounded.rfind(marker)
    return grounded[idx + len(marker):].strip() if idx >= 0 else grounded.strip()


def _empty_arg_obs(tool_name, question):
    """空参拦截回填文案：知识库/网搜在已配置代码库时强制改道；其余回带原问题辅助提取标识符。"""
    user_q = _clip(_user_question(question), 200)
    if tool_name in ("search_knowledge", "web_search") and get_runtime("code_root"):
        return _EMPTY_ARG_CODE_REDIRECT.format(tool=tool_name, question=user_q)
    return _EMPTY_ARG_OBS.format(tool=tool_name, question=user_q)

# 工具步数耗尽后，先强制模型基于已有观察收尾的次数；仍不收尾则用观察证据确定性兜底。
_MAX_FORCED_FINALS = 1
_RE_WRITE_INTENT = re.compile(
    r"修改|修复|改正|改一下|改成|改好|重构|新建|创建|新增|添加|加上|"
    r"补全|删掉|删除|移除|替换|重写|提交代码|帮我改|动手改|fix|refactor"
)
_WRITE_BLOCKED_OBS = (
    "安全拦截：用户本轮【没有】明确要求修改代码，写操作被禁止执行。"
    "请不要再次调用写工具，直接基于已有观察，在 Final Answer 中报告问题、"
    "文件行号与建议改法（供用户自行决定是否修改）。"
)


def _has_write_intent(question):
    return bool(_RE_WRITE_INTENT.search(question or ""))


def _format_verbatim(action, obs):
    if action == "calculate":
        return f"计算结果为：{obs}"
    if action == "python_exec":
        return f"代码执行结果：\n{obs}"
    # gen_video_prompt：提示词本身即交付物，原样输出
    return obs


def _is_failure(obs):
    if not obs or not obs.strip():
        return True
    return any(marker in obs for marker in _FAILURE_MARKERS)


class Agent:
    def __init__(self, llm=None):
        self.llm = llm or LLMClient()
        self.history = []  # 多轮对话记忆：[{"user":..,"assistant":..}]

    def _build_messages(self, question, images=None):
        """组装消息列表：系统提示 + 项目规则（若有）+ 多轮历史 + 当前问题。

        项目规则来自运行时 project_rules（/api/ingest_code 读入的 DOCMIND_RULES.md），
        作为第二条 system 消息注入，优先级高于通用 SYSTEM_PROMPT，随项目自动切换。

        images（base64 字符串列表，视觉输入）只挂在【本轮】问题消息上：ollama
        服务端无状态，ReAct 每次重放 head 都会带上图片；多轮历史只存文本，
        不在后续轮次重发旧图（避免上下文无谓膨胀）。
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        rules = get_runtime("project_rules", "")
        if rules:
            messages.append(
                {
                    "role": "system",
                    "content": "【本项目规则，优先级高于上述通用指引，必须逐条遵守】\n" + rules,
                }
            )
        # 历史只回放最近 AGENT_HISTORY_TURNS 轮（滑窗），长回答截断后回放，
        # 避免多轮对话把本就紧张的上下文预算吃光。
        for turn in self.history[-AGENT_HISTORY_TURNS:]:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append(
                {"role": "assistant", "content": _clip(turn["assistant"], HISTORY_ANSWER_CHARS)}
            )
        current = {"role": "user", "content": question}
        if images:
            current["images"] = list(images)
        messages.append(current)
        return messages

    def _prompt_tokens(self, head, trail):
        """整段待发送消息的 token 数（llamacpp 走 /tokenize 精算，失败走保守估算）。"""
        text = "\n".join((m.get("content") or "") for m in head + trail)
        return self.llm.count_tokens(text)

    def _fit_budget(self, head, trail):
        """把整段 prompt 压到 PROMPT_TOKEN_BUDGET 以内，返回最终消息列表。
        裁剪顺序（保住最相关上下文）：
        1) trail 最早的工具/反思/续写往返（至少保留最近 1 轮）；
        2) head 中最早的历史问答对（system 规则与当前问题永不动）。
        """
        guard = 0
        while self._prompt_tokens(head, trail) > PROMPT_TOKEN_BUDGET and guard < 40:
            guard += 1
            found = _find_prunable_trail(trail)
            if found is not None:
                idx, span = found
                del trail[idx:idx + span]
                continue
            # head: [system, (system rules), 历史问答对..., 当前问题]
            sys_end = 2 if len(head) > 1 and head[1].get("role") == "system" else 1
            if len(head) > sys_end + 1:
                del head[sys_end:sys_end + 2]
                continue
            break
        return head + trail

    def run(self, question, stream=True, images=None):
        """执行一次问答，yield 出流式事件：
        token / thought / action / observation / reflection / final / done。

        images: 可选的 base64 图片列表（视觉模型输入），挂在本轮问题消息上。
        消息分两部分：head（系统提示/项目规则/历史滑窗/当前问题，固定）+
        trail（本轮 ReAct 决策与观察，动态增长，超预算时成对丢弃最早的往返）。
        """
        head = self._build_messages(question, images=images)
        trail = []

        failures = 0
        nudges = 0
        tool_steps = 0
        iterations = 0
        repeats = 0  # 完全相同参数重复调用同一工具的次数
        executed = set()  # 本轮已执行过的 (工具, 参数)，用于防空转循环
        last_action = None
        last_obs = None
        forced_finals = 0  # 已发出的强制收尾提示次数（步数耗尽 / 重复空转共用一次机会）
        forced_final_reason = ""  # 触发强制收尾的原因，证据兜底 final 里原样告知用户
        evidence = []  # 本轮已执行工具的简要清单（action(input)），耗尽时兜底用

        def _evidence_final(reason):
            """模型在强制收尾后仍不给出 Final Answer：用本轮真实观察做确定性兜底。"""
            steps_used = "；".join(evidence) or "（无）"
            last = _clip(last_obs or "", OBS_MAX_CHARS)
            return {
                "type": "final",
                "text": (
                    f"{reason}\n"
                    "以下为本轮真实检索到的证据，请缩小问题范围后重问；"
                    f"未在观察中出现的结论请勿采信。\n"
                    f"已执行：{steps_used}\n最后观察：\n{last}"
                ),
            }

        while True:
            iterations += 1
            if iterations > MAX_AGENT_STEPS + _MAX_NUDGES + _MAX_FORCED_FINALS + 4:
                yield {
                    "type": "final",
                    "text": "（已达到最大推理步数，请尝试更具体的问题，或补充知识库内容。）",
                }
                return

            # 每轮送模型前都做一次 token 预算裁剪（trail 往返 -> head 历史）
            messages = self._fit_budget(head, trail)
            acc = ""
            finish_reason = None
            if stream:
                chat_stream = self.llm.chat(messages, stream=True)
                for tok in chat_stream:
                    acc += tok
                    yield {"type": "token", "text": tok}
                finish_reason = getattr(chat_stream, "finish_reason", None)
            else:
                acc = self.llm.chat(messages, stream=False)

            parsed = parse_response(acc)
            if parsed["thought"]:
                yield {"type": "thought", "text": parsed["thought"]}

            if parsed["action"] and parsed["action"] in TOOLS:
                action_name = parsed["action"]
                action_arg = _normalize_tool_arg(action_name, (parsed["action_input"] or "").strip())
                sig = (action_name, action_arg)

                # 强制收尾（步数耗尽/重复空转）已发出后，模型仍输出任何 Action——
                # 即使换了不同参数——也不再执行/回填，直接用已有观察证据确定性兜底。
                if forced_finals >= _MAX_FORCED_FINALS:
                    yield _evidence_final(forced_final_reason)
                    return

                # 写操作同意护栏：审查/问答类问题未明确要求修改时，拒绝真正落盘，
                # 以一条 Observation 把模型引导回"只报告"模式（不消耗工具步数）。
                if parsed["action"] in _WRITE_TOOLS and not _has_write_intent(question):
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                    trail.append(
                        {"role": "user", "content": "Observation: " + _WRITE_BLOCKED_OBS}
                    )
                    executed.add(sig)  # 再试同样的写调用会被防重复逻辑拦住
                    yield {
                        "type": "action",
                        "text": f"{parsed['action']}({parsed['action_input']}) [已拦截：未授权写操作]",
                    }
                    yield {
                        "type": "observation",
                        "text": f"[安全拦截] {parsed['action']} 未执行：用户没有要求修改代码。",
                    }
                    continue

                # 防重复空转：同参数调用结果已在上文 Observation，不再执行第二次
                if sig in executed:
                    repeats += 1
                    if repeats >= 3:
                        # 不直接硬停：先强制模型基于已有 Observation 收尾一次
                        # （答案可能已在观察中，如刚 grep 到目标行号）；仍要调工具再证据兜底。
                        if forced_finals < _MAX_FORCED_FINALS:
                            forced_finals += 1
                            forced_final_reason = (
                                "（模型反复用完全相同的参数调用同一工具，已要求其停止工具调用并收尾。）"
                            )
                            trail.append(
                                {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                            )
                            trail.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Nudge: 你已多次用完全相同的参数调用同一工具，重复调用不会"
                                        "返回任何新结果。禁止再调用任何工具（即使更换参数也不行）。"
                                        "请立即基于上文全部 Observation 输出 `Final Answer:`："
                                        "用中文简洁总结已确认的结论并附文件:行号；关键结论尚未确认的"
                                        "部分如实说明「当前无法确认」，不要编造，也不要输出 Action。"
                                    ),
                                }
                            )
                            yield {
                                "type": "reflection",
                                "text": "检测到模型重复空转，正在要求模型基于已有观察强制收尾。",
                            }
                            continue
                        yield _evidence_final(forced_final_reason)
                        return
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                    if repeats >= 2:
                        note = (
                            "Nudge: 你已经用完全相同的参数重复调用该工具，结果就在上文 "
                            "Observation 中。禁止再次重复调用；请直接基于已有观察输出简短的 "
                            "`Final Answer: ...`。"
                        )
                    else:
                        note = (
                            f"Reflection: 你刚刚已经用完全相同的参数调用过 {parsed['action']}，"
                            "其结果已在上一条 Observation 中给出，不要重复调用。"
                            "请直接利用该结果给出 Final Answer，或换一个【不同参数】的工具调用。"
                        )
                    trail.append({"role": "user", "content": note})
                    yield {
                        "type": "reflection",
                        "text": f"检测到重复调用 {parsed['action']}（相同参数），已拦截并提示模型利用已有结果。",
                    }
                    continue

                # 空参数护栏：需要输入的工具不允许空参调用（不执行、不计工具步数）。
                # 放在重复计数之后：连续空参第 2/3 次直接走上面的重复升级（Nudge→停止），
                # 避免弱模型靠空参空转刷到迭代上限。
                if not action_arg and action_name not in (_NO_ARG_TOOLS | _OPTIONAL_ARG_TOOLS):
                    executed.add(sig)
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                    empty_obs = _empty_arg_obs(action_name, question)
                    trail.append({"role": "user", "content": empty_obs})
                    yield {
                        "type": "action",
                        "text": f"{action_name}() [已拦截：缺少参数]",
                    }
                    yield {
                        "type": "observation",
                        "text": empty_obs,
                    }
                    continue

                if tool_steps >= MAX_AGENT_STEPS:
                    # 步数耗尽：先强制模型基于已有 Observation 收尾（不执行新工具、不计步），
                    # 给一次机会产出带证据的 Final Answer；仍要调工具则由前置拦截证据兜底。
                    if forced_finals < _MAX_FORCED_FINALS:
                        forced_finals += 1
                        forced_final_reason = (
                            f"（已达到最大工具调用步数 {MAX_AGENT_STEPS}，模型未能自行收尾。）"
                        )
                        trail.append(
                            {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                        )
                        trail.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Nudge: 工具调用步数已达上限（{MAX_AGENT_STEPS} 步），"
                                    "禁止再调用任何工具。请立即基于上文全部 Observation 输出 "
                                    "`Final Answer:`：用中文简洁总结已确认的结论并附文件:行号；"
                                    "证据不足的部分如实说明"
                                    "「当前无法确认」，不要编造，也不要输出 Action。"
                                ),
                            }
                        )
                        yield {
                            "type": "reflection",
                            "text": "工具步数已用尽，正在要求模型基于已有观察收尾。",
                        }
                        continue
                    # 防御性兜底：正常路径已被循环顶部的前置拦截覆盖
                    yield _evidence_final(forced_final_reason)
                    return
                executed.add(sig)
                tool_steps += 1
                # 事件展示 / 证据清单 / 实际派发必须统一用归一化后的 action_arg：
                # 弱模型的 query:/pattern:/path: 关键字风格已在此处还原为工具真实入参。
                evidence.append(f"{action_name}({_clip(action_arg, 120)})")
                yield {"type": "action", "text": f"{action_name}({action_arg})"}
                obs = TOOLS[action_name]["func"](action_arg)
                yield {"type": "observation", "text": obs}
                last_action = parsed["action"]
                last_obs = obs

                # 自我反思：工具未返回有效结果时，标记反思并提示换思路重试
                if _is_failure(obs) and failures < _MAX_REFLECTIONS:
                    failures += 1
                    yield {
                        "type": "reflection",
                        "text": f"工具 {parsed['action']} 未返回有效结果，正在换思路重试（改用其他工具或改写查询）。",
                    }
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                    trail.append(
                        {
                            "role": "user",
                            "content": (
                                f"Reflection: 上一工具 {parsed['action']} 未得到有效结果，请换一种方式"
                                f"（例如改用 web_search，或换关键词）。\n\nObservation was: "
                                f"{_clip(obs, OBS_MAX_CHARS)}"
                            ),
                        }
                    )
                    continue

                # 写操作改变了代码库状态：清空已执行记录，允许随后用【相同命令】
                # 重新跑测试做验证（防重复护栏针对的是无意义空转，不是改后复验）。
                if parsed["action"] in ("apply_edit", "create_file", "dev_region_edit"):
                    executed.clear()

                # "产出即答案"的工具：结果已经正确，直接作为最终回答返回，
                # 不再给模型多一轮（避免小模型反复调用同一工具导致步数耗尽 / 死循环）。
                if parsed["action"] in _VERBATIM_TOOLS:
                    final_text = _format_verbatim(parsed["action"], obs)
                    self.history.append({"user": question, "assistant": final_text})
                    yield {"type": "final", "text": final_text}
                    return

                # 把本轮结果回填，进入下一轮推理。观察先截断，防止撑爆 n_ctx。
                # 即便模型在同一轮里也写了 Final Answer，也以真实观察为准再走一轮，
                # 避免它在没看到工具结果前就给出最终答案（小模型常把 Action+Final 写在一起）。
                trail.append(
                    {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                )
                trail.append(
                    {
                        "role": "user",
                        "content": (
                            f"Observation: {_clip(obs, OBS_MAX_CHARS)}"
                            "\n\n（请基于观察继续，或给出 Final Answer）"
                        ),
                    }
                )
                continue

            truncated = finish_reason == "length"
            has_real_final = bool(_RE_HAS_REAL_FINAL.search(acc))

            if parsed["final"]:
                # 截断处恰好停在 Final Answer 中间（半句结论）：先要求简短重写，
                # 不把半句直接抛给用户；纠偏额度耗尽后才接受这半句真实结论兜底。
                if truncated and nudges < _MAX_NUDGES:
                    nudges += 1
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                    trail.append(
                        {
                            "role": "user",
                            "content": (
                                "Nudge: 你上一轮的 Final Answer 在长度上限处被截断，结尾不完整。"
                                "请用 3-6 句简短中文重新输出一个【完整】的 Final Answer，"
                                "只给最终结论，不要 Action、不要复述代码、不要铺垫。"
                            ),
                        }
                    )
                    yield {
                        "type": "reflection",
                        "text": "Final Answer 被截断为半句，正在要求模型简短重写。",
                    }
                    continue

                final_text = parsed["final"]
                # 若上一步是"产出即答案"的工具且返回有效，强制透传工具结果，
                # 避免小模型在 Final Answer 里改写数字/格式导致错误。
                if last_action in _VERBATIM_TOOLS and last_obs and not _is_failure(last_obs):
                    final_text = _format_verbatim(last_action, last_obs)
                self.history.append({"user": question, "assistant": final_text})
                yield {"type": "final", "text": final_text}
                return

            # 没有有效动作也没有合格 Final：可能是输出被长度截断 / 只有思考没有正文 /
            # 格式没写完。自动「续写纠偏」最多 _MAX_NUDGES 次，绝不把残句静默当答案。
            if nudges < _MAX_NUDGES and (truncated or not acc.strip() or not has_real_final):
                nudges += 1
                if acc.strip():
                    trail.append(
                        {"role": "assistant", "content": _clip(acc, TRAIL_ASSISTANT_CHARS)}
                    )
                if truncated:
                    if "Action:" in acc:
                        # 已写到 Action 就被切断：让它立刻补全调用
                        hint = (
                            "Nudge: 你的输出在长度上限处中断，Action 没有写完。"
                            "请立即用一句 Thought 加完整的 `Action:` 与 `Action Input:` 补全"
                            "你上一轮要做的调用，不要重复之前的长分析；信息已足够时才直接给 "
                            "`Final Answer: ...`。"
                        )
                    elif nudges == 1 and tool_steps == 0:
                        # 还没检索/读文件就烧光长度：先去拿证据，而不是逼它凭空回答
                        hint = (
                            "Nudge: 你的上一轮在长度上限处中断，且还没有调用任何工具。"
                            "请先用一句话 Thought，然后直接输出一个 Action"
                            "（代码问题优先 search_code / read_file / grep）去获取信息，"
                            "禁止长篇思考；拿到结果后再给 Final Answer。"
                        )
                    else:
                        hint = (
                            "Nudge: 输出再次在长度上限处中断。请不要再调用工具，"
                            "直接基于已有信息，用简洁中文给出 `Final Answer: ...`，只答关键结论。"
                        )
                elif not acc.strip():
                    hint = (
                        "Nudge: 你上一轮没有输出任何正文内容。请严格按格式直接给出最终回答："
                        "`Final Answer: ...`（中文、简洁、不要再只做思考）。"
                    )
                else:
                    hint = (
                        "Nudge: 你上一轮的输出没有完成（既无有效 Action，也没有 Final Answer）。"
                        "若还需调用工具，请输出完整的 Action 与 Action Input；"
                        "否则直接给出 `Final Answer: ...` 结束回答。"
                    )
                trail.append({"role": "user", "content": hint})
                yield {
                    "type": "reflection",
                    "text": "回答未完整结束（可能被长度截断），正在续写纠偏。",
                }
                continue

            # 续写纠偏后仍不完整：给出明确错误，不再把半句 Thought 冒充答案；
            # 这类错误结果不写入历史，避免污染下一轮上下文。
            if truncated:
                final_text = (
                    "模型回答因上下文长度限制被截断，未能给出完整答案。"
                    "请缩小问题范围（例如指定具体文件/函数）或开新对话后重试。"
                )
            elif not acc.strip():
                final_text = "模型本轮未返回有效正文（可能在思考阶段耗尽输出长度）。请重试或换一个更具体的问题。"
            else:
                # 连纠偏后仍不合格式的残句：原样兜底（保持旧行为），但记入历史
                self.history.append({"user": question, "assistant": acc})
                yield {"type": "final", "text": acc}
                return
            yield {"type": "final", "text": final_text}
            return
