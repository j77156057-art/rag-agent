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
