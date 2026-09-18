<script setup lang="ts">
// 阶段 3｜AI 工具流画布 —— trace 回看：
// 把每一轮问答（/api/trace 的一条记录）画成纵向流水线：
//   提问 → AI 决策 → 调用工具（检索/改码/分区/试玩/联网…）→ … → AI 组织回答 → 结果
// 每个节点显示真实元数据（耗时 / 输入输出规模 / 成败 / token / 花费），失败节点标红，
// 点节点看右侧详情。trace 按隐私设计只存元数据（不存提问正文与文件路径），这里不伪造跳转。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { VueFlow, useVueFlow, Handle, Position } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import type { Node, Edge } from '@vue-flow/core'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import { harnessApi, fmtTraceError, type TraceItem, type TraceStep, type FlowNode } from '../api'
import { demoMode, demoTraceItems } from '../composables/demo'
import { useWorkbench } from '../composables/workbench'
import {
  useFlowRunner, FLOW_ACTIONS, actionMeta as flowActionMeta, type StepState,
} from '../composables/flowRunner'

const { flowOpen, closeFlow } = useWorkbench()
// 回看 / 编排两个 VueFlow 实例用显式 id，避免默认 store 串扰（同一时刻只挂载其一）
const { fitView } = useVueFlow('flow-review')

// --------------------------------------------------------------- 动作元数据
// 后端真实工具名 → 中文类别/名称（与 tools.py TOOLS 注册表对齐；未知动作不编造，原样展示）
type CatKey = 'retrieve' | 'write' | 'region' | 'run' | 'web' | 'mcp' | 'asset' | 'bug' | 'task' | 'agent' | 'other'
const CAT_META: Record<CatKey, { text: string; glyph: string; color: string }> = {
  retrieve: { text: '检索', glyph: '检', color: '#2f6fed' },
  write: { text: '改码', glyph: '写', color: '#b3561f' },
  region: { text: '分区/Git', glyph: '区', color: '#7a4fd1' },
  run: { text: '运行校验', glyph: '运', color: '#128053' },
  web: { text: '联网', glyph: '网', color: '#0e8a8f' },
  mcp: { text: '引擎连接器', glyph: 'M', color: '#5a67d8' },
  asset: { text: '素材', glyph: '素', color: '#c4761a' },
  bug: { text: 'Bug', glyph: 'B', color: '#d23b42' },
  task: { text: '任务数值', glyph: '务', color: '#5a6778' },
  agent: { text: '多智能体', glyph: '派', color: '#9333ea' },
  other: { text: '其他', glyph: '?', color: '#8a94a6' },
}
const ACTION_META: Record<string, { cat: CatKey; label: string }> = {
  search_code: { cat: 'retrieve', label: '语义搜代码' },
  read_file: { cat: 'retrieve', label: '读取文件' },
  grep: { cat: 'retrieve', label: '关键字搜索' },
  list_dir: { cat: 'retrieve', label: '列出目录' },
  search_knowledge: { cat: 'retrieve', label: '知识库检索' },
  game_impact: { cat: 'retrieve', label: '影响面分析' },
  read_external_file: { cat: 'retrieve', label: '读取外部文件' },
  apply_edit: { cat: 'write', label: '应用代码修改' },
  create_file: { cat: 'write', label: '新建文件' },
  dev_region_edit: { cat: 'write', label: '分区内改码' },
  dev_refactor: { cat: 'write', label: '跨区重构' },
  create_external_file: { cat: 'write', label: '新建外部文件' },
  edit_external_file: { cat: 'write', label: '修改外部文件' },
  delete_external_file: { cat: 'write', label: '删除文件' },
  init_regions: { cat: 'region', label: '初始化分区' },
  dev_list_regions: { cat: 'region', label: '查看分区' },
  dev_region_read: { cat: 'region', label: '读取分区' },
  dev_region_verify: { cat: 'region', label: '校验分区改动' },
  dev_commit: { cat: 'region', label: '提交变更集' },
  dev_commit_all: { cat: 'region', label: '全部提交' },
  dev_verify_contracts: { cat: 'region', label: '契约校验' },
  dev_rebuild_index: { cat: 'region', label: '重建索引' },
  dev_list_changesets: { cat: 'region', label: '变更集列表' },
  dev_rollback_changeset: { cat: 'region', label: '回滚变更集' },
  dev_propose_regions: { cat: 'region', label: '生成分区方案' },
  dev_apply_regions: { cat: 'region', label: '应用分区' },
  dev_add_region: { cat: 'region', label: '新增分区' },
  dev_approve: { cat: 'region', label: '审批' },
  dev_approval_status: { cat: 'region', label: '审批状态' },
  run_command: { cat: 'run', label: '运行命令' },
  game_playtest: { cat: 'run', label: '试玩验证' },
  game_validate_data: { cat: 'run', label: '配置格式校验' },
  game_release_check: { cat: 'run', label: '发布前检查' },
  python_exec: { cat: 'run', label: '执行 Python' },
  web_research: { cat: 'web', label: '联网研究' },
  web_fetch: { cat: 'web', label: '读取网页' },
  web_search: { cat: 'web', label: '联网搜索' },
  web_subtitles: { cat: 'web', label: '读取视频字幕' },
  dev_http_request: { cat: 'web', label: 'HTTP 请求' },
  dev_mcp_call: { cat: 'mcp', label: '调用引擎连接器' },
  dev_list_connectors: { cat: 'mcp', label: '连接器列表' },
  dev_route_connector: { cat: 'mcp', label: '选择连接器' },
  dev_list_connector_tools: { cat: 'mcp', label: '连接器工具清单' },
  search_assets: { cat: 'asset', label: '搜索素材' },
  dev_asset_get: { cat: 'asset', label: '获取素材' },
  dev_asset_register: { cat: 'asset', label: '素材登记入库' },
  dev_capture_bug: { cat: 'bug', label: '抓取 Bug' },
  dev_list_bugs: { cat: 'bug', label: 'Bug 列表' },
  dev_update_bug: { cat: 'bug', label: '更新 Bug' },
  game_upsert_task: { cat: 'task', label: '开发任务' },
  game_simulate: { cat: 'task', label: '数值模拟' },
  calculate: { cat: 'task', label: '计算' },
  gen_video_prompt: { cat: 'task', label: '生成视频提示词' },
  delegate: { cat: 'agent', label: '委派子智能体' },
  orchestrate: { cat: 'agent', label: '编排多智能体' },
}
function actionMeta(action: string) {
  return ACTION_META[action] || { cat: 'other' as CatKey, label: action }
}

// --------------------------------------------------------------- 状态
const traces = ref<TraceItem[]>([])
const loading = ref(false)
const errorMsg = ref('')
const selectedTurnId = ref('')
const selectedNodeId = ref<string | null>(null)
const filterMode = ref<'all' | 'issue'>('all')
const filterText = ref('')

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    if (demoMode.value) {
      traces.value = [...demoTraceItems]
    } else {
      const r = await harnessApi.trace(60)
      traces.value = r.items || []
    }
    if (!selectedTurnId.value || !traces.value.some(t => t.turn_id === selectedTurnId.value)) {
      selectedTurnId.value = traces.value.length ? traces.value[traces.value.length - 1].turn_id : ''
    }
    selectedNodeId.value = null
    void refreshFit()
  } catch (e) {
    errorMsg.value = (e as Error).message || '操作记录加载失败'
  } finally {
    loading.value = false
  }
}
watch(flowOpen, (v) => { if (v) void load() })

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape' && flowOpen.value) closeFlow()
}
window.addEventListener('keydown', onKey)
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))

// --------------------------------------------------------------- 回合列表
const turnsNewFirst = computed(() => [...traces.value].reverse())
const filteredTurns = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  return turnsNewFirst.value.filter((t) => {
    if (filterMode.value === 'issue' && !isIssueTurn(t)) return false
    if (!q) return true
    const hay = [t.provider, t.model, t.outcome, t.session_id, ...(t.steps || []).map(s => s.action)].join(' ').toLowerCase()
    return hay.includes(q)
  })
})
function isIssueTurn(t: TraceItem) {
  return !!(t.aborted || t.error || (t.steps || []).some(s => s.ok === false))
}
function turnStatus(t: TraceItem) {
  if (t.aborted) return { text: '停止', cls: 'stop' }
  if (t.error) return { text: '出错', cls: 'err' }
  if (t.outcome === 'completed') return { text: '完成', cls: 'ok' }
  return { text: t.outcome || '完成', cls: 'warn' }
}
function fmtTime(iso: string) {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function fmtMs(ms: number) {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function money(v: number) {
  return `¥${Number(v || 0).toFixed(4)}`
}
function selectTurn(id: string) {
  if (selectedTurnId.value === id) return
  selectedTurnId.value = id
  selectedNodeId.value = null
  void refreshFit()
}
async function refreshFit() {
  await nextTick()
  await nextTick()
  try { fitView({ padding: 0.25, duration: 200 }) } catch { /* 节点为空时忽略 */ }
}

const selectedTurn = computed(() => traces.value.find(t => t.turn_id === selectedTurnId.value) || null)

// --------------------------------------------------------------- 构图
type FlNodeData = {
  kind: 'start' | 'think' | 'tool' | 'end'
  turn: TraceItem
  step?: TraceStep
  index?: number
  total?: number
}
const W_TOOL = 300, W_LLM = 200, W_TERM = 300
const H_START = 96, H_LLM = 52, H_TOOL = 104, H_END = 108, GAP = 26

const graphModel = computed<{ nodes: Node<FlNodeData>[]; edges: Edge[] }>(() => {
  const t = selectedTurn.value
  if (!t) return { nodes: [], edges: [] }
  const nodes: Node<FlNodeData>[] = []
  const seq: string[] = []
  let y = 0
  const push = (id: string, type: string, w: number, h: number, data: FlNodeData) => {
    nodes.push({ id, type, position: { x: -w / 2, y }, data, draggable: false, selectable: false })
    seq.push(id)
    y += h + GAP
  }
  push('start', 'fl-start', W_TERM, H_START, { kind: 'start', turn: t })
  const steps = t.steps || []
  const hasLLM = (t.llm_calls || 0) > 0
  steps.forEach((s, i) => {
    if (hasLLM) push(`think${i}`, 'fl-think', W_LLM, H_LLM, { kind: 'think', turn: t, index: i + 1, total: steps.length + 1 })
    push(`step${i}`, 'fl-tool', W_TOOL, H_TOOL, { kind: 'tool', turn: t, step: s, index: i, total: steps.length })
  })
  if (hasLLM) push('thinkFinal', 'fl-think', W_LLM, H_LLM, { kind: 'think', turn: t, index: steps.length + 1, total: steps.length + 1 })
  push('end', 'fl-end', W_TERM, H_END, { kind: 'end', turn: t })

  const edgeColor = (id: string) => {
    if (id.startsWith('step')) {
      const i = Number(id.slice(4))
      return steps[i] && steps[i].ok === false ? '#e0525a' : undefined
    }
    if (id === 'end') return t.error ? '#e0525a' : t.aborted ? '#e0a13a' : undefined
    return undefined
  }
  const edges: Edge[] = []
  for (let i = 1; i < seq.length; i++) {
    const c = edgeColor(seq[i])
    edges.push({
      id: `e${i}`, source: seq[i - 1], target: seq[i],
      type: 'smoothstep', style: c ? { stroke: c, strokeWidth: 2 } : undefined,
    })
  }
  return { nodes, edges }
})

const nodes = computed(() => graphModel.value.nodes)
const edges = computed(() => graphModel.value.edges)

function onNodeClick(p: { node: Node }) {
  selectedNodeId.value = p.node.id
}
const selectedDetail = computed<FlNodeData | null>(() => {
  if (!selectedNodeId.value) return null
  return graphModel.value.nodes.find(n => n.id === selectedNodeId.value)?.data ?? null
})

// 节点卡内展示用小工具
function stepMeta(s: TraceStep) { return actionMeta(s.action) }
function endStatus(t: TraceItem) {
  if (t.aborted) return { text: '手动停止', cls: 'stop', glyph: '⏸', desc: '这一轮在完成前被你手动停止。' }
  if (t.error) return { text: '出错中断', cls: 'err', glyph: '!', desc: '执行过程中发生错误，未生成最终回答。' }
  if (t.outcome === 'max_steps') return { text: '步数用尽', cls: 'warn', glyph: '⌛', desc: '工具调用达到上限后被迫收尾。' }
  if (t.outcome === 'completed') return { text: '完成回答', cls: 'ok', glyph: '✓', desc: 'AI 已基于工具结果给出最终回答。' }
  return { text: t.outcome || '完成', cls: 'warn', glyph: '?', desc: `结局标记：${t.outcome || '无'}` }
}
function providerLabel(p: string) {
  if (!p || p === '?') return '测试/离线'
  return p
}

// =============================================================== 阶段 3b：编排模式
// 与回看模式共用同一套 .fl-node 渲染与红/黄/绿样式；编排模式在其上增加：
// 可拖拽节点面板、连线编辑（拖拽 Handle 连线）、参数表单、模板保存/列表、一键执行逐步高亮。
const mode = ref<'review' | 'build'>('review')
const {
  flows: flowList, current: flowCurrent, running: flowRunning,
  activeId: flowActiveId, message: flowMessage, messageKind: flowMessageKind,
  lastChangeset: flowLastChangeset,
  stateOf: flowStateOf, orderNodes: flowOrderNodes,
  loadFlows, selectFlow, newFlow, addNode, removeNode, moveNode, connect,
  setNodeParams, saveFlow, deleteFlow, run: runFlow, runSingle: runFlowSingle,
  resumeFrom: resumeFlowFrom, stop: stopFlow,
} = useFlowRunner()

const addAction = ref<string>('dev_region_verify')
const buildSelectedId = ref<string>('')
const buildSelected = computed<FlowNode | null>(() => {
  const f = flowCurrent.value
  if (!f || !buildSelectedId.value) return null
  return f.nodes.find((n) => n.id === buildSelectedId.value) || null
})

watch(flowOpen, (v) => { if (v && mode.value === 'build') void loadFlows() })
watch(mode, (v) => { if (v === 'build') void loadFlows() })

type BuildNodeData = { node?: FlowNode; st?: StepState; order?: number; count?: number }
const W_BSTEP = 300, H_BSTEP = 100, H_BTERM = 62

const buildModel = computed<{ nodes: Node<BuildNodeData>[]; edges: Edge[] }>(() => {
  const f = flowCurrent.value
  if (!f) return { nodes: [], edges: [] }
  const order = flowOrderNodes()
  const orderIdx = new Map(order.map((n, i) => [n.id, i]))
  const nodes: Node<BuildNodeData>[] = []
  nodes.push({ id: '__start', type: 'fl-bstart', position: { x: -W_BSTEP / 2, y: 0 }, data: {}, draggable: false, selectable: false })
  let y = H_BTERM + 30
  f.nodes.forEach((n, i) => {
    nodes.push({
      id: n.id, type: 'fl-bstep',
      position: { x: (n.x ?? (-W_BSTEP / 2)), y: (n.y ?? y) },
      data: { node: n, st: flowStateOf(n.id), order: (orderIdx.get(n.id) ?? i) + 1 },
      draggable: true, selectable: true,
    })
    if (n.x == null) y += H_BSTEP + 28
  })
  nodes.push({ id: '__end', type: 'fl-bend', position: { x: -W_BSTEP / 2, y: y + 6 }, data: { count: f.nodes.length }, draggable: false, selectable: false })
  const edges: Edge[] = []
  const first = order[0]?.id
  const last = order[order.length - 1]?.id
  if (first) edges.push({ id: 'be-start', source: '__start', target: first, type: 'smoothstep', style: { stroke: '#b9c9e2', strokeDasharray: '4 3' } })
  f.edges.forEach((e, i) => {
    const st = flowStateOf(e.source)
    const c = st?.status === 'fail' ? '#e0525a' : st?.status === 'ok' ? '#2fbf7e' : undefined
    edges.push({
      id: 'be' + i, source: e.source, target: e.target, type: 'smoothstep',
      animated: st?.status === 'run',
      style: c ? { stroke: c, strokeWidth: 2 } : undefined,
    })
  })
  if (last) edges.push({ id: 'be-end', source: last, target: '__end', type: 'smoothstep', style: { stroke: '#b9c9e2', strokeDasharray: '4 3' } })
  return { nodes, edges }
})
const buildNodes = computed(() => buildModel.value.nodes)
const buildEdges = computed(() => buildModel.value.edges)

// Vue Flow v1 事件载荷是单个对象 { node, ... }；两种形态都兜住（与 SceneCanvas 同款）
function pickNode(args: unknown[]): Node | undefined {
  const looksLikeNode = (value: unknown): value is Node =>
    !!value && typeof value === 'object' && 'id' in value && 'position' in value
  if (looksLikeNode(args[1])) return args[1]
  const first = args[0] as { node?: unknown } | null | undefined
  return looksLikeNode(first?.node) ? (first!.node as Node) : undefined
}
function onBuildNodeClick(p: { node: Node }) { buildSelectedId.value = p.node.id }
function onBuildNodeDragStop(...args: unknown[]) {
  const node = pickNode(args)
  const f = flowCurrent.value
  if (!node || !f) return
  const n = f.nodes.find((x) => x.id === node.id)
  if (n) { n.x = node.position.x; n.y = node.position.y }
}
function onBuildConnect(conn: unknown) {
  const c = conn as { source?: string | null; target?: string | null } | null
  if (c && c.source && c.target) connect(c.source, c.target)
}
function onParam(id: string, name: string, value: string) { setNodeParams(id, { [name]: value }) }
function onFlowName(v: string) { if (flowCurrent.value) flowCurrent.value.name = v }
function addStep() {
  addNode(addAction.value)
  const f = flowCurrent.value
  if (f && f.nodes.length) buildSelectedId.value = f.nodes[f.nodes.length - 1].id
}
function removeSelected() {
  if (!buildSelected.value) return
  const id = buildSelected.value.id
  removeNode(id)
  buildSelectedId.value = ''
}
function stepStatusClass(st?: StepState) { return st ? ('st-' + st.status) : '' }
function glyphStyle(action: string) {
  const cat = flowActionMeta(action).cat as CatKey
  const c = (CAT_META[cat] || CAT_META.other).color
  return { background: c + '1f', color: c, borderColor: c + '55' }
}
</script>

<template>
  <div v-if="flowOpen" class="fl-overlay" @mousedown.self="closeFlow">
    <div class="fl-panel" role="dialog" aria-label="AI 工作流">
      <!-- 头部 -->
      <div class="fl-head">
        <div class="fl-title">
          <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="3.5" cy="3" r="1.6" fill="none" stroke="#2f6fed" stroke-width="1.1" />
            <rect x="1.8" y="7.4" width="3.4" height="2.4" rx="0.6" fill="none" stroke="#b3561f" stroke-width="1.1" />
            <circle cx="12.5" cy="12.5" r="1.6" fill="none" stroke="#128053" stroke-width="1.1" />
            <path d="M3.5 4.6 V7.4 M3.5 9.8 C3.5 11.4 6 11 8 11.6 C10 12.2 11 11.4 11.2 10.9" fill="none" stroke="#93a0b5" stroke-width="1" stroke-dasharray="2 2" />
          </svg>
          <b>AI 工作流</b>
          <span v-if="mode === 'review'">每一轮问答的操作流水线：提问 → 思考 → 调工具 → 回答（耗时 / 成败 / token 全记录）</span>
          <span v-else>把常用动作链编排成流水线：定位分区 → 改码 → 校验 → 提交变更集 → 导出试玩 → 不行就回滚</span>
        </div>
        <div class="fl-head-actions">
          <div class="fl-seg fl-mode-seg">
            <button :class="{ on: mode === 'review' }" @click="mode = 'review'">回看</button>
            <button :class="{ on: mode === 'build' }" @click="mode = 'build'">编排</button>
          </div>
          <button class="fl-iconbtn" :title="mode === 'review' ? '重新加载' : '刷新流程列表'" @click="mode === 'review' ? load() : loadFlows()">
            <svg width="13" height="13" viewBox="0 0 13 13"><path d="M11 2.8 V5.4 H8.4 M2.2 7.2 A4.6 4.6 0 1 0 3 4.1 L11 5.4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <button class="fl-iconbtn" title="关闭（Esc）" @click="closeFlow">×</button>
        </div>
      </div>

      <div v-if="mode === 'review' && demoMode" class="fl-demo">示例演示模式：以下为演示流水线。真实使用时，你在 AI 助手里的每轮问答都会自动记录并画成图。</div>
      <p v-if="mode === 'review' && errorMsg" class="fl-err">{{ errorMsg }} <button class="fl-link" @click="load">重试</button></p>

      <div v-if="mode === 'review'" class="fl-body">
        <!-- 左：回合列表 -->
        <aside class="fl-turns">
          <div class="fl-filter">
            <div class="fl-seg">
              <button :class="{ on: filterMode === 'all' }" @click="filterMode = 'all'">全部 {{ traces.length }}</button>
              <button :class="{ on: filterMode === 'issue' }" @click="filterMode = 'issue'">异常</button>
            </div>
            <input v-model="filterText" placeholder="筛选模型 / 工具名" />
          </div>
          <div v-if="loading" class="fl-hint">加载中…</div>
          <div v-else-if="!filteredTurns.length" class="fl-hint">
            还没有操作记录。<br />到 AI 助手问一个问题或让它改代码，这里会自动画出流水线。
          </div>
          <div v-else class="fl-turn-list">
            <button
              v-for="t in filteredTurns"
              :key="t.turn_id"
              class="fl-turn"
              :class="{ on: t.turn_id === selectedTurnId }"
              @click="selectTurn(t.turn_id)"
            >
              <div class="fl-turn-row">
                <span class="fl-turn-time">{{ fmtTime(t.ts) }}</span>
                <span :class="['fl-dot', turnStatus(t).cls]" :title="turnStatus(t).text" />
              </div>
              <div class="fl-turn-model">{{ providerLabel(t.provider) }}<template v-if="t.model"> · {{ t.model }}</template></div>
              <div class="fl-turn-meta">
                <span>{{ (t.steps || []).length }} 步操作</span>
                <span>{{ t.total_tokens.toLocaleString() }} tok</span>
                <span>{{ fmtMs(t.elapsed_ms) }}</span>
              </div>
              <div v-if="isIssueTurn(t)" class="fl-turn-issue">
                {{ t.aborted ? '已停止' : t.error ? '出错' : '含失败步骤' }}
              </div>
            </button>
          </div>
        </aside>

        <!-- 中：流水线画布 -->
        <section class="fl-canvas-wrap">
          <div v-if="selectedTurn" class="fl-canvas-head">
            <span class="fl-chip model">{{ providerLabel(selectedTurn.provider) }}<template v-if="selectedTurn.model"> · {{ selectedTurn.model }}</template></span>
            <span class="fl-chip">{{ (selectedTurn.steps || []).length }} 个工具调用</span>
            <span class="fl-chip">{{ selectedTurn.llm_calls }} 次模型思考 · {{ fmtMs(selectedTurn.llm_ms) }}</span>
            <span class="fl-chip">{{ selectedTurn.total_tokens.toLocaleString() }} token（入 {{ selectedTurn.prompt_tokens.toLocaleString() }} / 出 {{ selectedTurn.completion_tokens.toLocaleString() }}）</span>
            <span class="fl-chip">花费 {{ money(selectedTurn.cost_cny) }}</span>
            <span class="fl-chip">总耗时 {{ fmtMs(selectedTurn.elapsed_ms) }}</span>
          </div>
          <div v-if="selectedTurn" class="fl-canvas">
            <VueFlow
              id="flow-review"
              :nodes="nodes"
              :edges="edges"
              :min-zoom="0.25"
              :max-zoom="1.6"
              :pan-on-scroll="false"
              :zoom-on-scroll="true"
              :fit-view-on-init="true"
              @node-click="onNodeClick"
            >
              <Background :gap="18" :size="1.2" pattern-color="#cfd8e6" />
              <Controls position="bottom-right" :show-interactive="false" />

              <!-- 起点：提问 -->
              <template #node-fl-start="{ data, id }">
                <div :class="['fl-node', 'fl-n-start', { sel: selectedNodeId === id }]">
                  <div class="fl-n-head"><span class="fl-n-glyph start">问</span><b>用户提问</b></div>
                  <div class="fl-n-sub">问题 {{ data.turn.question_chars }} 字 · 上下文 {{ data.turn.messages_count }} 条消息</div>
                  <div class="fl-n-sub faint">会话 {{ data.turn.session_id }} · 出于隐私不记录问题原文</div>
                </div>
              </template>

              <!-- AI 思考（决策点） -->
              <template #node-fl-think="{ data, id }">
                <div :class="['fl-node', 'fl-n-think', { sel: selectedNodeId === id }]">
                  <span class="fl-think-dot" />
                  <b>AI 思考{{ data.total && data.total > 1 ? ` ${data.index}/${data.total}` : '' }}</b>
                  <span class="fl-n-sub">分析结果，决定下一步工具</span>
                </div>
              </template>

              <!-- 工具步骤 -->
              <template #node-fl-tool="{ data, id }">
                <div :class="['fl-node', 'fl-n-tool', { fail: data.step && data.step.ok === false, sel: selectedNodeId === id }]">
                  <div class="fl-n-head">
                    <span
                      class="fl-n-glyph"
                      :style="{ background: (CAT_META[stepMeta(data.step!).cat].color) + '1f', color: CAT_META[stepMeta(data.step!).cat].color, borderColor: CAT_META[stepMeta(data.step!).cat].color + '55' }"
                    >{{ CAT_META[stepMeta(data.step!).cat].glyph }}</span>
                    <b>{{ stepMeta(data.step!).label }}</b>
                    <span class="fl-n-idx">{{ (data.index ?? 0) + 1 }}/{{ data.total }}</span>
                    <span v-if="data.step && data.step.ok === false" class="fl-n-fail">失败</span>
                  </div>
                  <div class="fl-n-sub mono">{{ data.step!.action }}</div>
                  <div class="fl-n-stats">
                    <span>入 {{ data.step!.arg_chars ?? 0 }} 字</span>
                    <span>出 {{ data.step!.obs_chars ?? 0 }} 字</span>
                    <span>{{ fmtMs(data.step!.latency_ms || 0) }}</span>
                    <span :class="['fl-n-ok', data.step!.ok === false ? 'bad' : 'good']">{{ data.step!.ok === false ? '未成功' : '成功' }}</span>
                  </div>
                </div>
              </template>

              <!-- 终点：结果 -->
              <template #node-fl-end="{ data, id }">
                <div :class="['fl-node', 'fl-n-end', endStatus(data.turn).cls, { sel: selectedNodeId === id }]">
                  <div class="fl-n-head">
                    <span class="fl-n-glyph end" :class="endStatus(data.turn).cls">{{ endStatus(data.turn).glyph }}</span>
                    <b>{{ endStatus(data.turn).text }}</b>
                  </div>
                  <div class="fl-n-sub">{{ endStatus(data.turn).desc }}</div>
                  <div class="fl-n-sub faint">回答 {{ data.turn.final_chars }} 字 · finish: {{ data.turn.finish_reason || data.turn.outcome }}</div>
                </div>
              </template>
            </VueFlow>
          </div>
          <div v-else class="fl-canvas fl-empty-canvas">
            <span>选择左侧一轮记录查看流水线</span>
          </div>
        </section>

        <!-- 右：节点详情 -->
        <aside class="fl-detail">
          <template v-if="selectedDetail">
            <h4>节点详情</h4>
            <template v-if="selectedDetail.kind === 'start'">
              <p class="fl-d-line"><label>类型</label><span>用户提问（回合起点）</span></p>
              <p class="fl-d-line"><label>时间</label><span>{{ fmtTime(selectedDetail.turn.ts) }}</span></p>
              <p class="fl-d-line"><label>会话</label><span class="mono">{{ selectedDetail.turn.session_id }}</span></p>
              <p class="fl-d-line"><label>模型</label><span>{{ providerLabel(selectedDetail.turn.provider) }} · {{ selectedDetail.turn.model || '—' }}</span></p>
              <p class="fl-d-line"><label>问题长度</label><span>{{ selectedDetail.turn.question_chars }} 字</span></p>
              <p class="fl-d-line"><label>上下文</label><span>{{ selectedDetail.turn.messages_count }} 条消息</span></p>
            </template>
            <template v-else-if="selectedDetail.kind === 'think'">
              <p class="fl-d-line"><label>类型</label><span>模型决策点</span></p>
              <p class="fl-d-plain">每次调用工具前后，模型都会基于已有观测决定下一步：选哪个工具、传什么参数。</p>
              <p class="fl-d-line"><label>本回合模型调用</label><span>{{ selectedDetail.turn.llm_calls }} 次 · 共 {{ fmtMs(selectedDetail.turn.llm_ms) }}</span></p>
              <p class="fl-d-line"><label>本回合 token</label><span>入 {{ selectedDetail.turn.prompt_tokens.toLocaleString() }} / 出 {{ selectedDetail.turn.completion_tokens.toLocaleString() }}</span></p>
            </template>
            <template v-else-if="selectedDetail.kind === 'tool' && selectedDetail.step">
              <p class="fl-d-line"><label>工具</label><span>{{ stepMeta(selectedDetail.step).label }}</span></p>
              <p class="fl-d-line"><label>分类</label><span>{{ CAT_META[stepMeta(selectedDetail.step).cat].text }}</span></p>
              <p class="fl-d-line"><label>动作名</label><span class="mono">{{ selectedDetail.step.action }}</span></p>
              <p class="fl-d-line"><label>步骤序号</label><span>第 {{ (selectedDetail.index ?? 0) + 1 }} / {{ selectedDetail.turn.steps?.length || 0 }} 步</span></p>
              <p class="fl-d-line"><label>参数规模</label><span>{{ selectedDetail.step.arg_chars ?? 0 }} 字符（不记录原文）</span></p>
              <p class="fl-d-line"><label>返回规模</label><span>{{ selectedDetail.step.obs_chars ?? 0 }} 字符（不记录原文）</span></p>
              <p class="fl-d-line"><label>耗时</label><span>{{ fmtMs(selectedDetail.step.latency_ms || 0) }}</span></p>
              <p class="fl-d-line"><label>结果</label>
                <span :class="selectedDetail.step.ok === false ? 'fl-bad' : 'fl-good'">{{ selectedDetail.step.ok === false ? '未成功（AI 会看到失败原因并调整）' : '成功' }}</span>
              </p>
            </template>
            <template v-else-if="selectedDetail.kind === 'end'">
              <p class="fl-d-line"><label>结局</label><span>{{ endStatus(selectedDetail.turn).text }}</span></p>
              <p class="fl-d-line"><label>finish_reason</label><span class="mono">{{ selectedDetail.turn.finish_reason || '—' }}</span></p>
              <p class="fl-d-line"><label>回答长度</label><span>{{ selectedDetail.turn.final_chars }} 字</span></p>
              <p class="fl-d-line"><label>总耗时</label><span>{{ fmtMs(selectedDetail.turn.elapsed_ms) }}</span></p>
              <p class="fl-d-line"><label>token</label><span>入 {{ selectedDetail.turn.prompt_tokens.toLocaleString() }} / 出 {{ selectedDetail.turn.completion_tokens.toLocaleString() }}</span></p>
              <p class="fl-d-line"><label>花费</label><span>{{ money(selectedDetail.turn.cost_cny) }}（本地模型为 ¥0）</span></p>
              <p v-if="selectedDetail.turn.error" class="fl-d-line"><label>错误</label><span class="fl-bad">{{ fmtTraceError(selectedDetail.turn.error) }}</span></p>
            </template>
            <p class="fl-d-privacy">trace 只记录操作元数据（动作名 / 字数 / 耗时 / 成败），不记录问题、代码与文件路径原文。</p>
          </template>
          <div v-else class="fl-detail-empty">
            点击流水线中的任意节点<br />查看这一步的详细数据
          </div>
        </aside>
      </div>

      <!-- 编排模式 -->
      <div v-else class="fl-body">
        <!-- 左：流程模板列表 -->
        <aside class="fl-turns">
          <div class="fl-filter">
            <div class="fl-seg">
              <button class="on" style="cursor:default">流程模板 {{ flowList.length }}</button>
            </div>
            <button class="fl-btn-block" @click="newFlow">＋ 新建流程</button>
          </div>
          <div v-if="!flowList.length" class="fl-hint">
            还没有保存的流程。<br />点「新建流程」开始编排。
          </div>
          <div v-else class="fl-turn-list">
            <button
              v-for="fl in flowList"
              :key="fl.id"
              class="fl-turn"
              :class="{ on: flowCurrent && flowCurrent.id === fl.id }"
              @click="selectFlow(fl)"
            >
              <div class="fl-turn-row">
                <span class="fl-turn-model">{{ fl.name }}</span>
                <span class="fl-turn-time">{{ fl.nodes.length }} 步</span>
              </div>
              <div class="fl-turn-meta"><span>{{ fl.desc || '（无说明）' }}</span></div>
            </button>
          </div>
        </aside>

        <!-- 中：编排画布 -->
        <section class="fl-canvas-wrap">
          <div class="fl-build-bar">
            <input
              class="fl-name"
              :value="flowCurrent ? flowCurrent.name : ''"
              placeholder="流程名称"
              @input="onFlowName(($event.target as HTMLInputElement).value)"
            />
            <div class="fl-action-add">
              <select v-model="addAction" class="fl-select">
                <option v-for="a in FLOW_ACTIONS" :key="a.action" :value="a.action">
                  {{ a.label }}{{ a.mutating ? '（写）' : '' }}
                </option>
              </select>
              <button class="fl-btn-mini" :disabled="!flowCurrent" @click="addStep">＋ 添加步骤</button>
            </div>
            <div class="fl-build-ops">
              <button class="fl-btn-mini ok" :disabled="flowRunning || !flowCurrent" @click="runFlow(0)">▶ 执行</button>
              <button class="fl-btn-mini" :disabled="!flowRunning" @click="stopFlow">■ 停止</button>
              <button class="fl-btn-mini" :disabled="flowRunning || !flowCurrent" @click="saveFlow">保存模板</button>
              <button class="fl-btn-mini danger" :disabled="!flowCurrent || !flowCurrent.id" @click="flowCurrent && deleteFlow(flowCurrent.id)">删除</button>
            </div>
          </div>
          <p v-if="flowMessage" class="fl-build-msg" :class="flowMessageKind">{{ flowMessage }}</p>
          <p v-if="demoMode" class="fl-demo fl-demo-inline">
            示例演示模式：点「执行」跑完整条流水线。校验步刻意「首次失败」，用来演示「红卡 → 单步重跑修复 → 从失败步续跑」。
          </p>
          <div v-if="flowCurrent" class="fl-canvas">
            <VueFlow
              id="flow-build"
              :nodes="buildNodes"
              :edges="buildEdges"
              :min-zoom="0.25"
              :max-zoom="1.6"
              :pan-on-scroll="false"
              :zoom-on-scroll="true"
              :fit-view-on-init="true"
              :nodes-draggable="true"
              :nodes-connectable="true"
              @node-click="onBuildNodeClick"
              @node-drag-stop="onBuildNodeDragStop"
              @connect="onBuildConnect"
            >
              <Background :gap="18" :size="1.2" pattern-color="#cfd8e6" />
              <Controls position="bottom-right" :show-interactive="false" />

              <template #node-fl-bstart>
                <div class="fl-bterm start">
                  <span>起点 · 开始执行</span>
                  <Handle type="source" :position="Position.Bottom" class="fl-handle" />
                </div>
              </template>

              <template #node-fl-bstep="{ data, id }">
                <div :class="['fl-node', 'fl-n-tool', stepStatusClass(data.st), { sel: buildSelectedId === id, run: flowActiveId === id }]">
                  <Handle type="target" :position="Position.Top" class="fl-handle" />
                  <div class="fl-n-head">
                    <span class="fl-n-glyph" :style="glyphStyle(data.node.action)">{{ flowActionMeta(data.node.action).glyph }}</span>
                    <b>{{ data.node.label || flowActionMeta(data.node.action).label }}</b>
                    <span class="fl-n-idx">第 {{ data.order }} 步</span>
                  </div>
                  <div class="fl-n-sub mono">
                    {{ data.node.action }}<span v-if="flowActionMeta(data.node.action).mutating" class="fl-n-mut">写盘</span>
                  </div>
                  <div class="fl-n-stats">
                    <span v-if="!data.st || data.st.status === 'pending'">待执行</span>
                    <span v-else-if="data.st.status === 'run'">执行中…</span>
                    <span v-else-if="data.st.status === 'skipped'">已跳过</span>
                    <span v-else>{{ fmtMs(data.st.latency_ms) }}</span>
                    <span v-if="data.st && data.st.status !== 'pending'">入 {{ data.st.arg_chars }} / 出 {{ data.st.obs_chars }} 字</span>
                    <span v-if="data.st && data.st.status === 'fail'" class="fl-n-ok bad">失败</span>
                    <span v-else-if="data.st && data.st.status === 'ok'" class="fl-n-ok good">成功</span>
                  </div>
                  <div v-if="data.st && data.st.error" class="fl-n-err">{{ data.st.error }}</div>
                  <Handle type="source" :position="Position.Bottom" class="fl-handle" />
                </div>
              </template>

              <template #node-fl-bend="{ data }">
                <div class="fl-bterm end">
                  <Handle type="target" :position="Position.Top" class="fl-handle" />
                  <span>终点 · 共 {{ data.count }} 步</span>
                </div>
              </template>
            </VueFlow>
          </div>
          <div v-else class="fl-canvas fl-empty-canvas">
            <span>点左侧选择一个流程模板，或新建一条</span>
          </div>
        </section>

        <!-- 右：节点参数表单 -->
        <aside class="fl-detail">
          <template v-if="buildSelected">
            <h4>{{ buildSelected.label || flowActionMeta(buildSelected.action).label }}</h4>
            <p class="fl-d-line"><label>动作</label><span class="mono">{{ buildSelected.action }}</span></p>
            <p class="fl-d-line"><label>类别</label><span>{{ (CAT_META[flowActionMeta(buildSelected.action).cat as CatKey] || CAT_META.other).text }}</span></p>
            <p class="fl-d-plain">{{ flowActionMeta(buildSelected.action).summary }}</p>
            <p v-if="flowActionMeta(buildSelected.action).mutating" class="fl-d-warn">⚠ 该步骤会写盘（分区内受控写 / 提交 / 回滚）。</p>

            <div v-for="fld in flowActionMeta(buildSelected.action).fields" :key="fld.name" class="fl-form-row">
              <label>{{ fld.label }}<span v-if="fld.required" class="fl-req">*</span></label>
              <textarea
                v-if="fld.kind === 'text'"
                :value="buildSelected.params[fld.name] || ''"
                :placeholder="fld.placeholder"
                rows="4"
                @input="onParam(buildSelected.id, fld.name, ($event.target as HTMLTextAreaElement).value)"
              />
              <input
                v-else
                :value="buildSelected.params[fld.name] || ''"
                :placeholder="fld.placeholder"
                @input="onParam(buildSelected.id, fld.name, ($event.target as HTMLInputElement).value)"
              />
            </div>
            <p v-if="!flowActionMeta(buildSelected.action).fields.length" class="fl-d-plain">该动作无需参数。</p>

            <div class="fl-form-actions">
              <button @click="moveNode(buildSelected.id, -1)">上移</button>
              <button @click="moveNode(buildSelected.id, 1)">下移</button>
              <button class="danger" @click="removeSelected">删除</button>
            </div>
            <button class="fl-btn-block primary" :disabled="flowRunning" @click="runFlowSingle(buildSelected.id)">↻ 重跑此步</button>
            <button class="fl-btn-block" :disabled="flowRunning" @click="resumeFlowFrom(buildSelected.id)">▶ 从此步续跑</button>
            <p v-if="flowLastChangeset" class="fl-d-line"><label>变更集</label><span class="mono">{{ flowLastChangeset }}</span></p>
            <p class="fl-d-privacy">
              节点动作都在受控动作白名单内；写盘/校验/回滚全部走既有受控端点（分区写越区即拒），运行过程按元数据写 trace，回看模式可见。
            </p>
          </template>
          <div v-else class="fl-detail-empty">
            点画布节点编辑参数，<br />或点上方「添加步骤」新增
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style scoped>
.fl-overlay {
  position: fixed; inset: 0; z-index: 90;
  background: rgba(38, 52, 77, 0.38);
  backdrop-filter: blur(2px);
  display: flex; align-items: center; justify-content: center;
  padding: 22px;
}
.fl-panel {
  width: min(1340px, 98vw);
  height: min(880px, 94vh);
  background: #f4f6fa;
  border: 1px solid #d3dcea;
  border-radius: 14px;
  box-shadow: 0 22px 60px rgba(31, 45, 72, .28);
  display: flex; flex-direction: column; overflow: hidden;
}
.fl-head {
  flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; background: #fff; border-bottom: 1px solid #e2e8f1;
}
.fl-title { display: flex; align-items: center; gap: 8px; min-width: 0; }
.fl-title b { font-size: 14.5px; color: #1b2433; white-space: nowrap; }
.fl-title span { font-size: 11.5px; color: #93a0b5; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.fl-head-actions { display: flex; gap: 6px; flex: 0 0 auto; }
.fl-iconbtn {
  width: 28px; height: 28px; border: 1px solid #d3dcea; background: #fff; border-radius: 7px;
  color: #5a6778; cursor: pointer; font-size: 16px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
}
.fl-iconbtn:hover { border-color: #9fb0c6; background: #f5f8fc; color: #23304a; }
.fl-demo {
  flex: 0 0 auto; margin: 10px 16px 0; padding: 7px 11px; border-radius: 8px;
  font-size: 11.5px; line-height: 1.6; color: #8a5a16; background: #fdf2e0; border: 1px solid #f0d29a;
}
.fl-err { flex: 0 0 auto; margin: 10px 16px 0; font-size: 12px; color: #d23b42; }
.fl-link { border: none; background: none; color: #2f6fed; cursor: pointer; font: inherit; padding: 0; }
.fl-body { flex: 1 1 auto; min-height: 0; display: flex; }

/* 左：回合列表 */
.fl-turns {
  flex: 0 0 248px; display: flex; flex-direction: column; min-height: 0;
  background: #fff; border-right: 1px solid #e2e8f1;
}
.fl-filter { padding: 10px; border-bottom: 1px solid #eef1f6; display: flex; flex-direction: column; gap: 8px; }
.fl-seg { display: flex; background: #f3f6fb; border-radius: 8px; padding: 3px; gap: 2px; }
.fl-seg button {
  flex: 1; border: none; background: transparent; font: inherit; font-size: 11.5px; font-weight: 600;
  color: #5a6778; padding: 5px 0; border-radius: 6px; cursor: pointer;
}
.fl-seg button.on { background: #fff; color: #2f6fed; box-shadow: 0 1px 4px rgba(35,52,84,.12); }
.fl-filter input {
  padding: 6px 9px; border: 1px solid #d3dcea; border-radius: 7px; font: inherit; font-size: 11.5px;
  color: #23304a; background: #f7f9fc; outline: none;
}
.fl-filter input:focus { border-color: #2f6fed; background: #fff; }
.fl-turn-list { flex: 1 1 auto; overflow: auto; padding: 8px; display: flex; flex-direction: column; gap: 7px; }
.fl-hint { padding: 22px 14px; font-size: 11.5px; line-height: 1.8; color: #93a0b5; text-align: center; }
.fl-turn {
  text-align: left; border: 1px solid #e2e8f1; background: #fafbfd; border-radius: 10px;
  padding: 8px 10px; cursor: pointer; font: inherit; display: flex; flex-direction: column; gap: 3px;
}
.fl-turn:hover { border-color: #b9c9e2; background: #f6f9ff; }
.fl-turn.on { border-color: #8fb2f2; background: #f0f6ff; box-shadow: 0 0 0 2px rgba(47,111,237,.10); }
.fl-turn-row { display: flex; align-items: center; justify-content: space-between; }
.fl-turn-time { font-size: 10.5px; color: #7a869a; font-variant-numeric: tabular-nums; }
.fl-dot { width: 8px; height: 8px; border-radius: 50%; flex: 0 0 auto; }
.fl-dot.ok { background: #2fbf7e; }
.fl-dot.err { background: #e0525a; }
.fl-dot.stop { background: #e0a13a; }
.fl-dot.warn { background: #e0a13a; }
.fl-turn-model { font-size: 11.5px; font-weight: 600; color: #46536a; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fl-turn-meta { display: flex; gap: 8px; font-size: 10px; color: #93a0b5; font-variant-numeric: tabular-nums; }
.fl-turn-issue { font-size: 10px; font-weight: 700; color: #c0434a; }

/* 中：画布 */
.fl-canvas-wrap { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; min-height: 0; }
.fl-canvas-head {
  flex: 0 0 auto; padding: 8px 12px; display: flex; flex-wrap: wrap; gap: 6px;
  background: #eef2f8; border-bottom: 1px solid #e2e8f1;
}
.fl-chip {
  font-size: 10.5px; color: #46536a; background: #fff; border: 1px solid #dbe2ee;
  border-radius: 999px; padding: 2px 9px; font-variant-numeric: tabular-nums; white-space: nowrap;
}
.fl-chip.model { color: #7a4fd1; border-color: #d9c9f5; background: #f8f4ff; font-weight: 600; }
.fl-canvas { flex: 1 1 auto; min-height: 0; position: relative; }
.fl-empty-canvas { display: flex; align-items: center; justify-content: center; color: #93a0b5; font-size: 13px; }

/* Vue Flow 自定义节点 */
.fl-node {
  background: #fff; border: 1.5px solid #d3dcea; border-radius: 12px;
  padding: 10px 12px; box-shadow: 0 3px 10px rgba(31, 45, 72, .08);
}
.fl-n-head { display: flex; align-items: center; gap: 8px; }
.fl-n-head b { font-size: 12.5px; color: #1b2433; }
.fl-n-glyph {
  width: 22px; height: 22px; border-radius: 7px; border: 1px solid; flex: 0 0 auto;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
}
.fl-n-glyph.start { background: #eaf1fe; color: #2f6fed; border-color: #c8dcfa; }
.fl-n-glyph.end.ok { background: #e6f7ee; color: #128053; border-color: #b6e6c8; }
.fl-n-glyph.end.err { background: #fdecec; color: #d23b42; border-color: #f3c4c7; }
.fl-n-glyph.end.stop, .fl-n-glyph.end.warn { background: #fdf2e0; color: #b3741a; border-color: #f0d8a8; }
.fl-n-idx { margin-left: auto; font-size: 10px; color: #93a0b5; font-variant-numeric: tabular-nums; }
.fl-n-fail {
  font-size: 10px; font-weight: 700; color: #fff; background: #e0525a;
  border-radius: 999px; padding: 1px 8px;
}
.fl-n-sub { margin-top: 5px; font-size: 10.5px; color: #5a6778; line-height: 1.5; }
.fl-n-sub.faint { color: #9aa5b8; }
.fl-n-sub.mono { font-family: var(--font-mono, ui-monospace, Consolas, monospace); font-size: 10px; color: #7a869a; }
.fl-n-stats { margin-top: 7px; display: flex; flex-wrap: wrap; gap: 5px; }
.fl-n-stats > span {
  font-size: 10px; color: #5a6778; background: #f3f6fb; border: 1px solid #e2e8f1;
  border-radius: 5px; padding: 1px 7px; font-variant-numeric: tabular-nums;
}
.fl-n-ok.good { color: #128053 !important; background: #e6f7ee !important; border-color: #b6e6c8 !important; }
.fl-n-ok.bad { color: #b32d33 !important; background: #fdecec !important; border-color: #f3c4c7 !important; font-weight: 700; }
.fl-n-tool.fail { border-color: #ef9ba0; box-shadow: 0 3px 12px rgba(224,82,90,.18); }
.fl-n-think {
  display: flex; align-items: center; gap: 8px; padding: 8px 12px;
  border-style: dashed; border-color: #b9c9e2; background: #f7faff;
}
.fl-n-think b { font-size: 11.5px; color: #3a5684; }
.fl-n-think .fl-n-sub { margin-top: 0; }
.fl-think-dot {
  width: 8px; height: 8px; border-radius: 50%; background: #6d93d8; flex: 0 0 auto;
  box-shadow: 0 0 0 4px rgba(109,147,216,.18);
}
.fl-n-start { border-color: #b9cdf2; background: linear-gradient(180deg, #f8fbff, #f2f7ff); }
.fl-n-end.ok { border-color: #9ed8b5; background: linear-gradient(180deg, #f7fef9, #effaf3); }
.fl-n-end.err { border-color: #ef9ba0; background: linear-gradient(180deg, #fff7f7, #fdeeee); }
.fl-n-end.stop, .fl-n-end.warn { border-color: #ecc98f; background: linear-gradient(180deg, #fffdf7, #fdf6e7); }
.vue-flow__node { cursor: pointer; }
.fl-node.sel { outline: 2px solid rgba(47,111,237,.55); outline-offset: 2px; }

/* 右：详情 */
.fl-detail {
  flex: 0 0 268px; background: #fff; border-left: 1px solid #e2e8f1;
  padding: 14px 14px 18px; overflow: auto;
}
.fl-detail h4 { margin: 0 0 12px; font-size: 13px; color: #1b2433; }
.fl-d-line { display: flex; gap: 8px; margin: 0 0 10px; font-size: 11.5px; line-height: 1.6; }
.fl-d-line label { flex: 0 0 62px; color: #93a0b5; }
.fl-d-line span { flex: 1 1 auto; color: #334055; word-break: break-all; }
.fl-d-plain { margin: 0 0 10px; font-size: 11px; line-height: 1.7; color: #7a869a; }
.fl-d-privacy { margin-top: 14px; font-size: 10.5px; line-height: 1.7; color: #a2adbf; border-top: 1px dashed #e2e8f1; padding-top: 10px; }
.fl-good { color: #128053; }
.fl-bad { color: #d23b42; }
.fl-detail-empty { color: #93a0b5; font-size: 11.5px; line-height: 1.9; text-align: center; margin-top: 80px; }
.mono { font-family: var(--font-mono, ui-monospace, Consolas, monospace); }

@media (max-width: 1080px) {
  .fl-turns { flex-basis: 208px; }
  .fl-detail { flex-basis: 228px; }
}
@media (max-width: 860px) {
  .fl-overlay { padding: 0; }
  .fl-panel { width: 100vw; height: 100vh; border-radius: 0; }
  .fl-detail { display: none; }
  .fl-turns { flex-basis: 176px; }
  .fl-title span { display: none; }
}

/* ---------- 编排模式 ---------- */
.fl-mode-seg { flex: 0 0 auto; background: #eef2f8; }
.fl-mode-seg button { padding: 5px 12px; }
.fl-btn-block {
  width: 100%; border: 1px solid #d3dcea; background: #fff; color: #33455f;
  border-radius: 8px; padding: 7px 10px; font: inherit; font-size: 12px; cursor: pointer;
}
.fl-btn-block:hover { border-color: #9fb0c6; background: #f5f8fc; }
.fl-btn-block.primary { background: #2f6fed; border-color: #2f6fed; color: #fff; font-weight: 600; }
.fl-btn-block.primary:hover { background: #255ecf; }
.fl-btn-block:disabled, .fl-btn-mini:disabled { opacity: .5; cursor: not-allowed; }

.fl-build-bar {
  flex: 0 0 auto; display: flex; flex-wrap: wrap; align-items: center; gap: 8px;
  padding: 9px 12px; background: #eef2f8; border-bottom: 1px solid #e2e8f1;
}
.fl-name {
  flex: 1 1 160px; min-width: 120px; padding: 6px 10px; border: 1px solid #d3dcea;
  border-radius: 7px; font: inherit; font-size: 12.5px; font-weight: 600; color: #23304a;
  background: #fff; outline: none;
}
.fl-name:focus { border-color: #2f6fed; }
.fl-action-add { display: flex; gap: 6px; align-items: center; }
.fl-select {
  padding: 6px 8px; border: 1px solid #d3dcea; border-radius: 7px; font: inherit;
  font-size: 11.5px; color: #33455f; background: #fff; max-width: 190px;
}
.fl-btn-mini {
  border: 1px solid #d3dcea; background: #fff; color: #33455f; border-radius: 7px;
  padding: 6px 10px; font: inherit; font-size: 11.5px; cursor: pointer; white-space: nowrap;
}
.fl-btn-mini:hover { border-color: #9fb0c6; background: #f5f8fc; }
.fl-btn-mini.ok { background: #e6f7ee; border-color: #b6e6c8; color: #128053; font-weight: 600; }
.fl-btn-mini.ok:hover { background: #d7f1e3; }
.fl-btn-mini.danger { color: #c0434a; }
.fl-btn-mini.danger:hover { background: #fdecec; border-color: #f3c4c7; }
.fl-build-ops { display: flex; gap: 6px; margin-left: auto; }
.fl-build-msg {
  flex: 0 0 auto; margin: 8px 12px 0; padding: 6px 10px; border-radius: 7px;
  font-size: 11.5px; line-height: 1.5;
}
.fl-build-msg.info { color: #46536a; background: #eef2f8; border: 1px solid #dbe2ee; }
.fl-build-msg.ok { color: #128053; background: #e6f7ee; border: 1px solid #b6e6c8; }
.fl-build-msg.err { color: #b32d33; background: #fdecec; border: 1px solid #f3c4c7; }
.fl-demo-inline { margin: 8px 12px 0; }

.fl-bterm {
  position: relative; background: #fff; border: 1.5px dashed #b9c9e2; border-radius: 12px;
  padding: 12px 16px; text-align: center; font-size: 12px; color: #3a5684; font-weight: 600;
  min-width: 220px; box-shadow: 0 3px 10px rgba(31, 45, 72, .06);
}
.fl-bterm.end { border-color: #9ed8b5; color: #128053; background: #f7fef9; }
.fl-handle { width: 8px; height: 8px; background: #2f6fed; border: 1.5px solid #fff; }
.fl-n-tool.st-run { border-color: #8fb2f2; box-shadow: 0 0 0 2px rgba(47,111,237,.18); }
.fl-n-tool.st-ok { border-color: #9ed8b5; }
.fl-n-tool.st-fail { border-color: #ef9ba0; box-shadow: 0 3px 12px rgba(224,82,90,.18); }
.fl-n-tool.st-skipped { opacity: .6; border-style: dashed; }
.fl-n-tool.run { outline: 2px solid rgba(47,111,237,.5); outline-offset: 2px; }
.fl-n-mut {
  margin-left: 8px; font-size: 9.5px; font-weight: 700; color: #fff; background: #b3561f;
  border-radius: 999px; padding: 0 6px;
}
.fl-n-err {
  margin-top: 6px; font-size: 10px; line-height: 1.5; color: #b32d33;
  background: #fdecec; border: 1px solid #f3c4c7; border-radius: 6px; padding: 4px 7px;
  max-height: 52px; overflow: auto; word-break: break-word;
}
.fl-form-row { margin-bottom: 10px; }
.fl-form-row label { display: block; font-size: 11px; color: #6b7789; margin-bottom: 4px; }
.fl-form-row .fl-req { color: #d23b42; margin-left: 3px; }
.fl-form-row input, .fl-form-row textarea {
  width: 100%; box-sizing: border-box; padding: 6px 8px; border: 1px solid #d3dcea;
  border-radius: 7px; font: inherit; font-size: 11.5px; color: #23304a; background: #f7f9fc;
  outline: none; resize: vertical;
}
.fl-form-row input:focus, .fl-form-row textarea:focus { border-color: #2f6fed; background: #fff; }
.fl-form-actions { display: flex; gap: 6px; margin: 4px 0 10px; }
.fl-form-actions button {
  flex: 1; border: 1px solid #d3dcea; background: #fff; color: #33455f; border-radius: 7px;
  padding: 6px 0; font: inherit; font-size: 11.5px; cursor: pointer;
}
.fl-form-actions button:hover { border-color: #9fb0c6; background: #f5f8fc; }
.fl-form-actions button.danger { color: #c0434a; }
.fl-form-actions button.danger:hover { background: #fdecec; border-color: #f3c4c7; }
.fl-d-warn {
  margin: 0 0 10px; font-size: 11px; line-height: 1.6; color: #8a5a16;
  background: #fdf2e0; border: 1px solid #f0d29a; border-radius: 7px; padding: 6px 8px;
}
</style>
