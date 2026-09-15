// 离线演示模式：页面打开时探测同源后端（/api/health）。
// 静态预览（如 IGA Pages）没有 FastAPI 后端，探测失败后切换为演示数据，
// 让第一次打开的人也能看懂界面，而不是对着无限转圈的加载态发懵。
import { ref } from 'vue'

export const demoMode = ref(false)
export const demoProbed = ref(false)
let probing: Promise<boolean> | null = null

export function probeBackend(timeoutMs = 2500): Promise<boolean> {
  if (probing) return probing
  probing = (async () => {
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), timeoutMs)
    try {
      const res = await fetch('/api/health', { signal: controller.signal })
      demoMode.value = !res.ok
    } catch {
      demoMode.value = true
    } finally {
      window.clearTimeout(timer)
      demoProbed.value = true
    }
    return !demoMode.value
  })()
  return probing
}

// ---------------------------------------------------------------- 演示数据
// 字段与真实端点对齐（/api/budget、/api/sessions、/api/trace、/api/skills、/api/hooks）。

export const demoBudget = {
  status: {
    global_limit: 5,
    global_spent: 0,
    day: new Date().toISOString().slice(0, 10),
    day_spent: 0,
    per_minute_calls_limit: 30,
    per_minute_cost_limit: 1,
    minute_calls: 0,
    minute_cost: 0,
    sessions: {} as Record<string, { spent: number; limit: number }>,
    pricing_file: '（演示模式）',
  },
  check: {
    ok: true, reason: '', global_limit: 5, global_spent: 0,
    session_limit: 0, session_spent: 0, day_spent: 0,
    minute_calls: 0, minute_cost: 0,
    per_minute_calls_limit: 30, per_minute_cost_limit: 1,
  },
}

export const demoSessions = [
  { session_id: 'web-default', turns: 12, has_summary: true, updated_at: new Date(Date.now() - 1000 * 60 * 8).toISOString() },
  { session_id: '玩家数值排查', turns: 5, has_summary: false, updated_at: new Date(Date.now() - 1000 * 60 * 47).toISOString() },
  { session_id: '新手引导对话', turns: 3, has_summary: false, updated_at: new Date(Date.now() - 1000 * 60 * 60 * 3).toISOString() },
]

export const demoTraceSummary = {
  turns: 42, prompt_tokens: 38650, completion_tokens: 9420, total_tokens: 48070,
  avg_elapsed_ms: 6300, aborted: 0, errors: 1,
  by_provider: { ollama: { turns: 42, tokens: 48070 } },
}

export const demoTraceItems = [
  {
    turn_id: 'demo-001', ts: new Date(Date.now() - 1000 * 60 * 6).toISOString(),
    session_id: 'web-default', provider: 'ollama', model: 'qwen2.5:7b', route: 'local',
    question_chars: 18, messages_count: 6, prompt_tokens: 1284, completion_tokens: 386,
    total_tokens: 1670, cost_cny: 0, llm_calls: 2, llm_ms: 5200, elapsed_ms: 6100,
    outcome: 'completed', aborted: false, error: '',
    steps: [
      { i: 0, action: 'search_code', latency_ms: 420, obs_chars: 312, ok: true },
      { i: 1, action: 'read_file', latency_ms: 90, obs_chars: 1024, ok: true },
    ],
  },
  {
    turn_id: 'demo-002', ts: new Date(Date.now() - 1000 * 60 * 14).toISOString(),
    session_id: '玩家数值排查', provider: 'ollama', model: 'qwen3:14b', route: 'local',
    question_chars: 23, messages_count: 4, prompt_tokens: 980, completion_tokens: 245,
    total_tokens: 1225, cost_cny: 0, llm_calls: 1, llm_ms: 3900, elapsed_ms: 4300,
    outcome: 'completed', aborted: false, error: '',
    steps: [
      { i: 0, action: 'grep', latency_ms: 120, obs_chars: 488, ok: true },
    ],
  },
  {
    turn_id: 'demo-003', ts: new Date(Date.now() - 1000 * 60 * 26).toISOString(),
    session_id: '新手引导对话', provider: 'ollama', model: 'qwen2.5:7b', route: 'local',
    question_chars: 12, messages_count: 2, prompt_tokens: 410, completion_tokens: 0,
    total_tokens: 410, cost_cny: 0, llm_calls: 1, llm_ms: 0, elapsed_ms: 800,
    outcome: 'aborted', aborted: true, error: '用户手动停止',
    steps: [],
  },
]

export const demoSkills = {
  skills_dir: '（演示模式）.docmind/skills', exists: true, count: 2, errors: [] as string[],
  items: [
    { name: '引擎项目初始化', description: '检测本机 Godot/Unity/Unreal 安装并生成启动配置', when_to_use: '第一次接入某个游戏引擎时', path: 'engine-project-setup/SKILL.md' },
    { name: '冻结发布', description: '按标准清单完成测试、构建、打包与记录', when_to_use: '需要发布桌面新版本时', path: 'docmind-frozen-release/SKILL.md' },
  ],
}

export const demoHooks = {
  hooks_dir: '（演示模式）.docmind/hooks', exists: false,
  counts: { pre_tool: 0, post_tool: 0, pre_turn: 0, post_turn: 0 },
  sources: {} as Record<string, unknown>, errors: [] as string[],
}

// ---------------------------------------------------------------- 阶段 1：语义定位 / 分区卡片演示数据
export const demoLocateFiles = [
  {
    path: 'scripts/player/player_stats.gd', name: 'player_stats.gd', score: 12.5,
    reasons: ['业务标签命中', '语义向量检索'], line: 14, symbol: 'take_damage',
    region: 'values', region_name: '数值区',
    tags: ['玩家属性', '生命与战斗数值'], summary: '管理玩家生命值、攻击力与受伤结算',
  },
  {
    path: 'scripts/combat/damage_calc.gd', name: 'damage_calc.gd', score: 10.2,
    reasons: ['语义向量检索'], line: 31, symbol: 'calc_final_damage',
    region: 'values', region_name: '数值区',
    tags: ['伤害计算', '战斗系统'], summary: '攻防换算、暴击与减伤公式',
  },
  {
    path: 'scripts/player/player_controller.gd', name: 'player_controller.gd', score: 6.4,
    reasons: ['文件名命中'], line: 8, symbol: '',
    region: 'behaviors', region_name: '角色行为区',
    tags: ['玩家角色', '输入控制'], summary: '玩家移动、跳跃与输入响应',
  },
]

export const demoLocateRegions = [
  { key: 'values', name: '数值区', dir: 'values', desc: '玩家与敌人的数值配置' },
]

export const demoTagMap = {
  'scripts/player/player_stats.gd': {
    mtime: 0, size: 0, tags: ['玩家属性', '生命与战斗数值'], summary: '管理玩家生命值',
    symbols: ['take_damage', 'heal'], origin: 'llm',
  },
  'scripts/player/player_controller.gd': {
    mtime: 0, size: 0, tags: ['玩家角色', '输入控制'], summary: '玩家移动与输入',
    symbols: ['_physics_process'], origin: 'rules',
  },
  'scripts/inventory_system.gd': {
    mtime: 0, size: 0, tags: ['背包道具'], summary: '道具拾取与背包管理',
    symbols: ['add_item'], origin: 'rules',
  },
  'scripts/combat/damage_calc.gd': {
    mtime: 0, size: 0, tags: ['伤害计算', '战斗系统'], summary: '伤害公式',
    symbols: ['calc_final_damage'], origin: 'llm',
  },
  'scripts/enemy/enemy_ai.gd': {
    mtime: 0, size: 0, tags: ['敌人AI'], summary: '巡逻与追击状态机',
    symbols: ['patrol', 'chase_target'], origin: 'rules',
  },
} as Record<string, {
  mtime: number; size: number; tags: string[]; summary: string;
  symbols: string[]; origin: string;
}>

export const demoRegionCards = [
  {
    key: 'values', name: '数值区', dir: 'values', desc: '玩家与敌人的数值配置',
    access: '', depends_on: [], exports: ['stats_api.gd'], missing_exports: [],
    verify: '', exists: true, git: true, branch: 'main', dirty: true, files: 12,
    dirty_count: 2, own_repo: true,
    last_commit: {
      hash: 'a1b2c3d', full_hash: 'a1b2c3d4', message: 'feat: 调整玩家初始生命值为 120',
      author: '你', time: Date.now() / 1000 - 3600 * 5, time_raw: '',
    },
  },
  {
    key: 'behaviors', name: '角色行为区', dir: 'behaviors',
    desc: '角色行为逻辑（玩家/敌人 AI）', access: '', depends_on: ['values'],
    exports: ['behavior_api.gd'], missing_exports: [], verify: '', exists: true,
    git: true, branch: 'main', dirty: false, files: 23, dirty_count: 0, own_repo: true,
    last_commit: {
      hash: 'e4f5g6h', full_hash: 'e4f5g6h7', message: 'fix: 敌人追击穿墙的问题',
      author: '你', time: Date.now() / 1000 - 3600 * 30, time_raw: '',
    },
  },
  {
    key: 'ui', name: '界面区', dir: 'ui', desc: 'HUD、菜单与弹窗',
    access: '', depends_on: ['values'], exports: [], missing_exports: [],
    verify: '', exists: false, git: false, branch: '', dirty: false, files: 0,
    dirty_count: 0, own_repo: false, last_commit: null,
  },
]
