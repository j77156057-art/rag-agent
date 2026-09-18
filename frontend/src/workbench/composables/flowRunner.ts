// 阶段 3b｜AI 工具流编排器（前端运行器）
//
// 职责：
//  1. 加载 / 保存 / 删除流程模板（/api/flows）；
//  2. 按流程定义顺序（由连线拓扑排序得出）逐步调用既有的**受控端点**（/api/flows/run-step）；
//  3. 每一步产出与 trace step 同构的状态（pending / run / ok / fail / skipped），
//     失败即停；支持单步重跑（从失败步继续）；
//  4. 演示态（?demo=1）：不连后端，走本地模拟，保证离线也能演示整条流水线。
//
// 受控边界：前端只传「动作名 + 参数」，所有写盘/校验都落在后端受控端点，前端不直连 agent、
// 不绕过分区约束。
import { ref } from 'vue'
import { flowsApi, type FlowAction, type FlowDef, type FlowEdge, type FlowNode, type FlowStepResult } from '../api'
import { demoMode, demoFlows, demoFlowFlaky } from './demo'

/** 单步状态：与 trace step 的字段对齐（动作名/参数字数/返回字数/耗时/成败）。 */
export type StepStatus = 'pending' | 'run' | 'ok' | 'fail' | 'skipped'
export interface StepState {
  id: string
  action: string
  label: string
  status: StepStatus
  latency_ms: number
  arg_chars: number
  obs_chars: number
  output: string
  error: string
  detail: Record<string, unknown> | null
}

// ---------------------------------------------------------------------------
// 受控动作目录（后端 flows.py 白名单的前端镜像；仅用于渲染面板与参数表单）
// ---------------------------------------------------------------------------
function f(name: string, label: string, kind: FlowAction['fields'][number]['kind'],
           required = false, placeholder = ''): FlowAction['fields'][number] {
  return { name, label, kind, required, placeholder }
}

export const FLOW_ACTIONS: FlowAction[] = [
  { action: 'dev_list_regions', label: '列出分区', glyph: '区', cat: 'region', mutating: false,
    summary: '读取全部已配置分区（定位目标分区）。', fields: [] },
  { action: 'dev_list_changesets', label: '变更集列表', glyph: '集', cat: 'region', mutating: false,
    summary: '列出已记录的跨区变更集，便于选择回滚目标。', fields: [] },
  { action: 'dev_verify_contracts', label: '契约校验', glyph: '契', cat: 'region', mutating: false,
    summary: '校验全部分区的依赖方向无环、导出接口齐全。', fields: [] },
  { action: 'dev_region_verify', label: '分区校验', glyph: '运', cat: 'run', mutating: false,
    summary: '在指定分区内执行其 verify 命令（或校验导出接口）。',
    fields: [f('region', '分区 key', 'region', true, 'values')] },
  { action: 'dev_region_read', label: '读取分区文件', glyph: '读', cat: 'retrieve', mutating: false,
    summary: '读取某分区内的文件。',
    fields: [f('region', '分区 key', 'region', true, 'values'), f('path', '分区内相对路径', 'relpath', true, 'balance.json')] },
  { action: 'dev_region_edit', label: '分区内改码', glyph: '写', cat: 'write', mutating: true,
    summary: '受控修改/新建分区内文件（越区写被拒、.py 语法校验）。',
    fields: [
      f('region', '分区 key', 'region', true, 'values'),
      f('path', '分区内相对路径', 'relpath', true, 'balance.json'),
      f('new_text', '新内容', 'text', true, '替换后的完整片段…'),
      f('old_text', '精确旧片段（局部替换）', 'text', false, '要被替换的原文…'),
    ] },
  { action: 'dev_commit_all', label: '提交变更集', glyph: '提', cat: 'region', mutating: true,
    summary: '把所有分区改动各提交一次并绑定为可整体回滚的变更集。',
    fields: [f('message', '提交说明', 'message', false, 'docmind: …')] },
  { action: 'dev_rollback_changeset', label: '回滚变更集', glyph: '滚', cat: 'region', mutating: true,
    summary: '整体回滚某变更集；changeset 留空 = 回滚本次运行刚创建的那个。',
    fields: [f('changeset', '变更集 id（留空=本次运行创建的）', 'changeset', false, '')] },
  { action: 'engine_web_export', label: 'Web 导出试玩', glyph: '玩', cat: 'run', mutating: false,
    summary: '把 Godot 项目导出为 Web 产物供 iframe 试玩。', fields: [] },
]

const ACTION_MAP: Record<string, FlowAction> = Object.fromEntries(FLOW_ACTIONS.map((a) => [a.action, a]))

export function actionMeta(action: string): FlowAction {
  return ACTION_MAP[action] || { action, label: action, glyph: '?', cat: 'other', mutating: false, summary: '', fields: [] }
}

/** 新建节点 id（流程内唯一）。 */
export function newNodeId(nodes: FlowNode[]): string {
  const used = new Set(nodes.map((n) => n.id))
  let i = nodes.length + 1
  while (used.has('n' + i)) i++
  return 'n' + i
}

/** 某动作的默认参数（必填字段留空字符串，运行时由后端校验）。 */
export function defaultParams(action: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const fld of actionMeta(action).fields) out[fld.name] = ''
  return out
}

/** 由连线做拓扑排序得到执行顺序；无连线按数组顺序；有环则回退数组顺序。 */
export function deriveOrder(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  if (!edges.length) return [...nodes]
  const idx = new Map(nodes.map((n, i) => [n.id, i]))
  const indeg = new Map<string, number>()
  const adj = new Map<string, string[]>()
  for (const n of nodes) { indeg.set(n.id, 0); adj.set(n.id, []) }
  const seen = new Set<string>()
  for (const e of edges) {
    if (!byId.has(e.source) || !byId.has(e.target) || e.source === e.target) continue
    const key = e.source + '>' + e.target
    if (seen.has(key)) continue
    seen.add(key)
    adj.get(e.source)!.push(e.target)
    indeg.set(e.target, (indeg.get(e.target) || 0) + 1)
  }
  const order: FlowNode[] = []
  const ready = nodes.filter((n) => (indeg.get(n.id) || 0) === 0).map((n) => n.id)
  const sortReady = () => ready.sort((a, b) => (idx.get(a) || 0) - (idx.get(b) || 0))
  sortReady()
  const queued = new Set(ready)
  while (ready.length) {
    const cur = ready.shift()!
    order.push(byId.get(cur)!)
    for (const nxt of adj.get(cur)!) {
      indeg.set(nxt, (indeg.get(nxt) || 0) - 1)
      if ((indeg.get(nxt) || 0) === 0 && !queued.has(nxt)) { queued.add(nxt); ready.push(nxt); sortReady() }
    }
  }
  return order.length === nodes.length ? order : [...nodes]
}

function cloneFlow(f: FlowDef): FlowDef {
  return JSON.parse(JSON.stringify(f)) as FlowDef
}

function argChars(params: Record<string, string>): number {
  try { return JSON.stringify(params || {}).length } catch { return 0 }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

/** 演示态：按动作给出可信的中文结果。 */
function demoResult(node: FlowNode, ok: boolean): FlowStepResult {
  const p = node.params || {}
  switch (node.action) {
    case 'dev_list_regions':
      return { ok: true, action: node.action, status: 'ok', output: '共 8 个分区：values｜数值区（values/）…', latency_ms: 42 }
    case 'dev_region_read':
      return { ok: true, action: node.action, status: 'ok', output: `{ "player_hp": 100, "player_atk": 12 }（${p.path || 'balance.json'}）`, obs_chars: 40, latency_ms: 38 }
    case 'dev_region_edit':
      return ok
        ? { ok: true, action: node.action, status: 'ok', output: `已写入 ${p.region || 'values'}/${p.path || 'balance.json'}（128 字节，路径沙箱校验通过）。`, latency_ms: 96 }
        : { ok: false, action: node.action, status: 'fail', output: '', error: `安全限制：未找到 old_text 的匹配，请重新读取确认当前内容。`, latency_ms: 88 }
    case 'dev_region_verify':
      return ok
        ? { ok: true, action: node.action, status: 'ok', output: '运行 数值区 的内置校验（builtin:json）：已校验 3 个 JSON/TOML 文件，全部可解析。', latency_ms: 210 }
        : { ok: false, action: node.action, status: 'fail', output: '', error: 'JSON 解析失败:\nbalance.json: Expecting \',\' delimiter（行 12）', latency_ms: 205 }
    case 'dev_verify_contracts':
      return { ok: true, action: node.action, status: 'ok', output: '契约校验通过（依赖方向无环、导出接口齐全）。', latency_ms: 64 }
    case 'dev_commit_all':
      return { ok: true, action: node.action, status: 'ok', output: '已创建变更集 demo-cs-1（message=调平玩家初始生命值）。', detail: { changeset: 'demo-cs-1' }, latency_ms: 340 }
    case 'dev_rollback_changeset':
      return { ok: true, action: node.action, status: 'ok', output: `已回滚变更集 ${p.changeset || 'demo-cs-1'}。`, latency_ms: 300 }
    case 'engine_web_export':
      return { ok: true, action: node.action, status: 'ok', output: 'Web 导出完成：/play/demo-token/index.html', detail: { url: '/play/demo-token/index.html' }, latency_ms: 8600 }
    default:
      return { ok, action: node.action, status: ok ? 'ok' : 'fail', output: ok ? '完成。' : '', error: ok ? '' : '演示：该步骤失败。', latency_ms: 120 }
  }
}

// ---------------------------------------------------------------------------
// 运行器
// ---------------------------------------------------------------------------
export function useFlowRunner() {
  const flows = ref<FlowDef[]>([])
  const current = ref<FlowDef | null>(null)
  const steps = ref<StepState[]>([])
  const running = ref(false)
  const activeId = ref('')       // 正在执行的节点 id
  const runId = ref('')
  const message = ref('')        // 顶部状态提示
  const messageKind = ref<'info' | 'ok' | 'err'>('info')
  const lastChangeset = ref('')  // 本次运行最近创建的变更集 id

  let abort = false
  const attempts: Record<string, number> = {}

  // 直接读 steps（reactive）以让画布节点颜色随状态更新（不额外维护一份不可变 map）
  function stateOf(id: string): StepState | undefined {
    return steps.value.find((s) => s.id === id)
  }

  function setMessage(text: string, kind: 'info' | 'ok' | 'err' = 'info') {
    message.value = text
    messageKind.value = kind
  }

  async function loadFlows() {
    if (demoMode.value) {
      const existing = flows.value.length
      flows.value = demoFlows.map(cloneFlow)
      if (!current.value && !existing && flows.value.length) selectFlow(flows.value[0])
      return
    }
    try {
      const r = await flowsApi.list()
      flows.value = (r.flows || []).map(cloneFlow)
      if (current.value) {
        const still = flows.value.find((x) => x.id === current.value!.id)
        if (still) selectFlow(still)
      }
    } catch (e) {
      setMessage((e as Error).message || '流程列表加载失败', 'err')
    }
  }

  function selectFlow(flow: FlowDef) {
    current.value = cloneFlow(flow)
    buildSteps()
    setMessage(`已载入流程「${flow.name}」，共 ${flow.nodes.length} 个步骤。`, 'info')
  }

  function buildSteps() {
    const f = current.value
    if (!f) { steps.value = []; return }
    const order = deriveOrder(f.nodes, f.edges)
    steps.value = order.map((n) => ({
      id: n.id, action: n.action, label: n.label || actionMeta(n.action).label,
      status: 'pending' as StepStatus, latency_ms: 0, arg_chars: 0, obs_chars: 0,
      output: '', error: '', detail: null,
    }))
  }

  function newFlow() {
    current.value = {
      id: '', name: '新流程', desc: '',
      nodes: [{ id: 'n1', action: 'dev_verify_contracts', label: '契约校验', params: {} }],
      edges: [],
    }
    buildSteps()
    setMessage('已新建空白流程：点左侧动作面板添加步骤。', 'info')
  }

  /** 新增一个步骤节点（追加在末尾，并自动按当前顺序重连）。 */
  function addNode(action: string) {
    const f = current.value
    if (!f) return
    if (f.nodes.length >= 40) { setMessage('节点数已达上限（40）。', 'err'); return }
    const id = newNodeId(f.nodes)
    f.nodes.push({ id, action, label: actionMeta(action).label, params: defaultParams(action) })
    relinkChain()
    buildSteps()
  }

  function removeNode(id: string) {
    const f = current.value
    if (!f) return
    f.nodes = f.nodes.filter((n) => n.id !== id)
    f.edges = f.edges.filter((e) => e.source !== id && e.target !== id)
    relinkChain()
    buildSteps()
  }

  function moveNode(id: string, dir: -1 | 1) {
    const f = current.value
    if (!f) return
    const i = f.nodes.findIndex((n) => n.id === id)
    const j = i + dir
    if (i < 0 || j < 0 || j >= f.nodes.length) return
    const [n] = f.nodes.splice(i, 1)
    f.nodes.splice(j, 0, n)
    relinkChain()
    buildSteps()
  }

  /** 按当前节点数组顺序，把连线重连成一条链。 */
  function relinkChain() {
    const f = current.value
    if (!f) return
    f.edges = f.nodes.slice(0, -1).map((n, i) => ({ source: n.id, target: f.nodes[i + 1].id }))
  }

  function connect(source: string, target: string) {
    const f = current.value
    if (!f || source === target) return
    if (f.edges.some((e) => e.source === source && e.target === target)) return
    f.edges.push({ source, target })
    buildSteps()
  }

  function disconnect(source: string, target: string) {
    const f = current.value
    if (!f) return
    f.edges = f.edges.filter((e) => !(e.source === source && e.target === target))
    buildSteps()
  }

  function setNodeParams(id: string, patch: Record<string, string>) {
    const f = current.value
    if (!f) return
    const n = f.nodes.find((x) => x.id === id)
    if (!n) return
    n.params = { ...n.params, ...patch }
  }

  async function saveFlow(): Promise<boolean> {
    const f = current.value
    if (!f) return false
    if (demoMode.value) {
      const id = f.id || ('flow-demo-' + Math.random().toString(16).slice(2, 8))
      const saved = cloneFlow({ ...f, id })
      const i = flows.value.findIndex((x) => x.id === id)
      if (i >= 0) flows.value[i] = saved
      else flows.value.push(saved)
      current.value = cloneFlow(saved)
      setMessage('演示模式：已保存流程（仅本地，不写盘）。', 'ok')
      return true
    }
    try {
      const r = await flowsApi.save(f)
      if (!r.ok || !r.flow) {
        setMessage(r.error || '流程保存失败（后端校验未通过）。', 'err')
        return false
      }
      current.value = cloneFlow(r.flow)
      const i = flows.value.findIndex((x) => x.id === r.flow!.id)
      if (i >= 0) flows.value[i] = cloneFlow(r.flow)
      else flows.value.push(cloneFlow(r.flow))
      setMessage(`已保存流程「${r.flow.name}」。`, 'ok')
      return true
    } catch (e) {
      setMessage((e as Error).message || '流程保存失败', 'err')
      return false
    }
  }

  async function deleteFlow(id: string): Promise<boolean> {
    if (demoMode.value) {
      flows.value = flows.value.filter((x) => x.id !== id)
      if (current.value?.id === id) { current.value = null; buildSteps() }
      setMessage('演示模式：已删除流程。', 'ok')
      return true
    }
    try {
      const r = await flowsApi.remove(id)
      if (!r.ok) { setMessage(r.error || '删除失败', 'err'); return false }
      flows.value = flows.value.filter((x) => x.id !== id)
      if (current.value?.id === id) { current.value = null; buildSteps() }
      setMessage('已删除流程。', 'ok')
      return true
    } catch (e) {
      setMessage((e as Error).message || '删除失败', 'err')
      return false
    }
  }

  function resetStatuses(fromIndex: number) {
    const order = orderNodes()
    for (let i = fromIndex; i < order.length; i++) {
      const st = stateOf(order[i].id)
      if (st) {
        st.status = 'pending'; st.latency_ms = 0; st.arg_chars = 0; st.obs_chars = 0
        st.output = ''; st.error = ''; st.detail = null
      }
    }
  }

  function orderNodes(): FlowNode[] {
    const f = current.value
    return f ? deriveOrder(f.nodes, f.edges) : []
  }

  async function callStep(node: FlowNode, runIdVal: string, finish: boolean): Promise<FlowStepResult> {
    const params = { ...(node.params || {}) }
    if (node.action === 'dev_rollback_changeset' && !params.changeset) params.changeset = lastChangeset.value
    if (demoMode.value) {
      await delay(360 + Math.floor(Math.random() * 420))
      const n = (attempts[node.id] = (attempts[node.id] || 0) + 1)
      const flaky = (demoFlowFlaky[current.value?.id || ''] || []).includes(node.id)
      const ok = !(flaky && n === 1)
      const res = demoResult(node, ok)
      res.arg_chars = argChars(params)
      res.obs_chars = (res.output || '').length
      if (res.ok && res.detail && typeof res.detail.changeset === 'string') lastChangeset.value = res.detail.changeset
      return res
    }
    const res = await flowsApi.runStep({
      action: node.action, params, run_id: runIdVal,
      flow_id: current.value?.id || '', flow_name: current.value?.name || '', node_id: node.id, finish,
    })
    res.arg_chars = res.arg_chars ?? argChars(params)
    res.obs_chars = res.obs_chars ?? (res.output || '').length
    if (res.ok && res.detail && typeof res.detail.changeset === 'string') lastChangeset.value = res.detail.changeset
    return res
  }

  function apply(st: StepState, res: FlowStepResult) {
    st.status = res.ok ? 'ok' : 'fail'
    st.latency_ms = res.latency_ms || 0
    st.arg_chars = res.arg_chars ?? 0
    st.obs_chars = res.obs_chars ?? 0
    st.output = res.output || ''
    st.error = res.error || ''
    st.detail = res.detail || null
  }

  /** 从 fromIndex 开始按序执行；失败即停。 */
  async function run(fromIndex = 0) {
    const f = current.value
    if (!f || running.value) return
    const order = orderNodes()
    if (!order.length) { setMessage('没有可执行的步骤。', 'err'); return }
    running.value = true
    abort = false
    runId.value = 'run-' + Date.now().toString(36) + Math.random().toString(16).slice(2, 6)
    if (fromIndex === 0) lastChangeset.value = ''
    resetStatuses(fromIndex)
    setMessage('开始执行流水线…', 'info')

    let failedAt = -1
    for (let i = fromIndex; i < order.length; i++) {
      if (abort) {
        for (let j = i; j < order.length; j++) {
          const st = stateOf(order[j].id); if (st) st.status = 'skipped'
        }
        break
      }
      const node = order[i]
      const st = stateOf(node.id)
      if (!st) continue
      st.status = 'run'
      activeId.value = node.id
      setMessage(`第 ${i + 1}/${order.length} 步：${st.label} …`, 'info')
      let res: FlowStepResult
      try {
        res = await callStep(node, runId.value, i === order.length - 1)
      } catch (e) {
        res = { ok: false, action: node.action, status: 'fail', output: '', error: (e as Error).message || '请求失败' }
      }
      apply(st, res)
      if (!res.ok) { failedAt = i; break }
    }
    activeId.value = ''
    running.value = false
    if (abort) setMessage('已停止（未执行的步骤标为跳过）。', 'err')
    else if (failedAt >= 0) setMessage(`执行在第 ${failedAt + 1} 步失败（红）。修复后可从该步「重跑」。`, 'err')
    else setMessage('流水线执行完成（全绿）。', 'ok')
  }

  /** 单步重跑：只执行该节点（作为独立回合即时记录 trace）。 */
  async function runSingle(id: string) {
    const f = current.value
    if (!f || running.value) return
    const node = f.nodes.find((n) => n.id === id)
    const st = stateOf(id)
    if (!node || !st) return
    running.value = true
    abort = false
    st.status = 'run'
    activeId.value = id
    setMessage(`重跑单步：${st.label} …`, 'info')
    let res: FlowStepResult
    try {
      res = await callStep(node, '', true)
    } catch (e) {
      res = { ok: false, action: node.action, status: 'fail', output: '', error: (e as Error).message || '请求失败' }
    }
    apply(st, res)
    activeId.value = ''
    running.value = false
    setMessage(res.ok ? `单步「${st.label}」成功。` : `单步「${st.label}」失败：${st.error}`, res.ok ? 'ok' : 'err')
  }

  /** 从某个节点开始继续执行（失败步修复后续跑）。 */
  async function resumeFrom(id: string) {
    const order = orderNodes()
    const i = order.findIndex((n) => n.id === id)
    if (i < 0) { setMessage('未找到该步骤。', 'err'); return }
    await run(i)
  }

  function stop() { abort = true }

  return {
    // state
    flows, current, steps, running, activeId, message, messageKind, lastChangeset,
    // helpers
    stateOf, orderNodes,
    // actions
    loadFlows, selectFlow, newFlow, buildSteps,
    addNode, removeNode, moveNode, relinkChain, connect, disconnect, setNodeParams,
    saveFlow, deleteFlow, run, runSingle, resumeFrom, stop,
  }
}

export type FlowRunner = ReturnType<typeof useFlowRunner>
