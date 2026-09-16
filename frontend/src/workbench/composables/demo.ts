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

// ---------------------------------------------------------------- 阶段 2：素材中心演示数据
/** 生成内联 SVG 缩略图（离线演示无网络，占位图也要能直接显示）。 */
function svgThumb(inner: string, bg = '#eef2f8'): string {
  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="336" height="220" viewBox="0 0 336 220">` +
    `<rect width="336" height="220" fill="${bg}"/>${inner}</svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

const thumbCube = svgThumb(
  '<path d="M168 52 L238 88 L238 158 L168 194 L98 158 L98 88 Z M168 52 L168 122 M98 88 L168 122 L238 88 M168 122 L168 194" '
  + 'fill="none" stroke="#2f6fed" stroke-width="3" stroke-linejoin="round"/>')
const thumbTree = svgThumb(
  '<path d="M168 40 L214 118 H122 Z M168 84 L226 168 H110 Z" fill="rgba(47,111,237,.18)" stroke="#2f6fed" stroke-width="3" stroke-linejoin="round"/>'
  + '<rect x="160" y="166" width="16" height="26" fill="#8a6d4b"/>')
const thumbWood = svgThumb(
  '<g stroke="#b98a57" stroke-width="3">' + [58, 88, 118, 148, 178].map((y) =>
    `<path d="M40 ${y} Q120 ${y - 12} 200 ${y + 4} T296 ${y - 2}" fill="none"/>`).join('') + '</g>', '#f3ead9')
const thumbGrid = svgThumb(
  '<g stroke="rgba(47,111,237,.55)" stroke-width="2">' +
  [108, 168, 228].map((x) => `<line x1="${x}" y1="40" x2="${x}" y2="180"/>`).join('') +
  [70, 110, 150].map((y) => `<line x1="78" y1="${y}" x2="258" y2="${y}"/>`).join('') + '</g>')
const thumbHdri = svgThumb(
  '<circle cx="168" cy="110" r="62" fill="none" stroke="#2f6fed" stroke-width="3"/>'
  + '<circle cx="168" cy="110" r="20" fill="rgba(47,111,237,.25)"/>'
  + '<g stroke="#2f6fed" stroke-width="2"><line x1="168" y1="30" x2="168" y2="48"/><line x1="168" y1="172" x2="168" y2="190"/>'
  + '<line x1="88" y1="110" x2="106" y2="110"/><line x1="230" y1="110" x2="248" y2="110"/></g>')
const thumbSunset = svgThumb(
  '<circle cx="168" cy="128" r="40" fill="rgba(214,137,52,.55)" stroke="#c97a2b" stroke-width="3"/>'
  + '<line x1="40" y1="150" x2="296" y2="150" stroke="#8a6d4b" stroke-width="3"/>',
  '#f6e7d3')
const thumbUi = svgThumb(
  '<rect x="78" y="78" width="180" height="64" rx="8" fill="#fff" stroke="#2f6fed" stroke-width="3"/>'
  + '<rect x="96" y="98" width="80" height="10" rx="5" fill="rgba(47,111,237,.45)"/>'
  + '<rect x="96" y="118" width="50" height="10" rx="5" fill="rgba(47,111,237,.25)"/>'
  + '<rect x="196" y="106" width="46" height="22" rx="6" fill="#2f6fed"/>')
const thumbAudio = svgThumb(
  '<g stroke="#2f6fed" stroke-width="4" stroke-linecap="round">' +
  [86, 110, 134, 158, 182, 206, 230].map((x, i) =>
    `<line x1="${x}" y1="${110 - 28 - (i % 3) * 12}" x2="${x}" y2="${110 + 28 + ((i + 1) % 3) * 12}"/>`).join('') + '</g>')

export const demoAssetResults = [
  { id: 'vintage_armchair', source: 'polyhaven', kind: 'model', name: '复古扶手椅',
    author: 'Kirill Sannikov', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbCube, tags: ['家具', '室内'], summary: '带布艺坐垫的木质扶手椅，室内场景道具' },
  { id: 'lowpoly_oak', source: 'polyhaven', kind: 'model', name: '低多边形橡树',
    author: 'Dairon Sanchez', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbTree, tags: ['自然', '植物'], summary: '风格化低多边形树木，可直接放进关卡' },
  { id: 'wood_planks_01', source: 'polyhaven', kind: 'texture', name: '旧木板纹理',
    author: 'Poly Haven', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbWood, tags: ['木材', 'PBR'], summary: '无缝拼接旧木板 PBR 贴图（颜色/法线/粗糙度）' },
  { id: 'prototype_grid', source: 'polyhaven', kind: 'texture', name: '原型网格贴图',
    author: 'Poly Haven', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbGrid, tags: ['原型', '网格'], summary: '灰盒阶段用的网格贴图，便于判断比例' },
  { id: 'studio_softbox', source: 'polyhaven', kind: 'hdri', name: '摄影棚柔光',
    author: 'Poly Haven', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbHdri, tags: ['室内', '布光'], summary: '中性柔光摄影棚环境，展示模型默认灯光' },
  { id: 'sunset_field', source: 'polyhaven', kind: 'hdri', name: '黄昏旷野',
    author: 'Poly Haven', license: 'CC0', page_url: 'https://polyhaven.com/',
    thumb_url: thumbSunset, tags: ['户外', '黄昏'], summary: '暖色调黄昏户外环境光' },
]

export const demoKenneyPacks = [
  { slug: 'prototype-kit', name: '原型套件', kinds: ['model'], summary: '灰盒原型用几何体模块，搭关卡最快的一套',
    source: 'kenney', license: 'CC0', page_url: 'https://kenney.nl/assets/prototype-kit', thumb_url: thumbCube },
  { slug: 'nature-kit', name: '自然套件', kinds: ['model'], summary: '树木、岩石、灌木等自然道具',
    source: 'kenney', license: 'CC0', page_url: 'https://kenney.nl/assets/nature-kit', thumb_url: thumbTree },
  { slug: 'prototype-textures', name: '原型纹理集', kinds: ['texture'], summary: '网格/棋盘格原型贴图，快速拼出灰盒关卡',
    source: 'kenney', license: 'CC0', page_url: 'https://kenney.nl/assets/prototype-textures', thumb_url: thumbGrid },
  { slug: 'ui-pack', name: 'UI 套件', kinds: ['2d'], summary: '按钮、面板、图标等通用界面素材',
    source: 'kenney', license: 'CC0', page_url: 'https://kenney.nl/assets/ui-pack', thumb_url: thumbUi },
]

export const demoPackFiles = [
  { path: 'Models/primitive_cube.glb', show_path: 'Models/primitive_cube.glb', ext: '.glb', kind: 'model', size: 1820, dep: false },
  { path: 'Models/primitive_cylinder.glb', show_path: 'Models/primitive_cylinder.glb', ext: '.glb', kind: 'model', size: 2140, dep: false },
  { path: 'Models/primitive_plane.glb', show_path: 'Models/primitive_plane.glb', ext: '.glb', kind: 'model', size: 980, dep: false },
  { path: 'Models/ramp_01.glb', show_path: 'Models/ramp_01.glb', ext: '.glb', kind: 'model', size: 1560, dep: false },
  { path: 'Textures/grid_01.png', show_path: 'Textures/grid_01.png', ext: '.png', kind: 'image', size: 3400, dep: false },
  { path: 'License.txt', show_path: 'License.txt', ext: '.txt', kind: 'other', size: 1200, dep: false },
]

export const demoAssetLibrary = [
  { path: 'assets/models/vintage_armchair.glb', name: 'vintage_armchair.glb', kind: 'model', size: 248320,
    mtime: Date.now() / 1000 - 3600, source: 'polyhaven', author: 'Kirill Sannikov',
    license: 'CC0', imported_at: '', duplicate: false, thumb: thumbCube },
  { path: 'assets/models/lowpoly_oak.glb', name: 'lowpoly_oak.glb', kind: 'model', size: 184200,
    mtime: Date.now() / 1000 - 7200, source: 'polyhaven', author: 'Dairon Sanchez',
    license: 'CC0', imported_at: '', duplicate: false, thumb: thumbTree },
  { path: 'assets/textures/wood_planks_01.jpg', name: 'wood_planks_01.jpg', kind: 'texture', size: 924532,
    mtime: Date.now() / 1000 - 86400, source: 'polyhaven', author: 'Poly Haven',
    license: 'CC0', imported_at: '', duplicate: false, thumb: thumbWood },
  { path: 'assets/generated/hero_bg.png', name: 'hero_bg.png', kind: 'image', size: 512004,
    mtime: Date.now() / 1000 - 172800, source: 'comfyui', author: '',
    license: '', imported_at: '', duplicate: false, thumb: thumbSunset },
  { path: 'assets/interface-sounds/Audio/click_01.wav', name: 'click_01.wav', kind: 'audio', size: 18420,
    mtime: Date.now() / 1000 - 259200, source: 'kenney', author: 'Kenney',
    license: 'CC0', imported_at: '', duplicate: false, thumb: thumbAudio },
]

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
