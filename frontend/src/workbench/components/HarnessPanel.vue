<script setup lang="ts">
// AI 运行台：把 harness 后端能力（成本预算 / 会话 / 逐轮轨迹 / 技能与钩子）
// 做成普通人看得懂的面板。静态预览（无后端）时自动使用演示数据。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  harnessApi, setSessionId, getSessionId, startNewSession, fmtTraceError,
  type BudgetStatus, type BudgetCheck, type SessionInfo,
  type TraceItem, type TraceSummary, type SkillInfo,
  agentApi, type WorkflowState, type WorkflowBackendStatus, type LangSmithStatus, type WorkflowEvaluation,
  type RetrievalTraceEvent, type RetrievalRuntimeStatus,
} from '../api'
import {
  demoMode,
  demoBudget, demoSessions, demoTraceItems, demoTraceSummary, demoSkills, demoHooks,
} from '../composables/demo'
import { useWorkbench } from '../composables/workbench'

const { openFlow } = useWorkbench()

const open = ref(false)
const tab = ref<'cost' | 'sessions' | 'trace' | 'skills' | 'workflow'>('cost')
const loading = ref(false)
const error = ref('')

const budget = ref<BudgetStatus | null>(null)
const budgetCheck = ref<BudgetCheck | null>(null)
const sessions = ref<SessionInfo[]>([])
/** 当前标签页的会话 id：用于在列表里高亮「当前会话」。 */
const currentId = ref(getSessionId())
const traces = ref<TraceItem[]>([])
const traceSummary = ref<TraceSummary | null>(null)
const skills = ref<SkillInfo[]>([])
const skillsDir = ref('')
const hooksCounts = ref<Record<string, number>>({ pre_tool: 0, post_tool: 0, pre_turn: 0, post_turn: 0 })
const hooksWorkflowCounts = ref<Record<string, number>>({})
const hookKinds = ref<string[]>([])
const hookBreakpoints = ref<Record<string, { enabled?: boolean; block?: boolean; match?: string; reason?: string }>>({})
const breakpointKind = ref('before_tool')
const breakpointMatch = ref('')
const breakpointReason = ref('')
const breakpointBlock = ref(true)
const hooksTotal = computed(() => Object.values(hooksCounts.value).reduce((a, b) => a + b, 0))

const limitInput = ref('')
const actionMsg = ref('')
const workflow = ref<WorkflowState | null>(null)
const workflowPrompt = ref('')
const workflowResearch = ref('')
const workflowCustom = ref('')
const workflowCustomPending = ref(false)
const revisionOpen = ref(false)
const revisionTaskId = ref('')
const revisionTaskText = ref('')
const revisionTaskDeps = ref('')
const workflowBusy = ref(false)
const workflowBackend = ref<WorkflowBackendStatus | null>(null)
const langsmith = ref<LangSmithStatus | null>(null)
const workflowEvaluation = ref<WorkflowEvaluation | null>(null)
const retrievalEvents = ref<RetrievalTraceEvent[]>([])
const retrievalRuntime = ref<RetrievalRuntimeStatus['retrieval'] | null>(null)
const selectedSubagentId = ref('')

const TABS = [
  { key: 'cost', label: '花费' },
  { key: 'sessions', label: '对话' },
  { key: 'trace', label: '操作记录' },
  { key: 'skills', label: '技能与扩展' },
  { key: 'workflow', label: '游戏工作流' },
] as const

function money(v: number | null | undefined): string {
  const n = Number(v || 0)
  return `¥${n.toFixed(4)}`
}
function ago(iso: string): string {
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return iso
  const d = Date.now() - t
  const m = Math.floor(d / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return new Date(iso).toLocaleDateString('zh-CN')
}
function fmtTime(iso: string): string {
  const t = new Date(iso)
  if (Number.isNaN(t.getTime())) return iso
  return t.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}
function fmtMs(ms: number): string {
  if (!ms) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
function outcomeTag(item: TraceItem): { text: string; cls: string } {
  if (item.aborted) return { text: '已停止', cls: 'hp-tag-stop' }
  if (item.error) return { text: '出错', cls: 'hp-tag-err' }
  if (item.outcome === 'completed') return { text: '完成', cls: 'hp-tag-ok' }
  return { text: item.outcome || '完成', cls: 'hp-tag-ok' }
}
function toolNames(item: TraceItem): string[] {
  return (item.steps || []).map((s) => s.action)
}
function providerLabel(p: string): string {
  if (!p) return '测试/离线'
  if (p === '?') return '未知'
  return p
}
function checkpointText(): string {
  const health = workflowBackend.value?.checkpoint_health
  if (!health) return '未读取'
  if (health.healthy) return `${health.backend || '已启用'} · v${health.schema_version ?? '—'}`
  return health.error || health.state || '异常'
}
function contextCompressionText(): string {
  const meta = (workflow.value?.context_layers?.snapshot_meta || {}) as Record<string, unknown>
  if (!Object.keys(meta).length) return '未生成'
  const state = String(meta.job_state || '同步')
  const valid = meta.valid === false ? ' · digest 异常' : meta.valid === true ? ' · digest 正常' : ''
  return `${state}${valid}`
}
function langsmithText(): string {
  if (!langsmith.value) return '未读取'
  if (langsmith.value.realtime) {
    const pending = Number(langsmith.value.pending_exports || 0)
    return `实时 · ${langsmith.value.project || 'default'}${pending ? ` · 待发送 ${pending}` : ''}`
  }
  if (!langsmith.value.installed) return '未安装'
  if (!langsmith.value.key_configured) return '未配置密钥'
  return '未启用'
}
function taskStatus(task: Record<string, unknown>): string {
  const id = String(task.id || '')
  const result = workflow.value?.results?.[id]
  const status = String(result?.status || 'pending')
  return ({ ok: '完成', failed: '失败', blocked: '阻塞', pending: '待执行' } as Record<string, string>)[status] || status
}
function taskStatusClass(task: Record<string, unknown>): string {
  const status = taskStatus(task)
  return status === '完成' ? 'ok' : status === '失败' || status === '阻塞' ? 'bad' : 'pending'
}
const WORKFLOW_STAGES = [
  { key: 'clarify', label: '澄清' },
  { key: 'research', label: '检索' },
  { key: 'plan', label: '规划' },
  { key: 'execute', label: '执行' },
  { key: 'review', label: '复核' },
] as const
function workflowStageIndex(): number {
  const phase = String(workflow.value?.phase || '')
  const status = String(workflow.value?.status || '')
  if (phase === 'complete' || status === 'completed') return WORKFLOW_STAGES.length
  if (phase === 'fail' || status === 'failed') return Math.max(0, WORKFLOW_STAGES.findIndex(item => item.key === 'review'))
  const index = WORKFLOW_STAGES.findIndex(item => item.key === phase)
  return index >= 0 ? index : 0
}
function workflowStageClass(index: number): string {
  const current = workflowStageIndex()
  const status = String(workflow.value?.status || '')
  if (status === 'failed' && index === current) return 'bad'
  if (index < current || status === 'completed') return 'done'
  if (index === current) return 'current'
  return 'pending'
}
function workflowStatusLabel(): string {
  const status = String(workflow.value?.status || '')
  return ({ executing: '正在执行', planning: '正在规划', reviewing: '正在复核',
    awaiting_choice: '等待选择', awaiting_approval: '等待审核', awaiting_research: '等待检索',
    interrupted: '已暂停', completed: '已完成', failed: '执行失败' } as Record<string, string>)[status]
    || (workflow.value?.phase ? `进行中 · ${workflow.value.phase}` : '准备中')
}
function workflowCurrentEvent(): Record<string, unknown> | null {
  const events = workflow.value?.events
  return Array.isArray(events) && events.length ? events[events.length - 1] as Record<string, unknown> : null
}
function workflowEventLabel(): string {
  const event = workflowCurrentEvent()
  if (!event) return '等待工作流事件'
  const labels: Record<string, string> = {
    route_research: '已进入检索', route_research_gate: '等待检索结果',
    options_generated: '已生成方案选项', choice_gate: '等待用户选择',
    plan: '任务图已生成', plan_gate: '等待计划审核', execute_wave: '正在执行任务波次',
    review: '正在复核结果', review_replan: '复核失败，准备重规划', replan: '正在重规划',
    complete: '工作流已完成', fail: '工作流失败',
  }
  return labels[String(event.kind || event.event || '')] || String(event.kind || event.event || '工作流事件')
}
const workflowCompletedTasks = computed(() => Object.values(workflow.value?.results || {}).filter(item => item?.status === 'ok').length)
const workflowTaskCount = computed(() => workflow.value?.tasks?.length || Object.keys(workflow.value?.results || {}).length)
function scoreText(): string {
  const score = workflowEvaluation.value?.score
  return typeof score === 'number' ? `${Math.round(score * 100)}%` : '未评估'
}
function traceId(): string {
  const id = String(workflow.value?.langsmith_trace?.root_run_id || '')
  return id ? `${id.slice(0, 8)}…` : '—'
}
function traceEventCount(): number {
  return Number(workflow.value?.langsmith_trace?.event_count || 0)
}
function traceExportErrors(): Array<{ name?: string; error?: string }> {
  return Array.isArray(langsmith.value?.recent_export_errors)
    ? langsmith.value!.recent_export_errors! : []
}
function retrievalTime(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour12: false })
}
function retrievalScore(value: unknown): string {
  return typeof value === 'number' ? value.toFixed(4) : '—'
}
function skillHistoryText(skill: SkillInfo): string {
  return (skill.history_versions || []).map(item => item.version || '—').join('、')
}
function reviewFailures(): Array<{ kind?: string; message?: string; recovery?: string }> {
  const failures = workflow.value?.review?.failures
  return Array.isArray(failures) ? failures as Array<{ kind?: string; message?: string; recovery?: string }> : []
}
type WorkflowTraceRow = { task: string; index: number; action: string; observation: string; kind: string; ok?: boolean; tokens?: string; elapsed?: string }
const workflowTraceRows = computed<WorkflowTraceRow[]>(() => {
  const rows: WorkflowTraceRow[] = []
  for (const [task, raw] of Object.entries(workflow.value?.results || {})) {
    const trace = (raw as Record<string, unknown>)?.trace
    const steps = trace && typeof trace === 'object' ? (trace as Record<string, unknown>).steps : []
    const stepItems = Array.isArray(steps) ? steps : []
    const rawTrace = trace && typeof trace === 'object' ? trace as Record<string, unknown> : {}
    stepItems.forEach((step, index) => {
      if (!step || typeof step !== 'object') return
      const item = step as Record<string, unknown>
      const action = String(item.action || item.tool || '未命名步骤')
      const lower = action.toLowerCase()
      const kind = lower.includes('mcp') || lower.includes('connector') ? 'MCP' : '工具'
      const tokenMeta = rawTrace.tokens && typeof rawTrace.tokens === 'object' ? rawTrace.tokens as Record<string, unknown> : null
      const tokens = tokenMeta && (tokenMeta.in !== undefined || tokenMeta.out !== undefined)
        ? `${String(tokenMeta.in ?? 0)}/${String(tokenMeta.out ?? 0)}` : undefined
      rows.push({ task, index: index + 1, action, observation: String(item.obs || item.observation || item.error || ''), kind,
        ok: typeof item.ok === 'boolean' ? item.ok : undefined,
        tokens, elapsed: rawTrace.elapsed_ms !== undefined ? fmtMs(Number(rawTrace.elapsed_ms)) : undefined })
    })
    const hooks = Array.isArray(rawTrace.hooks) ? rawTrace.hooks : []
    hooks.forEach((hook, index) => {
      if (!hook || typeof hook !== 'object') return
      const item = hook as Record<string, unknown>
      const payload = item.payload && typeof item.payload === 'object' ? item.payload as Record<string, unknown> : {}
      const details = [
        item.blocked ? `已阻断：${String(item.reason || '需要审核')}` : '',
        Number(item.error_count || 0) ? `Hook 异常 ${String(item.error_count)} 次` : '',
        payload.tool ? `tool=${String(payload.tool)}` : '',
        payload.connector ? `connector=${String(payload.connector)}` : '',
      ].filter(Boolean).join(' · ')
      rows.push({ task, index: stepItems.length + index + 1,
        action: `hook:${String(item.kind || 'workflow')}`,
        observation: details || 'Hook 已执行，未阻断', kind: 'Hook',
        ok: typeof item.ok === 'boolean' ? item.ok : undefined })
    })
  }
  return rows.slice(-80).reverse()
})
function workflowEventDetail(event: Record<string, unknown>): string {
  const fields = ['task_id', 'role', 'planner', 'added', 'attempt', 'reason', 'error', 'status', 'backend', 'task_thread']
  return fields.filter(key => event[key] !== undefined && event[key] !== '').map(key => `${key}=${String(event[key])}`).join(' · ')
}
const SUBAGENT_ROLE_META: Record<string, { label: string; avatar: string }> = {
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
function subagentMeta(agent: { role?: string }) {
  return SUBAGENT_ROLE_META[String(agent.role || '').toLowerCase()] || { label: agent.role || 'Subagent', avatar: '代' }
}
function subagentTask(agent: { id?: string }): string {
  const task = (workflow.value?.tasks || []).find(item => String(item.id || '') === String(agent.id || ''))
  return task ? String(task.task || task.description || '未提供任务描述') : '任务详情不可用'
}
function subagentStatusLabel(status?: string): string {
  return ({ pending: '待执行', running: '执行中', ok: '已完成', failed: '执行失败', blocked: '已阻塞' } as Record<string, string>)[String(status || '')] || String(status || '待执行')
}
function subagentStatusClass(status?: string): string {
  const value = String(status || 'pending')
  return value === 'ok' ? 'ok' : value === 'failed' || value === 'blocked' ? 'bad' : value === 'running' ? 'running' : 'pending'
}
function subagentReflectionLabel(agent: { reflection_result?: { ok?: boolean }; reflection?: boolean }): string {
  if (agent.reflection_result) return agent.reflection_result.ok ? '复核通过' : '复核未通过'
  return agent.reflection === false ? '不复核' : '待复核'
}
const selectedSubagent = computed(() => {
  const members = workflow.value?.subagents || []
  if (!members.length) return null
  const selected = members.find(item => String(item.id || '') === selectedSubagentId.value)
  if (selected) return selected
  return members.find(item => item.status === 'running' || item.status === 'failed' || item.status === 'blocked') || members[0]
})
const selectedSubagentEvents = computed(() => {
  const id = String(selectedSubagent.value?.id || '')
  if (!id) return []
  return (workflow.value?.events || []).filter(event => String(event.task_id || '') === id).slice(-16).reverse()
})
const selectedSubagentTrace = computed(() => {
  const id = String(selectedSubagent.value?.id || '')
  if (!id) return []
  return workflowTraceRows.value.filter(row => row.task === id).slice(0, 16)
})
function selectSubagent(id?: string) {
  selectedSubagentId.value = String(id || '')
}
function selectedSubagentConclusion(): string {
  const id = String(selectedSubagent.value?.id || '')
  const result = workflow.value?.results?.[id]
  return String(result?.conclusion || result?.summary || result?.error || '该成员尚未返回结果')
}
async function retrySelectedSubagent() {
  if (!workflow.value || !selectedSubagent.value) return
  const id = String(selectedSubagent.value.id || '')
  if (!id || !['failed', 'blocked'].includes(String(selectedSubagent.value.status || ''))) return
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowSubagentRetry(workflow.value.workflow_id, id, getSessionId())
    if (r.ok && r.workflow) {
      workflow.value = r.workflow
      actionMsg.value = `${id} 已完成第 ${r.workflow.subagent_retries?.[id] || selectedSubagent.value.retry_count || 1} 次单任务重试。`
      void refreshWorkflowDiagnostics()
    } else actionMsg.value = r.error || 'Subagent 重试失败'
  } finally { workflowBusy.value = false }
}

async function loadAll() {
  loading.value = true
  error.value = ''
  currentId.value = getSessionId()   // 每次刷新列表时同步「当前会话」标记
  try {
    if (demoMode.value) {
      budget.value = demoBudget.status
      budgetCheck.value = demoBudget.check
      sessions.value = [...demoSessions]
      traces.value = [...demoTraceItems]
      traceSummary.value = demoTraceSummary as unknown as TraceSummary
      skills.value = [...demoSkills.items]
      skillsDir.value = demoSkills.skills_dir
      hooksCounts.value = { ...demoHooks.counts }
      return
    }
    const [b, s, t, sk, hk] = await Promise.all([
      harnessApi.budget(),
      harnessApi.sessions(),
      harnessApi.trace(30),
      harnessApi.skills(),
      harnessApi.hooks(),
    ])
    budget.value = b.status
    budgetCheck.value = b.check
    sessions.value = s.items || []
    traces.value = t.items || []
    traceSummary.value = t.summary
    skills.value = sk.items || []
    skillsDir.value = sk.skills_dir
    hooksCounts.value = hk.counts || hooksCounts.value
    hooksWorkflowCounts.value = hk.workflow_counts || hooksWorkflowCounts.value
    hookKinds.value = hk.workflow_kinds || hookKinds.value
    hookBreakpoints.value = hk.breakpoints || hookBreakpoints.value
  } catch (e) {
    error.value = (e as Error).message || '数据获取失败'
  } finally {
    loading.value = false
  }
}

watch(open, (v) => {
  if (v) {
    void loadAll()
    if (tab.value === 'workflow') startWorkflowPolling()
  } else {
    stopWorkflowPolling()
  }
})

// 项目切换：会话按项目隔离，面板开着时必须重拉（否则仍显示旧项目的会话列表）。
function onProjectChanged() {
  currentId.value = getSessionId()
  if (open.value) void loadAll()
}
function onOpenHarnessWorkflow(ev?: Event) {
  const prompt = String((ev as CustomEvent<{ prompt?: string }> | undefined)?.detail?.prompt || '').trim()
  if (!prompt) return
  open.value = true
  tab.value = 'workflow'
  workflowPrompt.value = prompt
  workflowCustomPending.value = false
  actionMsg.value = '正在根据你的目标生成方案，请稍候…'
  if (demoMode.value) {
    actionMsg.value = '演示模式只展示入口；连接真实项目后会由 AI 生成方案。'
    return
  }
  void startWorkflow()
}
onMounted(() => {
  window.addEventListener('docmind:project-changed', onProjectChanged)
  window.addEventListener('docmind:open-harness-workflow', onOpenHarnessWorkflow as EventListener)
})
onBeforeUnmount(() => {
  window.removeEventListener('docmind:project-changed', onProjectChanged)
  window.removeEventListener('docmind:open-harness-workflow', onOpenHarnessWorkflow as EventListener)
})

function switchTab(k: typeof tab.value) {
  tab.value = k
  if (k === 'workflow') {
    void refreshWorkflowDiagnostics()
    if (open.value) startWorkflowPolling()
  } else {
    stopWorkflowPolling()
  }
}

let workflowPollTimer: ReturnType<typeof setInterval> | undefined
function stopWorkflowPolling() {
  if (workflowPollTimer !== undefined) {
    clearInterval(workflowPollTimer)
    workflowPollTimer = undefined
  }
}
function startWorkflowPolling() {
  if (workflowPollTimer !== undefined) return
  workflowPollTimer = setInterval(() => {
    // The backend persists workflow events incrementally, so polling makes
    // subagent starts, wave progress, failures and reflection visible while
    // long-running actions are executing.
    if (!open.value || tab.value !== 'workflow') return
    // Do not let a background GET overwrite the optimistic state while an
    // action is changing the workflow. The next interval observes the durable
    // result after the action releases the busy lock.
    if (workflowBusy.value) return
    if (!workflow.value?.workflow_id) {
      void refreshWorkflowDiagnostics()
      return
    }
    const terminal = ['completed', 'failed', 'interrupted'].includes(workflow.value.status)
    void refreshWorkflow()
    if (terminal) stopWorkflowPolling()
  }, 2000)
}

async function refreshWorkflowDiagnostics() {
  if (demoMode.value) {
    workflowBackend.value = { ok: true, backend: 'demo', langgraph_installed: false,
      checkpoint_backend: 'demo', persistent_checkpoint: false,
      checkpoint_health: { healthy: true, state: 'demo', schema_version: null } }
    langsmith.value = { ok: true, installed: false, key_configured: false, enabled: false, realtime: false, project: 'demo' }
    workflowEvaluation.value = null
    retrievalEvents.value = []
    retrievalRuntime.value = { backend: 'demo', collection: 'demo', top_k: 5,
      bm25: true, reranker: 'demo', lexical_persistence: true,
      lexical_index: { state: 'ready', documents: 0 } }
    return
  }
  try {
    const [backend, smith, retrieval] = await Promise.all([
      agentApi.workflowBackend(), agentApi.langsmith(), agentApi.retrievalStatus(),
    ])
    workflowBackend.value = backend
    langsmith.value = smith
    retrievalRuntime.value = retrieval.retrieval || null
    retrievalEvents.value = retrieval.retrieval?.recent_events || []
    if (workflow.value?.workflow_id && !demoMode.value) {
      const r = await agentApi.workflowEvaluation(workflow.value.workflow_id)
      if (r.ok && r.evaluation) workflowEvaluation.value = r.evaluation
    }
  } catch (e) {
    actionMsg.value = (e as Error).message || '工作流状态获取失败'
  }
}

async function refreshWorkflow() {
  if (!workflow.value?.workflow_id || demoMode.value) { void refreshWorkflowDiagnostics(); return }
  const [r, e] = await Promise.all([
    agentApi.workflow(workflow.value.workflow_id),
    agentApi.workflowEvaluation(workflow.value.workflow_id),
  ])
  if (r.ok && r.workflow) workflow.value = r.workflow
  if (e.ok && e.evaluation) workflowEvaluation.value = e.evaluation
  void refreshWorkflowDiagnostics()
}
async function startWorkflow() {
  const prompt = workflowPrompt.value.trim()
  if (!prompt) { actionMsg.value = '请先描述想做的游戏'; return }
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowStart(prompt, { use_llm: true, web_enabled: true })
    if (r.ok && r.workflow) { workflow.value = r.workflow; workflowEvaluation.value = null; workflowPrompt.value = ''; workflowCustomPending.value = false; actionMsg.value = '已生成方案，请选择下一步'; void refreshWorkflowDiagnostics() }
    else actionMsg.value = r.error || '工作流启动失败'
  } catch (e) { actionMsg.value = (e as Error).message || '工作流启动失败' }
  finally { workflowBusy.value = false }
}
async function workflowChoice(choice: string) {
  if (!workflow.value || workflowBusy.value) return
  if (choice === 'custom') {
    workflowCustomPending.value = true
    workflowCustom.value = ''
    return
  }
  workflowCustomPending.value = false
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowChoice(workflow.value.workflow_id, choice, workflowPrompt.value)
    if (r.ok && r.workflow) { workflow.value = r.workflow; workflowPrompt.value = ''; void refreshWorkflowDiagnostics() }
    else actionMsg.value = r.error || '方案选择失败'
  } catch (e) {
    actionMsg.value = (e as Error).message || '方案选择失败'
  } finally { workflowBusy.value = false }
}
async function submitResearch() {
  if (!workflow.value || !workflowResearch.value.trim()) return
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowResearch(workflow.value.workflow_id, workflowResearch.value)
    if (r.ok && r.workflow) { workflow.value = r.workflow; workflowResearch.value = ''; void refreshWorkflowDiagnostics() }
    else actionMsg.value = r.error || '检索结果提交失败'
  } finally { workflowBusy.value = false }
}
async function runResearch() {
  if (!workflow.value) return
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowResearchRun(workflow.value.workflow_id, workflow.value.request || workflowPrompt.value)
    if (r.ok && r.workflow) { workflow.value = r.workflow; void refreshWorkflowDiagnostics() }
    else actionMsg.value = r.error || '联网检索失败'
  } finally { workflowBusy.value = false }
}
async function workflowAction(action: 'plan' | 'approve' | 'execute' | 'interrupt' | 'resume') {
  if (!workflow.value) return
  workflowBusy.value = true
  try {
    const id = workflow.value.workflow_id
    const r = action === 'plan' ? await agentApi.workflowPlan(id)
      : action === 'approve' ? await agentApi.workflowApprove(id, true)
      : action === 'execute' ? await agentApi.workflowExecute(id, getSessionId())
      : action === 'interrupt' ? await agentApi.workflowInterrupt(id)
      : await agentApi.workflowResume(id)
    if (r.ok && r.workflow) { workflow.value = r.workflow; void refreshWorkflowDiagnostics() }
    else actionMsg.value = r.error || '工作流操作失败'
  } finally { workflowBusy.value = false }
}
async function submitCustomWorkflow() {
  if (!workflow.value || !workflowCustom.value.trim()) {
    actionMsg.value = '请先补充自定义目标'
    return
  }
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowChoice(workflow.value.workflow_id, 'custom', workflowCustom.value.trim())
    if (r.ok && r.workflow) {
      workflow.value = r.workflow
      workflowCustom.value = ''
      workflowCustomPending.value = false
      void refreshWorkflowDiagnostics()
    } else actionMsg.value = r.error || '自定义方案提交失败'
  } finally { workflowBusy.value = false }
}
async function approveRevision(approved: boolean) {
  if (!workflow.value) return
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowReviseApprove(workflow.value.workflow_id, approved)
    if (r.ok && r.workflow) workflow.value = r.workflow
    else actionMsg.value = r.error || '任务 DAG 审核失败'
  } finally { workflowBusy.value = false }
}

function openTaskRevision() {
  const tasks = workflow.value?.tasks || []
  const first = tasks.find(task => {
    const status = String(workflow.value?.results?.[String(task.id || '')]?.status || 'pending')
    return !['ok', 'running'].includes(status)
  }) || tasks[0]
  revisionTaskId.value = String(first?.id || '')
  revisionTaskText.value = String(first?.task || '')
  const deps = first?.depends_on
  revisionTaskDeps.value = Array.isArray(deps) ? deps.map(item => String(item)).join(', ') : String(deps || '')
  revisionOpen.value = true
}

function syncRevisionTask() {
  const task = (workflow.value?.tasks || []).find(item => String(item.id || '') === revisionTaskId.value)
  if (!task) return
  revisionTaskText.value = String(task.task || '')
  const deps = task.depends_on
  revisionTaskDeps.value = Array.isArray(deps) ? deps.map(item => String(item)).join(', ') : String(deps || '')
}

async function submitTaskRevision() {
  if (!workflow.value || !revisionTaskId.value.trim() || !revisionTaskText.value.trim()) return
  const id = revisionTaskId.value.trim()
  const result = workflow.value.results?.[id]
  if (result && ['ok', 'running'].includes(String(result.status || ''))) {
    actionMsg.value = '已经执行的任务不能直接改写，请修改尚未执行的任务。'
    return
  }
  const tasks = (workflow.value.tasks || []).map(task => {
    if (String(task.id || '') !== id) return { ...task }
    const deps = revisionTaskDeps.value.split(',').map(item => item.trim()).filter(Boolean)
    return { ...task, task: revisionTaskText.value.trim(), depends_on: deps }
  })
  workflowBusy.value = true
  try {
    const r = await agentApi.workflowRevise(workflow.value.workflow_id, tasks)
    if (r.ok && r.workflow) {
      workflow.value = r.workflow
      revisionOpen.value = false
      actionMsg.value = '任务修改已提交，等待审核后才会生效。'
    } else actionMsg.value = r.error || '任务修改未提交'
  } finally { workflowBusy.value = false }
}

async function saveLimit() {
  actionMsg.value = ''
  const v = Number(limitInput.value)
  if (!Number.isFinite(v) || v < 0) { actionMsg.value = '请输入不小于 0 的数字（0 = 不限预算）'; return }
  if (demoMode.value) { actionMsg.value = '演示模式不能修改预算'; return }
  const r = await harnessApi.setBudgetLimit(v)
  if (r.ok && r.check) { budgetCheck.value = r.check; actionMsg.value = '预算已保存' }
  else actionMsg.value = r.error || '保存失败'
  if (r.ok) void loadAll()
}
async function resetSpent() {
  if (demoMode.value) { actionMsg.value = '演示模式不能清零'; return }
  if (!window.confirm('确定把累计花费计数清零吗？（不影响模型与账本文件，只重置计数）')) return
  const r = await harnessApi.resetBudget()
  if (r.ok) actionMsg.value = '累计计数已清零'
  void loadAll()
}
async function removeSession(id: string) {
  if (demoMode.value) {
    sessions.value = sessions.value.filter((x) => x.session_id !== id)
    actionMsg.value = '演示数据已移除（刷新后恢复）'
    return
  }
  if (!window.confirm(`删除会话「${id}」的历史记录？此操作不可恢复。`)) return
  const r = await harnessApi.deleteSession(id)
  if (!r.ok) return
  sessions.value = sessions.value.filter((x) => x.session_id !== id)
  // 删掉的正是当前会话：自动切到一段全新会话并让助手刷新（否则会停在已删除的空 id 上）。
  if (id === currentId.value) {
    currentId.value = await startNewSession()
    open.value = false
    window.dispatchEvent(new CustomEvent('docmind:focus-chat', { detail: { reload: true } }))
  }
}
/** 继续某段历史对话：把该 session_id 切为当前会话并让 AI 助手回灌其历史。 */
async function continueSession(id: string) {
  if (demoMode.value) { actionMsg.value = '演示模式不能续聊'; return }
  if (!await setSessionId(id)) { actionMsg.value = '该会话正在另一个页面使用，请先关闭那个页面。'; return }
  currentId.value = id
  open.value = false
  window.dispatchEvent(new CustomEvent('docmind:focus-chat', { detail: { reload: true } }))
}
/** 开一段全新对话：切到全新 session id（新 id 无历史），让 AI 助手显示空态。 */
async function newSession() {
  if (demoMode.value) { actionMsg.value = '演示模式不能新建会话'; return }
  currentId.value = await startNewSession()
  open.value = false
  window.dispatchEvent(new CustomEvent('docmind:focus-chat', { detail: { reload: true } }))
}
async function clearTraces() {
  if (demoMode.value) { traces.value = []; actionMsg.value = '演示记录已清空（刷新后恢复）'; return }
  if (!window.confirm('清空全部操作记录（trace 账本）？')) return
  await harnessApi.clearTrace()
  void loadAll()
}
async function reloadSkills() {
  if (demoMode.value) { actionMsg.value = '演示模式无需重载'; return }
  const r = await harnessApi.reloadSkills()
  actionMsg.value = r.ok ? `已重载，发现 ${r.count ?? 0} 个技能` : '重载失败'
  void loadAll()
}
async function rollbackSkill(skill: SkillInfo) {
  if (demoMode.value) { actionMsg.value = '演示模式不能回滚技能'; return }
  if (skill.source !== 'user') return
  if (!window.confirm(`回滚技能「${skill.name}」到上一版本？`)) return
  const result = await harnessApi.rollbackSkill(skill.name)
  actionMsg.value = result.approval_required ? '回滚需要审批' : result.ok ? (result.restored_previous ? '已恢复上一版本' : '已移除该技能') : (result.error || '回滚失败')
  if (result.ok) void loadAll()
}
async function reloadHooks() {
  if (demoMode.value) { actionMsg.value = '演示模式无需重载'; return }
  const r = await harnessApi.reloadHooks()
  actionMsg.value = r.ok ? '钩子已重载' : '重载失败'
  void loadAll()
}
async function saveHookBreakpoint() {
  try {
    const r = await harnessApi.setHookBreakpoint({
      kind: breakpointKind.value, enabled: true, block: breakpointBlock.value,
      match: breakpointMatch.value, reason: breakpointReason.value,
    })
    actionMsg.value = r.ok ? `已保存断点：${breakpointKind.value}` : (r.error || '断点保存失败')
    if (r.ok) void loadAll()
  } catch (e) { actionMsg.value = (e as Error).message || '断点保存失败' }
}
async function removeHookBreakpoint(kind: string) {
  try {
    const r = await harnessApi.removeHookBreakpoint(kind)
    actionMsg.value = r.ok ? `已移除断点：${kind}` : (r.error || '断点移除失败')
    if (r.ok) void loadAll()
  } catch (e) { actionMsg.value = (e as Error).message || '断点移除失败' }
}

onBeforeUnmount(() => { stopWorkflowPolling(); open.value = false })
</script>

<template>
  <div class="hp">
    <button class="hp-trigger" :title="'AI 运行台：花费 / 历史对话 / 每步操作记录 / 技能扩展'" @click="open = !open">
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none">
        <path d="M1 6.5 H3.2 L4.6 2 L6.8 11 L8.2 6.5 H12" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span class="hp-label">AI 运行台</span>
    </button>

    <template v-if="open">
      <Teleport to="body">
        <div class="hp-backdrop" @click="open = false" />
        <div class="hp-pop" role="dialog" aria-label="AI 运行台" :aria-busy="workflowBusy">
        <div class="hp-head">
          <div>
            <b>AI 运行台</b>
            <p>看 AI 每次回答花了多少、操作了什么、装了哪些扩展</p>
          </div>
          <button class="hp-x" title="关闭" @click="open = false">×</button>
        </div>

        <div v-if="demoMode" class="hp-demo">
          示例演示模式：以下是演示数据。在本地启动 DocMind 桌面端后，这里会显示你的真实记录。
        </div>
        <p v-if="error" class="hp-err">{{ error }} <button class="hp-link" @click="loadAll">重试</button></p>
        <p v-if="actionMsg" class="hp-actionmsg">{{ actionMsg }}</p>

        <div class="hp-tabs">
          <button v-for="t in TABS" :key="t.key" :class="{ active: tab === t.key }" @click="switchTab(t.key)">{{ t.label }}</button>
        </div>

        <!-- ---------------- 花费 ---------------- -->
        <div v-if="tab === 'cost'" class="hp-pane">
          <div class="hp-statgrid">
            <div class="hp-stat">
              <span>今日 AI 花费</span>
              <b>{{ money(budget?.day_spent) }}</b>
              <em>{{ budget?.day || '' }}</em>
            </div>
            <div class="hp-stat">
              <span>累计花费</span>
              <b>{{ money(budget?.global_spent) }}</b>
              <em>按模型官方单价折算</em>
            </div>
            <div class="hp-stat">
              <span>预算上限</span>
              <b v-if="(budget?.global_limit || 0) > 0">{{ money(budget?.global_limit) }}</b>
              <b v-else class="hp-none">不限</b>
              <em>超过预算 AI 会自动停下</em>
            </div>
          </div>
          <p class="hp-plain">
            本地模型（Ollama / llama.cpp）在你自己的显卡上运行，花费永远为 ¥0；
            使用云端 API 时，这里按官方单价累计费用，防止 AI 跑超支。
          </p>
          <div class="hp-row">
            <input v-model="limitInput" placeholder="输入累计预算上限（元），0 表示不限" />
            <button class="hp-btn primary" @click="saveLimit">保存预算</button>
            <button class="hp-btn" @click="resetSpent">计数清零</button>
          </div>
          <div v-if="budget && (budget.per_minute_calls_limit > 0 || budget.per_minute_cost_limit > 0)" class="hp-rateline">
            每分钟限流：{{ budget.per_minute_calls_limit || '不限' }} 次
            <template v-if="budget.per_minute_cost_limit > 0"> / {{ money(budget.per_minute_cost_limit) }}</template>
          </div>
        </div>

        <!-- ---------------- 对话 ---------------- -->
        <div v-else-if="tab === 'sessions'" class="hp-pane">
          <div class="hp-sesshead">
            <p class="hp-plain">每段对话都会单独保存，互不串台。删除后该段问答历史不可恢复。</p>
            <button class="hp-btn sm" title="开始一段全新对话（当前会话保留在列表里，可随时「继续」）" @click="newSession">+ 新会话</button>
          </div>
          <div v-if="!sessions.length" class="hp-empty">还没有保存的对话。到下方「AI 助手」问一个问题试试。</div>
          <div v-else class="hp-list">
            <div v-for="s in sessions" :key="s.session_id" class="hp-item" :class="{ on: s.session_id === currentId }">
              <div class="hp-item-main">
                <b>{{ s.session_id }}<span v-if="s.session_id === currentId" class="hp-cur">当前</span></b>
                <span>{{ s.turns }} 轮问答<template v-if="s.has_summary"> · 已生成早期摘要</template></span>
                <em>{{ ago(s.updated_at) }}（{{ fmtTime(s.updated_at) }}）</em>
              </div>
              <button class="hp-btn sm" title="切到这段对话并载入其历史" @click="continueSession(s.session_id)">继续</button>
              <button class="hp-btn danger sm" title="删除这段对话" @click="removeSession(s.session_id)">删除</button>
            </div>
          </div>
        </div>

        <!-- ---------------- 轨迹 ---------------- -->
        <div v-else-if="tab === 'trace'" class="hp-pane">
          <div v-if="traceSummary" class="hp-sumline">
            共 <b>{{ traceSummary.turns }}</b> 轮回答 ·
            累计 <b>{{ traceSummary.total_tokens.toLocaleString() }}</b> token ·
            平均每轮 <b>{{ fmtMs(traceSummary.avg_elapsed_ms) }}</b> ·
            错误 <b :class="{ bad: traceSummary.errors > 0 }">{{ traceSummary.errors }}</b> ·
            中止 <b>{{ traceSummary.aborted }}</b>
          </div>
          <p class="hp-plain">AI 每轮回答调用了哪些工具、用了多少 token、结果如何，都记在这里（不记录对话正文，只记操作元数据）。</p>
          <div v-if="!traces.length" class="hp-empty">暂无操作记录。</div>
          <div v-else class="hp-list">
            <div v-for="it in traces" :key="it.turn_id" class="hp-trace">
              <div class="hp-trace-head">
                <span class="hp-time">{{ fmtTime(it.ts) }}</span>
                <span class="hp-model">{{ providerLabel(it.provider) }}<template v-if="it.model"> · {{ it.model }}</template></span>
                <span :class="['hp-tag', outcomeTag(it).cls]">{{ outcomeTag(it).text }}</span>
                <span class="hp-spacer" />
                <span class="hp-nums">{{ it.total_tokens.toLocaleString() }} token · {{ money(it.cost_cny) }} · {{ fmtMs(it.elapsed_ms) }}</span>
              </div>
              <div v-if="toolNames(it).length" class="hp-tools">
                <i v-for="(a, i) in toolNames(it)" :key="i">{{ a }}</i>
              </div>
              <div v-else-if="it.error" class="hp-traceerr">{{ fmtTraceError(it.error) }}</div>
            </div>
          </div>
          <div class="hp-footrow">
            <a class="hp-linkline" href="/trace.html" target="_blank" rel="noreferrer">打开完整轨迹页 ↗</a>
            <button class="hp-btn sm" title="把每轮问答画成「提问→思考→调工具→回答」节点流水线" @click="open = false; openFlow()">流程图查看</button>
            <button class="hp-btn sm" @click="clearTraces">清空记录</button>
          </div>
        </div>

        <!-- ---------------- 技能与扩展 ---------------- -->
        <div v-else-if="tab === 'skills'" class="hp-pane">
          <div class="hp-subhead">
            <b>技能</b>
            <button class="hp-btn sm" @click="reloadSkills">重新扫描</button>
          </div>
          <p class="hp-plain">技能是写给 AI 的「专项操作手册」：遇到对应任务时它会先读手册再动手。</p>
          <div v-if="!skills.length" class="hp-empty">
            还没有安装技能。把 SKILL.md 放进 .docmind/skills/ 目录后点「重新扫描」即可。
          </div>
          <div v-else class="hp-list">
            <div v-for="sk in skills" :key="sk.path" class="hp-skill">
              <b>{{ sk.name }}</b>
              <span>{{ sk.description }}</span>
              <em>适用：{{ sk.when_to_use }}</em>
              <small>版本 {{ sk.version || '—' }} · 使用 {{ sk.stats?.uses || 0 }} 次 · 成功 {{ sk.stats?.successes || 0 }} · 失败 {{ sk.stats?.failures || 0 }}</small>
              <small v-if="sk.history_versions?.length" class="hp-skill-history">历史版本：{{ skillHistoryText(sk) }}</small>
              <button v-if="sk.source === 'user'" class="hp-btn sm hp-skill-rollback" @click="rollbackSkill(sk)">回滚</button>
            </div>
          </div>

          <div class="hp-subhead" style="margin-top:14px">
            <b>自动钩子（高级）</b>
            <button class="hp-btn sm" @click="reloadHooks">重新扫描</button>
          </div>
          <p class="hp-plain">
            钩子是在「AI 每次调工具 / 每轮回答」前后自动运行的 Python 脚本，
            当前已启用 <b>{{ hooksTotal }}</b> 个
            （调工具前 {{ hooksCounts.pre_tool || 0 }} · 调工具后 {{ hooksCounts.post_tool || 0 }} ·
            每轮前 {{ hooksCounts.pre_turn || 0 }} · 每轮后 {{ hooksCounts.post_turn || 0 }}）。
            钩子与本服务同权限运行，只应放置你自己信任的脚本。
          </p>
          <div class="hp-hook-breakpoint">
            <div class="hp-subhead"><b>安全断点</b><small>仅允许预定义生命周期，不执行用户输入的代码</small></div>
            <div class="hp-row">
              <select v-model="breakpointKind">
                <option v-for="kind in hookKinds" :key="kind" :value="kind">{{ kind }}</option>
              </select>
              <input v-model="breakpointMatch" placeholder="匹配内容（可留空，匹配全部）" />
            </div>
            <div class="hp-row">
              <input v-model="breakpointReason" placeholder="暂停时展示给用户的原因" />
              <label class="hp-check"><input v-model="breakpointBlock" type="checkbox" /> 命中即暂停</label>
              <button class="hp-btn primary" @click="saveHookBreakpoint">保存断点</button>
            </div>
            <div v-if="Object.keys(hookBreakpoints).length" class="hp-hook-list">
              <div v-for="(item, kind) in hookBreakpoints" :key="kind" class="hp-hook-item">
                <span><b>{{ kind }}</b> · {{ item.match || '全部' }} · {{ item.block ? '阻断' : '仅记录' }}</span>
                <button class="hp-btn sm danger" @click="removeHookBreakpoint(String(kind))">移除</button>
              </div>
            </div>
          </div>
        </div>

        <!-- ---------------- 游戏开发工作流 ---------------- -->
        <div v-else-if="tab === 'workflow'" class="hp-pane">
          <p class="hp-plain">从自然语言目标开始，查看方案、检索、子代理分工、审批和复核结果。任何文件修改仍需经过后端安全边界。</p>
          <div v-if="workflowBusy" class="hp-wf-busy" role="status">
            <span class="hp-wf-spinner" aria-hidden="true" />
            正在生成方案，首次连接模型可能需要一点时间…
          </div>
          <div class="hp-row">
            <input v-model="workflowPrompt" placeholder="例如：做一个带移动和跳跃的 2D 原型" @keyup.enter="startWorkflow" />
            <button class="hp-btn primary" :disabled="workflowBusy" @click="startWorkflow">开始</button>
          </div>
          <div v-if="workflow" class="hp-workflow">
            <div class="hp-wf-head">
              <div><b>{{ workflow.workflow_id }}</b><span class="hp-wf-status">{{ workflowStatusLabel() }}</span></div>
              <button class="hp-btn sm" :disabled="workflowBusy" @click="refreshWorkflow">刷新</button>
            </div>
            <div class="hp-wf-livebar">
              <div class="hp-wf-stage-strip">
                <div v-for="(stage, i) in WORKFLOW_STAGES" :key="stage.key" :class="['hp-wf-stage', workflowStageClass(i)]">
                  <span class="hp-wf-stage-dot">{{ i < workflowStageIndex() ? '✓' : i + 1 }}</span>
                  <b>{{ stage.label }}</b>
                </div>
              </div>
              <div class="hp-wf-live-meta">
                <span class="hp-wf-live-dot" :class="workflowStageClass(workflowStageIndex())" />
                <span>{{ workflowEventLabel() }}</span>
                <em>{{ workflowCompletedTasks }}/{{ workflowTaskCount }} 个任务完成 · {{ workflow.steps || 0 }} 步 · 重规划 {{ workflow.replans || 0 }}</em>
              </div>
            </div>
            <div class="hp-wf-diagnostics">
              <div><span>运行后端</span><b>{{ workflowBackend?.backend || '未读取' }}<small v-if="workflowBackend?.langgraph_installed"> · LangGraph</small></b></div>
              <div><span>检查点</span><b :class="{ bad: workflowBackend?.checkpoint_health && !workflowBackend.checkpoint_health.healthy }">{{ checkpointText() }}</b></div>
              <div><span>LangSmith</span><b>{{ langsmithText() }}</b><small v-if="traceId() !== '—'">根轨迹 {{ traceId() }} · 事件 {{ traceEventCount() }}</small><small v-for="(failure, i) in traceExportErrors().slice(-2)" :key="i" class="hp-wf-trace-error">{{ failure.name || '导出' }} {{ failure.error || '失败' }}</small></div>
              <div><span>质量评估</span><b :class="{ good: workflowEvaluation?.passed, bad: workflowEvaluation && !workflowEvaluation.passed }">{{ scoreText() }}</b></div>
              <div><span>五层上下文</span><b>{{ contextCompressionText() }}</b><small>route · project · task · subagent · output</small></div>
              <div><span>生产检索</span><b :class="{ bad: retrievalRuntime && retrievalRuntime.lexical_persistence === false }">{{ retrievalRuntime?.backend || '未读取' }} · {{ retrievalRuntime?.collection || '—' }}</b><small>BM25 {{ retrievalRuntime?.bm25 ? '已启用' : '未启用' }} · 词法索引 {{ retrievalRuntime?.lexical_persistence ? '持久化' : '快照' }}<template v-if="retrievalRuntime?.reranker"> · 重排 {{ retrievalRuntime.reranker }}</template></small></div>
              <div><span>运行遥测</span><b>{{ fmtMs(Number(workflow.observability?.duration_ms || 0)) }} · {{ Number(workflow.observability?.total_tokens || 0).toLocaleString() }} token</b><small>工具 {{ workflow.observability?.tool_events || 0 }} · MCP {{ workflow.observability?.mcp_events || 0 }} · 失败 {{ workflow.observability?.failures || 0 }}</small></div>
            </div>
            <div v-if="retrievalEvents.length" class="hp-wf-retrieval">
              <div class="hp-wf-retrieval-head"><b>最近检索</b><small>本地诊断，不上传正文</small></div>
              <details v-for="(event, i) in retrievalEvents.slice(0, 6)" :key="`${event.ts || 0}-${i}`" class="hp-wf-retrieval-item">
                <summary>
                  <span>{{ retrievalTime(event.ts) }} · {{ event.mode || 'retrieval' }}</span>
                  <span>{{ event.duration_ms || 0 }}ms · {{ event.returned || 0 }} 条</span>
                </summary>
                <div class="hp-wf-retrieval-query">{{ event.query || '—' }}</div>
                <div v-for="(doc, j) in (event.documents || []).slice(0, 5)" :key="j" class="hp-wf-retrieval-doc">
                  <span><b>{{ j + 1 }}</b> {{ doc.source || doc.id || '未命名来源' }}<small v-if="doc.start_line">:L{{ doc.start_line }}</small></span>
                  <em>BM25 {{ retrievalScore(doc.bm25_score) }} · Hybrid {{ retrievalScore(doc.hybrid_score) }}<template v-if="doc.rerank_score !== undefined"> · 重排 {{ retrievalScore(doc.rerank_score) }}</template></em>
                  <p>{{ doc.snippet }}</p>
                </div>
              </details>
            </div>
            <div v-if="workflow.interrupt_reason" class="hp-wf-alert">{{ workflow.interrupt_reason }}</div>
            <div v-if="workflow.pending_tasks?.length" class="hp-wf-revision">
              <b>待审核的任务 DAG 修改</b>
              <span v-for="task in workflow.pending_tasks" :key="String(task.id)">{{ task.id }} · {{ task.task }}</span>
              <div class="hp-wf-actions">
                <button class="hp-btn primary" :disabled="workflowBusy" @click="approveRevision(true)">批准重排</button>
                <button class="hp-btn danger" :disabled="workflowBusy" @click="approveRevision(false)">拒绝修改</button>
              </div>
            </div>
            <div v-if="workflow.status === 'awaiting_choice'" class="hp-wf-options">
              <button v-for="option in (workflow.options || [])" :key="option.id" class="hp-wf-option" :disabled="workflowBusy" @click="workflowChoice(option.id)">
                <b>{{ option.title }}<em v-if="option.recommended">推荐</em></b><span>{{ option.summary }}</span>
              </button>
            </div>
            <div v-if="workflow.status === 'awaiting_choice' && workflowCustomPending" class="hp-row hp-wf-custom">
              <input v-model="workflowCustom" placeholder="描述你真正想要的玩法、限制或参考效果" @keyup.enter="submitCustomWorkflow" />
              <button class="hp-btn primary" :disabled="workflowBusy" @click="submitCustomWorkflow">提交自定义目标</button>
            </div>
            <div v-if="workflow.status === 'awaiting_research'" class="hp-row">
              <input v-model="workflowResearch" placeholder="粘贴联网检索摘要或来源" />
              <button class="hp-btn" :disabled="workflowBusy" @click="runResearch">自动检索</button>
              <button class="hp-btn primary" :disabled="workflowBusy" @click="submitResearch">提交检索</button>
            </div>
            <div class="hp-wf-actions">
              <button v-if="workflow.status === 'planning'" class="hp-btn" :disabled="workflowBusy" @click="workflowAction('plan')">生成分工</button>
              <button v-if="workflow.status === 'awaiting_approval'" class="hp-btn primary" :disabled="workflowBusy" @click="workflowAction('approve')">批准执行</button>
              <button v-if="workflow.status === 'planned'" class="hp-btn primary" :disabled="workflowBusy" @click="workflowAction('execute')">执行任务</button>
              <button v-if="['executing', 'planned'].includes(workflow.status)" class="hp-btn danger" :disabled="workflowBusy" @click="workflowAction('interrupt')">中断</button>
              <button v-if="workflow.status === 'interrupted'" class="hp-btn" :disabled="workflowBusy" @click="workflowAction('resume')">恢复</button>
              <button v-if="['planned', 'executing', 'interrupted'].includes(workflow.status) && workflow.tasks?.length" class="hp-btn" :disabled="workflowBusy" @click="openTaskRevision">修改未执行任务</button>
            </div>
            <div v-if="revisionOpen" class="hp-wf-revision hp-wf-edit">
              <b>调整一个尚未执行的任务</b>
              <div class="hp-row">
                <select v-model="revisionTaskId" @change="syncRevisionTask">
                  <option disabled value="">选择任务</option>
                  <option v-for="task in (workflow.tasks || [])" :key="String(task.id)" :value="String(task.id)">{{ task.id }} · {{ task.task }}</option>
                </select>
                <input v-model="revisionTaskText" placeholder="新的任务目标与验收标准" />
              </div>
              <div class="hp-row">
                <input v-model="revisionTaskDeps" placeholder="依赖任务 ID，用逗号分隔；没有可留空" />
                <button class="hp-btn primary" :disabled="workflowBusy" @click="submitTaskRevision">提交修改</button>
                <button class="hp-btn" :disabled="workflowBusy" @click="revisionOpen = false">取消</button>
              </div>
              <small>修改只会生成待审核版本，不会直接覆盖正在执行的任务。</small>
            </div>
            <div v-if="workflow.tasks?.length" class="hp-wf-tasks">
              <div v-for="task in workflow.tasks" :key="String(task.id)" class="hp-wf-task">
                <b>{{ task.id }}</b>
                <span>{{ task.role }} · {{ task.task }}
                  <small v-if="task.persona || task.mcp">{{ task.persona || '默认人设' }} · MCP {{ task.mcp || 'auto' }}</small>
                </span>
                <em :class="taskStatusClass(task)">{{ taskStatus(task) }}</em>
              </div>
            </div>
            <div v-if="workflow.subagents?.length" class="hp-wf-team">
              <div class="hp-wf-team-head">
                <b>团队执行</b>
                <small>{{ workflow.subagents.length }} 位成员 · 主 Agent 负责汇总与终审，成员数量由规划结果决定</small>
              </div>
              <div class="hp-wf-supervisor">
                <span class="hp-wf-avatar supervisor">总</span>
                <span class="hp-wf-member-main"><b>交付总监</b><small>{{ workflowEventLabel() }}</small></span>
                <em class="hp-wf-member-status" :class="subagentStatusClass(workflow.status === 'completed' ? 'ok' : workflow.status === 'failed' ? 'failed' : 'running')">{{ workflowStatusLabel() }}</em>
              </div>
              <div class="hp-wf-subagents">
                <div v-for="agent in workflow.subagents" :key="String(agent.id)" class="hp-wf-subagent" :class="[`state-${subagentStatusClass(agent.status)}`, { selected: String(agent.id) === String(selectedSubagent?.id) }]" role="button" tabindex="0" @click="selectSubagent(agent.id)" @keydown.enter="selectSubagent(agent.id)">
                  <span class="hp-wf-avatar">{{ subagentMeta(agent).avatar }}</span>
                  <span class="hp-wf-member-main">
                    <b>{{ subagentMeta(agent).label }}</b><small>{{ agent.id }} · {{ agent.persona || '默认人设' }}</small>
                    <span class="hp-wf-member-task">{{ subagentTask(agent) }}</span>
                    <small>工具 {{ (agent.tools || []).join(', ') || '角色默认' }} · MCP {{ agent.mcp || 'auto' }} · {{ subagentReflectionLabel(agent) }}</small>
                  </span>
                  <span class="hp-wf-member-side"><em class="hp-wf-member-status" :class="subagentStatusClass(agent.status)">{{ subagentStatusLabel(agent.status) }}</em><small v-if="agent.elapsed_ms">{{ fmtMs(Number(agent.elapsed_ms)) }}</small><small v-if="agent.error" class="bad">{{ agent.error }}</small></span>
                </div>
              </div>
              <div v-if="selectedSubagent" class="hp-wf-agent-detail">
                <div class="hp-wf-agent-detail-head">
                  <div>
                    <b>{{ subagentMeta(selectedSubagent).label }} · {{ selectedSubagent.id }}</b>
                    <small>{{ subagentStatusLabel(selectedSubagent.status) }} · {{ selectedSubagent.task_thread || '任务线程未返回' }}</small>
                  </div>
                  <em class="hp-wf-member-status" :class="subagentStatusClass(selectedSubagent.status)">{{ subagentReflectionLabel(selectedSubagent) }}</em>
                </div>
                <p class="hp-wf-agent-task">{{ subagentTask(selectedSubagent) }}</p>
                <div class="hp-wf-agent-meta">人设：{{ selectedSubagent.persona || '默认人设' }} · 工具：{{ (selectedSubagent.tools || []).join(', ') || '角色默认' }} · MCP：{{ selectedSubagent.mcp || 'auto' }}</div>
                <div class="hp-wf-agent-result">{{ selectedSubagentConclusion() }}</div>
                <div v-if="selectedSubagentEvents.length" class="hp-wf-agent-events">
                  <div v-for="(event, i) in selectedSubagentEvents" :key="`${String(event.ts || '')}-${i}`">
                    <b>{{ String(event.kind || '事件') }}</b><small>{{ workflowEventDetail(event) || '无附加信息' }}</small>
                  </div>
                </div>
                <div v-if="selectedSubagentTrace.length" class="hp-wf-agent-tools">
                  <div v-for="row in selectedSubagentTrace" :key="`${row.task}-${row.index}-${row.action}`"><b>{{ row.index }}. {{ row.action }}</b><small>{{ row.observation || '无观察结果' }}</small></div>
                </div>
              </div>
            </div>
            <div v-if="workflowTraceRows.length" class="hp-wf-trace">
              <div class="hp-wf-trace-head"><b>工具、MCP 与 Hook 轨迹</b><small>{{ workflowTraceRows.length }} 条（最多保留最近 80 条）</small></div>
              <details v-for="(row, i) in workflowTraceRows" :key="`${row.task}-${row.index}-${i}`" class="hp-wf-trace-item">
                <summary>
                  <span><b>{{ row.kind }}</b> {{ row.task }} · 第 {{ row.index }} 步</span>
                  <em :class="{ bad: row.ok === false }">{{ row.action }}</em>
                </summary>
                <p>{{ row.observation || '没有返回可展示的观察结果' }}</p>
                <small v-if="row.tokens || row.elapsed" class="hp-wf-trace-meta">token 输入/输出 {{ row.tokens || '—' }} · 耗时 {{ row.elapsed || '—' }}</small>
              </details>
            </div>
            <div v-if="workflow.events?.length" class="hp-wf-events">
              <details v-for="(event, i) in workflow.events.slice(-20).reverse()" :key="i" class="hp-wf-event-detail">
                <summary>{{ event.kind || '事件' }}</summary>
                <small>{{ workflowEventDetail(event) || '无附加字段' }}</small>
              </details>
            </div>
            <div v-if="workflow.review" class="hp-wf-review">复核：{{ workflow.review.ok ? '通过' : '未通过' }} · 步数 {{ workflow.steps || 0 }} · 重规划 {{ workflow.replans || 0 }}</div>
            <div v-if="reviewFailures().length" class="hp-wf-failures">
              <div v-for="(failure, i) in reviewFailures()" :key="i"><b>{{ failure.kind || '审核' }}</b><span>{{ failure.message }}</span><em>{{ failure.recovery }}</em></div>
            </div>
            <div v-if="workflowEvaluation?.checks?.length" class="hp-wf-checks">
              <span v-for="check in workflowEvaluation.checks" :key="String(check.name)"><i :class="{ bad: !check.ok }">{{ check.ok ? '✓' : '!' }}</i>{{ check.name }}</span>
            </div>
          </div>
          <div v-else class="hp-empty">还没有运行中的游戏工作流。</div>
        </div>
        </div>
      </Teleport>
    </template>
  </div>
</template>

<style scoped>
.hp { position: relative; }
.hp-trigger {
  display: inline-flex; align-items: center; gap: 6px;
  height: 28px; padding: 0 11px;
  border: 1px solid #c8dcfa;
  background: linear-gradient(180deg, #f3f8ff, #eaf1fe);
  color: #2f6fed;
  border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer;
  white-space: nowrap;
}
.hp-trigger:hover { border-color: #2f6fed; filter: brightness(1.02); }
.hp-backdrop { position: fixed; inset: 0; z-index: 40; }
.hp-pop {
  position: fixed; right: 16px; top: 54px; z-index: 1001;
  width: 480px; max-width: calc(100vw - 32px);
  max-height: calc(100vh - 70px); overflow: auto;
  background: #fff; border: 1px solid #dde3ee; border-radius: 14px;
  box-shadow: 0 18px 50px rgba(35, 52, 84, .22);
  padding: 15px 16px 14px;
}
.hp-wf-busy { display: flex; align-items: center; gap: 7px; margin-top: 9px; padding: 8px 10px; border: 1px solid #c8dcfa; border-radius: 8px; background: #f2f7ff; color: #3767aa; line-height: 1.5; }
.hp-wf-spinner { width: 12px; height: 12px; flex: 0 0 12px; border: 2px solid #b8cdf1; border-top-color: #2f6fed; border-radius: 50%; animation: hp-spin .75s linear infinite; }
@keyframes hp-spin { to { transform: rotate(360deg); } }
.hp-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.hp-head b { font-size: 14.5px; color: #1b2433; }
.hp-head p { margin: 3px 0 0; font-size: 11.5px; color: #98a3b4; }
.hp-x { border: none; background: transparent; font-size: 18px; line-height: 1; color: #98a3b4; cursor: pointer; padding: 2px 6px; border-radius: 6px; }
.hp-x:hover { background: #f3f6fb; color: #222b38; }
.hp-demo { margin-top: 10px; font-size: 11.5px; line-height: 1.6; color: #8a5a16; background: #fdf2e0; border: 1px solid #f0d29a; border-radius: 8px; padding: 7px 10px; }
.hp-err { margin: 8px 0 0; font-size: 12px; color: #d23b42; }
.hp-actionmsg { margin: 8px 0 0; font-size: 11.5px; color: #1c9e66; }
.hp-tabs { display: flex; gap: 4px; margin: 12px 0 10px; background: #f3f6fb; padding: 3px; border-radius: 9px; }
.hp-tabs button {
  flex: 1; border: none; background: transparent; cursor: pointer;
  font-size: 12px; font-weight: 600; color: #5a6778; padding: 6px 0; border-radius: 7px;
}
.hp-tabs button.active { background: #fff; color: #2f6fed; box-shadow: 0 1px 4px rgba(35,52,84,.12); }
.hp-pane { font-size: 12px; }
.hp-statgrid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 9px; }
.hp-stat { background: #f7f9fc; border: 1px solid #e4e9f2; border-radius: 11px; padding: 10px 11px; display: flex; flex-direction: column; gap: 3px; }
.hp-stat span { font-size: 10.5px; color: #98a3b4; }
.hp-stat b { font-size: 17px; color: #1b2433; font-variant-numeric: tabular-nums; }
.hp-stat b.hp-none { color: #1c9e66; font-size: 14px; }
.hp-stat em { font-style: normal; font-size: 10px; color: #a7b2c2; line-height: 1.4; }
.hp-plain { margin: 10px 0 0; font-size: 11.5px; line-height: 1.75; color: #5a6778; }
.hp-row { display: flex; gap: 7px; margin-top: 10px; }
.hp-row input { flex: 1; min-width: 0; padding: 7px 10px; border: 1px solid #c4cedd; border-radius: 7px; font: inherit; color: #222b38; background: #f6f8fb; outline: none; }
.hp-row select { min-width: 170px; padding: 7px 9px; border: 1px solid #c4cedd; border-radius: 7px; font: inherit; color: #222b38; background: #f6f8fb; outline: none; }
.hp-wf-edit small { color: #8a6a35; }
.hp-row input:focus { border-color: #2f6fed; background: #fff; box-shadow: 0 0 0 3px rgba(47,111,237,.12); }
.hp-btn { border: 1px solid #c4cedd; background: #fff; color: #334055; border-radius: 7px; padding: 7px 12px; font-size: 12px; font-weight: 600; cursor: pointer; white-space: nowrap; }
.hp-btn:hover { border-color: #9fb0c6; background: #f7f9fc; }
.hp-btn.primary { background: linear-gradient(180deg,#3b7ef2,#2f6fed); border-color: #2560d4; color: #fff; }
.hp-btn.primary:hover { filter: brightness(1.06); }
.hp-btn.danger { color: #d23b42; border-color: #efc4c6; }
.hp-btn.danger:hover { background: #fdecec; border-color: #e0484f; }
.hp-btn.sm { padding: 4px 10px; font-size: 11px; }
.hp-link { border: none; background: none; color: #2f6fed; cursor: pointer; font: inherit; padding: 0; }
.hp-rateline { margin-top: 9px; font-size: 11px; color: #5a6778; }
.hp-empty { margin-top: 10px; padding: 16px; text-align: center; font-size: 12px; color: #98a3b4; background: #f7f9fc; border: 1px dashed #d5dce7; border-radius: 10px; line-height: 1.7; }
.hp-list { margin-top: 9px; display: flex; flex-direction: column; gap: 7px; max-height: 380px; overflow: auto; }
.hp-item { display: flex; align-items: center; gap: 8px; background: #fafbfd; border: 1px solid #e4e9f2; border-radius: 10px; padding: 8px 11px; }
.hp-item-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.hp-item-main b { font-size: 12px; color: #1b2433; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hp-item-main span { font-size: 11px; color: #5a6778; }
.hp-item-main em { font-style: normal; font-size: 10px; color: #a7b2c2; }
.hp-item.on { border-color: #9fc0f5; background: #f2f7ff; box-shadow: 0 0 0 2px rgba(47,111,237,.10); }
.hp-cur { margin-left: 6px; font-size: 9.5px; font-weight: 700; color: #2f6fed; background: #eaf1fe; border: 1px solid #c8dcfa; border-radius: 999px; padding: 0 6px; }
.hp-sesshead { display: flex; align-items: center; gap: 10px; }
.hp-sesshead .hp-plain { flex: 1; margin: 0; }
.hp-sumline { font-size: 11.5px; color: #5a6778; background: #f7f9fc; border: 1px solid #e4e9f2; border-radius: 9px; padding: 8px 10px; line-height: 1.8; }
.hp-sumline b { color: #1b2433; }
.hp-sumline b.bad { color: #d23b42; }
.hp-trace { background: #fafbfd; border: 1px solid #e4e9f2; border-radius: 10px; padding: 8px 11px; }
.hp-trace-head { display: flex; align-items: center; gap: 8px; font-size: 11px; }
.hp-time { color: #5a6778; font-variant-numeric: tabular-nums; }
.hp-model { color: #7a4fd1; font-weight: 600; }
.hp-spacer { flex: 1; }
.hp-nums { color: #98a3b4; font-variant-numeric: tabular-nums; }
.hp-tag { font-size: 10px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
.hp-tag-ok { color: #128053; background: #e6f7ee; }
.hp-tag-err { color: #b32d33; background: #fdecec; }
.hp-tag-stop { color: #8a5a16; background: #fdf2e0; }
.hp-tools { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.hp-tools i { font-style: normal; font-family: var(--font-mono); font-size: 10px; color: #2f6fed; background: #eaf1fe; border: 1px solid #c8dcfa; border-radius: 5px; padding: 1px 7px; }
.hp-traceerr { margin-top: 5px; font-size: 11px; color: #b32d33; }
.hp-footrow { display: flex; align-items: center; justify-content: space-between; margin-top: 10px; }
.hp-linkline { font-size: 12px; color: #2f6fed; text-decoration: none; }
.hp-linkline:hover { text-decoration: underline; }
.hp-subhead { display: flex; align-items: center; justify-content: space-between; }
.hp-subhead b { font-size: 12.5px; color: #1b2433; }
.hp-skill { background: #fafbfd; border: 1px solid #e4e9f2; border-radius: 10px; padding: 9px 11px; display: flex; flex-direction: column; gap: 3px; }
.hp-skill b { font-size: 12px; color: #1b2433; }
.hp-skill span { font-size: 11.5px; color: #5a6778; line-height: 1.6; }
.hp-skill em { font-style: normal; font-size: 10.5px; color: #8a4fd1; }
.hp-skill small { font-size: 10px; color: #7b8796; }
.hp-skill-history { color: #5374a5 !important; }
.hp-skill-rollback { align-self: flex-start; margin-top: 4px; }
.hp-workflow { margin-top: 10px; }
.hp-wf-head { display:flex; justify-content:space-between; align-items:center; gap:8px; color:#1b2433; }
.hp-wf-head b { font-size:12px; }
.hp-wf-status { margin-left:7px; color:#2f6fed; font-size:11px; }
.hp-wf-livebar { margin-top:9px; padding:8px 9px; border:1px solid #dfe7f3; border-radius:8px; background:#fbfcff; }
.hp-wf-stage-strip { display:grid; grid-template-columns:repeat(5, minmax(0, 1fr)); gap:3px; }
.hp-wf-stage { position:relative; display:flex; flex-direction:column; align-items:center; gap:3px; min-width:0; color:#a7b2c2; font-size:9px; }
.hp-wf-stage:not(:last-child)::after { content:""; position:absolute; top:8px; left:58%; width:84%; height:1px; background:#e4e9f2; z-index:0; }
.hp-wf-stage-dot { position:relative; z-index:1; display:flex; align-items:center; justify-content:center; width:17px; height:17px; border:1px solid #d8e0ec; border-radius:50%; background:#fff; color:#a7b2c2; font-size:9px; font-weight:700; }
.hp-wf-stage b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }
.hp-wf-stage.done { color:#128053; }
.hp-wf-stage.done .hp-wf-stage-dot { border-color:#8dd5b1; background:#e6f7ee; color:#128053; }
.hp-wf-stage.done:not(:last-child)::after { background:#8dd5b1; }
.hp-wf-stage.current { color:#2f6fed; }
.hp-wf-stage.current .hp-wf-stage-dot { border-color:#7faaf2; background:#edf4ff; color:#2f6fed; box-shadow:0 0 0 3px rgba(47,111,237,.1); }
.hp-wf-stage.bad { color:#b32d33; }
.hp-wf-stage.bad .hp-wf-stage-dot { border-color:#efb2b5; background:#fdecec; color:#b32d33; }
.hp-wf-live-meta { display:flex; align-items:center; gap:5px; margin-top:8px; color:#334055; font-size:10.5px; }
.hp-wf-live-meta em { margin-left:auto; color:#98a3b4; font-size:9px; font-style:normal; white-space:nowrap; }
.hp-wf-live-dot { flex:none; width:7px; height:7px; border-radius:50%; background:#2f6fed; box-shadow:0 0 0 3px rgba(47,111,237,.1); }
.hp-wf-live-dot.done { background:#128053; box-shadow:0 0 0 3px rgba(18,128,83,.1); }
.hp-wf-live-dot.bad { background:#b32d33; box-shadow:0 0 0 3px rgba(179,45,51,.1); }
.hp-wf-live-dot.pending { background:#a7b2c2; box-shadow:none; }
.hp-wf-diagnostics { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:6px; margin-top:9px; }
.hp-wf-diagnostics > div { min-width:0; display:flex; flex-direction:column; gap:2px; padding:7px 8px; background:#f7f9fc; border:1px solid #e4e9f2; border-radius:7px; }
.hp-wf-diagnostics span { color:#98a3b4; font-size:10px; }
.hp-wf-diagnostics b { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#334055; font-size:10.5px; }
.hp-wf-diagnostics b.good { color:#128053; }
.hp-wf-diagnostics b.bad { color:#b32d33; }
.hp-wf-diagnostics small { color:#7a8798; font-size:9px; font-weight:400; }
.hp-wf-retrieval { margin-top:9px; border:1px solid #e4e9f2; border-radius:8px; background:#fbfcfe; padding:7px 8px; }
.hp-wf-retrieval-head { display:flex; align-items:center; justify-content:space-between; color:#334055; font-size:11px; }
.hp-wf-retrieval-head small { color:#98a3b4; font-size:9px; font-weight:400; }
.hp-wf-retrieval-item { margin-top:5px; border-top:1px solid #edf0f5; padding-top:5px; }
.hp-wf-retrieval-item summary { cursor:pointer; display:flex; justify-content:space-between; gap:8px; color:#5a6778; font-size:10px; }
.hp-wf-retrieval-query { margin-top:5px; color:#334055; font-size:10px; white-space:pre-wrap; word-break:break-word; }
.hp-wf-retrieval-doc { margin-top:5px; padding:5px 6px; border-left:2px solid #c8dcfa; background:#fff; font-size:9.5px; }
.hp-wf-retrieval-doc > span { color:#334055; display:block; }
.hp-wf-retrieval-doc > span b { color:#2f6fed; margin-right:3px; }
.hp-wf-retrieval-doc > span small { color:#98a3b4; }
.hp-wf-retrieval-doc em { display:block; color:#7a8798; font-style:normal; margin-top:2px; }
.hp-wf-retrieval-doc p { margin:3px 0 0; color:#7a8798; line-height:1.45; max-height:34px; overflow:hidden; }
.hp-wf-alert { margin-top:8px; padding:7px 9px; border-radius:8px; color:#8a5a16; background:#fdf2e0; border:1px solid #f0d29a; font-size:11px; }
.hp-wf-revision { display:flex; flex-direction:column; gap:4px; margin-top:8px; padding:8px 9px; color:#5a6778; background:#fff8ea; border:1px solid #f0d29a; border-radius:8px; font-size:10.5px; }
.hp-wf-revision b { color:#8a5a16; font-size:11px; }
.hp-wf-revision span { padding-left:6px; }
.hp-wf-options { display:flex; flex-direction:column; gap:6px; margin-top:9px; }
.hp-wf-custom { margin-top:8px; }
.hp-wf-option { display:flex; flex-direction:column; gap:3px; text-align:left; border:1px solid #e4e9f2; background:#fafbfd; border-radius:9px; padding:8px 10px; cursor:pointer; color:#334055; }
.hp-wf-option:hover { border-color:#9fc0f5; background:#f2f7ff; }
.hp-wf-option b { font-size:12px; color:#1b2433; }
.hp-wf-option span { font-size:11px; color:#5a6778; line-height:1.5; }
.hp-wf-option em { margin-left:6px; font-style:normal; color:#2f6fed; font-size:10px; }
.hp-wf-actions { display:flex; gap:6px; flex-wrap:wrap; margin-top:9px; }
.hp-wf-tasks { display:flex; flex-direction:column; gap:5px; margin-top:10px; }
.hp-wf-task { display:flex; gap:7px; padding:6px 8px; background:#f7f9fc; border-radius:7px; font-size:10.5px; }
.hp-wf-task b { color:#2f6fed; }
.hp-wf-task span { color:#5a6778; flex:1; min-width:0; }
.hp-wf-task em { font-style:normal; flex:none; color:#8a8798; }
.hp-wf-task em.ok { color:#128053; }
.hp-wf-task em.bad { color:#b32d33; }
.hp-wf-events { display:flex; gap:4px; flex-wrap:wrap; margin-top:9px; }
.hp-wf-events span { font-size:9px; color:#7a4fd1; background:#f0eafe; border-radius:4px; padding:2px 5px; }
.hp-wf-task span small, .hp-wf-subagent small { display:block; margin-top:2px; color:#7c8798; font-size:10px; }
.hp-wf-team { margin-top:10px; padding:8px; border:1px solid #e1e7f0; border-radius:9px; background:#fff; }
.hp-wf-team-head { display:flex; align-items:baseline; justify-content:space-between; gap:8px; color:#334055; font-size:11px; }
.hp-wf-team-head small { color:#98a3b4; font-size:9px; }
.hp-wf-supervisor, .hp-wf-subagent { display:flex; align-items:center; gap:7px; min-width:0; }
.hp-wf-supervisor { margin-top:7px; padding:7px 8px; border:1px solid #d8e5fb; border-radius:7px; background:#f4f8ff; font-size:10.5px; }
.hp-wf-avatar { flex:0 0 25px; width:25px; height:25px; display:inline-flex; align-items:center; justify-content:center; border-radius:50%; background:#eef2f8; color:#4b6386; font-size:10px; font-weight:700; }
.hp-wf-avatar.supervisor { background:#2f6fed; color:#fff; }
.hp-wf-member-main { min-width:0; flex:1; color:#354052; }
.hp-wf-member-main b { color:#243047; font-size:10.5px; }
.hp-wf-member-main small { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.hp-wf-member-task { display:block; margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#5a6778; font-size:10px; }
.hp-wf-member-side { display:flex; flex:0 0 auto; flex-direction:column; align-items:flex-end; gap:2px; }
.hp-wf-member-side small { max-width:130px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#98a3b4; font-size:9px; }
.hp-wf-member-side small.bad { color:#b32d33; }
.hp-wf-member-status { flex:none; padding:2px 6px; border-radius:10px; color:#7c8798; background:#f0f2f6; font-size:9px; font-style:normal; white-space:nowrap; }
.hp-wf-member-status.running { color:#2f6fed; background:#eaf2ff; }
.hp-wf-member-status.ok { color:#128053; background:#e6f7ee; }
.hp-wf-member-status.bad { color:#b32d33; background:#fdecec; }
.hp-wf-subagents { display:grid; gap:5px; margin-top:6px; }
.hp-wf-subagent { border:1px solid #e5e9f1; background:#fbfcfe; padding:7px 8px; border-radius:7px; font-size:10px; }
.hp-wf-subagent.state-running { border-color:#bcd2f8; background:#f8fbff; }
.hp-wf-subagent.state-bad { border-color:#efc4c6; background:#fffafa; }
.hp-wf-subagent { cursor:pointer; }
.hp-wf-subagent.selected { border-color:#6d9de8; box-shadow:0 0 0 2px rgba(86,137,214,.12); }
.hp-wf-agent-detail { margin-top:7px; padding:9px; border:1px solid #dbe3ef; border-radius:7px; background:#f8fafc; }
.hp-wf-agent-detail-head { display:flex; align-items:flex-start; justify-content:space-between; gap:8px; }
.hp-wf-agent-detail-head b { display:block; color:#25344a; font-size:11px; }
.hp-wf-agent-detail-head small, .hp-wf-agent-meta, .hp-wf-agent-events small, .hp-wf-agent-tools small { display:block; margin-top:3px; color:#7c8798; font-size:9px; }
.hp-wf-agent-task { margin:7px 0 4px; color:#334055; font-size:10px; line-height:1.45; }
.hp-wf-agent-result { margin-top:7px; padding:6px 7px; border-left:2px solid #9db9e8; color:#45536a; background:#fff; font-size:10px; line-height:1.45; }
.hp-wf-agent-events, .hp-wf-agent-tools { display:grid; gap:4px; margin-top:7px; }
.hp-wf-agent-events > div, .hp-wf-agent-tools > div { padding:4px 6px; border:1px solid #e4e9f1; border-radius:5px; background:#fff; }
.hp-wf-agent-events b, .hp-wf-agent-tools b { color:#50627a; font-size:9px; }
.hp-wf-trace { margin-top:9px; border:1px solid #e4e9f2; border-radius:8px; background:#fbfcfe; padding:7px 8px; }
.hp-wf-trace-head { display:flex; justify-content:space-between; align-items:center; color:#334055; font-size:11px; }
.hp-wf-trace-head small { color:#98a3b4; font-size:9px; font-weight:400; }
.hp-wf-trace-item { margin-top:5px; border-top:1px solid #edf0f5; padding-top:5px; }
.hp-wf-trace-item summary { cursor:pointer; display:flex; justify-content:space-between; gap:8px; color:#5a6778; font-size:10px; }
.hp-wf-trace-item summary b { color:#7a4fd1; font-size:9px; margin-right:3px; }
.hp-wf-trace-item summary em { max-width:55%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:#334055; font-style:normal; }
.hp-wf-trace-item summary em.bad { color:#b32d33; }
.hp-wf-trace-item p { margin:5px 0 0; color:#7a8798; font-size:9.5px; line-height:1.45; white-space:pre-wrap; word-break:break-word; max-height:120px; overflow:auto; }
.hp-wf-trace-meta { display:block; margin-top:3px; color:#98a3b4; font-size:9px; }
.hp-wf-event-detail { display:inline-block; margin:4px 4px 0 0; max-width:100%; }
.hp-wf-event-detail summary { cursor:pointer; font-size:9px; color:#7a4fd1; background:#f0eafe; border-radius:4px; padding:2px 5px; }
.hp-wf-event-detail small { display:block; margin-top:3px; padding:3px 5px; color:#7a8798; background:#fafbfd; border:1px solid #edf0f5; border-radius:4px; font-size:9px; white-space:pre-wrap; word-break:break-word; }
.hp-wf-review { margin-top:8px; color:#5a6778; font-size:11px; }
.hp-wf-trace-error { display:block; margin-top:3px; color:#b32d33; }
.hp-wf-failures { display:flex; flex-direction:column; gap:5px; margin-top:8px; padding:7px 9px; background:#fff4f4; border:1px solid #efc4c6; border-radius:8px; color:#7c3c41; font-size:10.5px; }
.hp-wf-failures div { display:flex; gap:6px; flex-wrap:wrap; }
.hp-wf-failures b { color:#b32d33; }
.hp-wf-failures span { color:#5a6778; }
.hp-wf-failures em { width:100%; padding-left:6px; font-style:normal; color:#8a5a16; }
.hp-wf-checks { display:flex; flex-wrap:wrap; gap:5px 9px; margin-top:8px; color:#5a6778; font-size:10px; }
.hp-wf-checks i { display:inline-flex; align-items:center; justify-content:center; width:14px; height:14px; margin-right:3px; border-radius:50%; font-style:normal; color:#128053; background:#e6f7ee; }
.hp-wf-checks i.bad { color:#b32d33; background:#fdecec; }
.hp-hook-breakpoint { margin-top:10px; padding:8px 9px; border:1px solid #e4e9f2; border-radius:8px; background:#fbfcfe; }
.hp-hook-breakpoint .hp-subhead { margin:0 0 6px; }
.hp-hook-breakpoint .hp-subhead small { color:#98a3b4; font-size:9px; font-weight:400; margin-left:6px; }
.hp-hook-breakpoint select { min-width:125px; }
.hp-check { display:inline-flex; align-items:center; gap:4px; color:#5a6778; font-size:10px; white-space:nowrap; }
.hp-check input { margin:0; }
.hp-hook-list { display:flex; flex-direction:column; gap:4px; margin-top:7px; }
.hp-hook-item { display:flex; align-items:center; justify-content:space-between; gap:6px; padding:5px 6px; border-top:1px solid #edf0f5; color:#5a6778; font-size:10px; }
.hp-hook-item b { color:#7a4fd1; }
</style>
