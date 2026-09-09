"""ReAct Agent：思考 -> 行动 -> 观察 循环，带多轮对话记忆与流式输出。

这是整个项目的核心：它不是「检索完直接喂给 LLM」的朴素 RAG，而是让 LLM
自主决定「调用哪个工具 / 何时停止」，形成一个可解释、可扩展的 Agent 推理链路。
"""
import re

from config import MAX_AGENT_STEPS, get_runtime
from llm import LLMClient
from tools import TOOLS

SYSTEM_PROMPT = """你是一个严谨的多工具问答 Agent，可以调用以下工具来获取信息或执行动作。
若系统消息中还附有「本项目规则」（分区约定 / 修改约束），其优先级高于本通用指引，必须逐条遵守。
可用工具：
- search_knowledge(query): 在本地知识库中检索相关文档片段。回答"某文档里讲了什么/某概念怎么定义"类问题。
- search_assets(query): 在精选游戏素材目录中检索素材（角色精灵/tileset/UI/音效等），回答"找素材/美术资源/角色精灵/tileset"类问题。
- calculate(expression): 计算数学表达式，如 '23*45+12'。仅支持 + - * / % 和括号。
- web_search(query): 联网搜索（DuckDuckGo，无需 Key）。当知识库不足、信息有时效性、或需要外部资料时使用。
- python_exec(code): 在受限子进程中执行 Python 代码并返回输出。用于数值计算、数据处理、文本变换等需要"真正动手"的任务。
- gen_video_prompt(spec): 按 MiniMax H3 的三段结构，把一段创意描述生成为结构化视频提示词（可直接粘贴进 ComfyUI）。
- search_code(query): 在已索引的源代码/配置中检索相关函数、类、配置片段。回答"某功能在哪实现/某函数做什么/某配置怎么写"等关于代码库的问题。
- read_file(path): 读取代码库中的某个文件内容（path 为相对代码根目录的路径或文件名）。需要看完整文件、或某文件细节时用。
- grep(pattern): 在代码库中按正则搜索文本/符号，返回匹配的文件路径与行号。定位某段代码、某变量、某错误出现位置时用。
- apply_edit(path, old_text?, new_text): 受控修改代码库中【已存在】的文件（不能新建、不能越界写）。两种用法：① 局部安全替换——提供 path、old_text（要被替换的【精确】旧片段）、new_text（替换后内容），工具在文件中唯一匹配处替换；② 整体重写——只提供 path 与 new_text（省略 old_text），但前提是你已用 read_file 读取过该文件。修改前请务必先用 read_file 确认当前内容；.py 写入后会做语法校验，不通过自动回滚。Action Input 按多行格式写：第一行 `path: <路径>`，可选 `old_text: <精确旧片段>`，最后 `new_text: <新内容（可多行）>`。
- create_file(path, content): 在代码库内【新建】一个文件（不能覆盖已有文件，修改已有文件请用 apply_edit）。用于新增模块/分区（如新建 combat/crit.py）。同样受路径沙箱、单文件 200KB 上限、.py 语法校验约束；父目录不存在会自动创建（仍在 code_root 内）。Action Input 格式：第一行 `path: <路径>`，最后 `new_text: <文件内容（可多行）>`。新建前建议先用 search_code/grep 确认不会与已有实现重复（防堆叠）。
- run_command(cmd): 在代码库根目录内执行 shell 命令（如 pytest / npm run build / gradle test），返回合并后的标准输出与错误（截断 1500 字，超时 12s）。需要跑构建、跑测试、执行项目内命令来验证改动或查看结果时用。命令在 code_root 内执行，危险操作（rm -rf /、format、shutdown 等）会被拦截。输入为完整命令字符串。

工具选择指引：
- 数学计算优先用 calculate，复杂计算/数据处理/画图数据用 python_exec。
- 知识库能答的优先 search_knowledge；知识库没有、或需要最新/外部信息时用 web_search。
- 用户想要"视频提示词/分镜/短视频脚本"类产出时用 gen_video_prompt。
- 关于"代码/工程/实现/函数/类/配置/报错"的问题，优先用 search_code / read_file / grep：
  · 先用 search_code 概览相关函数/类；需要看完整实现再用 read_file 打开具体文件；需要定位某符号或报错位置再用 grep。
  · grep / search_code 的输入必须是【纯符号或关键词】（例如 seekTo、PlayerManager、setOnClickListener），只写要检索的标识符本身，不要附加中文说明、不要写整句——「seekTo 进行进度跳转」是错误的，应只写 `seekTo`。
  · 没有配置代码库时（search_code 提示未配置），可改用 python_exec 在本地读取文件做兜底，但优先引导用户先用 /api/ingest_code 索引代码目录。
  · 需要【修改】代码库中的文件时，使用 apply_edit。无论哪种用法，都请先 read_file 看清当前内容再动手：能用 old_text 精确局部替换就用它（最安全，能避免误改）；只有确实需要整体重写且已 read_file 过该文件时，才用不带 old_text 的重写模式。修改成功后可用 read_file 复查确认变更。apply_edit 只能改已存在文件，不要指望它创建新文件或越界写。
  · 需要【新建】文件/模块（例如为项目新增一个分区目录与源文件）时，使用 create_file；它不能覆盖已有文件（覆盖请用 apply_edit）。新建前务必先用 search_code/grep 确认没有重复实现，避免堆叠；父目录不存在时会自动创建（仍在代码根目录内）。新建 .py 文件会通过语法校验。
  · 若 apply_edit / create_file 返回「待人工确认 #id」，说明写操作已暂存、等待用户在界面确认后才会真正写入；此时你应在 Final Answer 中如实转述 diff 内容并提示用户确认，不要再继续其它写操作，也不要声称已经写入。
  · 改完代码后需要【验证】改动是否破坏构建/测试时，使用 run_command 跑 pytest / npm run build 等命令（命令在代码根目录内执行，危险操作会被拦截）。这是"改完即验证"的闭环关键一步。
- 调用 python_exec 时，Action Input 必须是完整、可直接执行的 Python 代码（用 print 输出结果）；
  不要加 ``` 代码围栏，也不要只写 "python" 等语言名。

回答质量要求（Final Answer）：
- 简洁综合总结检索/执行结果，2-4 句话或简明的要点列表即可，不要大段复制原文。
- 用中文回答；如检索原文含英文片段，请翻译或概括，不要直接混杂长英文片段。
- 面向"里面讲了什么/总结/介绍"类问题，给出提炼后的要点，不要逐条罗列原文编号。
- 尽量保持客观，不要编造检索结果中没有的信息。
- 工具已返回明确结果（尤其是数字/代码片段）时，Final Answer 应直接引用工具给出的内容，不要自行重算或改写其中的数字。
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
_RE_ACTION = re.compile(r"Action:\s*(\w+)", re.S)
_RE_ACTION_INPUT = re.compile(r"Action Input:\s*(.*?)(?=\n\s*(?:Thought|Action|Final Answer)\s*:|$)", re.S)
_RE_FINAL = re.compile(r"Final Answer:\s*(.*)", re.S)


def parse_response(text):
    thought = _RE_THOUGHT.search(text)
    action = _RE_ACTION.search(text)
    action_input = _RE_ACTION_INPUT.search(text)
    final = _RE_FINAL.search(text)
    return {
        "thought": thought.group(1).strip() if thought else "",
        "action": action.group(1).strip() if action else None,
        "action_input": action_input.group(1).strip() if action_input else "",
        "final": final.group(1).strip() if final else None,
    }


# 工具未返回有效结果的判定（触发自我反思 / 换工具重试）
_MAX_REFLECTIONS = 2
_FAILURE_MARKERS = ("未找到相关内容", "计算失败", "表达式包含非法字符",
                    "搜索失败", "搜索未返回结果", "未提供", "安全限制", "拒绝写入")

# 产出即最终交付物的工具：其返回内容应原样呈现给用户，不被模型二次改写
# （小模型常常"重新生成"而非照抄，导致数字/格式出错，这里强制透传）。
_VERBATIM_TOOLS = {"calculate", "python_exec", "gen_video_prompt"}


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

    def _build_messages(self, question):
        """组装消息列表：系统提示 + 项目规则（若有）+ 多轮历史 + 当前问题。

        项目规则来自运行时 project_rules（/api/ingest_code 读入的 DOCMIND_RULES.md），
        作为第二条 system 消息注入，优先级高于通用 SYSTEM_PROMPT，随项目自动切换。
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
        for turn in self.history:
            messages.append({"role": "user", "content": turn["user"]})
            messages.append({"role": "assistant", "content": turn["assistant"]})
        messages.append({"role": "user", "content": question})
        return messages

    def run(self, question, stream=True):
        """执行一次问答，yield 出流式事件：
        token / thought / action / observation / final / done。
        """
        messages = self._build_messages(question)

        failures = 0
        last_action = None
        last_obs = None
        for _ in range(MAX_AGENT_STEPS):
            acc = ""
            if stream:
                for tok in self.llm.chat(messages, stream=True):
                    acc += tok
                    yield {"type": "token", "text": tok}
            else:
                acc = self.llm.chat(messages, stream=False)

            parsed = parse_response(acc)
            if parsed["thought"]:
                yield {"type": "thought", "text": parsed["thought"]}

            if parsed["action"] and parsed["action"] in TOOLS:
                yield {"type": "action", "text": f"{parsed['action']}({parsed['action_input']})"}
                obs = TOOLS[parsed["action"]]["func"](parsed["action_input"])
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
                    messages.append({"role": "assistant", "content": acc})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Reflection: 上一工具 {parsed['action']} 未得到有效结果，请换一种方式"
                                f"（例如改用 web_search，或换关键词）。\n\nObservation was: {obs}"
                            ),
                        }
                    )
                    continue

                # "产出即答案"的工具：结果已经正确，直接作为最终回答返回，
                # 不再给模型多一轮（避免小模型反复调用同一工具导致步数耗尽 / 死循环）。
                if parsed["action"] in _VERBATIM_TOOLS:
                    final_text = _format_verbatim(parsed["action"], obs)
                    self.history.append({"user": question, "assistant": final_text})
                    yield {"type": "final", "text": final_text}
                    return

                # 把本轮结果回填，进入下一轮推理。
                # 即便模型在同一轮里也写了 Final Answer，也以真实观察为准再走一轮，
                # 避免它在没看到工具结果前就给出最终答案（小模型常把 Action+Final 写在一起）。
                messages.append({"role": "assistant", "content": acc})
                messages.append(
                    {
                        "role": "user",
                        "content": f"Observation: {obs}\n\n（请基于观察继续，或给出 Final Answer）",
                    }
                )
                continue

            if parsed["final"]:
                final_text = parsed["final"]
                # 若上一步是"产出即答案"的工具且返回有效，强制透传工具结果，
                # 避免小模型在 Final Answer 里改写数字/格式导致错误。
                if last_action in _VERBATIM_TOOLS and last_obs and not _is_failure(last_obs):
                    final_text = _format_verbatim(last_action, last_obs)
                self.history.append({"user": question, "assistant": final_text})
                yield {"type": "final", "text": final_text}
                return

            # 无法解析出有效动作：把原文当作最终回答兜底
            self.history.append({"user": question, "assistant": acc})
            yield {"type": "final", "text": acc}
            return

        yield {
            "type": "final",
            "text": "（已达到最大推理步数，请尝试更具体的问题，或补充知识库内容。）",
        }
