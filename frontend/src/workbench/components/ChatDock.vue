<script setup lang="ts">
// P0 底部 AI 对话台：全局代码问答/定位入口（ReAct agent，SSE）。
// - 答案中的文件引用渲染为可点击卡片：跳转代码行 + 文件树展开闪烁 + 分区高亮；
// - 头部「引擎」弹层：MCP 服务器连接状态（godot-ai stdio / unity / unreal HTTP）
//   与 godot-ai 插件安装引导（安装前必须用户确认）。
import { nextTick, reactive, ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'
import { useWorkbench, askConfirm, askAlert } from '../composables/workbench'
import { aiApi, agentApi, visionApi, mcpApi, modelApi, contextApi, harnessApi, getSessionId, getProjectId, setSessionId, startNewSession, startTabProbe } from '../api'
import type { McpServer, ModelConfigInfo, ContextUsage, SessionInfo } from '../api'
import type { SseEvent } from '../api'
import { mdToHtml, extractFileRefs, extractWebRefs } from '../markdown'
import type { FileRef } from '../markdown'
import { demoMode } from '../composables/demo'
import ModelSettingsDialog from './ModelSettingsDialog.vue'
import WorkflowCard from './WorkflowCard.vue'
import WorkflowMembersDock from './WorkflowMembersDock.vue'
import type { WorkflowState, WorkflowSummary } from '../api'
import type { PreviewFeedbackRequest } from '../previewFeedback'

const {
  nodeExists, revealPath, jumpToLine,
} = useWorkbench()

// ---------------------------------------------------------------- 对话状态
interface ChatMsg {
  id: number
  role: 'user' | 'assistant'
  text: string
  status: 'streaming' | 'done' | 'error' | 'stopped'
  trace: { type: string; text: string; at?: number; elapsedMs?: number }[]
  reasoning: string        // 深度思考模型的 reasoning_content 流
  notices: string[]        // 系统通知（如上下文自动压缩）
  plan: string[]           // 计划模式步骤
  startedAt?: number
  lastActivityAt?: number
  finishedAt?: number
  error?: string
  recoverable?: boolean
  imageCount?: number
  /** 对话内工作流卡片（start_workflow 触发，后续全走 SSE，不再弹独立面板） */
  workflow?: { workflowId: string; seed?: Partial<WorkflowState> }
}

/** 活跃工作流团队（左下角成员抽屉数据源，终态自动移除） */
interface WfTeam {
  workflowId: string
  active: boolean
  members: Array<{ id: string; label: string; role: string; status: string; task: string }>
}
const wfTeams = ref<WfTeam[]>([])
const wfActiveTeam = computed(() => {
  const live = [...wfTeams.value].reverse().find(t => t.active && t.members.length)
  return live || null
})
// 所有未终结的工作流（含停在方案门、成员尚为空的）——并发拦截与离开确认都以此为准
const activeWorkflowIds = computed(() =>
  wfTeams.value.filter(t => t.active).map(t => t.workflowId))
// 正打开人工审批门的工作流集合：此时锁定对话台操作区（发送/工具/头部按钮），
// 但不锁聊天滚动与编辑器——审批可以边看代码边做
const gateOpenIds = ref<Set<string>>(new Set())
const gateBlocking = computed(() => gateOpenIds.value.size > 0)
function onWfGate(workflowId: string, open: boolean) {
  const next = new Set(gateOpenIds.value)
  if (open) next.add(workflowId); else next.delete(workflowId)
  gateOpenIds.value = next
}
/**
 * 离开当前对话（切换/新建/清空）前调用：有未终结工作流时必须先征得同意，
 * 并在用户确认后尽力中断它们——否则卡片随消息卸载，后端工作流会停在
 * 审批门成为无法操作的孤儿。文件改动不会回滚，需在文案里明示。
 */
async function confirmLeaveWorkflows(): Promise<boolean> {
  const ids = activeWorkflowIds.value
  if (!ids.length) return true
  const ok = await askConfirm({
    title: '工作流仍在进行',
    message: `有 ${ids.length} 个工作流正在等待审批或执行中。离开后将无法继续操作，系统会自动中断它（工作流已产生的文件改动不会自动回滚）。确定离开？`,
    confirmText: '中断并离开',
  })
  if (!ok) return false
  await Promise.allSettled(ids.map(id =>
    agentApi.workflowInterrupt(id, '用户离开当前对话，自动中断')))
  return true
}
function onWfTeam(payload: WfTeam) {
  const rest = wfTeams.value.filter(t => t.workflowId !== payload.workflowId)
  if (payload.active) rest.push(payload)
  wfTeams.value = rest
  // 工作流终结：给对应回合盖结束时间，让秒表定格（计时钟也随之停走）
  if (!payload.active) {
    const turn = messages.value.find(m => m.workflow?.workflowId === payload.workflowId)
    if (turn && turn.startedAt && !turn.finishedAt) turn.finishedAt = Date.now()
    if (orphanWf.value?.workflow_id === payload.workflowId) orphanWf.value = null
  }
}

// ---------------------------------------------------------------- 孤儿工作流恢复
// 页面刷新/会话切换后对话消息是纯文本回灌，工作流卡片不会重建，但后端同项目
// 互斥仍然存活 → 新请求被拒且界面无任何操作入口。挂载/切会话/发送后探测一次，
// 发现「后端未终结、本地无卡片」的工作流时给出恢复条（挂回卡片或中断）。
const WF_STATUS_LABEL: Record<string, string> = {
  generating_options: '正在生成方案', awaiting_choice: '等待选择方案',
  awaiting_research: '等待联网检索', researching: '联网调研中',
  planning: '任务规划中', awaiting_plan_approval: '等待计划审批',
  awaiting_approval: '等待审核', planned: '待执行',
  executing: '子代理执行中', reviewing: '汇总检查中',
  completed: '已完成', failed: '已失败', interrupted: '已中断',
}
const orphanWf = ref<WorkflowState | null>(null)
const orphanBusy = ref(false)
const orphanError = ref('')
const orphanStatusLabel = computed(() =>
  orphanWf.value ? WF_STATUS_LABEL[orphanWf.value.status] || orphanWf.value.status : '')

async function detectOrphanWorkflow() {
  if (demoMode.value) return
  try {
    const r = await agentApi.workflowActive()
    const wf = r.ok ? (r.workflow ?? null) : null
    // 本地已有卡片在管的不算孤儿（卡片自身即可审批/中断）
    orphanWf.value = wf && !messages.value.some(m => m.workflow?.workflowId === wf.workflow_id)
      ? wf : null
    orphanError.value = ''
  } catch {
    /* 服务未启动/不可达时不显示恢复条 */
  }
}
/** 把孤儿工作流以卡片形式挂回当前对话流；卡片挂载后会自行拉完整状态并订阅 SSE。 */
async function reattachOrphan() {
  const wf = orphanWf.value
  if (!wf || orphanBusy.value) return
  orphanBusy.value = true
  try {
    orphanWf.value = null
    messages.value.push({
      id: msgSeq++, role: 'assistant', text: '', status: 'done',
      trace: [], reasoning: '', notices: [], plan: [],
      workflow: { workflowId: wf.workflow_id, seed: wf },
      startedAt: Date.now(),
    })
    stickToBottom.value = true
    await nextTick(scrollToBottom)
  } finally {
    orphanBusy.value = false
  }
}
async function interruptOrphan() {
  const wf = orphanWf.value
  if (!wf || orphanBusy.value) return
  orphanBusy.value = true
  orphanError.value = ''
  try {
    const r = await agentApi.workflowInterrupt(
      wf.workflow_id, '页面刷新后卡片丢失，用户在孤儿恢复条中断')
    if (r.ok === false) throw new Error(r.error || '中断失败')
    orphanWf.value = null
  } catch (e) {
    orphanError.value = (e as { message?: string }).message || '中断失败，请重试'
  } finally {
    orphanBusy.value = false
  }
}
async function focusWfMember(taskId: string) {
  const team = wfActiveTeam.value
  if (!team) return
  // 面板折叠时先展开（卡片常驻未卸载），下一帧再定位，否则 scrollIntoView
  // 落在被 overflow:hidden 裁掉的容器里用户看不到
  if (collapsed.value) {
    collapsed.value = false
    await nextTick()
  }
  window.dispatchEvent(new CustomEvent('docmind:wf-focus-member', {
    detail: { workflowId: team.workflowId, taskId },
  }))
}

interface InterruptedRecovery {
  user: string
  assistant: string
  trace: ChatMsg['trace']
  reasoning: string
  plan: string[]
  startedAt?: number
}

let msgSeq = 1
const messages = ref<ChatMsg[]>([])
let chatProject = getProjectId(), chatSession = getSessionId(), chatEpoch = 0
const draftKey = () => 'docmind.workbenchChatDraft:' + chatProject + ':' + chatSession
const recoveryKey = () => 'docmind.interrupted:' + chatProject + ':' + chatSession
const historyError = ref('')
const sessionItems = ref<SessionInfo[]>([])
const sessionOpen = ref(false)
const sessionBusy = ref(false)
const sessionError = ref('')
function readDraft() { try { return sessionStorage.getItem(draftKey()) || '' } catch { return '' } }
function readInterruptedRecovery(): InterruptedRecovery | null {
  try {
    const row = JSON.parse(sessionStorage.getItem(recoveryKey()) || 'null') as Partial<InterruptedRecovery> | null
    if (!row || typeof row.user !== 'string' || typeof row.assistant !== 'string') return null
    return {
      user: row.user,
      assistant: row.assistant,
      trace: Array.isArray(row.trace) ? row.trace.filter(item => item && typeof item.text === 'string').slice(-24) : [],
      reasoning: typeof row.reasoning === 'string' ? row.reasoning : '',
      plan: Array.isArray(row.plan) ? row.plan.filter(item => typeof item === 'string').slice(-12) : [],
      startedAt: typeof row.startedAt === 'number' ? row.startedAt : undefined,
    }
  } catch { return null }
}
function isContinuationRequest(value: string): boolean {
  const normalized = value.trim().replace(/[。！!？?，,、；;：:"“”‘’\s]/g, '')
  return /^(继续|继续完成任务|继续上次任务|继续回答|继续执行|接着做|接着完成|恢复任务|恢复回答|从中断处继续)$/.test(normalized)
}
function continuationQuestion(request: string, recovery: InterruptedRecovery): string {
  const trace = recovery.trace.map(item => `${item.type}: ${item.text}`).join('\n').slice(-5000)
  const plan = recovery.plan.join('\n').slice(-1800)
  return [
    '这是上一个被页面切换或连接中断的回合的继续请求。不要把“继续”当成新的独立问题。',
    `原始用户请求：${recovery.user.slice(0, 3000)}`,
    `上次已收到的助手内容：${recovery.assistant.slice(-6000)}`,
    trace ? `上次已记录的执行步骤：\n${trace}` : '',
    plan ? `上次执行计划：\n${plan}` : '',
    '请先依据以上上下文判断已经完成的步骤，再从中断处继续；不要重复已经确认完成的有副作用工具调用。若原始请求只是询问能力，请直接继续回答原始问题。',
    `用户续接指令：${request}`,
  ].filter(Boolean).join('\n\n')
}
// 兼容旧版会话：早期 API 曾把内部路由上下文拼在用户问题前并落盘。
// 仅清理从字符串开头出现的完整旧前缀，正文中提到“系统提示”不受影响。
const legacyPromptPrefix = /^\s*【系统提示】[\s\S]*?用户问题：\s*/
function cleanLegacyPrompt(value: string): string {
  return String(value || '').replace(legacyPromptPrefix, '').trimStart()
}
const input = ref(readDraft())
watch(input, v => { try { sessionStorage.setItem(draftKey(), v) } catch {} }, { flush: 'sync' })
const sending = ref(false)
let abortCtl: AbortController | null = null
const TASK_EXAMPLES = [
  { id: 'feature', label: '实现一个功能', prompt: '在现有游戏项目中实现一个新功能，并先给我 2-4 个可选方案。' },
  { id: 'bug', label: '修复一个问题', prompt: '定位并修复现有游戏项目中的一个问题，先给我推荐方案和验收标准。' },
  { id: 'scene', label: '添加场景或 UI', prompt: '在现有游戏项目中添加一个场景或 UI，先给我推荐实现方案并说明影响范围。' },
  { id: 'custom', label: '描述我的目标', prompt: '' },
] as const

function startTaskExample(example: typeof TASK_EXAMPLES[number]) {
  if (example.id === 'custom') {
    input.value = ''
    void nextTick(() => inputEl.value?.focus())
    return
  }
  void startWorkflowTurn(example.prompt)
}

/** 快捷入口直接发起工作流：卡片内联挂到对话流，不再跳独立面板。 */
async function startWorkflowTurn(prompt: string) {
  if (sending.value || demoMode.value) return
  // 已有未终结工作流时禁止再启动（后端 /workflow/start 也有同项目互斥兜底）
  if (activeWorkflowIds.value.length) {
    await askAlert({
      title: gateBlocking.value ? '工作流等待你的处理' : '已有工作流进行中',
      message: gateBlocking.value
        ? '请先在居中的审批窗口中选择方案、批准或调整任务（也可中断该工作流），再启动新的。'
        : '请先在对话中的工作流卡片上完成审批、等待执行结束，或中断当前工作流后再启动新的。',
    })
    return
  }
  // 与普通问答共用 chatEpoch：启动阶段用户点「停止」会使 epoch 失效，
  // 迟到响应不再挂卡片（并尽力中断可能已创建的孤儿工作流，见下）。
  const epoch = ++chatEpoch
  messages.value.push({ id: msgSeq++, role: 'user', text: prompt, status: 'done', trace: [], reasoning: '', notices: [], plan: [] })
  const turn: ChatMsg = {
    id: msgSeq++, role: 'assistant', text: '', status: 'streaming', trace: [], reasoning: '', notices: [], plan: [],
    workflow: { workflowId: '', seed: { status: 'generating_options', kind: 'generic', request: prompt } },
    startedAt: Date.now(), lastActivityAt: Date.now(),
  }
  messages.value.push(turn)
  sending.value = true
  stickToBottom.value = true
  await nextTick(scrollToBottom)
  try {
    const r = await agentApi.workflowStart(prompt, { use_llm: true, web_enabled: webOn.value, kind: 'generic' })
    if (epoch !== chatEpoch) {
      // 启动已被用户取消：请求可能已在服务端创建工作流，挂卡片只会出现
      // 无法审批的孤儿，因此尽力中断它；UI 上的回合由 stop() 标记为已停止。
      if (r.ok && r.workflow) {
        void agentApi.workflowInterrupt(r.workflow.workflow_id, '用户在启动阶段取消').catch(() => {})
      }
      return
    }
    if (r.ok && r.workflow) {
      turn.workflow = { workflowId: r.workflow.workflow_id, seed: r.workflow }
      turn.status = 'done'
      window.dispatchEvent(new CustomEvent('docmind:workflow-started', { detail: { workflowId: r.workflow.workflow_id } }))
    } else {
      turn.workflow = undefined
      turn.status = 'error'
      turn.error = r.error || '工作流启动失败'
    }
  } catch (e) {
    if (epoch !== chatEpoch) return
    turn.workflow = undefined
    turn.status = 'error'
    turn.error = (e as Error).message || '工作流启动失败'
  } finally {
    // 仅当没有被更新的回合（停止/新发送）取代时才解除发送锁
    if (epoch === chatEpoch) {
      sending.value = false
      // 启动被互斥拒绝时，发现已存在的那个工作流并提供挂回/中断入口
      void detectOrphanWorkflow()
    }
    await nextTick(scrollToBottom)
  }
}

/** 工作流主 Agent 的 start_workflow 动作行与其观察不在对话重复展示（卡片即反馈）。 */
function visibleTrace(msg: ChatMsg) {
  const hidden = new Set<number>()
  msg.trace.forEach((item, i) => {
    if (item.type === 'action' && /^start_workflow\s*\(/.test(item.text.trim())) {
      hidden.add(i)
      if (msg.trace[i + 1]?.type === 'observation') hidden.add(i + 1)
    }
  })
  return msg.trace.map((item, index) => ({ item, index })).filter(x => !hidden.has(x.index))
}
function onCardActivity() {
  // 卡片内部子代理步骤频率高：按帧合批跟随，不做每事件 nextTick
  scheduleFollow()
}

const scroller = ref<HTMLElement | null>(null)
/** 仅当用户已贴底时才自动滚；用户上滚看历史时暂停自动滚动，回到底部再恢复 */
const stickToBottom = ref(true)
const inputEl = ref<HTMLTextAreaElement | null>(null)
const imageInput = ref<HTMLInputElement | null>(null)
const videoInput = ref<HTMLInputElement | null>(null)
const pendingImages = ref<File[]>([])
const attachmentError = ref('')
const videoBusy = ref(false)
let suppressScrollEvent = false

// ---------------------------------------------------------------- 折叠
const collapsed = ref(window.localStorage.getItem('docmind.chatDockCollapsed') === '1')
const expanded = ref(window.localStorage.getItem('docmind.chatDockExpanded') === '1')
watch(collapsed, (v) => window.localStorage.setItem('docmind.chatDockCollapsed', v ? '1' : '0'))
watch(expanded, (v) => window.localStorage.setItem('docmind.chatDockExpanded', v ? '1' : '0'))
function toggleDock() {
  // 审批门打开时禁止折叠（pointer-events 已挡鼠标，这里挡已聚焦元素的键盘触发），
  // 否则 33px 裁剪会把居中审批模态吞掉
  if (gateBlocking.value) return
  collapsed.value = !collapsed.value
  if (!collapsed.value) nextTick(() => inputEl.value?.focus())
}

// ---------------------------------------------------------------- 模型 / 联网 / 思考
const modelConfig = ref<ModelConfigInfo | null>(null)
const settingsOpen = ref(false)
// 联网默认关（代码问答不外联），选择持久化在本机
const webOn = ref(window.localStorage.getItem('docmind.chatWeb') === '1')
// 深度思考：'1'/'0'，仅对支持思考的模型才发送
const thinkingOn = ref(window.localStorage.getItem('docmind.chatThinking') === '1')
watch(webOn, (v) => window.localStorage.setItem('docmind.chatWeb', v ? '1' : '0'))
watch(thinkingOn, (v) => window.localStorage.setItem('docmind.chatThinking', v ? '1' : '0'))

const modelLabel = computed(() => {
  if (demoMode.value) return '离线演示模型'
  const c = modelConfig.value
  if (!c) return '模型加载中…'
  const name = c.provider_meta?.[c.llm_provider]?.label || c.llm_provider
  return `${name} · ${c.llm_model || '默认模型'}`
})
const thinkingMode = computed(() => modelConfig.value?.capability?.thinking || 'none')
const thinkingSupported = computed(() => thinkingMode.value === 'native' || thinkingMode.value === 'toggle')
// native 思考模型（reasoner 类）开关恒开且不可点；toggle 家族才允许用户切
const thinkingNative = computed(() => thinkingMode.value === 'native')
const thinkingEffective = computed(() => thinkingNative.value || thinkingOn.value)
const visionMode = computed(() => modelConfig.value?.capability?.vision || 'unknown')
const visionLabel = computed(() => {
  if (visionMode.value === 'native') return '当前模型直接识图'
  if (visionMode.value === 'harness') return 'Harness 视觉辅助'
  if (visionMode.value === 'none') return '当前模型不支持识图'
  return '识图能力待确认，可由 Harness 辅助'
})

// ---------------------------------------------------------------- 上下文窗口用量
// 后端按当前模型真实窗口估算（含系统提示/历史摘要/历史回放/当前问题），
// 每轮问答开工与收尾各推一次 SSE context 事件；刷新页面走 GET /api/context 恢复。
const usage = ref<ContextUsage | null>(null)
function applyUsage(ev: SseEvent) {
  if (typeof ev.percent !== 'number' || typeof ev.context_window !== 'number') return
  usage.value = {
    used_tokens: ev.used_tokens ?? 0,
    context_window: ev.context_window,
    prompt_budget: ev.prompt_budget ?? 0,
    percent: Math.max(0, Math.min(100, ev.percent)),
    level: (ev.level as ContextUsage['level']) || 'ok',
    history_tokens: ev.history_tokens,
    compact_trigger_tokens: ev.compact_trigger_tokens,
    compact_percent: typeof ev.compact_percent === 'number'
      ? Math.max(0, Math.min(100, ev.compact_percent))
      : undefined,
  }
}
function fmtTokens(n: number): string {
  if (n >= 1000) {
    const k = n / 1000
    return `${k >= 100 ? Math.round(k) : k.toFixed(k >= 10 ? 0 : 1)}k`
  }
  return String(n)
}
const usageTitle = computed(() => {
  const u = usage.value
  if (!u) return ''
  // 百分比按「可用 prompt 额度」计（模型窗口扣除输出预留）；level 现由「历史 / 压缩触发线」口径分级
  const tail = u.level === 'high'
    ? '（历史已达压缩触发线，早期对话会被自动压缩为摘要）'
    : u.level === 'warn'
      ? '（历史接近压缩触发线，即将自动压缩早期对话）'
      : '（历史达到触发线后早期对话会自动压缩为摘要，不影响新问答）'
  let line = `上下文已用 ${fmtTokens(u.used_tokens)} / 可用额度 ${fmtTokens(u.prompt_budget)} tokens`
    + `（模型窗口 ${fmtTokens(u.context_window)}，已预留输出空间）${tail}`
  if (typeof u.compact_percent === 'number') {
    line += `\n压缩进度：历史 ${fmtTokens(u.history_tokens ?? 0)}`
      + ` / 触发线 ${fmtTokens(u.compact_trigger_tokens ?? 0)} tokens（${u.compact_percent}%）`
  }
  return line
})

async function loadModelConfig() {
  if (demoMode.value) return
  try {
    modelConfig.value = await modelApi.get()
  } catch {
    /* 配置加载失败不阻塞聊天，芯片显示兜底文案 */
  }
}
function openSettings() {
  if (demoMode.value) return
  void loadModelConfig().then(() => { settingsOpen.value = true })
}
function onModelSaved(info: ModelConfigInfo) {
  modelConfig.value = info
  // 后端切换模型会清空多轮上下文，前端消息同步清空避免张冠李戴
  clearMessages()
  // 是否关闭弹窗由弹窗自身决定（有告警时停留，让用户看到告警内容）
}

// ---------------------------------------------------------------- 发送 / 停止
async function send(text?: string, attached?: File[], onAccepted?: () => void): Promise<boolean> {
  const q = (text ?? input.value).trim()
  const imgs = attached ? attached.slice() : pendingImages.value.slice()
  if ((!q && !imgs.length) || sending.value) return false
  // 审批门打开期间禁止发起新问答（CSS 已挡鼠标，这里挡 Ctrl+Enter 键盘发送）
  if (gateBlocking.value) {
    void askAlert({
      title: '工作流等待你的处理',
      message: '请先在居中的审批窗口中选择方案、批准或调整任务（也可中断该工作流），再继续提问。',
    })
    return false
  }
  const recovery = readInterruptedRecovery()
  const isResume = !!recovery && isContinuationRequest(q)
  const request = isResume ? continuationQuestion(q, recovery!) : q
  const epoch = ++chatEpoch
  historyError.value = ''
  try { sessionStorage.removeItem(recoveryKey()) } catch {}
  if (isResume) {
    const previous = messages.value[messages.value.length - 1]
    if (previous?.recoverable) previous.recoverable = false
  }
  if (attached === undefined) {
    input.value = ''
    pendingImages.value = []
    attachmentError.value = ''
  }
  stickToBottom.value = true  // 用户主动发送，恢复贴底自动滚动
  messages.value.push({ id: msgSeq++, role: 'user', text: q || '请分析附件图片', status: 'done', trace: [], reasoning: '', notices: [], plan: [], imageCount: imgs.length })
  const turn: ChatMsg = {
    id: msgSeq++, role: 'assistant', text: '', status: 'streaming', trace: [], reasoning: '', notices: [], plan: [],
    startedAt: Date.now(),
  }
  messages.value.push(turn)
  sending.value = true
  onAccepted?.()
  if (demoMode.value) {
    await nextTick(scrollToBottom)
    await demoAnswer(turn.id, q)
    sending.value = false
    await nextTick(scrollToBottom)
    return true
  }
  const ac = new AbortController()
  abortCtl = ac
  await nextTick(scrollToBottom)
  if (epoch !== chatEpoch) return false
  let finished = false

  const live = () => epoch === chatEpoch ? messages.value.find((m) => m.id === turn.id) : undefined
  const onEvent = (ev: SseEvent) => {
    if (epoch !== chatEpoch) return
    // 上下文用量是全局指示，不挂在某条消息上
    if (ev.type === 'context') {
      applyUsage(ev)
      return
    }
    const t = live()
    if (!t) return
    t.lastActivityAt = Date.now()
    if (ev.type === 'token' && typeof ev.text === 'string') {
      // 进帧缓冲：同一帧内到达的多个 token 合并成一次响应式提交
      let buf = streamBuffers.get(t.id)
      if (!buf) { buf = { turn: t, text: '', reasoning: '' }; streamBuffers.set(t.id, buf) }
      buf.text += ev.text
      scheduleStreamFlush()
    } else if (ev.type === 'final' && typeof ev.text === 'string' && ev.text) {
      streamBuffers.delete(t.id)
      t.text = ev.text
      // 立即停止节流渲染并预热完成态 HTML，避免最后一帧与成稿之间格式闪一下
      finishLiveMd(t.id, () => answerHtml(t))
    } else if (ev.type === 'reasoning' && typeof ev.text === 'string') {
      // 深度思考流：实时拼接到独立的思考窗口（与正文分开），同样按帧合批
      let buf = streamBuffers.get(t.id)
      if (!buf) { buf = { turn: t, text: '', reasoning: '' }; streamBuffers.set(t.id, buf) }
      buf.reasoning += ev.text
      if (!reasonOpen.value.has(t.id)) {
        reasonOpen.value = new Set([...reasonOpen.value, t.id])
      }
      scheduleStreamFlush()
    } else if (ev.type === 'notice' && ev.text) {
      t.notices.push(ev.text)
    } else if (ev.type === 'plan' && Array.isArray(ev.steps)) {
      t.plan = ev.steps
    } else if (ev.type === 'thought' || ev.type === 'action' ||
               ev.type === 'observation' || ev.type === 'reflection') {
      if (ev.text) appendTrace(t, ev.type, ev.text)
    } else if (ev.type === 'workflow') {
      // start_workflow 已执行：卡片直接挂在当前助手回合内，后续由卡片自己连 SSE。
      const w = ev as unknown as { workflow_id?: string; status?: string; phase?: string; kind?: string; request?: string }
      if (w.workflow_id && !t.workflow?.workflowId) {
        t.workflow = {
          workflowId: w.workflow_id,
          seed: { status: w.status, phase: w.phase, kind: w.kind, request: w.request },
        }
      }
    }
    // 结构类事件（计划/工具轨迹/通知）出现时跟随一次；token 滚动已在帧合批里处理
    if (ev.type !== 'token' && ev.type !== 'reasoning') scheduleFollow()
  }

  try {
    // 不支持思考的模型不传思考参数（null）；native 模型恒开，也无需显式传
    const thinkingOpt = thinkingSupported.value
      ? (thinkingNative.value ? null : thinkingOn.value)
      : null
    await aiApi.askGrounded(request, { onEvent, signal: ac.signal }, {
      web: webOn.value,
      thinking: thinkingOpt,
      images: imgs,
    })
    drainStreamNow()
    const t = live()
    if (t) {
      t.status = t.text ? 'done' : 'stopped'
      t.finishedAt = Date.now()
      finishLiveMd(t.id, () => answerHtml(t))
      finished = !!t.text
    }
  } catch (e) {
    drainStreamNow()
    const t = live()
    if (!t) return false
    finishLiveMd(t.id, () => answerHtml(t))
    if ((e as Error).name === 'AbortError') {
      t.status = 'stopped'
      t.finishedAt = Date.now()
      if (!t.text) t.text = '（已停止）'
    } else {
      t.status = 'error'
      t.finishedAt = Date.now()
      t.error = (e as { message?: string }).message || '请求失败'
    }
  } finally {
    if (epoch === chatEpoch) {
      sending.value = false; abortCtl = null
      // 问答可能由主 Agent 经 start_workflow 工具触发工作流；若撞上同项目互斥，
      // 错误只体现在回答文本里，这里补一次探测，让孤儿恢复条给出可操作入口。
      void detectOrphanWorkflow()
    }
    void refreshSessionList()
    await nextTick(scrollToBottom)
  }
  return finished
}

function stop() {
  // 主动停止也保存现场，便于用户之后点击“继续上次任务”。
  preserveInterrupted()
  drainStreamNow()
  const turn = messages.value[messages.value.length - 1]
  if (turn?.status === 'streaming' && !turn.workflow) turn.recoverable = true
  if (turn) finishLiveMd(turn.id, () => answerHtml(turn))
  if (turn?.status === 'streaming') {
    turn.status = 'stopped'
    turn.notices.push(turn.workflow
      ? '工作流启动已取消；若服务端已经创建，会自动中断。'
      : '回答已中断；已执行的工具操作不会自动撤销。')
  }
  ++chatEpoch
  abortCtl?.abort()
  abortCtl = null
  sending.value = false
}
function preserveInterrupted() {
  if (!sending.value) return
  const assistant = messages.value[messages.value.length - 1]
  const user = messages.value[messages.value.length - 2]
  try {
    sessionStorage.setItem(recoveryKey(), JSON.stringify({
      user: user?.text || '', assistant: assistant?.text || '',
      trace: assistant?.trace?.slice(-24) || [], reasoning: assistant?.reasoning || '',
      plan: assistant?.plan?.slice(-12) || [], startedAt: assistant?.startedAt,
    } satisfies InterruptedRecovery))
  } catch {}
}

function addImages(files: FileList | File[]) {
  const incoming = Array.from(files || [])
  const valid = incoming.filter((f) => /^image\/(png|jpeg|webp|gif)$/i.test(f.type))
  if (valid.length !== incoming.length) attachmentError.value = '仅支持 PNG、JPEG、WEBP、GIF 图片。'
  const merged = [...pendingImages.value, ...valid].slice(0, 4)
  if (pendingImages.value.length + valid.length > 4) attachmentError.value = '单条消息最多附带 4 张图片。'
  pendingImages.value = merged
}
function removeImage(index: number) { pendingImages.value.splice(index, 1) }
function onImagePick(ev: Event) {
  const input = ev.target as HTMLInputElement
  if (input.files) addImages(input.files)
  input.value = ''
}
function onPaste(ev: ClipboardEvent) {
  const files = Array.from(ev.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'))
  if (files.length) { ev.preventDefault(); addImages(files) }
}
function onDrop(ev: DragEvent) {
  ev.preventDefault()
  if (ev.dataTransfer?.files?.length) addImages(ev.dataTransfer.files)
}
function onDragover(ev: DragEvent) { ev.preventDefault() }
async function onVideoPick(ev: Event) {
  const fileInput = ev.target as HTMLInputElement
  const file = fileInput.files?.[0]
  fileInput.value = ''
  if (!file || videoBusy.value) return
  videoBusy.value = true
  attachmentError.value = ''
  try {
    const result = await visionApi.analyzeVideo(file)
    if (!result.ok) throw new Error(result.error || '视频分析失败')
    const observation = (result.context || []).join('\n\n')
    input.value = observation
    await nextTick(() => inputEl.value?.focus())
  } catch (e) {
    attachmentError.value = (e as { message?: string }).message || '视频分析失败'
  } finally {
    videoBusy.value = false
  }
}
function restoreInterrupted(turns: { user: string; assistant: string }[]) {
  try {
    const row = JSON.parse(sessionStorage.getItem(recoveryKey()) || 'null')
    if (!row || typeof row.user !== 'string' || typeof row.assistant !== 'string') return
    if (turns.some(t => t.user === row.user && t.assistant)) { sessionStorage.removeItem(recoveryKey()); return }
    messages.value.push({ id: msgSeq++, role: 'user', text: cleanLegacyPrompt(row.user), status: 'done', trace: [], reasoning: '', notices: [], plan: [] })
    messages.value.push({ id: msgSeq++, role: 'assistant', text: cleanLegacyPrompt(row.assistant), status: 'stopped',
      trace: row.trace || [], reasoning: row.reasoning || '', plan: row.plan || [], recoverable: true,
      notices: ['上次回答因离开页面或切换项目中断，已保留原始问题和执行上下文；可以点击“继续上次任务”或输入“继续”。'] })
  } catch {}
}
function resetChatContext() {
  preserveInterrupted()
  stop()
  chatProject = getProjectId(); chatSession = getSessionId()
  messages.value = []; usage.value = null; historyError.value = ''
  orphanWf.value = null
  input.value = readDraft()
  void restoreHistory(true).then(() => detectOrphanWorkflow())
}
function onPageHide() { preserveInterrupted(); stop() }
function onBeforeLeave(e: BeforeUnloadEvent) { if (sending.value) { preserveInterrupted(); e.preventDefault(); e.returnValue = '' } }


function onKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault()
    void send()
  }
}

function clearMessages() {
  if (sending.value) stop()
  messages.value = []
  usage.value = null
  answerHtmlCache.clear()
  refsCache.clear()
  webRefsCache.clear()
  liveMdTimers.forEach(t => clearTimeout(t))
  liveMdTimers.clear()
  liveMdLastAt.clear()
  liveHtml.clear()
  // 卡片随消息一起卸载，不会再发 team 事件；这里同步清掉左下角成员抽屉，
  // 否则会残留指向已消失卡片的幽灵成员
  wfTeams.value = []
  gateOpenIds.value = new Set()
}

/** 头部「清空对话」：清空本地消息，并删除当前标签页会话在磁盘上的多轮历史。
 *  会话 id 每标签页独立（见 api.ts::getSessionId），故这里只清理本标签页自己的会话，
 *  不影响其它标签页；删除失败（无会话文件 / 服务未启动）不阻塞清空 UI。 */
async function clearConversation() {
  if (!(await confirmLeaveWorkflows())) return
  try { sessionStorage.removeItem(recoveryKey()) } catch {}
  clearMessages()
  if (demoMode.value) return
  try {
    await harnessApi.deleteSession(getSessionId())
  } catch {
    /* 会话文件不存在或服务不可达：忽略，本地已清空 */
  }
  void refreshSessionList()
}

function sessionTitle(item: SessionInfo): string {
  return (item.title || item.preview || '').trim() || '未命名对话'
}
function sessionTime(value: string): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const delta = Math.max(0, Date.now() - date.getTime())
  if (delta < 60_000) return '刚刚'
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)} 分钟前`
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`
  return date.toLocaleDateString(undefined, { month: 'numeric', day: 'numeric' })
}
async function refreshSessionList() {
  if (demoMode.value) return
  try {
    const result = await harnessApi.sessions()
    // 会话按页面域隔离：问答页会话 id 为 ask- 前缀，工作台历史不展示它们
    sessionItems.value = (Array.isArray(result.items) ? result.items : [])
      .filter(i => !String(i.session_id || '').startsWith('ask-'))
    sessionError.value = ''
  } catch (e) {
    sessionError.value = (e as Error).message || '会话历史加载失败'
  }
}
async function prepareSessionChange(action: string): Promise<boolean> {
  if (!(await confirmLeaveWorkflows())) return false
  if (!sending.value && !messages.value.length) return true
  return askConfirm({
    title: action,
    message: `当前对话会保留在会话历史中，${action}后将显示另一段对话。继续？`,
    confirmText: '继续',
  })
}
async function switchSession(id: string) {
  const target = String(id || '').trim()
  if (!target || target === chatSession || sessionBusy.value) {
    sessionOpen.value = false
    return
  }
  if (!(await prepareSessionChange('切换会话'))) return
  sessionBusy.value = true
  sessionError.value = ''
  stop()
  try {
    const claimed = await setSessionId(target)
    if (!claimed) throw new Error('这个会话正在其他标签页使用，无法在当前标签切换。')
    chatProject = getProjectId()
    chatSession = target
    clearMessages()
    try { sessionStorage.removeItem(recoveryKey()) } catch {}
    input.value = readDraft()
    sessionOpen.value = false
    await restoreHistory(true)
    await loadContextUsage()
  } catch (e) {
    sessionError.value = (e as Error).message || '切换会话失败'
  } finally {
    sessionBusy.value = false
  }
}
async function createConversation() {
  if (!(await prepareSessionChange('新建对话'))) return
  sessionBusy.value = true
  sessionError.value = ''
  stop()
  try {
    const next = await startNewSession()
    if (!next) throw new Error('无法创建新的独立会话，请刷新页面重试。')
    chatProject = getProjectId()
    chatSession = next
    clearMessages()
    input.value = ''
    pendingImages.value = []
    attachmentError.value = ''
    try { sessionStorage.removeItem(recoveryKey()) } catch {}
    sessionOpen.value = false
    orphanWf.value = null
    await refreshSessionList()
    void detectOrphanWorkflow()
    await nextTick(() => inputEl.value?.focus())
  } catch (e) {
    sessionError.value = (e as Error).message || '新建对话失败'
  } finally {
    sessionBusy.value = false
  }
}

async function loadContextUsage() {
  if (demoMode.value) return
  try {
    const u = await contextApi.get()
    if (u) usage.value = u
  } catch {
    /* 未启动/无会话时不显示指示即可 */
  }
}

/** 删除会话历史中的某一条；当前会话走 clearConversation（含工作流离开确认）。 */
async function deleteSessionItem(id: string) {
  const target = String(id || '').trim()
  if (!target) return
  if (target === chatSession) {
    sessionOpen.value = false
    await clearConversation()
    return
  }
  if (sessionBusy.value) return
  const ok = await askConfirm({
    title: '删除对话',
    message: '确定删除这段对话历史？此操作不可恢复。',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  sessionBusy.value = true
  sessionError.value = ''
  try {
    const r = await harnessApi.deleteSession(target)
    if (r?.ok === false) throw new Error('服务端拒绝删除')
    await refreshSessionList()
  } catch (e) {
    sessionError.value = (e as Error).message || '删除失败'
  } finally {
    sessionBusy.value = false
  }
}

// ---------------------------------------------------------------- 工作流历史
// 快捷任务（不走对话 turns）与已结束工作流只落盘在 .docmind/workflows/，
// 会话历史里永远找不到它们。这里提供独立弹层：查看（挂回卡片）、中断运行中、
// 删除终态并清理磁盘文件。
const WF_TERMINAL = new Set(['completed', 'failed', 'interrupted'])
const wfHistoryOpen = ref(false)
const wfHistoryItems = ref<WorkflowSummary[]>([])
const wfHistoryBusy = ref(false)
const wfHistoryError = ref('')
const wfHistoryActingId = ref('')

function wfStatusLabel(status: string): string {
  return WF_STATUS_LABEL[status] || status
}
function wfTerminal(item: WorkflowSummary): boolean {
  return WF_TERMINAL.has(item.status)
}
function wfDotKind(item: WorkflowSummary): 'run' | 'ok' | 'err' | 'stop' {
  if (item.status === 'completed') return 'ok'
  if (item.status === 'failed') return 'err'
  if (item.status === 'interrupted') return 'stop'
  return 'run'
}
async function refreshWfHistory() {
  if (demoMode.value) return
  wfHistoryBusy.value = true
  wfHistoryError.value = ''
  try {
    const r = await agentApi.workflowList()
    wfHistoryItems.value = Array.isArray(r.items) ? r.items : []
  } catch (e) {
    wfHistoryError.value = (e as Error).message || '工作流历史加载失败'
  } finally {
    wfHistoryBusy.value = false
  }
}
function openWfHistory() {
  wfHistoryOpen.value = true
  void refreshWfHistory()
}
/** 把历史工作流挂回当前对话：先拉完整状态，再插入一张只读/可继续的卡片。 */
async function reattachWfHistory(item: WorkflowSummary) {
  if (wfHistoryActingId.value) return
  wfHistoryActingId.value = item.workflow_id
  try {
    const r = await agentApi.workflow(item.workflow_id)
    const wf = r.workflow
    if (r.ok === false || !wf) throw new Error(r.error || '工作流状态读取失败')
    wfHistoryOpen.value = false
    if (!messages.value.some(m => m.workflow?.workflowId === item.workflow_id)) {
      messages.value.push({
        id: msgSeq++, role: 'assistant', text: '', status: 'done',
        trace: [], reasoning: '', notices: [], plan: [],
        workflow: { workflowId: item.workflow_id, seed: wf },
        startedAt: Date.now(),
      })
      stickToBottom.value = true
      await nextTick(scrollToBottom)
    }
  } catch (e) {
    wfHistoryError.value = (e as Error).message || '挂回失败'
  } finally {
    wfHistoryActingId.value = ''
  }
}
async function interruptWfHistory(item: WorkflowSummary) {
  if (wfHistoryActingId.value) return
  const ok = await askConfirm({
    title: '中断工作流',
    message: '确定中断这个工作流？已产生的文件改动不会自动回滚。',
    confirmText: '中断',
    danger: true,
  })
  if (!ok) return
  wfHistoryActingId.value = item.workflow_id
  wfHistoryError.value = ''
  try {
    const r = await agentApi.workflowInterrupt(item.workflow_id, '用户在工作流历史中中断')
    if (r.ok === false) throw new Error(r.error || '中断失败')
    await refreshWfHistory()
    void detectOrphanWorkflow()
  } catch (e) {
    wfHistoryError.value = (e as Error).message || '中断失败'
  } finally {
    wfHistoryActingId.value = ''
  }
}
async function deleteWfHistory(item: WorkflowSummary) {
  if (wfHistoryActingId.value) return
  const ok = await askConfirm({
    title: '删除工作流记录',
    message: '将删除该工作流的状态记录与磁盘文件，此操作不可恢复。确定删除？',
    confirmText: '删除',
    danger: true,
  })
  if (!ok) return
  wfHistoryActingId.value = item.workflow_id
  wfHistoryError.value = ''
  try {
    const r = await agentApi.workflowDelete(item.workflow_id)
    if (r.ok === false) throw new Error(r.error || '删除失败')
    wfHistoryItems.value = wfHistoryItems.value.filter(i => i.workflow_id !== item.workflow_id)
  } catch (e) {
    wfHistoryError.value = (e as Error).message || '删除失败'
  } finally {
    wfHistoryActingId.value = ''
  }
}

// ---------------------------------------------------------------- 历史回灌
/** 把服务端 turns 按「用户→助手」交替重建成消息列表（历史消息均为已完成态）。 */
function rebuildFromTurns(turns: { user: string; assistant: string }[]) {
  const rebuilt: ChatMsg[] = []
  for (const t of turns) {
    if (!t) continue
    if (t.user) {
      rebuilt.push({ id: msgSeq++, role: 'user', text: cleanLegacyPrompt(t.user), status: 'done', trace: [], reasoning: '', notices: [], plan: [] })
    }
    if (t.assistant) {
      rebuilt.push({ id: msgSeq++, role: 'assistant', text: cleanLegacyPrompt(t.assistant), status: 'done', trace: [], reasoning: '', notices: [], plan: [] })
    }
  }
  // msgSeq 已随分配递增，天然大于历史消息最大 id，后续新消息 id 不会冲突。
  messages.value = rebuilt
  return rebuilt.length
}

/** 只恢复当前项目的当前会话，绝不自动续接其他历史。 */
async function restoreHistory(force = false) {
  if (demoMode.value) return
  if (sending.value) return
  if (!force && messages.value.length) return
  // 记住进入时的消息数：await 期间用户可能已发送新消息（SSE 正在流），
  // 重建会整体覆盖 messages 并让 live() 匹配不到、静默丢流，故 await 后需复检。
  const project = getProjectId(), session = getSessionId(), epoch = chatEpoch
  const before = messages.value.length
  try {
    const detail = await harnessApi.sessionDetail(session)
    if (project !== getProjectId() || session !== getSessionId() || epoch !== chatEpoch) return
    const turns = detail.turns || []
    // Never silently load a different conversation into a fresh session.
    if (force && !turns.length && !sending.value) messages.value = []
    if (turns.length) {
      // 竞态兜底：await 窗口内若有新消息涌入（或正在发送），放弃本次回灌，
      // 绝不覆盖用户正在进行的对话；force 切换只受 sending 拦截。
      if (sending.value || (!force && messages.value.length !== before)) return
      rebuildFromTurns(turns)
      stickToBottom.value = true
      await nextTick(scrollToBottom)
    }
    restoreInterrupted(turns)
  } catch (e) {
    if (epoch === chatEpoch) { historyError.value = '历史加载失败：' + (e as Error).message; restoreInterrupted([]) }
  }
}

// ---------------------------------------------------------------- 离线演示问答
function demoAnswer(turnId: number, q: string): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(() => {
      const t = messages.value.find((m) => m.id === turnId)
      if (!t) return resolve()
      t.trace = [
        { type: 'thought', text: '先理解问题涉及的关键词，再到代码库里检索相关文件。' },
        { type: 'action', text: 'search_code：玩家受伤 / take_damage / health' },
        { type: 'observation', text: '命中 4 个文件，逐个阅读最相关的 2 个。' },
      ]
      t.text = demoReply(q)
      t.status = 'done'
      resolve()
    }, 900)
  })
}
function demoReply(q: string): string {
  if (q.includes('数值') || q.includes('受伤') || q.includes('血') || q.includes('伤害')) {
    return [
      '**（这是演示回答）** 连接你的真实项目后，AI 会先在代码库里检索，再给出文件+行号。',
      '',
      '以一个典型 Godot 项目为例，玩家受伤数值通常在这几处：',
      '',
      '1. **生命值定义**：`player_stats.gd` 里的 `max_health / health`（角色初始数值）',
      '2. **受伤计算**：`damage_calc.gd` 的 `take_damage(amount)`，先扣护甲、再扣血',
      '3. **数值配置**：策划常把数值放在 `resources/player_values.tres`，改数字不用改代码',
      '',
      '真实使用时，下面会出现可点击的文件卡片，点一下直接跳到对应行。',
    ].join('\n')
  }
  if (q.includes('结构') || q.includes('梳理')) {
    return [
      '**（演示回答）** 连接真实项目后，AI 会按目录和入口梳理出：',
      '',
      '- **入口**：主场景与初始化脚本',
      '- **核心系统**：角色、背包、战斗、存档各自的目录',
      '- **数据配置**：数值表 / 资源文件放在哪',
      '- 还可以点顶部「代码地图」看函数调用关系图',
    ].join('\n')
  }
  return [
    '**（演示回答）** 在本地版中，AI 会带着这个问题去搜你的代码库：读文件、追调用链，',
    '然后用大白话解释，并附上可点击的文件与行号。',
    '',
    '你可以换个更具体的问题试试，比如「玩家受伤扣多少血在哪算的？」。',
  ].join('\n')
}

// 概览页「去问问 / 试试问 AI」：展开对话台、（可选）预填问题、定位输入框
// 运行台「继续这段对话」：detail.reload=true 时按当前存储的会话 id 重新回灌历史
function onStartWorkflow(ev: Event) {
  const prompt = (ev as CustomEvent<{ prompt?: string }>).detail?.prompt?.trim()
  if (!prompt) return
  collapsed.value = false
  void startWorkflowTurn(prompt)
}
function onFocusChat(ev?: Event) {
  const detail = (ev as CustomEvent<{ q?: string; reload?: boolean }> | undefined)?.detail
  const q = detail?.q
  collapsed.value = false
  if (detail?.reload) {
    resetChatContext()
    void nextTick(() => inputEl.value?.focus())
    return
  }
  nextTick(() => {
    if (q) input.value = q
    inputEl.value?.focus()
  })
}
async function onSendChat(ev: Event) {
  const detail = (ev as CustomEvent<PreviewFeedbackRequest>).detail
  const prompt = detail?.prompt?.trim()
  if (!prompt) { detail?.onStatus?.('failed', '反馈不能为空'); return }
  if (detail.projectId !== getProjectId()) { detail.onStatus?.('pending', '项目已切换，请回到原项目重试'); return }
  if (sending.value || gateBlocking.value) {
    detail.onStatus?.('pending', sending.value ? 'AI 正忙，反馈已保存，请稍后重试' : '请先完成当前审核，再重试反馈')
    return
  }
  collapsed.value = false
  const images = (detail?.images || []).filter((image): image is Blob => image instanceof Blob)
  const files = images.map((image, index) => new File(
    [image], `live-preview-${Date.now()}-${index}.png`, { type: image.type || 'image/png' }))
  await nextTick()
  if (detail.projectId !== getProjectId() || sending.value || gateBlocking.value) {
    detail.onStatus?.('pending', '当前对话暂不可接收，请稍后在原项目重试')
    return
  }
  try {
    const finished = await send(prompt, files, () => detail.onStatus?.('processing'))
    detail.onStatus?.(finished ? 'awaiting_review' : 'failed', finished ? 'AI 回复已结束，请检查预览效果' : '本次对话未完成，已执行操作不会自动撤销，请检查后重试')
  } catch {
    detail.onStatus?.('failed', '本次对话未完成，请检查项目当前状态后重试')
  }
}
onMounted(() => {
  window.addEventListener('docmind:focus-chat', onFocusChat as EventListener)
  window.addEventListener('docmind:send-chat', onSendChat as EventListener)
  window.addEventListener('docmind:start-workflow', onStartWorkflow as EventListener)
  window.addEventListener('docmind:project-context-changed', resetChatContext)
  window.addEventListener('pagehide', onPageHide)
  window.addEventListener('beforeunload', onBeforeLeave)
  startTabProbe()   // 启动跨标签存活探测（供唯一性门禁判断）
  void loadModelConfig()
  void loadContextUsage()
  void refreshSessionList()
  // 历史回灌完成后再探测孤儿：避免本会话卡片已随历史逻辑存在时误报
  void restoreHistory().then(() => detectOrphanWorkflow())
})
onBeforeUnmount(() => {
  if (elapsedTimer !== null) { window.clearInterval(elapsedTimer); elapsedTimer = null }
  liveMdTimers.forEach(t => clearTimeout(t))
  liveMdTimers.clear()
  onPageHide()
  window.removeEventListener('docmind:focus-chat', onFocusChat as EventListener)
  window.removeEventListener('docmind:send-chat', onSendChat as EventListener)
  window.removeEventListener('docmind:start-workflow', onStartWorkflow as EventListener)
  window.removeEventListener('docmind:project-context-changed', resetChatContext)
  window.removeEventListener('pagehide', onPageHide)
  window.removeEventListener('beforeunload', onBeforeLeave)
})

function isNearBottom(el: HTMLElement) {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
function onScroll() {
  if (suppressScrollEvent) return
  const el = scroller.value
  if (el) stickToBottom.value = isNearBottom(el)
}
function onWheel(ev: WheelEvent) {
  // A user scrolling upward is an explicit request to inspect history. Keep
  // the stream running, but stop pulling the viewport back to the bottom.
  if (ev.deltaY < 0) stickToBottom.value = false
}
function followBottom() {
  const el = scroller.value
  if (el && stickToBottom.value) {
    suppressScrollEvent = true
    el.scrollTop = el.scrollHeight
    // 滚动事件在本帧内派发，下一帧解除即可；比每 token 排一个 setTimeout(0) 便宜
    requestAnimationFrame(() => { suppressScrollEvent = false })
  }
}
function scrollToBottom() { followBottom() }

// 流式输出合批：SSE 一个数据帧常常只有 1~2 个 token。逐 token 触发响应式更新会让
// Vue 每 token 重渲染整段答案（v-html 全量重建）并强制一次滚动布局——这就是输出
// 「一卡一卡」的根因。增量文本先进缓冲，由单个 rAF 在每帧最多提交一次、滚动一次。
interface StreamBuffer { turn: ChatMsg; text: string; reasoning: string }
const streamBuffers = new Map<number, StreamBuffer>()
let streamRaf = 0
function flushStream() {
  streamRaf = 0
  if (!streamBuffers.size) return
  for (const buf of streamBuffers.values()) {
    if (buf.text) { buf.turn.text += buf.text; scheduleLiveMd(buf.turn) }
    if (buf.reasoning) buf.turn.reasoning += buf.reasoning
  }
  streamBuffers.clear()
  // Vue 的 DOM 刷新也排在微任务里，这里挂在其后：读到的就是本帧最新布局，
  // 一次 scrollTop 写入完成跟随，不在 SSE 事件里做强制布局。
  queueMicrotask(followBottom)
}
function scheduleStreamFlush() {
  if (!streamRaf) streamRaf = requestAnimationFrame(flushStream)
}
/** 流结束时同步排空（取消未触发的帧回调），保证 final 文本不丢。 */
function drainStreamNow() {
  if (streamRaf) { cancelAnimationFrame(streamRaf); streamRaf = 0 }
  if (streamBuffers.size) {
    for (const buf of streamBuffers.values()) {
      if (buf.text) buf.turn.text += buf.text
      if (buf.reasoning) buf.turn.reasoning += buf.reasoning
    }
    streamBuffers.clear()
  }
}

// 流式期的 markdown 是「节流富文本」折中：纯文本层最省，但用户想边出边看排版。
// token 仍逐帧进 m.text（便宜），markdown 重渲染按消息限流到 ~8 次/秒——
// 这是人眼「格式实时跟随」与「不逐 token 重建 DOM」之间的平衡点。
const LIVE_MD_INTERVAL = 125
const liveHtml = reactive(new Map<number, string>())
const liveMdTimers = new Map<number, ReturnType<typeof setTimeout>>()
let liveMdLastAt = new Map<number, number>()
function renderLiveMd(turn: ChatMsg) {
  liveMdTimers.delete(turn.id)
  liveMdLastAt.set(turn.id, performance.now())
  liveHtml.set(turn.id, mdToHtml(turn.text || ''))
}
function scheduleLiveMd(turn: ChatMsg) {
  if (liveMdTimers.has(turn.id)) return
  // 首个 token 帧立即成型，避免节流窗口内 fallback 每帧解析
  if (!liveMdLastAt.has(turn.id)) { renderLiveMd(turn); return }
  const last = liveMdLastAt.get(turn.id) ?? 0
  const wait = Math.max(0, LIVE_MD_INTERVAL - (performance.now() - last))
  liveMdTimers.set(turn.id, setTimeout(() => renderLiveMd(turn), wait))
}
/** 回合结束（done/stop/error/final 覆盖）：立即排一次待渲染并交还完成态渲染。 */
function finishLiveMd(id: number, renderNow: () => string) {
  const timer = liveMdTimers.get(id)
  if (timer) { clearTimeout(timer); liveMdTimers.delete(id) }
  liveMdLastAt.delete(id)
  liveHtml.delete(id)
  // 触发 answerHtml 缓存写入，完成态 v-html 与最后一帧不会出现格式回退
  renderNow()
}
function streamHtmlOf(msg: ChatMsg): string {
  return liveHtml.get(msg.id) ?? mdToHtml(msg.text || '')
}

// 工作流卡片自身高频事件（子代理每步都 emit activity）也按帧合批跟随，
// 避免多代理并行时每步一次 nextTick + 强制布局。
let followRaf = 0
function scheduleFollow() {
  if (!followRaf) followRaf = requestAnimationFrame(() => {
    followRaf = 0
    queueMicrotask(followBottom)
  })
}
function resumeAutoScroll() {
  stickToBottom.value = true
  void nextTick(scrollToBottom)
}

// ---------------------------------------------------------------- 答案引用卡片
// 模板每秒会因计时刷新重渲染；markdown 与引用提取都按 (消息 id, 文本) 缓存，
// 文本不变时不做重复解析（流式期间走纯文本层，根本不进 markdown 解析）。
const answerHtmlCache = new Map<number, { src: string; html: string }>()
function answerHtml(msg: ChatMsg): string {
  const hit = answerHtmlCache.get(msg.id)
  if (hit && hit.src === msg.text) return hit.html
  const html = mdToHtml(msg.text || '')
  answerHtmlCache.set(msg.id, { src: msg.text, html })
  return html
}
const refsCache = new Map<number, { src: string; refs: FileRef[] }>()
function refsOf(msg: ChatMsg): FileRef[] {
  // 只对真实存在于文件树中的路径生成卡片（过滤幻觉引用）
  const hit = refsCache.get(msg.id)
  if (hit && hit.src === msg.text) return hit.refs
  const refs = extractFileRefs(msg.text).filter((r) => nodeExists(r.path))
  refsCache.set(msg.id, { src: msg.text, refs })
  return refs
}

const webRefsCache = new Map<number, { src: string; refs: ReturnType<typeof extractWebRefs> }>()
function webRefsOf(msg: ChatMsg) {
  const hit = webRefsCache.get(msg.id)
  if (hit && hit.src === msg.text) return hit.refs
  const refs = extractWebRefs(msg.text)
  webRefsCache.set(msg.id, { src: msg.text, refs })
  return refs
}

async function openRef(r: FileRef) {
  await jumpToLine(r.path, r.line || 1)
  revealPath(r.path)
}

const TRACE_LABEL: Record<string, string> = {
  thought: '分析摘要', action: '执行动作', observation: '返回结果', reflection: '复核与重试',
}
const TRACE_GLYPH: Record<string, string> = {
  thought: '◌', action: '↗', observation: '✓', reflection: '↻',
}
const activityOpen = ref<Set<string>>(new Set())
const nowTick = ref(Date.now())
let elapsedTimer: number | null = null
// 计时钟只在「有活动回合」时走：空闲/全部结束后不再每 500ms 触发响应式刷新。
// 工作流回合在工作流终结时由 onWfTeam 写入 finishedAt，秒表随之定格。
const elapsedTicking = computed(() =>
  sending.value
  || messages.value.some(m => m.status === 'streaming' || (m.workflow?.workflowId && !m.finishedAt))
  || activeWorkflowIds.value.length > 0)
watch(elapsedTicking, (on) => {
  if (on && elapsedTimer === null) {
    nowTick.value = Date.now()
    elapsedTimer = window.setInterval(() => { nowTick.value = Date.now() }, 500)
  } else if (!on && elapsedTimer !== null) {
    window.clearInterval(elapsedTimer)
    elapsedTimer = null
    nowTick.value = Date.now()
  }
})

function appendTrace(msg: ChatMsg, type: string, text: string) {
  const at = Date.now()
  // 观察/反思通常是前一个工具动作的返回，将两者之间的时间显示为动作耗时。
  if (type === 'observation' || type === 'reflection') {
    const pending = [...msg.trace].reverse().find((item) => item.type === 'action' && !item.elapsedMs)
    if (pending?.at) pending.elapsedMs = Math.max(0, at - pending.at)
  }
  msg.trace.push({ type, text, at })
}

function activityKey(id: number, index: number) { return `${id}:${index}` }
function toggleActivity(id: number, index: number) {
  const key = activityKey(id, index)
  const next = new Set(activityOpen.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  activityOpen.value = next
}
function activityIsOpen(id: number, index: number, msg: ChatMsg) {
  return activityOpen.value.has(activityKey(id, index)) || (msg.status === 'streaming' && index === msg.trace.length - 1)
}
function traceTitle(item: { type: string; text: string }) {
  const raw = item.text.trim()
  const tool = raw.match(/^([\w.-]+)\s*\(/)?.[1]
  if (item.type === 'action' && tool) {
    const names: Record<string, string> = {
      search_code: '检索代码', search_knowledge: '检索知识库', read_file: '读取文件',
      grep: '定位代码', run_command: '运行命令', game_playtest: '运行校验',
      web_search: '网页搜索', web_fetch: '读取网页', dev_mcp_call: '调用 MCP',
      dev_list_connector_tools: '查看 MCP 工具', apply_edit: '修改文件', create_file: '创建文件',
      delegate: '委派 Subagent', orchestrate: '编排任务',
    }
    return names[tool] || tool
  }
  return TRACE_LABEL[item.type] || item.type
}
function traceSummary(item: { type: string; text: string }) {
  const raw = item.text.trim().replace(/\s+/g, ' ')
  if (item.type === 'action') {
    const open = raw.indexOf('(')
    if (open > 0) return raw.slice(open).replace(/\s*\[已拦截.*$/, '')
  }
  if (raw.length <= 92) return raw
  return raw.slice(0, 89) + '…'
}
function traceState(msg: ChatMsg, item: { type: string; text: string }, index: number): 'running' | 'ok' | 'warn' | 'error' {
  const raw = item.text
  if (/重复调用|重复空转/.test(raw)) return 'warn'
  if (/失败|错误|超时|拦截|阻断|未执行|不可用|exception/i.test(raw)) return 'error'
  if (item.type === 'reflection' && /重试|换思路|重新规划|注意/i.test(raw)) return 'warn'
  if (msg.status === 'streaming' && index === msg.trace.length - 1) return 'running'
  return 'ok'
}
function traceStateLabel(state: ReturnType<typeof traceState>, item?: { text: string }) {
  if (state === 'warn' && item && /重复调用|重复空转/.test(item.text)) return '已拦截'
  return state === 'running' ? '进行中' : state === 'error' ? '失败' : state === 'warn' ? '需关注' : '完成'
}
function traceElapsed(item: { elapsedMs?: number }) {
  if (!item.elapsedMs) return ''
  if (item.elapsedMs < 1000) return `${item.elapsedMs}ms`
  return `${(item.elapsedMs / 1000).toFixed(1)}s`
}
function elapsedLabel(msg: ChatMsg) {
  if (!msg.startedAt) return ''
  const end = msg.finishedAt || nowTick.value
  const seconds = Math.max(0, (end - msg.startedAt) / 1000)
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`
}
function slowResponseLabel(msg: ChatMsg) {
  if (msg.status !== 'streaming' || !msg.lastActivityAt) return ''
  const quiet = Math.max(0, nowTick.value - msg.lastActivityAt)
  if (quiet < 15000) return ''
  return `已经 ${Math.round(quiet / 1000)} 秒没有收到新进展；模型可能仍在推理或执行工具。现场会保留，必要时可停止后继续。`
}

// 深度思考面板折叠态（reasoning_content 独立窗口）
const reasonOpen = ref<Set<number>>(new Set())
function toggleReason(id: number) {
  const next = new Set(reasonOpen.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  reasonOpen.value = next
}

// 思考芯片：仅 toggle 家族可点；native 恒开；none 不渲染可点按钮
function toggleThinking() {
  if (!thinkingSupported.value || thinkingNative.value) return
  thinkingOn.value = !thinkingOn.value
}
const thinkingTitle = computed(() => {
  if (thinkingMode.value === 'unknown') return '深度思考能力待确认，请在模型设置中选择能力'
  if (!thinkingSupported.value) return '当前模型不支持深度思考，可点左侧模型芯片切换支持思考的模型'
  if (thinkingNative.value) return '该模型内置深度思考，始终开启'
  return thinkingOn.value ? '深度思考已开启：回答前先推理，耗时更长' : '点击开启深度思考'
})
const webTitle = computed(() => webOn.value
  ? '联网搜索已开启：AI 可检索互联网获取最新信息'
  : '联网搜索已关闭：仅检索当前代码库，点击开启')

// ---------------------------------------------------------------- 引擎 / MCP 弹层
const enginePopOpen = ref(false)
const servers = ref<McpServer[]>([])
const probing = ref<string | null>(null)
const probeResults = ref<Record<string, { ok: boolean; tool_count?: number; error?: string }>>({})
const connected = ref<Record<string, boolean>>({})
const addon = ref<Awaited<ReturnType<typeof mcpApi.addonStatus>> | null>(null)
const installing = ref(false)
const showAddForm = ref(false)
const savingServer = ref(false)
const newServer = ref<{ key: string; label: string; transport: 'stdio' | 'http'; command: string; url: string; enabled: boolean }>({
  key: '', label: '', transport: 'stdio', command: '', url: '', enabled: true,
})

async function refreshEnginePop() {
  try {
    const [s, a, st] = await Promise.allSettled([mcpApi.servers(), mcpApi.addonStatus(), mcpApi.status()])
    if (s.status === 'fulfilled') servers.value = s.value.servers
    if (a.status === 'fulfilled') addon.value = a.value
    if (st.status === 'fulfilled' && st.value.ok) {
      const active = st.value.active || []
      const next: Record<string, boolean> = {}
      for (const k of active) next[k] = true
      connected.value = next
    }
  } catch {
    /* 弹层打开失败保持空态 */
  }
}

watch(enginePopOpen, (open) => {
  if (open) void refreshEnginePop()
})

// Esc 关闭弹层
function onDocKey(ev: KeyboardEvent) {
  if (ev.key === 'Escape' && enginePopOpen.value) {
    enginePopOpen.value = false
  }
}
if (typeof window !== 'undefined') window.addEventListener('keydown', onDocKey)
onBeforeUnmount(() => window.removeEventListener('keydown', onDocKey))

async function probe(key: string) {
  probing.value = key
  try {
    const r = await Promise.race([
      mcpApi.probe(key),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error('连接超时：请先在 Godot 编辑器中打开项目并启用 godot-ai 插件')), 8000)),
    ])
    const ok = !!r.ok && !(r as { error?: string }).error
    probeResults.value[key] = { ok, tool_count: r.tool_count, error: r.error }
    // stdio 为长驻会话，连接成功后保持「已连接」；HTTP 无状态，不持有会话
    if (ok && servers.value.find((x) => x.key === key)?.transport === 'stdio') connected.value[key] = true
  } catch (e) {
    probeResults.value[key] = { ok: false, error: (e as Error).message }
  } finally {
    probing.value = null
  }
}

async function installAddon() {
  const st = addon.value
  const ok = await askConfirm({
    title: st?.installed ? '重装 godot-ai 插件？' : '安装 godot-ai 插件',
    message: st?.installed
      ? '将从 GitHub Releases 重新下载并覆盖 addons/godot_ai/，并改写 project.godot 的插件启用项。'
      : '将从 GitHub Releases 下载最新版插件到 addons/godot_ai/，并在 project.godot 中启用它。',
    detail: '不会改动其它代码文件。安装后需打开/重启 Godot 编辑器一次，AI 对话台才能通过 MCP 连接引擎。',
    confirmText: st?.installed ? '覆盖安装' : '安装',
  })
  if (!ok) return
  installing.value = true
  try {
    const r = await mcpApi.addonInstall(!!st?.installed)
    await askAlert({
      title: r.ok ? 'godot-ai 安装完成' : 'godot-ai 安装失败',
      message: r.ok ? `插件 v${r.version} 已安装并启用。${r.next || ''}` : (r.error || '未知错误'),
    })
    if (r.ok) await refreshEnginePop()
  } catch (e) {
    await askAlert({ title: 'godot-ai 安装失败', message: (e as Error).message })
  } finally {
    installing.value = false
  }
}

function resultOf(s: McpServer) {
  return probeResults.value[s.key]
}

async function closeServer(key: string) {
  try {
    await mcpApi.close(key)
  } catch {
    /* 断开失败保持原状 */
  } finally {
    connected.value[key] = false
  }
}

async function removeServer(key: string) {
  const ok = await askConfirm({
    title: '移除连接器',
    message: `确定移除「${key}」？内置预设会被禁用，自定义项会被删除。`,
    confirmText: '移除',
  })
  if (!ok) return
  try {
    await mcpApi.remove(key)
    connected.value[key] = false
    await refreshEnginePop()
  } catch {
    /* 移除失败保持原状 */
  }
}

async function addServer() {
  const ns = newServer.value
  if (!ns.key) return
  savingServer.value = true
  try {
    const cfg: Record<string, unknown> = { transport: ns.transport, enabled: ns.enabled }
    if (ns.label) cfg.label = ns.label
    if (ns.transport === 'stdio') cfg.command = ns.command
    else cfg.url = ns.url
    const r = await mcpApi.save(ns.key, cfg)
    if (!r.ok) {
      await askAlert({ title: '新增连接器失败', message: r.error || '未知错误' })
      return
    }
    showAddForm.value = false
    newServer.value = { key: '', label: '', transport: 'stdio', command: '', url: '', enabled: true }
    await refreshEnginePop()
  } catch (e) {
    await askAlert({ title: '新增连接器失败', message: (e as Error).message })
  } finally {
    savingServer.value = false
  }
}
function connectorGuide(s: McpServer) {
  if (s.key.includes('godot')) return '让 AI 读取场景、节点和运行日志；请先打开 Godot 项目。'
  if (s.key.includes('unity')) return '让 AI 查看 Unity 场景和组件；需先启动 Unity 并运行 MCP 插件。'
  if (s.key.includes('unreal')) return '让 AI 查询 Unreal 资产、Actor 和蓝图；需先启动 Editor Bridge。'
  return '让 AI 连接外部工具并调用其能力。'
}
</script>

<template>
  <section
    class="cd-dock"
    :class="{
      'cd-collapsed': collapsed,
      'cd-expanded': expanded && !collapsed,
      'cd-clipping': collapsed,
      'cd-gate-blocked': gateBlocking,
    }"
  >
    <header class="cd-head">
      <!-- 折叠/收起只响应明确的标题热区，避免选中提示文字等误触把面板收起来 -->
      <span
        class="cd-head-toggle"
        role="button"
        :aria-expanded="!collapsed"
        :title="collapsed ? '展开对话台' : '收起对话台'"
        tabindex="0"
        @click="toggleDock"
        @keydown.enter.prevent="toggleDock"
        @keydown.space.prevent="toggleDock"
      >
        <span class="cd-chevron" :class="{ rotated: !collapsed }">
          <svg width="9" height="9" viewBox="0 0 9 9"><path d="M2 1.5 L5.5 4.5 L2 7.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
        </span>
        <span class="cd-title">AI 助手</span>
        <span v-if="sending" class="cd-live">AI 正在查代码<span class="cd-dots">…</span></span>
        <!-- 折叠态也要感知后台进展：工作流执行期 sending 已释放，靠成员抽屉状态补指示 -->
        <span v-if="collapsed && activeWorkflowIds.length" class="cd-live cd-live-wf">
          <i class="cd-live-dot" aria-hidden="true" />工作流进行中
        </span>
      </span>
      <span class="cd-hint">用大白话问代码：数值在哪 · 逻辑怎么走 · 报错怎么改</span>
      <span class="cd-spacer" />
      <button
        class="cd-btn"
        :class="{ 'cd-btn-on': sessionOpen }"
        title="查看会话历史或开始新对话"
        @click.stop="sessionOpen = !sessionOpen; sessionOpen && refreshSessionList()"
      >
        <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
          <path d="M2 2.2h9v6.4H6.4L3.2 11V8.6H2z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
          <path d="M4.1 4.4h4.8M4.1 6.2h3.5" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
        </svg>
        <span>会话</span>
      </button>
      <button class="cd-btn cd-new-session" title="开始一段新的独立对话" @click.stop="createConversation">
        <span aria-hidden="true">＋</span><span>新对话</span>
      </button>
      <button
        class="cd-btn"
        :class="{ 'cd-btn-on': wfHistoryOpen }"
        title="工作流历史：查看进度、中断运行中的工作流、删除已结束记录"
        @click.stop="wfHistoryOpen ? (wfHistoryOpen = false) : openWfHistory()"
      >
        <svg width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
          <path d="M2.5 3.2h8v3.2h-8z M2.5 8h5v2.6h-5z" fill="none" stroke="currentColor" stroke-width="1.05" stroke-linejoin="round"/>
          <path d="M8.7 8.2l2.1 1.2-2.1 1.2z" fill="currentColor"/>
        </svg>
        <span>工作流</span>
      </button>
      <button
        class="cd-btn"
        :class="{ 'cd-btn-on': enginePopOpen }"
        title="连接游戏引擎（Godot / Unity / Unreal）：连上后 AI 能读场景和运行日志"
        @click.stop="enginePopOpen = !enginePopOpen"
      >
        <svg width="14" height="14" viewBox="0 0 14 14">
          <path d="M5 1.5 H1.5 V5 M9 1.5 H12.5 V5 M5 12.5 H1.5 V9 M9 12.5 H12.5 V9" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          <circle cx="7" cy="7" r="2.1" fill="none" stroke="currentColor" stroke-width="1.2"/>
        </svg>
        <span>引擎</span>
      </button>
      <button
        class="cd-btn cd-size-btn"
        :class="{ 'cd-btn-on': expanded }"
        :aria-pressed="expanded"
        :title="expanded ? '收窄对话区' : '展开对话区，查看更多回答内容'"
        @click.stop="expanded = !expanded"
      >
        <svg v-if="!expanded" width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
          <path d="M2 5 V2 H5 M8 2 H11 V5 M11 8 V11 H8 M5 11 H2 V8" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <svg v-else width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
          <path d="M5 2 H2 V5 M8 2 H11 V5 M11 8 V11 H8 M5 11 H2 V8" fill="none" stroke="currentColor" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <span>{{ expanded ? '收窄' : '展开' }}</span>
      </button>
      <button class="cd-btn" title="清空对话" @click.stop="clearConversation">
        <svg width="13" height="13" viewBox="0 0 13 13"><path d="M2.5 3.2 H10.5 M5.2 3.2 V2 Q5.2 1.5 5.7 1.5 H7.3 Q7.8 1.5 7.8 2 V3.2 M3.4 3.2 L3.8 11 Q3.8 11.6 4.4 11.6 H8.6 Q9.2 11.6 9.2 11 L9.6 3.2" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </header>

    <!-- 会话历史弹层：Teleport 到 body，折叠态（33px + overflow:hidden）下也能完整显示，
         且不会把输入栏挤出面板漏到编辑区 -->
    <Teleport to="body">
    <div v-if="sessionOpen" class="cd-pop-mask" @click="sessionOpen = false" />
    <div v-if="sessionOpen" class="cd-session-pop" @click.stop>
      <div class="cd-session-head">
        <div>
          <div class="cd-pop-title">会话历史</div>
          <div class="cd-session-subtitle">当前项目的对话会单独保存，切换后会恢复完整问答。</div>
        </div>
        <button class="cd-mini" :disabled="sessionBusy" title="刷新会话列表" @click="refreshSessionList">刷新</button>
      </div>
      <button class="cd-session-new" :disabled="sessionBusy" @click="createConversation">
        <span class="cd-session-new-icon">＋</span>
        <span><b>新建对话</b><small>开启一段空白会话，不影响历史记录</small></span>
      </button>
      <div v-if="sessionError" class="cd-session-error">{{ sessionError }}</div>
      <div v-if="!sessionItems.length && !sessionError" class="cd-session-empty">还没有已保存的对话</div>
      <div v-for="item in sessionItems" :key="item.session_id" class="cd-session-item" :class="{ active: item.session_id === chatSession }">
        <button class="cd-session-main" :disabled="sessionBusy" @click="switchSession(item.session_id)">
          <span class="cd-session-current" aria-hidden="true">{{ item.session_id === chatSession ? '●' : '○' }}</span>
          <span class="cd-session-copy">
            <strong>{{ sessionTitle(item) }}</strong>
            <small>{{ item.turns }} 轮<span v-if="sessionTime(item.updated_at)"> · {{ sessionTime(item.updated_at) }}</span></small>
          </span>
        </button>
        <button
          class="cd-session-delete"
          :title="item.session_id === chatSession ? '清空当前会话' : '删除这条对话历史'"
          :disabled="sessionBusy"
          @click.stop="deleteSessionItem(item.session_id)"
        >×</button>
      </div>
    </div>
    </Teleport>

    <!-- 工作流历史弹层：快捷任务/已结束工作流不在会话历史里，单独一个入口。
         同样 Teleport 到 body，避免折叠态裁剪。 -->
    <Teleport to="body">
    <div v-if="wfHistoryOpen" class="cd-pop-mask" @click="wfHistoryOpen = false" />
    <div v-if="wfHistoryOpen" class="cd-session-pop cd-wf-pop" @click.stop>
      <div class="cd-session-head">
        <div>
          <div class="cd-pop-title">工作流历史</div>
          <div class="cd-session-subtitle">当前项目发起过的工作流。点击条目可在对话中打开卡片；运行中可中断，已结束可删除记录与磁盘文件。</div>
        </div>
        <button class="cd-mini" :disabled="wfHistoryBusy" title="刷新工作流列表" @click="refreshWfHistory">刷新</button>
      </div>
      <div v-if="wfHistoryError" class="cd-session-error">{{ wfHistoryError }}</div>
      <div v-if="wfHistoryBusy && !wfHistoryItems.length" class="cd-session-empty">加载中…</div>
      <div v-else-if="!wfHistoryItems.length && !wfHistoryError" class="cd-session-empty">还没有发起过工作流</div>
      <div v-for="item in wfHistoryItems" :key="item.workflow_id" class="cd-session-item cd-wf-item">
        <button class="cd-session-main cd-wf-main" :disabled="!!wfHistoryActingId" @click="reattachWfHistory(item)">
          <span class="cd-wf-dot" :class="'cd-wf-dot-' + wfDotKind(item)" aria-hidden="true" />
          <span class="cd-session-copy">
            <strong>{{ item.request || '未命名工作流' }}</strong>
            <small>
              <em class="cd-wf-state" :class="'cd-wf-state-' + wfDotKind(item)">{{ wfStatusLabel(item.status) }}</em>
              <template v-if="item.task_count"> · {{ item.task_done ?? 0 }}/{{ item.task_count }} 个任务</template>
              <span v-if="sessionTime(item.updated_at || '')"> · {{ sessionTime(item.updated_at || '') }}</span>
            </small>
            <small v-if="wfTerminal(item) && (item.error || item.interrupt_reason)" class="cd-wf-sub">
              {{ item.error || item.interrupt_reason }}
            </small>
          </span>
        </button>
        <button
          v-if="!wfTerminal(item)"
          class="cd-session-delete cd-wf-act"
          title="中断这个工作流"
          :disabled="!!wfHistoryActingId"
          @click.stop="interruptWfHistory(item)"
        >{{ wfHistoryActingId === item.workflow_id ? '…' : '中断' }}</button>
        <button
          v-else
          class="cd-session-delete cd-wf-act cd-wf-act-danger"
          title="删除记录与磁盘文件"
          :disabled="!!wfHistoryActingId"
          @click.stop="deleteWfHistory(item)"
        >{{ wfHistoryActingId === item.workflow_id ? '…' : '删' }}</button>
      </div>
    </div>
    </Teleport>

    <!-- 引擎 / MCP 弹层（同样 Teleport 到 body） -->
    <!-- 点击外部关闭：透明遮罩截获弹层外点击 -->
    <Teleport to="body">
    <div v-if="enginePopOpen" class="cd-pop-mask" @click="enginePopOpen = false" />
    <div v-if="enginePopOpen" class="cd-pop" @click.stop>
      <div class="cd-pop-title">连接游戏引擎</div>
      <div class="cd-pop-subtitle">连接后，AI 才能读取引擎中的场景、脚本和运行日志。</div>
      <div v-if="addon && addon.is_godot_project" class="cd-addon">
        <div class="cd-addon-row">
          <span class="cd-dot" :class="addon.installed ? 'cd-dot-ok' : 'cd-dot-off'" />
          godot-ai 插件
          <span class="cd-addon-ver">{{ addon.installed ? 'v' + addon.version : '未安装' }}</span>
          <span v-if="addon.installed && addon.enabled" class="cd-tag cd-tag-ok">已启用</span>
          <span v-else-if="addon.installed" class="cd-tag cd-tag-warn">待在 Godot 中启用</span>
          <span class="cd-spacer" />
          <button class="cd-mini" :disabled="installing || !addon.uvx_available" @click="installAddon">
            {{ installing ? '安装中…' : (addon.installed ? '重装' : '安装') }}
          </button>
        </div>
        <div class="cd-preflight">
          <span :class="addon.godot_available ? 'cd-ok-text' : 'cd-err-text'">
            {{ addon.godot_available ? 'Godot 已发现' : 'Godot 未配置' }}
          </span>
          <span>·</span>
          <span :class="addon.uvx_available ? 'cd-ok-text' : 'cd-err-text'">
            {{ addon.uvx_available ? 'uvx 可用' : 'uvx 未安装' }}
          </span>
          <span class="cd-preflight-path" v-if="addon.godot">{{ addon.godot }}</span>
        </div>
        <div v-if="!addon.uvx_available" class="cd-warn-text">
          需先安装 uv（提供 uvx）：docs.astral.sh/uv，装完重启服务。
        </div>
      </div>
      <div v-for="s in servers" :key="s.key" class="cd-server">
        <div class="cd-server-row">
          <span class="cd-dot" :class="connected[s.key] ? 'cd-dot-ok' : (!s.enabled ? 'cd-dot-off' : 'cd-dot-idle')" />
          <span class="cd-server-name">{{ s.label || s.key }}</span>
          <span class="cd-server-meta">{{ s.transport === 'stdio' ? '本机插件' : '本机服务' }}</span>
          <span class="cd-spacer" />
          <button class="cd-mini" :disabled="probing === s.key || !s.enabled" @click="probe(s.key)">
            {{ probing === s.key ? '连接中…' : (connected[s.key] ? '重连' : '连接') }}
          </button>
          <button class="cd-mini cd-mini-stop" :disabled="!connected[s.key] || s.transport !== 'stdio'"
                  title="断开 stdio 长驻会话（HTTP 无状态服务无需断开）" @click="closeServer(s.key)">断开</button>
        </div>
        <div class="cd-server-guide">{{ connectorGuide(s) }}</div>
        <div v-if="connected[s.key]" class="cd-server-result">
          <span class="cd-ok-text">已连接{{ resultOf(s)?.tool_count != null ? ' · ' + resultOf(s)?.tool_count + ' 个工具可用' : '' }}</span>
        </div>
        <div v-else-if="resultOf(s)" class="cd-server-result">
          <span v-if="resultOf(s)?.ok" class="cd-ok-text">已断开（上次连接成功）</span>
          <span v-else class="cd-err-text" :title="resultOf(s)?.error">{{ resultOf(s)?.error || '连接失败（确认对应引擎/插件已运行）' }}</span>
        </div>
        <div v-if="s.help && !resultOf(s) && !connected[s.key]" class="cd-server-help">{{ s.help }}</div>
        <div class="cd-server-actions">
          <span class="cd-spacer" />
          <button class="cd-mini cd-mini-danger" @click="removeServer(s.key)">移除</button>
        </div>
      </div>

      <button class="cd-mini cd-add-toggle" @click="showAddForm = !showAddForm">+ 新增连接器</button>
      <div v-if="showAddForm" class="cd-add-form">
        <div class="cd-add-row">
          <input v-model="newServer.key" class="cd-input-sm" placeholder="key（字母数字_-，≤40）" />
          <input v-model="newServer.label" class="cd-input-sm" placeholder="显示名（可选）" />
        </div>
        <div class="cd-add-row">
          <select v-model="newServer.transport" class="cd-input-sm">
            <option value="stdio">stdio</option>
            <option value="http">http</option>
          </select>
          <label class="cd-add-chk"><input type="checkbox" v-model="newServer.enabled" /> 启用</label>
        </div>
        <div class="cd-add-row">
          <input v-if="newServer.transport === 'stdio'" v-model="newServer.command" class="cd-input-sm" placeholder="命令（如 uvx）" />
          <input v-else v-model="newServer.url" class="cd-input-sm" placeholder="url（http://…）" />
        </div>
        <div class="cd-add-actions">
          <button class="cd-mini" :disabled="savingServer || !newServer.key" @click="addServer">保存</button>
          <button class="cd-mini" @click="showAddForm = false">取消</button>
        </div>
      </div>
    </div>
    </Teleport>

    <!-- 折叠时不再卸载对话区：靠高度过渡 + 裁剪做顺滑展开/收起，状态与滚动位置保留 -->
      <div ref="scroller" class="cd-body" @scroll="onScroll" @wheel="onWheel">
        <p v-if="historyError" role="alert">{{ historyError }}</p>
        <div v-if="!messages.length && !sending" class="cd-task-entry">
          <span class="cd-task-entry-title">从一个目标开始</span>
          <button v-for="example in TASK_EXAMPLES" :key="example.id" class="cd-task-chip" @click="startTaskExample(example)">
            {{ example.label }}
          </button>
          <small>点击后先看 AI 方案，再选择或微调</small>
        </div>
        <div v-for="m in messages" :key="m.id" class="cd-msg" :class="`cd-msg-${m.role}`">
          <div v-if="m.role === 'user'" class="cd-user-bubble">
            <span v-if="m.imageCount" class="cd-msg-attachment">{{ m.imageCount }} 张图片</span>
            <span>{{ m.text }}</span>
          </div>
          <template v-else>
            <div v-if="m.trace.length || m.reasoning || m.status === 'streaming'" class="cd-run-head">
              <span class="cd-run-avatar">✦</span>
              <span class="cd-run-role">交付代理</span>
              <span class="cd-run-status" :class="`cd-run-${m.status}`">
                {{ m.status === 'streaming' ? '正在执行' : m.status === 'done' ? '已完成' : m.status === 'error' ? '执行失败' : '已停止' }}
              </span>
              <span class="cd-run-time" v-if="elapsedLabel(m)">· {{ elapsedLabel(m) }}</span>
            </div>
            <div v-for="(n, i) in m.notices" :key="'n' + i" class="cd-notice">
              <svg width="11" height="11" viewBox="0 0 11 11"><circle cx="5.5" cy="5.5" r="4.6" fill="none" stroke="currentColor" stroke-width="1"/><path d="M5.5 4.6 V7.6" stroke="currentColor" stroke-width="1.1" stroke-linecap="round"/><circle cx="5.5" cy="3" r=".75" fill="currentColor"/></svg>
              <span>{{ n }}</span>
            </div>
            <button v-if="m.recoverable" class="cd-resume" @click="send('继续完成任务')">
              ↻ 继续上次任务
            </button>
            <WorkflowCard
              v-if="m.workflow?.workflowId"
              :key="m.workflow.workflowId"
              :workflow-id="m.workflow.workflowId"
              :seed="m.workflow.seed"
              @activity="onCardActivity"
              @team="onWfTeam"
              @gate="(open: boolean) => onWfGate(m.workflow!.workflowId, open)"
            />
            <div v-else-if="m.workflow && m.status === 'streaming'" class="cd-wf-pending">
              <span class="cd-wf-spinner" aria-hidden="true" />
              正在创建工作流，方案马上在对话中展开…
            </div>
            <div v-if="m.plan.length" class="cd-plan">
              <div class="cd-plan-title">执行计划</div>
              <div v-for="(s, i) in m.plan" :key="'p' + i" class="cd-plan-step">
                <span class="cd-plan-idx">{{ i + 1 }}</span><span>{{ s }}</span>
              </div>
            </div>
            <div v-if="m.reasoning" class="cd-reason">
              <button class="cd-reason-head" @click="toggleReason(m.id)">
                {{ reasonOpen.has(m.id) ? '▾' : '▸' }} 深度思考
                <span v-if="m.status === 'streaming'" class="cd-dots">…</span>
              </button>
              <div v-if="reasonOpen.has(m.id)" class="cd-reason-body">{{ m.reasoning }}</div>
            </div>
            <div v-if="visibleTrace(m).length" class="cd-activity">
              <div v-for="row in visibleTrace(m)" :key="row.index" class="cd-activity-item" :class="`cd-activity-${traceState(m, row.item, row.index)}`">
                <button class="cd-activity-row" @click="toggleActivity(m.id, row.index)">
                  <span class="cd-activity-glyph">{{ TRACE_GLYPH[row.item.type] || '·' }}</span>
                  <span class="cd-activity-main">
                    <span class="cd-activity-title">{{ traceTitle(row.item) }}</span>
                    <span class="cd-activity-summary">{{ traceSummary(row.item) }}</span>
                  </span>
                  <span class="cd-activity-meta">
                    <span class="cd-activity-state">{{ traceStateLabel(traceState(m, row.item, row.index), row.item) }}</span>
                    <span v-if="traceElapsed(row.item)" class="cd-activity-time">{{ traceElapsed(row.item) }}</span>
                    <span class="cd-activity-chevron">{{ activityIsOpen(m.id, row.index, m) ? '▾' : '▸' }}</span>
                  </span>
                </button>
                <div v-if="activityIsOpen(m.id, row.index, m)" class="cd-activity-detail">{{ row.item.text }}</div>
              </div>
            </div>
            <div v-if="m.status === 'streaming'" class="cd-thinking">
              {{ m.reasoning ? '正在整理最终回答' : 'AI 正在翻代码、组织回答' }}<span class="cd-dots">…</span>
            </div>
            <div v-if="slowResponseLabel(m)" class="cd-slow-notice" role="status">
              {{ slowResponseLabel(m) }}
            </div>
            <!-- 流式中按 ~8fps 节流重渲染 markdown（格式边出边成型，又不逐 token 重建
                 DOM）；结束瞬间由 answerHtml 缓存接管成稿，视觉无跳变 -->
            <div v-if="m.text" class="ai-md cd-answer" :class="{ 'cd-answer-live': m.status === 'streaming' }">
              <template v-if="m.status === 'streaming'"><span v-html="streamHtmlOf(m)" /><span class="cd-caret" aria-hidden="true" /></template>
              <span v-else v-html="answerHtml(m)" />
            </div>
            <details v-if="m.status === 'done' && webRefsOf(m).length" class="cd-web-sources">
              <summary>联网来源（{{ webRefsOf(m).length }}）</summary>
              <a v-for="(s, i) in webRefsOf(m)" :key="s.url + i" class="cd-web-source" :href="s.url" target="_blank" rel="noopener noreferrer">
                <span class="cd-web-source-title">{{ s.title }}</span>
                <span class="cd-web-source-url">{{ s.url }}</span>
                <span v-if="s.score" class="cd-web-source-score">参考 {{ s.score }}</span>
              </a>
            </details>
            <div v-if="m.status === 'error'" class="cd-error">⚠ {{ m.error }}</div>
            <div v-if="m.status === 'done' && refsOf(m).length" class="cd-refs">
              <button
                v-for="r in refsOf(m)"
                :key="r.path + ':' + r.line"
                class="cd-ref"
                :title="'打开 ' + r.path + (r.line ? ':' + r.line : '')"
                @click="openRef(r)"
              >
                <svg width="11" height="11" viewBox="0 0 11 11"><path d="M2 1.5 H6.5 L9 4 V9.5 H2 Z M6.5 1.5 V4 H9" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/></svg>
                <span class="cd-ref-path">{{ r.path }}</span>
                <span v-if="r.line" class="cd-ref-line">:{{ r.line }}</span>
              </button>
            </div>
          </template>
        </div>
      </div>
      <button v-if="sending && !stickToBottom" class="cd-jump-bottom" title="恢复跟随最新输出" @click="resumeAutoScroll">
        ↓ 回到底部
      </button>

      <!-- 孤儿工作流恢复条：后端未终结但当前对话没有卡片（多为刷新后），不处理会持续拦截新请求 -->
      <div v-if="orphanWf" class="cd-orphan" role="alert">
        <span class="cd-orphan-glyph" aria-hidden="true">⚠</span>
        <div class="cd-orphan-body">
          <div class="cd-orphan-title">
            该项目有一个未结束的工作流（{{ orphanStatusLabel }}），当前对话里没有它的审批卡片
          </div>
          <div class="cd-orphan-desc">
            它可能创建于页面刷新前；在处理它之前，新的工作流请求会被拦截。ID：{{ orphanWf.workflow_id }}
          </div>
          <div v-if="orphanError" class="cd-orphan-err">{{ orphanError }}</div>
        </div>
        <button class="cd-mini" :disabled="orphanBusy" @click="reattachOrphan">挂回对话继续</button>
        <button class="cd-mini cd-mini-danger" :disabled="orphanBusy" @click="interruptOrphan">
          {{ orphanBusy ? '处理中…' : '中断它' }}
        </button>
      </div>

      <footer class="cd-inputbar">
        <div class="cd-tools">
          <input ref="imageInput" class="cd-file-input" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple @change="onImagePick" />
          <input ref="videoInput" class="cd-file-input" type="file" accept="video/mp4,video/quicktime,video/webm,video/x-matroska,video/avi,image/gif" @change="onVideoPick" />
          <button class="cd-chip cd-attach" :disabled="demoMode || sending" :title="visionLabel" @click="imageInput?.click()">
            <span aria-hidden="true">▧</span><span>图片</span>
          </button>
          <button class="cd-chip cd-attach" :disabled="demoMode || sending || videoBusy" title="视频会由 Harness 抽帧并生成时间轴观察" @click="videoInput?.click()">
            <span aria-hidden="true">▹</span><span>{{ videoBusy ? '分析视频…' : '视频' }}</span>
          </button>
          <span v-if="pendingImages.length" class="cd-attachments">
            <span v-for="(img, i) in pendingImages" :key="img.name + i" class="cd-attachment">
              {{ img.name || '图片' }}
              <button type="button" title="移除图片" @click="removeImage(i)">×</button>
            </span>
          </span>
          <span v-if="attachmentError" class="cd-attachment-error">{{ attachmentError }}</span>
          <button
            class="cd-chip cd-chip-model"
            :disabled="demoMode"
            :title="demoMode ? '离线演示模式无需配置模型' : '模型设置：切换云端 / 本地 / 任意 OpenAI 兼容接口'"
            @click="openSettings"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <rect x="2.6" y="2.6" width="6.8" height="6.8" rx="1" fill="none" stroke="currentColor" stroke-width="1"/>
              <path d="M4.6 0.9v1.7M7.4 0.9v1.7M4.6 9.4v1.7M7.4 9.4v1.7M0.9 4.6h1.7M0.9 7.4h1.7M9.4 4.6h1.7M9.4 7.4h1.7" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
            </svg>
            <span class="cd-chip-text">{{ modelLabel }}</span>
            <svg width="9" height="9" viewBox="0 0 9 9"><path d="M2 3.2 L4.5 5.7 L7 3.2" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>

          <button
            class="cd-chip"
            :class="{ 'cd-chip-on': webOn }"
            :title="webTitle"
            @click="webOn = !webOn"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <circle cx="6" cy="6" r="4.7" fill="none" stroke="currentColor" stroke-width="1"/>
              <path d="M1.3 6h9.4" fill="none" stroke="currentColor" stroke-width="1"/>
              <path d="M6 1.3c1.5 1.3 2.3 2.9 2.3 4.7S7.5 9.4 6 10.7C4.5 9.4 3.7 7.8 3.7 6S4.5 2.6 6 1.3z" fill="none" stroke="currentColor" stroke-width="1"/>
            </svg>
            <span>联网搜索</span>
            <span class="cd-switch" :class="{ on: webOn }"><i /></span>
          </button>

          <button
            v-if="thinkingSupported"
            class="cd-chip"
            :class="{ 'cd-chip-on': thinkingEffective, 'cd-chip-native': thinkingNative }"
            :disabled="thinkingNative"
            :title="thinkingTitle"
            @click="toggleThinking"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M6 1 L7.1 4.9 L11 6 L7.1 7.1 L6 11 L4.9 7.1 L1 6 L4.9 4.9 Z" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/>
            </svg>
            <span>深度思考</span>
            <span v-if="thinkingNative" class="cd-chip-badge">内置</span>
            <span v-else class="cd-switch" :class="{ on: thinkingEffective }"><i /></span>
          </button>
          <span v-else class="cd-chip cd-chip-off" :title="thinkingTitle">
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M6 1 L7.1 4.9 L11 6 L7.1 7.1 L6 11 L4.9 7.1 L1 6 L4.9 4.9 Z" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/>
            </svg>
            <span>深度思考</span>
            <span class="cd-chip-badge">{{ thinkingMode === 'unknown' ? '待确认' : '不支持' }}</span>
          </span>

          <!-- 上下文窗口占用：按当前模型真实窗口估算，65% 转黄、80%（压缩触发线）转红 -->
          <span
            v-if="usage"
            class="cd-chip cd-ctx cd-ctx-push"
            :class="'cd-ctx-' + usage.level"
            :title="usageTitle"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M1.5 9.5a4.5 4.5 0 0 1 9 0" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
              <path d="M3.3 9.5a2.7 2.7 0 0 1 5.4 0" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
              <circle cx="6" cy="9.5" r=".7" fill="currentColor"/>
            </svg>
            <span>上下文 {{ usage.percent }}%</span>
            <span class="cd-ctx-bar"><i :style="{ width: usage.percent + '%' }" /></span>
          </span>
        </div>
        <div class="cd-input-row">
          <textarea
            ref="inputEl"
            v-model="input"
            class="cd-input"
            rows="2"
            placeholder="提问：角色数值在哪 / 解释这段逻辑 / 这个报错怎么改…（Ctrl+Enter 发送）"
            @keydown="onKeydown"
            @paste="onPaste"
            @drop="onDrop"
            @dragover="onDragover"
          />
          <button v-if="sending" class="cd-send cd-stop" @click="stop">停止</button>
          <button v-else class="cd-send" :disabled="!input.trim() && !pendingImages.length" @click="send()">发送</button>
        </div>
      </footer>

    <WorkflowMembersDock
      :active="!!wfActiveTeam"
      :members="wfActiveTeam?.members || []"
      @focus="focusWfMember"
    />
    <ModelSettingsDialog
      :visible="settingsOpen"
      :config="modelConfig"
      @close="settingsOpen = false"
      @saved="onModelSaved"
    />
  </section>
</template>

<style scoped>
.cd-dock {
  position: relative;
  flex: 0 0 auto;
  border-top: 1px solid var(--border);
  background: var(--bg-raised);
  display: flex;
  flex-direction: column;
  height: min(62vh, 820px);
  min-height: 380px;
  max-height: calc(100vh - 86px);
  /* 高度是布局属性，动画期间只改高度一个变量；曲线先快后稳，配合 chevron 同步翻转 */
  transition: height .24s cubic-bezier(.32, .72, 0, 1),
              min-height .24s cubic-bezier(.32, .72, 0, 1);
}
.cd-dock.cd-expanded {
  height: calc(100vh - 86px);
  max-height: calc(100vh - 86px);
}
/* 折叠态用显式高度（=头部 32 + 顶边 1），auto 无法参与过渡；
   双类名保证在矮屏媒体查询里也能覆盖 .cd-dock 的高度声明 */
.cd-dock.cd-collapsed { height: 33px; min-height: 0; max-height: none; }
/* 折叠收起时裁掉对话区与输入栏（弹层已 Teleport 到 body，不受裁剪影响） */
.cd-dock.cd-clipping { overflow: hidden; }
/* 人工审批门打开：锁操作区与头部，但不锁聊天滚动（遮罩 pointer-events:none）。
   头部必须整体锁定（含折叠点击）：否则折叠后 33px 裁剪会把居中审批模态吞掉。 */
.cd-dock.cd-gate-blocked .cd-inputbar,
.cd-dock.cd-gate-blocked .cd-head,
.cd-dock.cd-gate-blocked .cd-task-entry {
  pointer-events: none;
}
.cd-dock.cd-gate-blocked .cd-inputbar { opacity: .55; }
@media (max-height: 620px) {
  .cd-dock {
    height: min(52vh, 340px);
    min-height: 240px;
    max-height: calc(100vh - 86px);
  }
  .cd-dock.cd-expanded { height: calc(100vh - 86px); }
}

.cd-head {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  user-select: none;
}
/* 唯一的折叠热区：箭头 + 标题 + 状态指示；提示文字与按钮不触发折叠 */
.cd-head-toggle {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 6px; margin-left: -6px;
  border-radius: 6px; cursor: pointer; user-select: none;
}
.cd-head-toggle:hover { background: var(--bg-hover); }
.cd-head-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.cd-head-toggle .cd-live-wf { cursor: inherit; }
.cd-head-toggle .cd-chevron { flex: 0 0 auto; }
.cd-chevron {
  display: flex; color: var(--text-muted);
  transform-origin: center;
  transition: transform .24s cubic-bezier(.32, .72, 0, 1);
  will-change: transform;
}
.cd-chevron.rotated { transform: rotate(90deg); }
.cd-title { font-size: 12px; font-weight: 600; color: var(--text); }
.cd-hint { font-size: 11px; color: var(--text-faint); }
.cd-live { font-size: 11px; color: var(--amber); margin-left: 4px; }
/* 折叠态工作流后台指示：脉冲点 + 文字，位于标题折叠热区内 */
.cd-live-wf {
  display: inline-flex; align-items: center; gap: 5px;
  color: var(--accent); cursor: pointer;
}
.cd-live-dot {
  width: 6px; height: 6px; border-radius: 50%;
  background: var(--accent);
  animation: cd-live-pulse 1.4s ease-in-out infinite;
}
@keyframes cd-live-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: .35; transform: scale(.7); }
}
@media (prefers-reduced-motion: reduce) { .cd-live-dot { animation: none; } }
.cd-spacer { flex: 1; }
.cd-btn {
  display: inline-flex; align-items: center; gap: 5px;
  height: 22px; padding: 0 8px;
  border: 1px solid var(--border); border-radius: 5px;
  background: transparent; color: var(--text-muted);
  font-size: 11px; cursor: pointer;
}
.cd-btn:hover { color: var(--text); border-color: var(--border-strong); }
.cd-btn-on { color: var(--accent); border-color: var(--accent); }

/* 弹层 */
.cd-pop-mask {
  position: fixed;
  inset: 0;
  z-index: 59;
}
.cd-pop {
  position: fixed;
  /* 26px 底部状态栏 + 原相对面板的 38px 间距 */
  left: 10px; bottom: 64px;
  width: 460px; max-width: calc(100vw - 24px);
  max-height: 60vh; overflow-y: auto;
  background: var(--bg-raised);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: 0 14px 38px rgba(35,52,84,.2);
  padding: 10px 12px;
  z-index: 60;
}
.cd-session-pop {
  /* Teleport 到 body：相对视口固定（26px 状态栏 + 原 38px 间距） */
  position: fixed;
  right: 10px; bottom: 64px;
  width: 360px; max-width: calc(100vw - 24px);
  max-height: min(62vh, 430px); overflow-y: auto;
  background: var(--bg-raised); border: 1px solid var(--border-strong);
  border-radius: 8px; box-shadow: 0 14px 38px rgba(35,52,84,.2);
  padding: 10px; z-index: 60;
}
.cd-session-head { display: flex; align-items: flex-start; gap: 8px; justify-content: space-between; }
.cd-session-subtitle { color: var(--text-faint); font-size: 10.5px; line-height: 1.45; margin-top: -4px; }
.cd-session-new {
  width: 100%; display: flex; align-items: center; gap: 9px; margin: 8px 0;
  padding: 8px 9px; border: 1px solid #c8dcfa; border-radius: 6px;
  background: #f3f8ff; color: var(--accent); text-align: left; cursor: pointer;
}
.cd-session-new:hover { background: var(--bg-selected); border-color: var(--accent); }
.cd-session-new:disabled { opacity: .55; cursor: default; }
.cd-session-new-icon { width: 20px; height: 20px; display: inline-flex; align-items: center; justify-content: center; border: 1px solid currentColor; border-radius: 50%; font-size: 16px; line-height: 1; }
.cd-session-new b, .cd-session-new small { display: block; }
.cd-session-new b { font-size: 11.5px; }
.cd-session-new small { margin-top: 2px; color: var(--text-muted); font-size: 10px; }
.cd-session-error { padding: 6px 8px; color: var(--danger); background: #fff4f4; border: 1px solid #f2caca; border-radius: 5px; font-size: 10.5px; }
.cd-session-empty { padding: 12px 5px; color: var(--text-faint); font-size: 11px; text-align: center; }
.cd-session-item { display: flex; align-items: stretch; border-top: 1px solid var(--border); }
.cd-session-item.active { background: var(--bg-selected); }
.cd-session-main { flex: 1; min-width: 0; display: flex; gap: 7px; align-items: center; padding: 8px 5px; border: 0; background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.cd-session-main:hover { background: var(--bg-hover); }
.cd-session-main:disabled { opacity: .6; cursor: default; }
.cd-session-current { flex: 0 0 14px; color: var(--text-faint); font-size: 10px; }
.cd-session-item.active .cd-session-current { color: var(--accent); }
.cd-session-copy { min-width: 0; display: block; }
.cd-session-copy strong, .cd-session-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cd-session-copy strong { font-size: 11.5px; font-weight: 600; }
.cd-session-copy small { margin-top: 2px; color: var(--text-faint); font-size: 10px; }
.cd-session-delete { align-self: center; margin-right: 5px; width: 22px; height: 22px; border: 0; background: transparent; color: var(--text-faint); cursor: pointer; font-size: 16px; }
.cd-session-delete:hover { color: var(--danger); }
.cd-session-delete:disabled { cursor: default; opacity: .5; }
/* 工作流历史弹层 */
.cd-wf-pop { max-height: min(70vh, 520px); }
.cd-wf-dot { flex: 0 0 auto; align-self: center; width: 8px; height: 8px; border-radius: 50%; }
.cd-wf-dot-run { background: var(--amber); box-shadow: 0 0 6px rgba(214,158,46,.6); animation: cd-wf-pulse 1.4s ease-in-out infinite; }
.cd-wf-dot-ok { background: var(--green); }
.cd-wf-dot-err { background: var(--danger); }
.cd-wf-dot-stop { background: var(--text-faint); }
@keyframes cd-wf-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
.cd-wf-state { font-style: normal; }
.cd-wf-state-run { color: var(--amber); }
.cd-wf-state-ok { color: var(--green); }
.cd-wf-state-err { color: var(--danger); }
.cd-wf-state-stop { color: var(--text-faint); }
.cd-wf-sub { display: block; color: var(--danger); margin-top: 2px; }
.cd-wf-act {
  width: auto; min-width: 34px; height: auto; align-self: center;
  margin-right: 5px; padding: 3px 8px; border: 1px solid var(--border);
  border-radius: 5px; font-size: 10.5px; color: var(--text-muted);
}
.cd-wf-act:hover:not(:disabled) { color: var(--amber); border-color: var(--amber); }
.cd-wf-act-danger:hover:not(:disabled) { color: var(--danger); border-color: var(--danger); }
.cd-new-session { color: var(--accent); border-color: #c8dcfa; }
.cd-pop-title { font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 8px; }
.cd-pop-subtitle { font-size: 11px; color: var(--text-dim); line-height: 1.5; margin-bottom: 8px; }
.cd-addon {
  border: 1px solid var(--border); border-radius: 7px;
  padding: 8px 10px; margin-bottom: 10px; background: var(--bg-hover);
}
.cd-addon-row, .cd-server-row { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--text); }
.cd-addon-ver { color: var(--text-muted); font-size: 11px; }
.cd-preflight { display: flex; gap: 6px; align-items: center; margin-top: 6px; font-size: 11px; color: var(--text-faint); flex-wrap: wrap; }
.cd-preflight-path { font-family: var(--font-mono); font-size: 10px; color: var(--text-faint); }
.cd-tag { font-size: 10px; padding: 1px 6px; border-radius: 8px; }
.cd-tag-ok { color: var(--green); background: rgba(69,201,140,.12); }
.cd-tag-warn { color: var(--amber); background: rgba(227,168,58,.12); }
.cd-ok-text { color: var(--green); }
.cd-err-text { color: var(--danger); }
.cd-warn-text { font-size: 11px; color: var(--amber); margin-top: 5px; }
.cd-mini {
  height: 21px; padding: 0 9px; font-size: 11px;
  border: 1px solid var(--border-strong); border-radius: 5px;
  background: transparent; color: var(--text-muted); cursor: pointer;
}
.cd-mini:hover:not(:disabled) { color: var(--text); border-color: var(--accent); }
.cd-mini:disabled { opacity: .45; cursor: default; }
.cd-mini-stop { border-color: var(--border-strong); }
.cd-mini-stop:hover:not(:disabled) { color: var(--danger); border-color: var(--danger); }
.cd-mini-danger:hover:not(:disabled) { color: var(--danger); border-color: var(--danger); }

.cd-server-actions { display: flex; align-items: center; padding-top: 5px; }
.cd-add-toggle { margin-top: 6px; }
.cd-add-form { margin-top: 6px; padding: 8px; border: 1px dashed var(--border); border-radius: 6px; display: flex; flex-direction: column; gap: 6px; }
.cd-add-row { display: flex; gap: 6px; align-items: center; }
.cd-input-sm {
  flex: 1; min-width: 0; height: 24px; padding: 0 7px;
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 5px;
  font-size: 11px; font-family: inherit; outline: none;
}
.cd-input-sm:focus { border-color: var(--accent); }
.cd-add-chk { font-size: 11px; color: var(--text-muted); display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
.cd-add-actions { display: flex; gap: 6px; justify-content: flex-end; }

.cd-server { padding: 7px 0; border-top: 1px dashed var(--border); }
.cd-server-name { font-size: 12px; }
.cd-server-meta { font-family: var(--font-mono); font-size: 10px; color: var(--text-faint); }
.cd-server-result { margin-top: 4px; font-size: 11px; word-break: break-all; }
.cd-server-help { margin-top: 3px; font-size: 10.5px; color: var(--text-faint); }
.cd-server-guide { margin: 4px 0 0 20px; font-size: 10.5px; color: var(--text-dim); line-height: 1.45; }
.cd-dot { width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px; }
.cd-dot-ok { background: var(--green); box-shadow: 0 0 6px rgba(69,201,140,.6); }
.cd-dot-idle { background: var(--amber); }
.cd-dot-off { background: var(--text-faint); }

/* 消息区 */
.cd-jump-bottom {
  position: absolute; right: 14px; bottom: 58px; z-index: 3;
  border: 1px solid #c8dcfa; border-radius: 999px; padding: 4px 9px;
  background: var(--bg-raised); color: var(--accent); font-size: 10.5px;
  box-shadow: 0 2px 8px rgba(30, 50, 90, .14); cursor: pointer;
}
.cd-jump-bottom:hover { background: var(--bg-selected); }
/* 孤儿工作流恢复条：贴在输入框上沿，琥珀色提示但不遮挡消息流 */
.cd-orphan {
  display: flex; align-items: center; gap: 8px;
  margin: 6px 12px 0; padding: 8px 10px;
  border: 1px solid var(--amber); border-radius: 8px;
  background: color-mix(in srgb, var(--amber) 10%, var(--bg-raised));
}
.cd-orphan-glyph { color: var(--amber); font-size: 13px; line-height: 1; flex: none; }
.cd-orphan-body { flex: 1 1 auto; min-width: 0; }
.cd-orphan-title { font-size: 11.5px; font-weight: 600; color: var(--text); }
.cd-orphan-desc {
  font-size: 10.5px; color: var(--text-muted); margin-top: 2px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.cd-orphan-err { font-size: 10.5px; color: var(--danger); margin-top: 2px; }
.cd-orphan .cd-mini { flex: none; white-space: nowrap; }
.cd-body { flex: 1 1 auto; overflow-y: auto; padding: 8px 14px 4px; min-height: 90px; }
.cd-empty { padding: 14px 6px; color: var(--text-muted); }
.cd-empty-title { font-size: 12px; margin: 0 0 10px; color: var(--text-muted); }
.cd-quicks { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.cd-quick {
  text-align: left; font-size: 11.5px; padding: 5px 10px;
  border: 1px solid var(--border); border-radius: 12px;
  background: transparent; color: var(--text-muted); cursor: pointer;
}
.cd-quick:hover { color: var(--accent); border-color: var(--accent); }
.cd-task-entry {
  display: flex; align-items: center; flex-wrap: wrap; gap: 6px;
  padding: 10px 6px 12px; color: var(--text-muted);
}
.cd-task-entry-title { width: 100%; font-size: 11px; color: var(--text-dim); }
.cd-task-chip {
  padding: 5px 9px; border: 1px solid var(--border); border-radius: 999px;
  background: transparent; color: var(--text-muted); font-size: 11px; cursor: pointer;
}
.cd-task-chip:hover { color: var(--accent); border-color: var(--accent); background: var(--bg-selected); }
.cd-task-entry small { color: var(--text-faint); font-size: 10px; }

.cd-msg { margin-bottom: 10px; }
.cd-user-bubble {
  display: inline-block; max-width: 82%;
  margin-left: auto;
  padding: 6px 10px; border-radius: 9px 9px 2px 9px;
  background: var(--bg-selected); color: var(--text);
  font-size: 12.5px; white-space: pre-wrap;
}
.cd-msg-user { display: flex; justify-content: flex-end; }
.cd-answer { font-size: 12.5px; max-width: 96%; }
/* 流式期的节流 markdown 与成稿同一样式；光标在块级内容后自然落到新行 */
.cd-caret {
  display: inline-block; width: 6px; height: 1.05em; margin-left: 2px;
  vertical-align: -0.18em; border-radius: 1px;
  background: var(--accent); animation: cd-caret 1.05s steps(1, end) infinite;
}
@keyframes cd-caret { 0%, 55% { opacity: 1; } 56%, 100% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .cd-caret { animation: none; } }
.cd-thinking, .cd-error { font-size: 12px; color: var(--text-muted); padding: 4px 0; }
.cd-slow-notice { margin: 6px 0; padding: 7px 9px; border: 1px solid color-mix(in srgb, var(--amber) 45%, var(--border)); border-radius: 6px; color: var(--text-muted); background: color-mix(in srgb, var(--amber) 10%, transparent); font-size: 11px; line-height: 1.45; }
.cd-error { color: var(--danger); }

.cd-run-head {
  display: flex; align-items: center; gap: 6px;
  min-height: 22px; margin: 1px 0 5px;
  color: var(--text-muted); font-size: 11px;
}
.cd-run-avatar {
  width: 17px; height: 17px; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; background: var(--accent); font-size: 10px;
}
.cd-run-role { color: var(--text); font-weight: 600; }
.cd-run-status { color: var(--text-faint); }
.cd-run-streaming { color: var(--accent); }
.cd-run-done { color: var(--green); }
.cd-run-error { color: var(--danger); }
.cd-run-stopped { color: var(--amber); }
.cd-run-time { color: var(--text-faint); font-variant-numeric: tabular-nums; }

.cd-wf-pending {
  display: flex; align-items: center; gap: 9px;
  margin: 4px 0 8px; padding: 10px 12px;
  border: 1px solid var(--border); border-radius: 10px;
  background: var(--bg-hover); color: var(--text-muted); font-size: 12.5px;
}
.cd-wf-spinner {
  width: 13px; height: 13px; border-radius: 50%; flex: 0 0 auto;
  border: 2px solid var(--border-strong); border-top-color: var(--accent);
  animation: cd-wf-spin .7s linear infinite;
}
@keyframes cd-wf-spin { to { transform: rotate(360deg); } }

/* 聊天内的纵向活动流：动作按 SSE 到达顺序实时追加，详情按行折叠。 */
.cd-activity {
  position: relative; margin: 4px 0 8px 8px;
  padding-left: 16px; border-left: 1px solid var(--border);
}
.cd-activity-item { position: relative; margin: 0; }
.cd-activity-item::before {
  content: ''; position: absolute; left: -20px; top: 9px;
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--text-faint); border: 2px solid var(--bg-raised);
  box-sizing: content-box;
}
.cd-activity-running::before { background: var(--accent); box-shadow: 0 0 0 3px rgba(37,96,212,.12); }
.cd-activity-ok::before { background: var(--green); }
.cd-activity-warn::before { background: var(--amber); }
.cd-activity-error::before { background: var(--danger); }
.cd-activity-row {
  width: 100%; min-width: 0; display: flex; align-items: center; gap: 7px;
  border: 0; background: transparent; padding: 4px 0; text-align: left;
  color: var(--text-muted); cursor: pointer;
}
.cd-activity-row:hover { color: var(--text); }
.cd-activity-glyph {
  flex: 0 0 15px; width: 15px; text-align: center;
  color: var(--text-faint); font-size: 13px; line-height: 1;
}
.cd-activity-running .cd-activity-glyph { color: var(--accent); }
.cd-activity-ok .cd-activity-glyph { color: var(--green); }
.cd-activity-warn .cd-activity-glyph { color: var(--amber); }
.cd-activity-error .cd-activity-glyph { color: var(--danger); }
.cd-activity-main { min-width: 0; flex: 1; display: flex; align-items: baseline; gap: 7px; }
.cd-activity-title { flex: 0 0 auto; color: var(--text); font-size: 12.5px; font-weight: 600; }
.cd-activity-summary {
  min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: var(--text-muted); font-size: 12px;
}
.cd-activity-meta { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 5px; font-size: 10.5px; }
.cd-activity-state { color: var(--text-faint); }
.cd-activity-running .cd-activity-state { color: var(--accent); }
.cd-activity-warn .cd-activity-state { color: var(--amber); }
.cd-activity-error .cd-activity-state { color: var(--danger); }
.cd-activity-time { color: var(--text-faint); font-variant-numeric: tabular-nums; }
.cd-activity-chevron { color: var(--text-faint); font-size: 10px; }
.cd-activity-detail {
  margin: 0 0 4px 22px; padding: 5px 8px;
  border-left: 2px solid var(--border); background: var(--bg-hover);
  color: var(--text-dim); font: 10.5px/1.5 var(--font-mono, monospace);
  white-space: pre-wrap; overflow-wrap: anywhere; max-height: 130px; overflow: auto;
}

.cd-refs { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
.cd-ref {
  display: inline-flex; align-items: center; gap: 4px;
  height: 22px; padding: 0 8px;
  border: 1px solid #c8dcfa; border-radius: 5px;
  background: #f3f8ff; color: var(--accent);
  font-family: var(--font-mono); font-size: 11px; cursor: pointer;
}
.cd-ref:hover { background: var(--bg-selected); border-color: var(--accent); }
.cd-ref-line { color: var(--amber); }
.cd-web-sources { margin-top: 8px; border-top: 1px solid var(--border); padding-top: 5px; max-width: 96%; }
.cd-web-sources summary { cursor: pointer; color: var(--text-muted); font-size: 11px; }
.cd-web-source { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 8px; margin-top: 5px; padding: 6px 8px; border: 1px solid var(--border); border-radius: 5px; color: var(--text); text-decoration: none; background: var(--bg); }
.cd-web-source:hover { border-color: var(--accent); background: var(--bg-selected); }
.cd-web-source-title { font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cd-web-source-url { grid-column: 1 / -1; color: var(--accent); font-size: 10px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.cd-web-source-score { color: var(--text-faint); font-size: 10px; }

/* 输入区 */
.cd-inputbar { display: flex; flex-direction: column; gap: 6px; padding: 7px 12px 9px; }
.cd-input-row { display: flex; gap: 8px; align-items: flex-end; }
.cd-input {
  flex: 1; resize: none;
  background: var(--bg); color: var(--text);
  border: 1px solid var(--border); border-radius: 7px;
  padding: 7px 10px; font-size: 12.5px; font-family: inherit;
  outline: none; max-height: 110px;
}
.cd-input:focus { border-color: var(--accent); }
.cd-send {
  height: 30px; padding: 0 16px; border-radius: 7px;
  border: 1px solid #2560d4; background: linear-gradient(180deg,#3b7ef2,#2f6fed);
  color: #fff; font-size: 12px; font-weight: 600; cursor: pointer;
}
.cd-send:disabled { opacity: .4; cursor: default; }
.cd-stop { background: transparent; color: var(--danger); border-color: var(--danger); }

/* 工具行：模型芯片 / 联网开关 / 深度思考开关 */
.cd-tools { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.cd-chip {
  display: inline-flex; align-items: center; gap: 5px;
  height: 23px; padding: 0 8px;
  border: 1px solid var(--border); border-radius: 12px;
  background: transparent; color: var(--text-muted);
  font-size: 11px; line-height: 1; cursor: pointer;
  max-width: 60%;
}
.cd-chip:hover:not(:disabled):not(.cd-chip-off) { border-color: var(--border-strong); color: var(--text); }
.cd-chip:disabled { cursor: default; }
.cd-chip-text {
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-mono);
}
.cd-chip-model { padding: 0 7px 0 8px; }
.cd-chip-on {
  color: var(--accent); border-color: var(--accent);
  background: rgba(37, 96, 212, .07);
}
.cd-chip-native { color: var(--violet, #7c5cd6); border-color: var(--violet, #7c5cd6); background: rgba(124, 92, 214, .08); }
.cd-chip-off { opacity: .6; cursor: default; }
.cd-chip-badge { font-size: 10px; color: var(--text-faint); }
.cd-chip-native .cd-chip-badge { color: var(--violet, #7c5cd6); }

/* 迷你开关 */
.cd-switch {
  position: relative; flex: 0 0 auto;
  width: 22px; height: 12px; border-radius: 7px;
  background: var(--border-strong); transition: background .15s;
}
.cd-switch i {
  position: absolute; top: 1.5px; left: 2px;
  width: 9px; height: 9px; border-radius: 50%;
  background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,.25);
  transition: left .15s;
}
.cd-switch.on { background: var(--accent); }
.cd-chip-native .cd-switch.on { background: var(--violet, #7c5cd6); }
.cd-switch.on i { left: 11px; }

/* 上下文窗口用量指示 */
.cd-ctx-push { margin-left: auto; cursor: default; max-width: none; }
.cd-ctx:hover { border-color: var(--border); color: var(--text-muted); }
.cd-ctx-bar {
  position: relative; flex: 0 0 auto;
  width: 34px; height: 4px; border-radius: 2px;
  background: var(--border-strong); overflow: hidden;
}
.cd-ctx-bar i {
  position: absolute; inset: 0 auto 0 0;
  border-radius: 2px; background: currentColor;
  transition: width .25s ease;
}
.cd-ctx-ok { color: var(--text-faint); }
.cd-ctx-high {
  color: #b47a1e; border-color: rgba(180, 122, 30, .45);
  background: rgba(180, 122, 30, .08);
}
.cd-ctx-warn {
  color: #cc5347; border-color: rgba(204, 83, 71, .5);
  background: rgba(204, 83, 71, .09);
}

/* 系统通知（上下文压缩等） */
.cd-notice {
  display: flex; align-items: flex-start; gap: 5px;
  margin: 2px 0 5px; padding: 4px 8px;
  border-radius: 5px; background: var(--bg-hover);
  font-size: 10.5px; color: var(--text-dim); line-height: 1.5;
}
.cd-notice svg { flex: 0 0 auto; margin-top: 2px; color: var(--text-faint); }
.cd-resume {
  display: inline-flex; align-items: center; gap: 5px;
  margin: 1px 0 7px; padding: 5px 9px;
  border: 1px solid var(--accent); border-radius: 5px;
  background: rgba(47,111,237,.08); color: var(--accent);
  font-size: 11px; cursor: pointer;
}
.cd-msg-attachment { display: inline-flex; margin-right: 6px; color: var(--accent); font-size: 10.5px; }
.cd-file-input { display: none; }
.cd-attach { cursor: pointer; }
.cd-attachments { display: inline-flex; gap: 4px; flex-wrap: wrap; max-width: 360px; }
.cd-attachment { display: inline-flex; align-items: center; gap: 3px; max-width: 150px; padding: 2px 5px; border: 1px solid var(--border); border-radius: 5px; color: var(--text-muted); font-size: 10px; }
.cd-attachment button { border: 0; background: transparent; color: var(--text-faint); cursor: pointer; padding: 0 1px; }
.cd-attachment-error { color: var(--danger); font-size: 10px; }
.cd-resume:hover { background: rgba(47,111,237,.15); }

/* 计划步骤 */
.cd-plan {
  margin: 2px 0 6px; padding: 7px 9px;
  border: 1px solid var(--border); border-radius: 7px; background: var(--bg-hover);
}
.cd-plan-title { font-size: 11px; font-weight: 600; color: var(--text-muted); margin-bottom: 5px; }
.cd-plan-step { display: flex; gap: 7px; align-items: flex-start; font-size: 11px; color: var(--text-dim); line-height: 1.55; }
.cd-plan-step + .cd-plan-step { margin-top: 3px; }
.cd-plan-idx {
  flex: 0 0 15px; width: 15px; height: 15px; margin-top: 1px;
  border-radius: 50%; background: var(--accent); color: #fff;
  font-size: 9.5px; display: inline-flex; align-items: center; justify-content: center;
}

/* 深度思考窗口（reasoning_content 流） */
.cd-reason {
  margin: 2px 0 6px; border: 1px solid var(--border);
  border-left: 2px solid var(--violet, #7c5cd6);
  border-radius: 6px; background: var(--bg-hover); overflow: hidden;
}
.cd-reason-head {
  width: 100%; text-align: left;
  border: none; background: none; padding: 5px 9px; cursor: pointer;
  font-size: 11px; color: var(--violet, #7c5cd6);
}
.cd-reason-head:hover { color: var(--text); }
.cd-reason-body {
  padding: 2px 10px 8px 20px;
  font-size: 11px; line-height: 1.65; color: var(--text-dim);
  white-space: pre-wrap; word-break: break-word;
  max-height: 220px; overflow-y: auto;
}

/* 新功能按钮增多后，窄宽度下逐级收起装饰性文字，保证头部不溢出 */
@media (max-width: 1280px) {
  .cd-hint { display: none; }
}
@media (max-width: 1020px) {
  .cd-live { display: none; }
  .cd-btn { padding: 0 7px; }
  .cd-btn span { display: none; }
  /* 「＋新对话」的图标本身也是 span，单独保回来 */
  .cd-new-session > span:first-child { display: inline-flex; }
  .cd-size-btn span { display: none; }
}
/* 尊重系统「减少动态效果」：高度/箭头翻转改为瞬时，光标不闪烁 */
@media (prefers-reduced-motion: reduce) {
  .cd-dock { transition: none; }
  .cd-chevron { transition: none; }
}
</style>
