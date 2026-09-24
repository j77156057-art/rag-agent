# -*- coding: utf-8 -*-
"""开发工作流领域画像注册表。

工作流不局限于游戏：模型/用户判断任务链较长时，可按领域选择画像（generic
通用开发 / game 游戏开发 / 后续 eda 等）。画像只决定**确定性兜底内容与提示词
口吻**——方案选项、兜底任务 DAG、LLM 生成器提示；状态机、审批门、步数预算等
机制完全复用，不为每个领域新增图结构。

刻意不 import game_workflow（避免循环导入）：选项以普通 dict 返回
（键与 WorkflowOption 的 asdict 一致），由管理器层转成 WorkflowOption。
"""
from dataclasses import dataclass
from typing import Callable

# 选项/兜底任务的固定契约键，与 agent_runtime.game_workflow.WorkflowOption 对齐
_OPTION_KEYS = ("id", "title", "summary", "recommended", "source", "requires_web")


def _option(oid, title, summary, *, recommended=False, source="local",
            requires_web=False):
    return {"id": oid, "title": title, "summary": summary,
            "recommended": recommended, "source": source,
            "requires_web": requires_web}


def _append_dynamic(options, sources):
    """web_research / custom 两个动态选项各领域一致。"""
    if "web" not in tuple(sources or ()):
        options.append(_option(
            "web_research", "联网补充资料后再选",
            "搜索引擎/插件/最新资料，再重新生成方案选项。",
            source="web", requires_web=True))
    options.append(_option(
        "custom", "我自己描述目标",
        "由用户补充更具体的效果、限制或参考作品。", source="user"))
    return options


# ---------------------------------------------------------------------------
# generic：领域无关的通用开发（软件/EDA/数据/工具链……）
# ---------------------------------------------------------------------------
def _generic_options(request: str, sources) -> list[dict]:
    source_name = "、".join(sources) if sources else "local"
    options = [
        _option("recommended", "最小可运行版本先行",
                "先交付端到端可运行的最小版本，再按验证结果迭代。",
                recommended=True, source=source_name),
        _option("design_first", "先完成设计与技术方案",
                "先明确目标、接口、数据与验收标准，再开始改动。"),
    ]
    return _append_dynamic(options, sources)


def _generic_fallback_tasks() -> list[dict]:
    return [
        {"id": "research", "role": "researcher",
         "task": "调研现状与约束：梳理已有实现与资料，列出关键事实、可行路径与风险",
         "depends_on": []},
        {"id": "implement", "role": "coder",
         "task": "基于调研结论实现最小可运行版本",
         "depends_on": ["research"]},
        {"id": "verify", "role": "tester",
         "task": "运行并验证最小版本：回报真实输出、失败证据与遗留问题",
         "depends_on": ["implement"]},
    ]


# ---------------------------------------------------------------------------
# eda：电子设计（原理图/PCB，经 EDA 连接器操作，ERC/DRC 收尾）
# ---------------------------------------------------------------------------
# EDA 子代理操作连接器的标准三件套：按语义选连接器 → 列出工具核实名称 → 调用。
EDA_CONNECTOR_TOOLS = ["dev_route_connector", "dev_list_connector_tools", "dev_mcp_call"]


def _eda_options(request: str, sources) -> list[dict]:
    options = [
        _option("recommended", "先做 ERC/DRC 体检",
                "先跑电气规则（ERC）与设计规则（DRC）检查，清零错误或逐项列明豁免后再改板。",
                recommended=True, source=("、".join(sources) if sources else "local")),
        _option("schematic_pcb", "按原理图 → PCB 流程推进",
                "先补齐元件库/封装与原理图、导出网表，再进行 PCB 布局布线与规则校验。"),
    ]
    return _append_dynamic(options, sources)


def _eda_fallback_tasks() -> list[dict]:
    return [
        {"id": "research", "role": "researcher",
         "task": "调研元件选型、封装、原理图库/PCB 封装库与设计约束（间距、载流、安规），"
                 "输出带来源的关键参数与风险清单",
         "tools": ["web_search", "web_fetch", "web_research"],
         "mcp": "deny", "depends_on": []},
        {"id": "schematic", "role": "schematic",
         "task": "依据调研绘制或修正原理图并导出网表：涉及 EDA 连接器的操作必须先 "
                 "dev_list_connector_tools 核实工具名再 dev_mcp_call；"
                 "产出原理图/网表与 ERC 结果（零错误或列明豁免依据）",
         "tools": EDA_CONNECTOR_TOOLS + ["read_file", "grep"],
         "mcp": "allow", "depends_on": ["research"]},
        {"id": "layout", "role": "layout",
         "task": "基于审核后的网表完成 PCB 布局与布线，遵守布局/间距/载流约束；"
                 "连接器操作同样先 dev_list_connector_tools 核实工具名；未跑 DRC 不视为完成",
         "tools": EDA_CONNECTOR_TOOLS + ["read_file", "grep"],
         "mcp": "allow", "depends_on": ["schematic"]},
        {"id": "verify", "role": "tester",
         "task": "执行 ERC/DRC 与导出物校验（网表、Gerber/钻孔、BOM 一致性）："
                 "DRC 必须零错误，否则逐项列明违规与豁免依据；必要时用 python_exec 解析导出文件",
         "tools": EDA_CONNECTOR_TOOLS + ["read_file", "python_exec"],
         "mcp": "allow", "depends_on": ["layout"]},
    ]


# ---------------------------------------------------------------------------
# game：保持改造前的游戏 Harness 文案与 design/prototype/verify 兜底
# ---------------------------------------------------------------------------
def _game_options(request: str, sources) -> list[dict]:
    lower = (request or "").lower()
    game_kind = "2D" if any(x in lower for x in ("2d", "横版", "像素", "俯视")) else "3D"
    source_name = "、".join(sources) if sources else "local"
    options = [
        _option("recommended", f"按 {game_kind} 游戏原型推进",
                "先建立最小可运行原型，再按验证结果迭代。",
                recommended=True, source=source_name),
        _option("design_first", "先完成设计与技术方案",
                "先拆玩法、场景、数据和验证标准，再开始改文件。"),
    ]
    return _append_dynamic(options, sources)


def _game_fallback_tasks() -> list[dict]:
    return [
        {"id": "design", "role": "designer",
         "task": "明确玩法、场景、输入和验收标准", "depends_on": []},
        {"id": "prototype", "role": "coder",
         "task": "创建最小可运行游戏原型", "depends_on": ["design"]},
        {"id": "verify", "role": "tester",
         "task": "执行自测、Playtest 并反馈失败证据", "depends_on": ["prototype"]},
    ]


@dataclass(frozen=True)
class Profile:
    kind: str
    display_name: str
    build_options: Callable[[str, tuple], list[dict]]
    fallback_tasks: Callable[[], list[dict]]
    # 给 LLM 方案/任务生成器的领域口吻（JSON 契约部分各领域共用，由 api 层拼）
    option_instruction: str
    task_instruction: str
    # 任务 JSON 中 role 字段允许枚举（拼进提示词 schema）
    task_roles: str
    # 经验沉淀/技能候选的领域措辞（game 保持历史原文）
    experience_title: str
    skill_name_prefix: str
    skill_description_prefix: str
    skill_body: str

    def option_dicts(self, request: str, sources) -> list[dict]:
        rows = self.build_options(request, tuple(sources or ()))
        return [{key: row[key] for key in _OPTION_KEYS} for row in rows]


GENERIC = Profile(
    kind="generic",
    display_name="通用开发",
    build_options=_generic_options,
    fallback_tasks=_generic_fallback_tasks,
    option_instruction=(
        "你是通用开发工作流的需求澄清器（任务可能涉及软件、EDA/硬件、数据处理、"
        "工具链或自动化等任意非游戏领域）。方案标题与摘要必须领域无关，"
        "禁止出现「游戏」「玩法」「关卡」等游戏专属措辞。"
    ),
    task_instruction=(
        "你是通用开发工作流主 Agent，主要负责汇总规划、审核结果和最终决策。"
        "根据用户目标、用户选择的方案和上下文，自行判断任务复杂度："
        "简单任务直接执行，复杂文件任务先派一个只读 dispatcher/planner 拆解文件与依赖；"
        "由它提出后续分工，主 Agent 校验后再动态决定执行型 Subagent 的数量；不要固定生成两个成员。"
        "任务描述必须领域无关，禁止出现「游戏」「玩法」措辞。"
    ),
    task_roles="dispatcher|planner|researcher|coder|reviewer|tester",
    experience_title="开发工作流：",
    skill_name_prefix="dev-workflow-",
    skill_description_prefix="经用户审批后可复用的开发工作流：",
    skill_body=(
        "# 开发工作流\n\n"
        "适用于：先澄清目标与方案，再拆分调研、实现和验证任务；\n"
        "执行前经过审批，失败后依据复核结果重规划。\n"),
)


GAME = Profile(
    kind="game",
    display_name="游戏开发",
    build_options=_game_options,
    fallback_tasks=_game_fallback_tasks,
    option_instruction="你是游戏开发 Harness 的需求澄清器。",
    task_instruction=(
        "你是游戏开发主 Agent，主要负责汇总规划、审核结果和最终决策。"
        "根据用户目标、用户选择的方案和上下文，"
        "自行判断任务复杂度：简单任务直接执行，复杂文件任务先派一个只读 dispatcher/planner 拆解文件与依赖；"
        "由它提出后续分工，主 Agent 校验后再动态决定执行型 Subagent 的数量；不要固定生成两个成员。"
    ),
    task_roles="dispatcher|planner|designer|coder|artist|tester|researcher|reviewer",
    experience_title="游戏工作流：",
    skill_name_prefix="game-workflow-",
    skill_description_prefix="经用户审批后可复用的游戏开发工作流：",
    skill_body=(
        "# 游戏开发工作流\n\n"
        "适用于：先澄清目标与方案，再拆分设计、实现和验证任务；\n"
        "执行前经过审批，失败后依据复核结果重规划。\n"),
)


EDA = Profile(
    kind="eda",
    display_name="EDA 电子设计",
    build_options=_eda_options,
    fallback_tasks=_eda_fallback_tasks,
    option_instruction=(
        "你是 EDA（电子设计自动化：原理图/PCB）开发工作流的需求澄清器。"
        "方案围绕元件与库资料、原理图与网表、PCB 布局布线、ERC/DRC 校验组织；"
        "标题与摘要使用 EDA 领域术语（ERC、DRC、网表、封装、Gerber 等）。"
    ),
    task_instruction=(
        "你是 EDA 开发工作流主 Agent，主要负责汇总规划、审核结果和最终决策。"
        "根据用户目标、用户选择的方案和上下文，自行判断任务复杂度："
        "简单任务直接执行，复杂任务先派只读 dispatcher/planner 拆解依赖；"
        "由它提出后续分工，主 Agent 校验后再动态决定执行型 Subagent 的数量；不要固定生成两个成员。"
        "涉及 EDA 连接器（dev_route_connector/dev_list_connector_tools/dev_mcp_call）的任务，"
        "任务 JSON 必须给出【显式 tools 白名单】且 mcp=\"allow\"，"
        "并在任务描述中要求子代理先 dev_list_connector_tools 核实真实工具名后再 dev_mcp_call，禁止臆造工具名；"
        "每个任务的验收必须可检验（例如 ERC/DRC 零错误，或逐项列明违规与豁免依据）。"
    ),
    task_roles="dispatcher|planner|researcher|schematic|layout|coder|reviewer|tester",
    experience_title="EDA 工作流：",
    skill_name_prefix="eda-workflow-",
    skill_description_prefix="经用户审批后可复用的 EDA 开发工作流：",
    skill_body=(
        "# EDA 开发工作流\n\n"
        "适用于：先澄清目标与方案，再拆分资料调研、原理图/网表、PCB 布局布线和 ERC/DRC 验证任务；\n"
        "连接器操作走显式工具白名单，执行前经过审批，失败后依据复核结果重规划。\n"),
)


PROFILES: dict[str, Profile] = {
    "generic": GENERIC,
    "game": GAME,
    "eda": EDA,
}

VALID_KINDS = tuple(PROFILES.keys())
DEFAULT_KIND = "generic"


def normalize_kind(kind) -> str:
    """白名单校验；未知/非法值回退 generic（调用方不应因此报错）。"""
    key = str(kind or "").strip().lower()
    return key if key in PROFILES else DEFAULT_KIND


def get_profile(kind) -> Profile:
    return PROFILES.get(normalize_kind(kind), GENERIC)
