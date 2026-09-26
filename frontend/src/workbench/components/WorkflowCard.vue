<script setup lang="ts">
// 对话流内的开发工作流卡片：方案选择/审批走 WorkflowGateDialog 模态（不打断对话滚动），
// 执行中每个子代理的 thought/action/observation 通过 SSE 实时追加、可折叠查看。
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  agentApi, workflowEvents,
} from '../api'
import type { AcceptanceItem, WorkflowChildTrace, WorkflowEvent, WorkflowState, WorkflowStepItem } from '../api'
import WorkflowGateDialog from './WorkflowGateDialog.vue'

const props = defineProps<{
  workflowId: string
  seed?: Partial<WorkflowState>
}>()
const emit = defineEmits<{
  (e: 'activity'): void
  (e: 'team', payload: { workflowId: string; active: boolean; members: Array<{ id: string; label: string; role: string; status: string; task: string }> }): void
  /** 人工审批门开合：父级据此锁定对话台的发送/切换等操作（聊天滚动不锁） */
  (e: 'gate', open: boolean): void
}>()

const TERMINAL = new Set(['completed', 'failed', 'interrupted'])
const GATE_STATUSES = new Set(['awaiting_choice', 'generating_options', 'awaiting_research', 'awaiting_approval', 'planned'])
const KIND_LABEL: Record<string, string> = { generic: '通用开发', game: '游戏开发', eda: '电子设计' }
const STATUS_LABEL: Record<string, string> = {
  awaiting_choice: '等待选择', generating_options: '正在生成方案', awaiting_research: '等待检索',
  planned: '待执行', awaiting_approval: '等待审核', executing: '执行中',
  planning: '规划中', interrupted: '已暂停', completed: '已完成', failed: '执行失败',
}
const ROLE_META: Record<string, { label: string; avatar: string }> = {
  dispatcher: { label: '文件派发员', avatar: '派' },
  planner: { label: '拆解规划员', avatar: '规' },
  designer: { label: '设计师', avatar: '设' },
  researcher: { label: '检索员', avatar: '检' },
  coder: { label: '实现员', avatar: '码' },
  tester: { label: 'QA 工程师', avatar: '测' },
  reviewer: { label: '评审员', avatar: '审' },
  artist: { label: '美术员', avatar: '美' },
  audio: { label: '音频员', avatar: '音' },
}
const STEP_GLYPH: Record<string, string> = { thought: '◌', action: '↗', observation: '✓', reflection: '↻' }
const STEP_LABEL: Record<string, string> = { thought: '思考', action: '工具调用', observation: '观察结果', reflection: '复核' }
const TOOL_NAMES: Record<string, string> = {
  search_code: '检索代码', search_knowledge: '检索知识库', read_file: '读取文件',
  grep: '定位代码', run_command: '运行命令', game_playtest: '运行校验',
  web_search: '网页搜索', web_fetch: '读取网页', dev_mcp_call: '调用 MCP',
  apply_edit: '修改文件', create_file: '创建文件',
  delegate: '委派子代理', orchestrate: '编排任务', game_screenshot: '画面截图',
}
const EVENT_LABELS: Record<string, string> = {
  options_pending: '方案就绪', clarify: '停在方案选择门', options_generated: '已生成方案选项',
  choice: '已选择方案', research: '检索资料已提交', plan: '任务图已生成',
  approval_required: '等待审批', execute_start: '开始执行', execute_wave: '执行任务波次',
  subagent_start: '子代理开始', subagent_complete: '子代理完成',
  subagent_retry_start: '子代理重试', subagent_retry_complete: '重试完成',
  review: '结果复核中', review_replan: '复核未过，重规划', replan: '正在重规划',
  dag_revision_requested: '请求修改 DAG', dag_revision_approved: 'DAG 修改已批准', dag_revision_denied: 'DAG 修改被拒绝',
  complete: '工作流完成', fail: '工作流失败', interrupt: '工作流中断', resume: '工作流恢复',
  approval_granted: '审批通过', approval_denied: '审批驳回', auto_execute_start: '自动开始执行',
  acceptance_revised: '验收条件已更新', acceptance_decided: '用户已完成验收决定',
}

function roleMeta(role?: string) {
  return ROLE_META[String(role || '').toLowerCase()] || { label: role || '子代理', avatar: '代' }
}

// ---------------------------------------------------------------- 状态
const state = ref<WorkflowState | null>(null)
const loadError = ref('')
const busy = ref(false)
const cardOpen = ref(true)
const gateDismissed = ref(false)
const finalNote = ref('')
const rootEl = ref<HTMLElement | null>(null)

/** SSE 实时轨迹（task_id -> steps），仅内存；终态后由 state.results[].trace 补全。 */
const liveSteps = reactive(new Map<string, WorkflowStepItem[]>())
/** subagent_start/complete 事件带来的临时成员信息（hydrate 间隙也能先展示）。 */
const liveMeta = new Map<string, { role?: string; task?: string; status?: string; elapsedMs?: number; error?: string; conclusion?: string; trace?: WorkflowChildTrace }>()
const openMembers = ref<Set<string>>(new Set())
let lastSeq = 0
let unsubscribe: (() => void) | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | undefined
let refreshTimer: ReturnType<typeof setTimeout> | undefined
let destroyed = false

// subagent_step 事件按帧合批：多代理并行时单帧可能到达十几条步骤，逐条写
// reactive Map 会让卡片整子树逐事件重渲染，并向父级逐条 emit activity 触发滚动，
// 这是工作流执行期对话卡顿的主要来源。队列在每帧只提交一次。
interface QueuedStep { id: string; type: string; text: string }
const stepQueue: QueuedStep[] = []
let stepRaf = 0
function flushSteps() {
  stepRaf = 0
  if (!stepQueue.length || destroyed) { stepQueue.length = 0; return }
  const queued = stepQueue.splice(0)
  const openNow = new Set(openMembers.value)
  for (const st of queued) {
    const list = liveSteps.get(st.id) || []
    list.push({ type: st.type, text: st.text })
    if (list.length > 240) list.splice(0, list.length - 240)
    liveSteps.set(st.id, list)
    openNow.add(st.id)
  }
  openMembers.value = openNow
  // 一帧最多通知父级跟随一次
  emit('activity')
}
function scheduleStepFlush() {
  if (!stepRaf) stepRaf = requestAnimationFrame(flushSteps)
}

const status = computed(() => state.value?.status || String(props.seed?.status || 'preparing'))
const terminal = computed(() => TERMINAL.has(status.value))
const gateNeeded = computed(() => GATE_STATUSES.has(status.value) && !terminal.value)
const gateOpen = computed(() => gateNeeded.value && !gateDismissed.value)
watch(gateOpen, (open) => emit('gate', open), { immediate: true })
watch(status, (next, prev) => {
  if (next !== prev && GATE_STATUSES.has(next)) gateDismissed.value = false
}, { flush: 'post' })

const stages = [
  { key: 'choice', label: '方案选择' },
  { key: 'plan', label: '任务规划' },
  { key: 'execute', label: '并行执行' },
  { key: 'review', label: '复核交付' },
]
function stageIndex(): number {
  const s = status.value
  if (['awaiting_choice', 'generating_options', 'awaiting_research'].includes(s)) return 0
  if (['planning', 'planned', 'awaiting_approval'].includes(s)) return 1
  if (['executing', 'interrupted'].includes(s)) return 2
  if (s === 'completed') return 4
  if (s === 'failed') return 2
  return 0
}
function stageClass(i: number): string {
  const cur = stageIndex()
  if (i < cur || cur === 4) return 'done'
  if (i === cur) return 'active'
  return 'todo'
}

const latestEventLabel = computed(() => {
  const events = state.value?.events
  const kind = events?.length ? String(events[events.length - 1].kind || '') : ''
  return EVENT_LABELS[kind] || kind || '准备中'
})

interface MemberRow {
  id: string; role: string; label: string; avatar: string; task: string
  status: string; elapsedMs?: number; error?: string; conclusion?: string
  steps: WorkflowStepItem[]; trace?: WorkflowChildTrace
}
function taskResults(wf: WorkflowState | null | undefined): Record<string, Record<string, unknown>> {
  const report = wf?.results || {}
  return (report.results as Record<string, Record<string, unknown>> | undefined)
    || report as Record<string, Record<string, unknown>>
}
const members = computed<MemberRow[]>(() => {
  const wf = state.value
  const order: string[] = []
  const base = new Map<string, Partial<MemberRow>>()
  for (const task of wf?.tasks || []) {
    const id = String(task.id || '')
    if (!id) continue
    order.push(id)
    base.set(id, {
      id, role: String(task.role || 'coder'),
      task: String(task.task || task.description || ''),
    })
  }
  for (const sub of wf?.subagents || []) {
    const id = String(sub.id || '')
    if (!id) continue
    if (!order.includes(id)) order.push(id)
    base.set(id, {
      ...base.get(id),
      id, role: String(sub.role || base.get(id)?.role || 'coder'),
      task: base.get(id)?.task || String(sub.task || ''),
      status: String(sub.status || 'running'),
      elapsedMs: sub.elapsed_ms, error: sub.error, conclusion: sub.conclusion,
      trace: sub.trace,
    })
  }
  for (const [id, result] of Object.entries(taskResults(wf))) {
    if (!order.includes(id)) order.push(id)
    const prev = base.get(id) || {}
    base.set(id, {
      ...prev, id,
      status: String(result.status || prev.status || 'ok'),
      conclusion: String(result.conclusion || result.summary || prev.conclusion || ''),
      error: String(result.error || prev.error || ''),
      elapsedMs: typeof result.elapsed_ms === 'number' ? result.elapsed_ms : prev.elapsedMs,
      trace: (result.trace as WorkflowChildTrace | undefined) || prev.trace,
    })
  }
  // SSE 先行数据（state hydrate 之前）
  for (const id of liveMeta.keys()) {
    if (!order.includes(id)) order.push(id)
    const meta = liveMeta.get(id)!
    base.set(id, { ...base.get(id), id, ...prevOrLive(id, meta) })
  }
  for (const id of liveSteps.keys()) {
    if (!order.includes(id)) order.push(id)
  }
  return order.map((id) => {
    const b = base.get(id) || {}
    const meta = roleMeta(b.role)
    return {
      id,
      role: b.role || 'coder',
      label: meta.label,
      avatar: meta.avatar,
      task: b.task || liveMeta.get(id)?.task || '执行中…',
      status: b.status || (liveSteps.has(id) ? 'running' : 'pending'),
      elapsedMs: b.elapsedMs || liveMeta.get(id)?.elapsedMs,
      error: b.error || (liveMeta.get(id)?.status === 'failed' ? liveMeta.get(id)?.error : ''),
      conclusion: b.conclusion || (liveMeta.get(id)?.status === 'ok' ? liveMeta.get(id)?.conclusion : ''),
      steps: liveSteps.get(id) || [],
      trace: b.trace,
    }
  })
})
function prevOrLive(id: string, meta: NonNullable<ReturnType<typeof liveMeta.get>>): Partial<MemberRow> {
  return {
    role: meta.role,
    task: meta.task,
    status: meta.status,
    elapsedMs: meta.elapsedMs,
    error: meta.error,
    conclusion: meta.conclusion,
    trace: meta.trace,
  }
}

watch([members, terminal], () => {
  emit('team', {
    workflowId: props.workflowId,
    active: !terminal.value,
    members: members.value.map(m => ({ id: m.id, label: m.label, role: m.role, status: m.status, task: m.task })),
  })
}, { immediate: true })

const completedCount = computed(() => members.value.filter(m => m.status === 'ok').length)
const reviewFailures = computed(() => {
  const failures = state.value?.review?.failures
  return Array.isArray(failures) ? failures as Array<{ kind?: string; message?: string; recovery?: string }> : []
})

// ---------------------------------------------------------------- SSE + hydrate
function setState(wf: WorkflowState | null | undefined) {
  if (!wf) return
  state.value = wf
  const lastEv = wf.events?.[wf.events.length - 1]
  if (typeof lastEv?.seq === 'number') lastSeq = Math.max(lastSeq, lastEv.seq)
}

async function hydrate(): Promise<boolean> {
  try {
    const r = await agentApi.workflow(props.workflowId)
    if (r.ok && r.workflow) { setState(r.workflow); return true }
    if (r.error) loadError.value = r.error
  } catch (e) {
    loadError.value = (e as Error).message || '工作流状态读取失败'
  }
  return false
}

function scheduleRefresh(delay = 600) {
  if (refreshTimer || destroyed) return
  refreshTimer = setTimeout(() => {
    refreshTimer = undefined
    void hydrate()
  }, delay)
}

// SSE 断连重连卫生：① 指数退避 2.5s→30s（旧实现固定 2.5s 无限重试，服务
// 重启瞬间 N 个旧标签齐刷 GET/SSE）；② 后台标签不排重连，回到前台立即补连
// （与 polling 治理的可见性感知一致）。收到任何事件说明连接健康，重置退避。
let connected = false
let reconnectDelay = 2500
const RECONNECT_MAX_DELAY = 30000

function connect() {
  unsubscribe?.()
  unsubscribe = workflowEvents(props.workflowId, lastSeq, {
    onEvent: (ev) => { reconnectDelay = 2500; handleEvent(ev) },
    onDone: () => {
      connected = false
      scheduleRefresh(0)
      scheduleReconnectStop()
    },
    onError: () => {
      connected = false
      if (!terminal.value && !destroyed) scheduleReconnect()
    },
  })
  connected = true
}
function scheduleReconnect() {
  if (reconnectTimer || destroyed) return
  if (typeof document !== 'undefined' && document.hidden) return
  const delay = reconnectDelay
  reconnectDelay = Math.min(RECONNECT_MAX_DELAY, reconnectDelay * 2)
  reconnectTimer = setTimeout(() => {
    reconnectTimer = undefined
    if (destroyed) return
    // 排期后页面被切到后台：本次不连，等 visibilitychange 回前台再补
    if (typeof document !== 'undefined' && document.hidden) return
    void hydrate().then(ok => {
      if (destroyed) return
      if (ok) { reconnectDelay = 2500; connect() }
      else scheduleReconnect()
    })
  }, delay)
}
function onVisibilityResume() {
  if (typeof document === 'undefined' || document.hidden) return
  if (!destroyed && !connected && !terminal.value && !reconnectTimer) {
    void hydrate().then(ok => { if (ok && !destroyed && !connected) connect() })
  }
}
function scheduleReconnectStop() {
  if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = undefined }
}

function handleEvent(ev: WorkflowEvent) {
  if (typeof ev.seq === 'number') lastSeq = Math.max(lastSeq, ev.seq)
  const kind = String(ev.kind || '')
  if (kind === 'subagent_step') {
    const id = String(ev.task_id || '')
    if (!id) return
    // 入队，由 rAF 在每帧统一提交（见 stepQueue/flushSteps）
    stepQueue.push({ id, type: String(ev.step_type || 'thought'), text: String(ev.text || '') })
    scheduleStepFlush()
    return
  }
  if (kind === 'subagent_start') {
    liveMeta.set(String(ev.task_id || ''), { role: String(ev.role || ''), task: String(ev.task || ''), status: 'running' })
    scheduleRefresh()
  } else if (kind === 'subagent_complete') {
    liveMeta.set(String(ev.task_id || ''), {
      role: String(ev.role || ''),
      task: String(ev.task || ''),
      status: String(ev.status || 'ok') === 'ok' ? 'ok' : 'failed',
      elapsedMs: typeof ev.elapsed_ms === 'number' ? ev.elapsed_ms : undefined,
      conclusion: String(ev.conclusion || ''),
      error: String(ev.error || ''),
      trace: ev.trace,
    })
    scheduleRefresh()
  } else {
    if (ev.status) patchStatus(String(ev.status), ev.phase ? String(ev.phase) : undefined)
    // 门状态切换 / 波次 / 终态等关键事件后拉一次完整状态（任务表、results、review）
    if (/^(options|clarify|choice|plan|approval|execute|review|replan|dag_|complete|fail|interrupt|resume|auto_execute|subagent_retry)/.test(kind)) {
      scheduleRefresh(kind.startsWith('subagent') ? 700 : 350)
    }
  }
  if (!state.value) scheduleRefresh(0)
}

function patchStatus(next: string, phase?: string) {
  if (!state.value) return
  state.value = { ...state.value, status: next, phase: phase || state.value.phase }
}

// ---------------------------------------------------------------- 交互
async function withBusy(fn: () => Promise<void>) {
  busy.value = true
  try { await fn() }
  catch (e) { loadError.value = (e as Error).message || '工作流操作失败' }
  finally { busy.value = false }
}
async function onChoose(choiceId: string, customText?: string) {
  await withBusy(async () => {
    const r = await agentApi.workflowChoice(props.workflowId, choiceId, customText || '')
    if (r.workflow) setState(r.workflow)
    else loadError.value = r.error || '方案选择失败'
  })
}
async function onResearchSubmit(findings: string) {
  await withBusy(async () => {
    const r = await agentApi.workflowResearch(props.workflowId, findings)
    if (r.workflow) setState(r.workflow)
  })
}
async function onResearchRun() {
  await withBusy(async () => {
    const r = await agentApi.workflowResearchRun(props.workflowId, state.value?.request)
    if (r.workflow) setState(r.workflow)
  })
}
async function onApprove() {
  await withBusy(async () => {
    const id = props.workflowId
    // planned=首次执行（后端会先落审批门）；awaiting_approval=批准后真正开跑
    const r = status.value === 'awaiting_approval'
      ? await agentApi.workflowApprove(id, true, true)
      : await agentApi.workflowExecute(id)
    if (r.workflow) setState(r.workflow)
    if (r.workflow && r.workflow.status === 'awaiting_approval') {
      const continued = await agentApi.workflowApprove(id, true, true)
      if (continued.workflow) setState(continued.workflow)
      else loadError.value = continued.error || '批准后启动失败'
    }
  })
}
async function onReject() {
  await withBusy(async () => {
    const r = await agentApi.workflowApprove(props.workflowId, false)
    if (r.workflow) setState(r.workflow)
  })
}
async function onRevise(taskId: string, task: string, deps: string[]) {
  const tasks = (state.value?.tasks || []).map(t =>
    String(t.id) === taskId ? { ...t, task, depends_on: deps } : t)
  await withBusy(async () => {
    const r = await agentApi.workflowRevise(props.workflowId, tasks)
    if (r.workflow) setState(r.workflow)
  })
}
async function onReviseApprove(approved: boolean) {
  await withBusy(async () => {
    const r = await agentApi.workflowReviseApprove(props.workflowId, approved)
    if (r.workflow) setState(r.workflow)
  })
}
async function onAcceptanceSave(items: AcceptanceItem[]) {
  await withBusy(async () => {
    const r = await agentApi.workflowAcceptance(props.workflowId, items)
    if (r.workflow) setState(r.workflow)
    else loadError.value = r.error || '保存验收条件失败'
  })
}
async function onFinalAcceptance(approved: boolean) {
  await withBusy(async () => {
    const r = await agentApi.workflowAcceptanceDecide(props.workflowId, approved, finalNote.value)
    if (r.workflow) setState(r.workflow)
    else loadError.value = r.error || '记录验收结果失败'
  })
}
async function interrupt() {
  await withBusy(async () => {
    const r = await agentApi.workflowInterrupt(props.workflowId)
    if (r.workflow) setState(r.workflow)
  })
}
async function resume() {
  await withBusy(async () => {
    const r = await agentApi.workflowResume(props.workflowId)
    if (r.workflow) setState(r.workflow)
  })
}
async function retryMember(id: string) {
  await withBusy(async () => {
    const r = await agentApi.workflowSubagentRetry(props.workflowId, id)
    if (r.workflow) setState(r.workflow)
  })
}

function toggleMember(id: string) {
  const next = new Set(openMembers.value)
  if (next.has(id)) next.delete(id); else next.add(id)
  openMembers.value = next
}
function memberOpen(id: string) {
  return openMembers.value.has(id)
}
function stepTitle(step: WorkflowStepItem): string {
  const raw = step.text.trim()
  const tool = raw.match(/^([\w.-]+)\s*\(/)?.[1]
  if (step.type === 'action' && tool) return TOOL_NAMES[tool] || tool
  return STEP_LABEL[step.type] || step.type
}
function stepSummary(step: WorkflowStepItem): string {
  const raw = step.text.trim().replace(/\s+/g, ' ')
  if (raw.length <= 110) return raw
  return raw.slice(0, 107) + '…'
}
function fmtMs(ms?: number): string {
  if (!ms) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function taskStatusOf(id: string): string {
  const r = taskResults(state.value)[id]
  if (r) return String(r.status || 'ok')
  return members.value.find(m => m.id === id)?.status || 'pending'
}
function criterionTaskResult(evidence: string[]): { status: string; conclusion: string } | null {
  const ref = evidence.find(value => value.startsWith('task:'))
  if (!ref) return null
  const result = taskResults(state.value)[ref.slice(5)]
  return result ? { status: String(result.status || 'unknown'),
    conclusion: String(result.conclusion || result.summary || result.error || '') } : null
}
function eventDetail(ev: WorkflowEvent): string {
  const fields = ['task_id', 'role', 'option', 'task_count', 'attempt', 'reason', 'error', 'status', 'task_thread']
  return fields.filter(k => ev[k] !== undefined && ev[k] !== '').map(k => `${k}=${String(ev[k])}`).join(' · ')
}
function focusTask(id: string) {
  cardOpen.value = true
  openMembers.value = new Set([...openMembers.value, id])
  rootEl.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
function onFocusEvent(ev: Event) {
  const detail = (ev as CustomEvent<{ workflowId?: string; taskId?: string }>).detail
  if (detail?.workflowId && detail.workflowId !== props.workflowId) return
  if (detail?.taskId) focusTask(detail.taskId)
}

onMounted(async () => {
  window.addEventListener('docmind:wf-focus-member', onFocusEvent as EventListener)
  document.addEventListener('visibilitychange', onVisibilityResume)
  if (props.seed) setState({ tasks: [], subagents: [], events: [], ...props.seed, workflow_id: props.workflowId } as WorkflowState)
  await hydrate()
  connect()
})
onBeforeUnmount(() => {
  destroyed = true
  unsubscribe?.()
  scheduleReconnectStop()
  if (refreshTimer) clearTimeout(refreshTimer)
  if (stepRaf) { cancelAnimationFrame(stepRaf); stepRaf = 0 }
  stepQueue.length = 0
  window.removeEventListener('docmind:wf-focus-member', onFocusEvent as EventListener)
  document.removeEventListener('visibilitychange', onVisibilityResume)
})
</script>

<template>
  <div ref="rootEl" class="wf-card" :class="`wf-${status}`">
    <header class="wf-head" @click="cardOpen = !cardOpen">
      <span class="wf-ico">🧭</span>
      <b class="wf-title">开发工作流</b>
      <span class="wf-kind">{{ KIND_LABEL[state?.kind || seed?.kind || 'generic'] || '开发流程' }}</span>
      <span class="wf-status" :class="`wf-status-${status}`">{{ STATUS_LABEL[status] || status }}</span>
      <span class="wf-sep">·</span>
      <span class="wf-event">{{ latestEventLabel }}</span>
      <span class="wf-spacer" />
      <button
        v-if="gateNeeded"
        class="wf-gate-btn"
        @click.stop="gateDismissed = false"
      >{{ status === 'awaiting_approval' || status === 'planned' ? '待审批' : '待选择' }} ›</button>
      <span class="wf-chevron">{{ cardOpen ? '▾' : '▸' }}</span>
    </header>

    <div v-if="cardOpen" class="wf-body">
      <!-- 阶段条 -->
      <div class="wf-stages">
        <div v-for="(stage, i) in stages" :key="stage.key" class="wf-stage" :class="`wf-stage-${stageClass(i)}`">
          <span class="wf-stage-dot">{{ stageClass(i) === 'done' ? '✓' : i + 1 }}</span>
          <span class="wf-stage-label">{{ stage.label }}</span>
          <span v-if="i < stages.length - 1" class="wf-stage-line" />
        </div>
      </div>

      <p v-if="loadError" class="wf-error">⚠ {{ loadError }}</p>

      <!-- 目标 -->
      <p v-if="state?.request" class="wf-goal">{{ state.request }}</p>

      <!-- 进度/控制条 -->
      <div class="wf-meta">
        <span>{{ completedCount }}/{{ state?.tasks?.length || members.length }} 个任务完成</span>
        <span v-if="state?.steps">· {{ state.steps }} 步执行</span>
        <span v-if="state?.replans">· 重规划 {{ state.replans }} 次</span>
        <span v-if="state?.observability?.duration_ms">· 耗时 {{ fmtMs(state.observability.duration_ms) }}</span>
        <span class="wf-spacer" />
        <button v-if="status === 'executing'" class="wf-mini-btn wf-mini-danger" :disabled="busy" @click="interrupt">中断</button>
        <button v-if="status === 'interrupted'" class="wf-mini-btn wf-mini-primary" :disabled="busy" @click="resume">恢复执行</button>
      </div>

      <div v-if="state?.interrupt_reason" class="wf-alert">{{ state.interrupt_reason }}</div>

      <!-- 任务 DAG -->
      <div v-if="state?.tasks?.length" class="wf-tasks">
        <div v-for="task in state.tasks" :key="String(task.id)" class="wf-task" :class="`wf-task-${taskStatusOf(String(task.id))}`">
          <span class="wf-task-dot" />
          <b class="wf-task-id">{{ task.id }}</b>
          <span class="wf-task-role">{{ roleMeta(String(task.role)).label }}</span>
          <span class="wf-task-text">{{ task.task }}</span>
          <em class="wf-task-state">{{ ({ pending: '待执行', running: '执行中', ok: '已完成', failed: '失败', blocked: '阻塞' } as Record<string, string>)[taskStatusOf(String(task.id))] || taskStatusOf(String(task.id)) }}</em>
        </div>
      </div>

      <!-- 成员实时轨迹 -->
      <div v-if="members.length" class="wf-members">
        <div class="wf-members-head">
          <b>团队成员（{{ members.length }}）</b>
          <small>每个成员的思考、工具调用与观察都实时显示在这里</small>
        </div>
        <div v-for="m in members" :key="m.id" class="wf-member" :class="`wf-member-${m.status}`">
          <button class="wf-member-row" @click="toggleMember(m.id)">
            <span class="wf-member-avatar">{{ m.avatar }}</span>
            <span class="wf-member-main">
              <b>{{ m.label }}</b>
              <span class="wf-member-task">{{ m.task }}</span>
            </span>
            <span class="wf-member-side">
              <em class="wf-member-status">{{ ({ pending: '待执行', running: '执行中', ok: '已完成', failed: '失败', blocked: '阻塞' } as Record<string, string>)[m.status] || m.status }}</em>
              <small v-if="m.elapsedMs">{{ fmtMs(m.elapsedMs) }}</small>
            </span>
            <span class="wf-member-chevron">{{ memberOpen(m.id) ? '▾' : '▸' }}</span>
          </button>
          <div v-if="memberOpen(m.id)" class="wf-member-detail">
            <div v-if="!m.steps.length && !m.trace?.steps?.length && m.status === 'running'" class="wf-member-wait">
              等待该成员的第一条输出<span class="wf-dots">…</span>
            </div>
            <div v-for="(step, i) in m.steps" :key="'l' + i" class="wf-step" :class="`wf-step-${step.type}`">
              <span class="wf-step-glyph">{{ STEP_GLYPH[step.type] || '·' }}</span>
              <div class="wf-step-body">
                <span class="wf-step-title">{{ stepTitle(step) }}</span>
                <span class="wf-step-summary">{{ stepSummary(step) }}</span>
                <pre class="wf-step-full">{{ step.text }}</pre>
              </div>
            </div>
            <div v-if="!m.steps.length && m.trace?.steps?.length" class="wf-step-hint">该成员的实时轨迹已折叠，保留收尾工具轨迹：</div>
            <div v-for="(row, i) in (m.trace?.steps || [])" :key="'t' + i" class="wf-step wf-step-action">
              <span class="wf-step-glyph">↗</span>
              <div class="wf-step-body">
                <span class="wf-step-title">{{ String(row.action || row.tool || '工具') }}</span>
                <span v-if="row.obs" class="wf-step-summary">{{ String(row.obs) }}</span>
              </div>
            </div>
            <div v-if="m.conclusion" class="wf-member-conclusion"><b>结论</b>{{ m.conclusion }}</div>
            <div v-if="m.error" class="wf-member-error"><b>错误</b>{{ m.error }}
              <button class="wf-mini-btn" :disabled="busy" @click="retryMember(m.id)">重试该成员</button>
            </div>
            <button
              v-else-if="m.status === 'failed' || m.status === 'blocked'"
              class="wf-mini-btn"
              :disabled="busy"
              @click="retryMember(m.id)"
            >重试该成员</button>
          </div>
        </div>
      </div>

      <!-- 复核结果 -->
      <section v-if="state?.acceptance_contract?.items?.length" class="wf-acceptance">
        <b>验收条件</b>
        <span v-if="state.acceptance_contract.approved_revision" class="wf-acceptance-meta">已随计划确认 · 第 {{ state.acceptance_contract.approved_revision }} 版</span>
        <span v-else class="wf-acceptance-meta">等待计划审核</span>
        <div v-for="item in state.acceptance_contract.items" :key="item.id" class="wf-acceptance-item">
          <span>{{ item.required ? '必需' : '可选' }}</span>
          <div><b>{{ item.statement }}</b><small>{{ item.method }} · 预期证据：{{ item.evidence.join('、') || '待补充' }}</small></div>
          <small v-if="criterionTaskResult(item.evidence)" class="wf-acceptance-result">任务结果：{{ criterionTaskResult(item.evidence)?.status }} · {{ criterionTaskResult(item.evidence)?.conclusion }}</small>
        </div>
        <template v-if="status === 'completed'">
          <p v-if="state.acceptance_contract.final_decision === 'accepted'" class="wf-ok">用户已验收通过</p>
          <p v-else-if="state.acceptance_contract.final_decision === 'rejected'" class="wf-bad">用户未通过验收：{{ state.acceptance_contract.final_note || '请继续说明不满足的效果' }}</p>
          <div v-else class="wf-acceptance-final">
            <p>工作流执行已结束。请按条件检查实际效果和证据，再做最终验收。</p>
            <textarea v-model="finalNote" rows="2" placeholder="未通过时请写明哪些效果需要继续修改；通过时可选填备注" />
            <div><button class="wf-mini-btn" :disabled="busy || !finalNote.trim()" @click="onFinalAcceptance(false)">未通过</button>
              <button class="wf-mini-btn wf-mini-primary" :disabled="busy" @click="onFinalAcceptance(true)">验收通过</button></div>
          </div>
        </template>
      </section>
      <div v-if="state?.review" class="wf-review">
        <b :class="state.review.ok ? 'wf-ok' : 'wf-bad'">{{ state.review.ok ? '✓ 复核通过' : '! 复核未通过' }}</b>
        <div v-for="(f, i) in reviewFailures" :key="i" class="wf-review-fail">
          <span>{{ f.message }}</span><small v-if="f.recovery">建议：{{ f.recovery }}</small>
        </div>
      </div>

      <!-- 事件日志（透明：整个流程发生了什么） -->
      <details v-if="state?.events?.length" class="wf-events">
        <summary>流程事件（{{ state.events.length }}）</summary>
        <div v-for="(ev, i) in state.events.slice(-16)" :key="i" class="wf-event">
          <b>{{ EVENT_LABELS[String(ev.kind)] || ev.kind }}</b>
          <small v-if="eventDetail(ev)">{{ eventDetail(ev) }}</small>
        </div>
      </details>
    </div>

    <WorkflowGateDialog
      v-if="state && gateOpen"
      :state="state"
      :busy="busy"
      @choose="onChoose"
      @research-submit="onResearchSubmit"
      @research-run="onResearchRun"
      @approve="onApprove"
      @reject="onReject"
      @revise="onRevise"
      @revise-approve="onReviseApprove"
      @acceptance-save="onAcceptanceSave"
      @dismiss="gateDismissed = true"
    />
  </div>
</template>

<style scoped>
.wf-card {
  margin: 4px 0 10px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-raised);
  overflow: hidden;
}
.wf-card.wf-executing { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(37,96,212,.08); }
.wf-card.wf-failed { border-color: rgba(214,78,78,.5); }
.wf-card.wf-completed { border-color: rgba(52,168,112,.45); }
.wf-head {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px; cursor: pointer; user-select: none;
  background: var(--bg-hover);
}
.wf-head:hover { filter: brightness(.985); }
.wf-ico { font-size: 13px; }
.wf-title { font-size: 13px; font-weight: 700; color: var(--text); }
.wf-kind { font-size: 10.5px; color: var(--text-faint); border: 1px solid var(--border); border-radius: 99px; padding: 1px 7px; }
.wf-status { font-size: 11px; font-weight: 600; }
.wf-status-executing, .wf-status-generating_options, .wf-status-planning { color: var(--accent); }
.wf-status-completed { color: var(--green); }
.wf-status-failed { color: var(--danger); }
.wf-status-awaiting_choice, .wf-status-awaiting_approval { color: var(--amber); }
.wf-sep { color: var(--text-faint); font-size: 10px; }
.wf-event { font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wf-spacer { flex: 1; }
.wf-chevron { color: var(--text-faint); font-size: 11px; }
.wf-gate-btn {
  border: 1px solid var(--amber); color: var(--amber);
  background: rgba(214,158,46,.08); border-radius: 99px;
  font-size: 11px; font-weight: 600; padding: 2px 10px; cursor: pointer;
}
.wf-gate-btn:hover { background: rgba(214,158,46,.16); }
.wf-body { padding: 11px 13px 12px; display: grid; gap: 10px; }
.wf-stages { display: flex; align-items: center; }
.wf-stage { position: relative; display: flex; align-items: center; gap: 5px; flex: 1; }
.wf-stage-dot {
  width: 18px; height: 18px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 10px; font-weight: 700; flex: 0 0 auto;
  border: 1px solid var(--border-strong); color: var(--text-faint); background: var(--bg-raised);
}
.wf-stage-label { font-size: 10.5px; color: var(--text-faint); white-space: nowrap; }
.wf-stage-line { flex: 1; height: 1px; background: var(--border); margin: 0 4px; min-width: 8px; }
.wf-stage-active .wf-stage-dot { border-color: var(--accent); color: #fff; background: var(--accent); }
.wf-stage-active .wf-stage-label { color: var(--accent); font-weight: 600; }
.wf-stage-done .wf-stage-dot { border-color: var(--green); background: var(--green); color: #fff; }
.wf-stage-done .wf-stage-label { color: var(--text-muted); }
.wf-stage:last-child .wf-stage-line { display: none; }
.wf-goal {
  margin: 0; padding: 8px 10px; font-size: 12.5px; line-height: 1.6;
  border-left: 3px solid var(--accent); background: var(--bg-hover); border-radius: 0 8px 8px 0;
  color: var(--text);
}
.wf-acceptance { display: grid; gap: 7px; padding: 10px; border: 1px solid var(--border); border-radius: 9px; font-size: 12px; }
.wf-acceptance-meta, .wf-acceptance-item small { color: var(--text-faint); font-size: 11px; }
.wf-acceptance-item { display: flex; gap: 8px; border-top: 1px solid var(--border); padding-top: 7px; }
.wf-acceptance-item > span { flex: 0 0 auto; color: var(--accent); font-size: 11px; }
.wf-acceptance-item > div { display: grid; gap: 3px; }
.wf-acceptance-final { display: grid; gap: 7px; }
.wf-acceptance-final p { margin: 0; color: var(--text-muted); }
.wf-acceptance-final textarea { width: 100%; box-sizing: border-box; padding: 7px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-raised); color: var(--text); resize: vertical; }
.wf-acceptance-final > div { display: flex; gap: 7px; }
.wf-meta { display: flex; align-items: center; gap: 6px; font-size: 11px; color: var(--text-faint); font-variant-numeric: tabular-nums; }
.wf-error { margin: 0; color: var(--danger); font-size: 11.5px; }
.wf-alert {
  margin: 0; padding: 6px 9px; font-size: 11.5px;
  border: 1px solid rgba(214,158,46,.5); border-radius: 7px; background: rgba(214,158,46,.08); color: var(--amber);
}
.wf-mini-btn {
  border: 1px solid var(--border); border-radius: 6px; background: var(--bg-raised);
  color: var(--text-muted); font-size: 11px; padding: 2px 9px; cursor: pointer;
}
.wf-mini-btn:hover:not(:disabled) { color: var(--text); border-color: var(--border-strong); }
.wf-mini-btn:disabled { opacity: .5; cursor: default; }
.wf-mini-danger { color: var(--danger); border-color: var(--danger); }
.wf-mini-danger:hover:not(:disabled) { background: var(--danger); color: #fff; }
.wf-mini-primary { color: var(--accent); border-color: var(--accent); }
.wf-tasks { display: grid; gap: 4px; }
.wf-task {
  display: flex; align-items: baseline; gap: 8px;
  padding: 6px 9px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-surface, var(--bg-hover)); font-size: 12px;
}
.wf-task-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-faint); flex: 0 0 auto; align-self: center; }
.wf-task-running .wf-task-dot { background: var(--accent); box-shadow: 0 0 0 3px rgba(37,96,212,.15); }
.wf-task-ok .wf-task-dot { background: var(--green); }
.wf-task-failed .wf-task-dot, .wf-task-blocked .wf-task-dot { background: var(--danger); }
.wf-task-id { font-family: ui-monospace, monospace; font-size: 10.5px; color: var(--accent); }
.wf-task-role { font-size: 10.5px; color: var(--text-faint); font-weight: 600; flex: 0 0 auto; }
.wf-task-text { flex: 1; min-width: 0; color: var(--text); line-height: 1.5; }
.wf-task-state { flex: 0 0 auto; font-style: normal; font-size: 10.5px; color: var(--text-faint); }
.wf-task-running .wf-task-state { color: var(--accent); }
.wf-task-ok .wf-task-state { color: var(--green); }
.wf-task-failed .wf-task-state, .wf-task-blocked .wf-task-state { color: var(--danger); }
.wf-members { display: grid; gap: 6px; }
.wf-members-head { display: flex; align-items: baseline; gap: 8px; }
.wf-members-head b { font-size: 12px; color: var(--text); }
.wf-members-head small { font-size: 10.5px; color: var(--text-faint); }
.wf-member { border: 1px solid var(--border); border-radius: 9px; background: var(--bg-surface, var(--bg-hover)); }
.wf-member-running { border-color: rgba(37,96,212,.45); }
.wf-member-row {
  width: 100%; display: flex; align-items: center; gap: 9px;
  padding: 8px 10px; border: 0; background: transparent; cursor: pointer; text-align: left;
}
.wf-member-row:hover { background: var(--bg-hover); border-radius: 9px; }
.wf-member-avatar {
  width: 24px; height: 24px; border-radius: 7px; flex: 0 0 auto;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 11px; font-weight: 700;
  background: var(--bg-selected); color: var(--accent); border: 1px solid var(--border);
}
.wf-member-main { flex: 1; min-width: 0; display: grid; gap: 1px; }
.wf-member-main b { font-size: 12px; color: var(--text); }
.wf-member-task { font-size: 11px; color: var(--text-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wf-member-side { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; }
.wf-member-status { font-style: normal; font-size: 10.5px; color: var(--text-faint); }
.wf-member-running .wf-member-status { color: var(--accent); }
.wf-member-ok .wf-member-status { color: var(--green); }
.wf-member-failed .wf-member-status, .wf-member-blocked .wf-member-status { color: var(--danger); }
.wf-member-side small { font-size: 10px; color: var(--text-faint); font-variant-numeric: tabular-nums; }
.wf-member-chevron { color: var(--text-faint); font-size: 10px; }
.wf-member-detail {
  margin: 0 10px 9px 42px;
  padding-left: 11px; border-left: 2px solid var(--border);
  display: grid; gap: 5px;
}
.wf-member-wait { font-size: 11.5px; color: var(--text-faint); padding: 3px 0; }
.wf-step { display: flex; gap: 7px; position: relative; }
.wf-step-glyph {
  width: 18px; flex: 0 0 auto; text-align: center;
  font-size: 11px; color: var(--text-faint); padding-top: 1px;
}
.wf-step-action .wf-step-glyph { color: var(--accent); }
.wf-step-observation .wf-step-glyph { color: var(--green); }
.wf-step-thought .wf-step-glyph { color: var(--text-muted); }
.wf-step-body { min-width: 0; display: grid; gap: 1px; }
.wf-step-title { font-size: 11px; font-weight: 600; color: var(--text); }
.wf-step-summary {
  font-size: 11.5px; line-height: 1.5; color: var(--text-muted);
  white-space: pre-wrap; word-break: break-word;
}
.wf-step-full {
  display: none; margin: 3px 0 0; padding: 7px 9px;
  background: var(--bg-raised); border: 1px solid var(--border); border-radius: 7px;
  font: inherit; font-size: 11px; line-height: 1.55; color: var(--text-muted);
  white-space: pre-wrap; word-break: break-word; max-height: 220px; overflow-y: auto;
}
.wf-step:hover .wf-step-full { display: block; }
.wf-step-hint { font-size: 10.5px; color: var(--text-faint); }
.wf-member-conclusion, .wf-member-error {
  margin-top: 2px; padding: 7px 9px; border-radius: 7px;
  font-size: 11.5px; line-height: 1.55; display: grid; gap: 3px;
}
.wf-member-conclusion b, .wf-member-error b { font-size: 10.5px; }
.wf-member-conclusion { background: rgba(52,168,112,.08); color: var(--text); }
.wf-member-conclusion b { color: var(--green); }
.wf-member-error { background: rgba(214,78,78,.07); color: var(--danger); }
.wf-member-error .wf-mini-btn { justify-self: start; margin-top: 3px; }
.wf-review {
  display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
  font-size: 11.5px; padding: 7px 9px; border-radius: 8px; background: var(--bg-hover);
}
.wf-ok { color: var(--green); }
.wf-bad { color: var(--danger); }
.wf-review-fail { display: grid; gap: 1px; width: 100%; font-size: 11px; color: var(--text-muted); }
.wf-review-fail small { color: var(--text-faint); }
.wf-events { font-size: 11px; }
.wf-events summary { cursor: pointer; color: var(--text-faint); }
.wf-event { display: flex; gap: 8px; padding: 2px 0; }
.wf-event b { font-weight: 600; color: var(--text-muted); flex: 0 0 auto; }
.wf-event small { color: var(--text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.wf-dots { animation: wf-blink 1.1s infinite; }
@keyframes wf-blink { 50% { opacity: .25; } }
</style>
