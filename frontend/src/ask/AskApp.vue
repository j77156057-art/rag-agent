<script setup lang="ts">
// AI 问答首页（/）：用大白话提问，AI 自动检索当前项目代码/资料后流式作答。
// 与代码工作台共享 api.ts / markdown / 主题变量 / 会话 session.js；
// 重操作（建索引、分区开发）统一收口到工作台，本页只保留问答必需能力。
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  aiApi, agentApi, modelApi, modelResidencyApi, projectApi, kbApi, promptApi, harnessApi,
  getProjectId, setProjectId,
} from '../workbench/api'
import type { ModelConfigInfo, ModelStatus, ProjectInfo, SseEvent, WorkflowState } from '../workbench/api'
import { demoMode, probeBackend } from '../workbench/composables/demo'
import AskSidebar from './components/AskSidebar.vue'
import AskMessage from './components/AskMessage.vue'
import FileViewer from './components/FileViewer.vue'
import ModelSettingsDialog from '../workbench/components/ModelSettingsDialog.vue'
import type { AskChatMsg, AskTraceItem } from './types'

// ---------------- 全局状态 ----------------
const config = ref<ModelConfigInfo | null>(null)
const power = ref<ModelStatus | null>(null)
const projects = ref<ProjectInfo[]>([])
const currentPid = ref(getProjectId())

const messages = ref<AskChatMsg[]>([])
let msgSeq = 1
const sending = ref(false)
const historyLoading = ref(false)

const draft = ref('')
const webOn = ref(false)
const thinkingOn = ref(false)
const attachmentError = ref('')
interface PendingImg { file: File; url: string }
const pendingImages = ref<PendingImg[]>([])

const settingsVisible = ref(false)
const viewer = ref<{ path: string; line: number } | null>(null)

const ingestBusy = ref(false)
const ingestMsg = ref('')

const scrollEl = ref<HTMLElement | null>(null)
const stick = ref(true)
let toastTimer = 0
const toast = ref('')

const SUGGESTIONS = [
  '玩家受伤扣血的逻辑在哪里实现？',
  '帮我解释这个项目的战斗数值计算公式',
  '新手引导流程涉及哪些文件？',
]

// ---------------- 派生 ----------------
const thinkingMode = computed(() => config.value?.capability?.thinking || 'none')
const thinkingSupported = computed(() => thinkingMode.value === 'native' || thinkingMode.value === 'toggle')
const thinkingNative = computed(() => thinkingMode.value === 'native')
const thinkingEffective = computed(() => thinkingNative.value || thinkingOn.value)
const visionSupported = computed(() => (config.value?.capability?.vision || 'none') !== 'none')
const guidance = computed(() => config.value?.ollama_status?.guidance || '')
const canSend = computed(() => !sending.value && !historyLoading.value && !gateBlocking.value
  && (draft.value.trim().length > 0 || pendingImages.value.length > 0))

// ---------------- 工作流门控 / 孤儿恢复 ----------------
// 卡片审批门打开期间锁住发送（与工作台一致），避免门后又起新对话
const gateWorkflowIds = ref<Set<string>>(new Set())
const gateBlocking = computed(() => gateWorkflowIds.value.size > 0)
function onWfGate(workflowId: string, open: boolean) {
  const next = new Set(gateWorkflowIds.value)
  if (open) next.add(workflowId); else next.delete(workflowId)
  gateWorkflowIds.value = next
}
function onWfActivity() { void nextTick(followScroll) }

// 刷新/重开后历史只回灌纯文本，工作流卡片不重建；若后端仍有未终结工作流，
// 给出挂回/中断入口（否则它停在审批门且本页没有任何可操作的地方）
const orphanWf = ref<WorkflowState | null>(null)
const orphanBusy = ref(false)
const orphanError = ref('')
async function detectOrphanWorkflow() {
  if (demoMode.value) return
  try {
    const r = await agentApi.workflowActive()
    const wf = r.ok ? (r.workflow ?? null) : null
    orphanWf.value = wf && !messages.value.some(m => m.workflow?.workflowId === wf.workflow_id)
      ? wf : null
    orphanError.value = ''
  } catch { /* 服务不可达时不显示 */ }
}
async function reattachOrphan() {
  const wf = orphanWf.value
  if (!wf || orphanBusy.value) return
  orphanBusy.value = true
  try {
    orphanWf.value = null
    messages.value.push({
      id: msgSeq++, role: 'assistant', text: '', status: 'done',
      trace: [], reasoning: '', notices: [],
      workflow: { workflowId: wf.workflow_id, seed: wf },
      startedAt: Date.now(),
    })
    stick.value = true
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
    const r = await agentApi.workflowInterrupt(wf.workflow_id, '问答页恢复条中断孤儿工作流')
    if (r.ok === false) throw new Error(r.error || '中断失败')
    orphanWf.value = null
  } catch (e) {
    orphanError.value = (e as Error).message || '中断失败，请重试'
  } finally {
    orphanBusy.value = false
  }
}

function sid(): string { return window.DocMindSession.get() }
function interruptedKey(): string { return `docmind.interrupted:${currentPid.value}:${sid()}` }
function draftKey(): string { return `docmind.questionDraft:${currentPid.value}` }

// ---------------- 通用提示 ----------------
function showToast(msg: string) {
  toast.value = msg
  window.clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => { toast.value = '' }, 5000)
}

// ---------------- 启动加载 ----------------
async function loadConfig() {
  config.value = await modelApi.get()
}
async function loadPower() {
  if (demoMode.value) return
  try {
    power.value = await modelResidencyApi.status()
  } catch {
    power.value = null
  }
}
async function loadProjects() {
  const r = await projectApi.list()
  projects.value = r.projects || []
  if (!currentPid.value) currentPid.value = r.current || (r.projects[0]?.project_id || '')
}
async function reloadAll() {
  await Promise.all([loadConfig(), loadProjects(), loadPower()])
}

function setupDemoSidebar() {
  currentPid.value = 'demo'
  projects.value = [{ project_id: 'demo', root: 'D:/Games/MyGame', name: '示例游戏项目' }]
  config.value = {
    llm_provider: 'ollama', llm_model: 'qwen2.5:7b', embedding_provider: 'local',
    has_key: false, providers: [], provider_meta: {}, custom_base_url: '',
    capability: { context_window: 32768, thinking: 'toggle', vision: 'native', video: 'none', cloud: false },
    ingested_files: [],
  }
}

// ---------------- 会话历史 ----------------
async function restoreHistory() {
  historyLoading.value = true
  try {
    const detail = await harnessApi.sessionDetail(sid())
    if (messages.value.length) return
    for (const turn of detail.turns || []) {
      if (turn.user) {
        messages.value.push({ id: msgSeq++, role: 'user', text: turn.user, status: 'done', trace: [], reasoning: '', notices: [] })
      }
      if (turn.assistant) {
        messages.value.push({ id: msgSeq++, role: 'assistant', text: turn.assistant, status: 'done', trace: [], reasoning: '', notices: [] })
      }
    }
    // 中断未恢复的半条回答：只回放内容，不自动重试
    try {
      const partial = JSON.parse(sessionStorage.getItem(interruptedKey()) || 'null') as
        { user?: string; assistant?: string } | null
      if (partial && typeof partial.user === 'string' && typeof partial.assistant === 'string') {
        const dup = (detail.turns || []).some((t) => t.user === partial.user && t.assistant)
        if (dup) {
          sessionStorage.removeItem(interruptedKey())
        } else {
          messages.value.push({ id: msgSeq++, role: 'user', text: partial.user, status: 'done', trace: [], reasoning: '', notices: [] })
          messages.value.push({
            id: msgSeq++, role: 'assistant',
            text: partial.assistant + '\n\n（上次回答已中断，仅恢复已收到的内容，不会自动重试。）',
            status: 'stopped', trace: [], reasoning: '', notices: [],
          })
        }
      }
    } catch { /* 会话存储不可用时忽略 */ }
    await nextTick(scrollToBottom)
  } catch {
    showToast('历史暂未加载，可刷新重试')
  } finally {
    historyLoading.value = false
  }
}

async function newSession() {
  const next = await window.DocMindSession.create()
  if (!next) { showToast('这个会话正在其他标签页使用，无法新建'); return }
  messages.value = []
  pendingImages.value = []
  try { sessionStorage.removeItem(interruptedKey()) } catch { /* ignore */ }
  orphanWf.value = null
  void detectOrphanWorkflow()
  await nextTick(scrollToBottom)
}

// ---------------- SSE 流式问答 ----------------
let chatEpoch = 0
let abortCtl: AbortController | null = null
let partial: { user: string; assistant: string } | null = null
let flushTurn: AskChatMsg | null = null
let flushText = ''
let flushReason = ''
let rafId = 0

function appendTrace(t: AskChatMsg, type: AskTraceItem['type'], text: string) {
  const at = Date.now()
  if (type === 'observation' || type === 'reflection') {
    const pendingAct = [...t.trace].reverse().find((x) => x.type === 'action' && !x.elapsedMs)
    if (pendingAct) pendingAct.elapsedMs = Math.max(0, at - pendingAct.at)
  }
  t.trace.push({ type, text, at })
}

function scheduleFlush() {
  if (rafId) return
  rafId = requestAnimationFrame(() => {
    rafId = 0
    if (flushTurn) {
      if (flushText) flushTurn.text += flushText
      if (flushReason) flushTurn.reasoning += flushReason
    }
    flushText = ''
    flushReason = ''
    followScroll()
  })
}
function drainFlush() {
  if (rafId) { cancelAnimationFrame(rafId); rafId = 0 }
  if (flushTurn) {
    if (flushText) flushTurn.text += flushText
    if (flushReason) flushTurn.reasoning += flushReason
  }
  flushTurn = null
  flushText = ''
  flushReason = ''
}

function savePartial() {
  if (partial?.assistant) {
    try { sessionStorage.setItem(interruptedKey(), JSON.stringify(partial)) } catch { /* ignore */ }
  }
}

async function send(preset?: string) {
  if (sending.value || historyLoading.value) return
  if (gateBlocking.value) { showToast('工作流正在等待你的选择或审批，请先在卡片中处理'); return }
  const q = (preset ?? draft.value).trim()
  if (!q && pendingImages.value.length === 0) return
  const imgs = pendingImages.value.slice()
  const userMsg: AskChatMsg = {
    id: msgSeq++, role: 'user', text: q || '请分析附件图片', status: 'done',
    images: imgs.map((i) => i.url), trace: [], reasoning: '', notices: [],
  }
  const turn: AskChatMsg = {
    id: msgSeq++, role: 'assistant', text: '', status: 'streaming',
    trace: [], reasoning: '', notices: [],
    startedAt: Date.now(),
  }
  messages.value.push(userMsg, turn)
  pendingImages.value = []
  attachmentError.value = ''
  draft.value = ''
  autosize()
  sending.value = true
  stick.value = true
  await nextTick(scrollToBottom)

  if (demoMode.value) {
    await demoAnswer(turn, q)
    return
  }

  const epoch = ++chatEpoch
  const ac = new AbortController()
  abortCtl = ac
  partial = { user: userMsg.text, assistant: '' }
  try { sessionStorage.removeItem(interruptedKey()) } catch { /* ignore */ }

  const live = () => (epoch === chatEpoch ? turn : undefined)
  // 后端在收尾异常时会补发一条「模型无响应…」error final（可能晚于真实 final 到达）。
  // 只接受第一条非空 final 作为正文，后续 final 降级为通知，避免覆盖正确答案。
  let finalApplied = false
  const onEvent = (ev: SseEvent) => {
    if (epoch !== chatEpoch) return
    const t = live()
    if (!t || ev.type === 'context') return
    if (ev.type === 'token' && typeof ev.text === 'string') {
      flushTurn = t
      flushText += ev.text
      scheduleFlush()
    } else if (ev.type === 'final' && typeof ev.text === 'string' && ev.text) {
      if (finalApplied) {
        t.notices.push(ev.text)
        return
      }
      finalApplied = true
      drainFlush()
      t.text = ev.text
      if (partial) partial.assistant = ev.text
      followScroll()
    } else if (ev.type === 'reasoning' && typeof ev.text === 'string') {
      flushTurn = t
      flushReason += ev.text
      scheduleFlush()
    } else if (ev.type === 'notice' && ev.text) {
      t.notices.push(ev.text)
    } else if (ev.type === 'thought' || ev.type === 'action'
               || ev.type === 'observation' || ev.type === 'reflection') {
      if (ev.text) appendTrace(t, ev.type, ev.text)
      followScroll()
    } else if (ev.type === 'workflow') {
      // start_workflow 已执行：卡片直接挂在当前回合内（与工作台同一组件、同一 SSE），
      // 方案选择 / 审批 / 实时进度都在问答页完成，不再只丢一条「转交工作台」通知。
      const w = ev as unknown as { workflow_id?: string; status?: string; phase?: string; kind?: string; request?: string }
      if (w.workflow_id && !t.workflow?.workflowId) {
        t.workflow = {
          workflowId: w.workflow_id,
          seed: { status: w.status, phase: w.phase, kind: w.kind, request: w.request },
        }
        followScroll()
      }
    }
  }

  try {
    const thinkingOpt = thinkingSupported.value
      ? (thinkingNative.value ? null : thinkingOn.value)
      : null
    await aiApi.askGrounded(q, { onEvent, signal: ac.signal }, {
      web: webOn.value,
      thinking: thinkingOpt,
      images: imgs.map((i) => i.file),
    })
    drainFlush()
    if (epoch === chatEpoch) {
      turn.status = turn.text ? 'done' : 'stopped'
      if (!turn.text) turn.text = '（没有返回内容）'
    }
  } catch (e) {
    drainFlush()
    if (epoch !== chatEpoch) return
    if ((e as Error).name === 'AbortError') {
      turn.status = 'stopped'
      if (!turn.text) turn.text = '（已停止）'
      savePartial()
    } else {
      turn.status = 'error'
      turn.error = (e as Error).message || '请求失败'
    }
  } finally {
    if (epoch === chatEpoch) {
      sending.value = false
      abortCtl = null
    }
    partial = null
    void loadPower()
    // 本轮可能发起了工作流：补发事件若因竞态未挂上卡片，靠 orphan 探测兜底
    void detectOrphanWorkflow()
    await nextTick(followScroll)
  }
}

function stop() {
  abortCtl?.abort()
  chatEpoch++
  drainFlush()
  const t = messages.value[messages.value.length - 1]
  if (t && t.role === 'assistant' && t.status === 'streaming') {
    t.status = 'stopped'
    if (!t.text) t.text = '（已停止）'
  }
  savePartial()
  sending.value = false
  abortCtl = null
}

// ---------------- 演示模式脚本回答 ----------------
async function demoAnswer(turn: AskChatMsg, q: string) {
  const epoch = ++chatEpoch
  const sleep = (ms: number) => new Promise((r) => window.setTimeout(r, ms))
  const steps: Array<[AskTraceItem['type'], string]> = [
    ['thought', `分析问题：在示例项目里定位「${q.slice(0, 24) || '该功能'}」相关实现。`],
    ['action', `search_code(query="${q.slice(0, 30) || '伤害逻辑'}", k=8)`],
    ['observation', '命中 8 个切片，最相关：scripts/combat/damage_calc.gd:31 calc_final_damage；scripts/player/player_stats.gd:14 take_damage。'],
    ['action', 'read_file(path="scripts/combat/damage_calc.gd", start_line=1, end_line=80)'],
    ['observation', '已读取文件：伤害公式在第 31 行，玩家扣血入口在 player_stats.gd:14。'],
  ]
  for (const [type, text] of steps) {
    await sleep(260)
    if (epoch !== chatEpoch) { turn.status = 'stopped'; sending.value = false; return }
    appendTrace(turn, type, text)
    await nextTick(followScroll)
  }
  await sleep(200)
  const answer =
    '根据当前项目代码，伤害计算位于 scripts/combat/damage_calc.gd:31 的 calc_final_damage()：\n\n'
    + '- 基础伤害 = 攻击力 × 暴击倍率\n- 防御减伤按 `defense / (defense + 100)` 收敛\n'
    + '- 玩家实际扣血入口在 scripts/player/player_stats.gd:14\n\n'
    + '建议先看这两个函数；需要调整数值或修复逻辑，可以直接在这里告诉我。'
  const chunks = answer.match(/.{1,4}/g) || []
  for (const c of chunks) {
    if (epoch !== chatEpoch) { turn.status = 'stopped'; sending.value = false; return }
    turn.text += c
    await sleep(16)
    followScroll()
  }
  turn.status = 'done'
  sending.value = false
  await nextTick(followScroll)
}

// ---------------- 输入与附件 ----------------
const textareaEl = ref<HTMLTextAreaElement | null>(null)
const imgInputEl = ref<HTMLInputElement | null>(null)
function autosize() {
  const el = textareaEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 160) + 'px'
}
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    void send()
  }
}
function addImages(files: FileList | File[]) {
  const incoming = Array.from(files || [])
  const valid = incoming.filter((f) => /^image\/(png|jpeg|webp|gif)$/i.test(f.type))
  if (valid.length !== incoming.length) attachmentError.value = '仅支持 PNG、JPEG、WEBP、GIF 图片。'
  else attachmentError.value = ''
  const merged = [...pendingImages.value, ...valid.map((f) => ({ file: f, url: URL.createObjectURL(f) }))].slice(0, 4)
  if (pendingImages.value.length + valid.length > 4) attachmentError.value = '单条消息最多附带 4 张图片。'
  pendingImages.value = merged
}
function removeImage(i: number) {
  const [item] = pendingImages.value.splice(i, 1)
  if (item) URL.revokeObjectURL(item.url)
}
function onImagePick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) addImages(input.files)
  input.value = ''
}
function onPaste(e: ClipboardEvent) {
  const files = Array.from(e.clipboardData?.files || []).filter((f) => f.type.startsWith('image/'))
  if (files.length) { e.preventDefault(); addImages(files) }
}
function onDrop(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer?.files?.length) addImages(e.dataTransfer.files)
}

async function enhance() {
  const d = draft.value.trim()
  if (!d) { showToast('请先写下要增强的提问草稿'); return }
  if (demoMode.value) { showToast('示例演示模式下无法增强，请在本机启动 DocMind 后使用'); return }
  try {
    const r = await promptApi.enhance(d)
    if (r.ok && r.enhanced) {
      draft.value = r.enhanced
      autosize()
      showToast(r.mode === 'llm' ? '已用模型重写提问，可编辑后发送' : '已本地优化提问，可编辑后发送')
    } else {
      showToast('增强失败：' + (r.error || '未知错误'))
    }
  } catch (e) {
    showToast('增强请求失败：' + (e as Error).message)
  }
}

// ---------------- 侧栏操作 ----------------
async function onIngest(file: File) {
  ingestBusy.value = true
  ingestMsg.value = `正在摄取：${file.name} …`
  try {
    const r = await kbApi.ingest(file)
    if (r.ok) {
      ingestMsg.value = `已摄取 ${file.name}（${r.chunks ?? 0} 个切片）`
      await loadConfig()
    } else {
      ingestMsg.value = '摄取失败：' + (r.error || '未知错误')
    }
  } catch (e) {
    ingestMsg.value = '摄取失败：' + (e as Error).message
  } finally {
    ingestBusy.value = false
    window.setTimeout(() => { ingestMsg.value = '' }, 8000)
  }
}

async function onPower(action: 'on' | 'off') {
  try {
    const r = await modelResidencyApi.power(action)
    if (r.ok) {
      showToast(action === 'off'
        ? `已卸载 ${(r.unloaded || []).join('、') || '模型'}，下次对话自动重载`
        : `模型 ${(r.preloaded || []).join('、') || ''} 已预加载`.trim())
    } else {
      showToast('模型开关操作失败：' + (r.error || '未知错误'))
    }
  } catch (e) {
    showToast('模型开关请求失败：' + (e as Error).message)
  }
  await loadPower()
}

async function onActivate(pid: string) {
  if (!pid || pid === currentPid.value) return
  try {
    const r = await projectApi.activate(pid)
    if (!r.ok) { showToast('切换项目失败：' + (r.error || '未知错误')); return }
    setProjectId(pid)
    currentPid.value = pid
    messages.value = []
    await window.DocMindSession.create()
    await reloadAll()
    showToast('已切换项目：' + (r.project?.name || pid))
    await nextTick(scrollToBottom)
  } catch (e) {
    showToast('切换项目失败：' + (e as Error).message)
  }
}

async function openSettings() {
  if (demoMode.value) { showToast('示例演示模式下不可修改模型设置'); return }
  try {
    await loadConfig()
    settingsVisible.value = true
  } catch (e) {
    showToast('读取模型配置失败：' + (e as Error).message)
  }
}
function onSettingsSaved(info: ModelConfigInfo) {
  config.value = info
  void loadPower()
  if (info.ollama_status?.guidance) showToast('设置已保存，但 Ollama 状态需要关注')
  else showToast('模型设置已保存')
}

function openFile(path: string, line: number) {
  viewer.value = { path, line }
}

// ---------------- 滚动 ----------------
function onScroll() {
  const el = scrollEl.value
  if (!el) return
  stick.value = el.scrollHeight - el.scrollTop - el.clientHeight < 80
}
function scrollToBottom() {
  const el = scrollEl.value
  if (el) el.scrollTop = el.scrollHeight
}
function followScroll() {
  if (stick.value) scrollToBottom()
}

// ---------------- 生命周期 ----------------
onMounted(async () => {
  try { draft.value = sessionStorage.getItem(draftKey()) || '' } catch { /* ignore */ }
  window.addEventListener('pagehide', () => {
    try { sessionStorage.setItem(draftKey(), draft.value) } catch { /* ignore */ }
    if (sending.value) { savePartial(); abortCtl?.abort() }
  })
  window.setInterval(() => { if (!demoMode.value && !document.hidden) void loadPower() }, 20000)
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !demoMode.value) void loadPower()
  })

  const online = await probeBackend()
  if (demoMode.value || !online) {
    setupDemoSidebar()
    return
  }
  try {
    await reloadAll()
    await restoreHistory()
    void detectOrphanWorkflow()
  } catch (e) {
    showToast('读取配置失败：' + (e as Error).message)
  }
})
</script>

<template>
  <div class="ask-shell">
    <AskSidebar
      :projects="projects"
      :current-pid="currentPid"
      :config="config"
      :power="power"
      :demo="demoMode"
      :ingest-busy="ingestBusy"
      :ingest-msg="ingestMsg"
      @activate="onActivate"
      @ingest="onIngest"
      @power="onPower"
      @open-settings="openSettings"
    />

    <main class="ask-main">
      <header class="ask-topbar">
        <div class="topbar-title">
          <h1>AI 问答</h1>
          <span class="topbar-sub">提问 → 自动检索当前项目代码与资料 → 给出带文件行号的答案</span>
        </div>
        <div class="topbar-actions">
          <button class="tb-btn" title="清空当前标签页对话并开始新会话" @click="newSession">
            <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M12 5V2L7 6l5 4V7a5 5 0 11-5 5H5a7 7 0 107-7z"/></svg>
            新会话
          </button>
          <a class="tb-btn primary" href="/workbench">
            代码工作台
            <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M8.5 16.5L16 9H9V7h10v10h-2v-7l-7.5 7.5z"/></svg>
          </a>
        </div>
      </header>

      <div v-if="demoMode" class="banner info">
        示例演示模式：当前未连接本地 DocMind 服务，以下回答为演示数据。启动本机服务后刷新即可使用真实问答。
      </div>
      <div v-else-if="guidance" class="banner warn">
        <span>{{ guidance }}</span>
      </div>

      <div ref="scrollEl" class="chat-scroll" @scroll="onScroll">
        <div class="chat-col">
          <div v-if="!messages.length" class="welcome">
            <div class="welcome-logo" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="26" height="26"><path fill="#fff" d="M12 2l2.4 5.6L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.6-1.4z"/></svg>
            </div>
            <h2>用大白话问，我帮你翻代码</h2>
            <p>AI 会先检索当前项目的代码与已上传资料，再给出答案，并附上可点击的文件行号。</p>
            <div class="suggestions">
              <button v-for="s in SUGGESTIONS" :key="s" class="suggestion" @click="send(s)">{{ s }}</button>
            </div>
          </div>

          <template v-else>
            <AskMessage
              v-for="m in messages" :key="m.id" :msg="m"
              @open-file="openFile"
              @wf-gate="(open: boolean) => onWfGate(m.workflow!.workflowId, open)"
              @wf-activity="onWfActivity"
            />
          </template>
        </div>
      </div>

      <!-- 孤儿工作流恢复条：刷新后卡片丢失但后端仍未终结 -->
      <div v-if="orphanWf" class="wf-orphan" role="alert">
        <span class="wf-orphan-glyph" aria-hidden="true">⚠</span>
        <div class="wf-orphan-body">
          <div>该项目有一个未结束的工作流（{{ orphanWf.status }}），当前对话里没有它的审批卡片。</div>
          <div v-if="orphanError" class="wf-orphan-err">{{ orphanError }}</div>
        </div>
        <button class="wf-orphan-btn" :disabled="orphanBusy" @click="reattachOrphan">挂回对话继续</button>
        <button class="wf-orphan-btn danger" :disabled="orphanBusy" @click="interruptOrphan">
          {{ orphanBusy ? '处理中…' : '中断它' }}
        </button>
      </div>

      <div v-if="gateBlocking" class="gate-hint">工作流正在等待你的选择或审批，处理后才能继续提问</div>

      <footer class="composer-wrap">
        <div class="composer" :class="{ blocked: gateBlocking }">
          <div v-if="pendingImages.length" class="img-preview">
            <div v-for="(img, i) in pendingImages" :key="img.url" class="img-thumb">
              <img :src="img.url" :alt="img.file.name">
              <button class="img-x" @click="removeImage(i)">×</button>
            </div>
          </div>
          <textarea
            ref="textareaEl"
            v-model="draft"
            rows="1"
            placeholder="问点什么：例如「玩家死亡后奖励是怎么结算的？」（Enter 发送，Shift+Enter 换行）"
            @keydown="onKeydown"
            @input="autosize"
            @paste="onPaste"
            @dragover.prevent
            @drop="onDrop"
          ></textarea>
          <div v-if="attachmentError" class="attach-err">{{ attachmentError }}</div>
          <div class="composer-bar">
            <div class="bar-left">
              <template v-if="visionSupported || demoMode">
                <button class="tool-btn" title="附加图片（PNG/JPEG/WEBP/GIF，最多 4 张，支持粘贴/拖拽）" @click="imgInputEl?.click()">
                  <svg viewBox="0 0 24 24" width="15" height="15"><path fill="currentColor" d="M4 5h16a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V7a2 2 0 012-2zm4 4a2 2 0 100 4 2 2 0 000-4zm12 7l-5-5-9 9H4v-2l8-8 6 6h2z"/></svg>
                </button>
                <input ref="imgInputEl" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden @change="onImagePick">
              </template>
              <button
                v-if="thinkingSupported"
                class="chip"
                :class="{ on: thinkingEffective, native: thinkingNative }"
                :disabled="thinkingNative"
                :title="thinkingNative ? '该模型内置深度思考，始终开启' : '回答前先推理，耗时更长但更稳'"
                @click="thinkingOn = !thinkingOn"
              >深度思考</button>
              <button
                class="chip"
                :class="{ on: webOn }"
                title="开启后 AI 可检索互联网获取最新信息"
                @click="webOn = !webOn"
              >联网搜索</button>
              <button class="chip ghost" title="把草稿重写为更清晰的提问" @click="enhance">增强提问</button>
            </div>
            <div class="bar-right">
              <button v-if="sending" class="send-btn stop" title="停止回答" @click="stop">
                <span class="stop-ico"></span> 停止
              </button>
              <button v-else class="send-btn" :disabled="!canSend" title="发送（Enter）" @click="send()">
                发送
                <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M2 21l21-9L2 3v7l15 2-15 2z"/></svg>
              </button>
            </div>
          </div>
        </div>
      </footer>

      <transition name="toast">
        <div v-if="toast" class="toast">{{ toast }}</div>
      </transition>

      <ModelSettingsDialog
        :visible="settingsVisible"
        :config="config"
        @close="settingsVisible = false"
        @saved="onSettingsSaved"
      />
      <FileViewer v-if="viewer" :path="viewer.path" :line="viewer.line" @close="viewer = null" />
    </main>
  </div>
</template>

<style scoped>
.ask-shell { display: flex; height: 100vh; overflow: hidden; }

.ask-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* 顶栏 */
.ask-topbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 24px;
  background: var(--bg-raised);
  border-bottom: 1px solid var(--border);
}
.topbar-title h1 { margin: 0; font-size: 15px; font-weight: 700; }
.topbar-sub { font-size: 12px; color: var(--text-faint); margin-left: 10px; }
.topbar-actions { display: flex; gap: 8px; }
.tb-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--border-strong);
  background: var(--bg-raised);
  color: var(--text);
  border-radius: 9px;
  padding: 6px 12px;
  font-size: 12.5px;
  cursor: pointer;
  text-decoration: none;
  transition: background .15s, border-color .15s;
}
.tb-btn:hover { background: var(--bg-hover); }
.tb-btn.primary { border-color: var(--accent); color: var(--accent); }
.tb-btn.primary:hover { background: var(--accent-soft); }

/* 横幅 */
.banner {
  flex: 0 0 auto;
  margin: 12px 24px 0;
  padding: 9px 14px;
  border-radius: 10px;
  font-size: 12.5px;
  line-height: 1.55;
}
.banner.info { background: rgba(47, 111, 237, .08); border: 1px solid rgba(47, 111, 237, .2); color: var(--accent); }
.banner.warn { background: rgba(200, 129, 28, .10); border: 1px solid rgba(200, 129, 28, .28); color: var(--amber); }

/* 对话区 */
.chat-scroll { flex: 1; overflow-y: auto; }
.chat-col {
  max-width: 860px;
  margin: 0 auto;
  padding: 28px 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.welcome { text-align: center; padding: 7vh 16px 0; color: var(--text-muted); }
.welcome-logo {
  width: 54px; height: 54px;
  margin: 0 auto 16px;
  border-radius: 16px;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 10px 26px rgba(47, 111, 237, .28);
}
.welcome h2 { margin: 0 0 8px; font-size: 20px; color: var(--text); }
.welcome p { margin: 0 0 22px; font-size: 13.5px; }
.suggestions { display: flex; flex-direction: column; gap: 8px; max-width: 480px; margin: 0 auto; }
.suggestion {
  border: 1px solid var(--border);
  background: var(--bg-raised);
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 13px;
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: border-color .15s, background .15s;
}
.suggestion:hover { border-color: var(--accent); background: var(--accent-soft); }

/* 输入区 */
.composer-wrap { flex: 0 0 auto; padding: 8px 24px 18px; background: linear-gradient(transparent, var(--bg) 28%); }
.composer {
  max-width: 860px;
  margin: 0 auto;
  background: var(--bg-raised);
  border: 1px solid var(--border-strong);
  border-radius: 16px;
  box-shadow: var(--shadow-soft);
  padding: 10px 12px 8px;
}
.composer:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(47, 111, 237, .10); }
.composer textarea {
  width: 100%;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  font-family: inherit;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text);
  padding: 4px 6px;
  max-height: 160px;
}
.composer textarea::placeholder { color: var(--text-faint); }

.img-preview { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 6px 8px; }
.img-thumb { position: relative; }
.img-thumb img {
  width: 64px; height: 64px; object-fit: cover;
  border: 1px solid var(--border); border-radius: 9px;
}
.img-x {
  position: absolute; top: -7px; right: -7px;
  width: 18px; height: 18px;
  border: none; border-radius: 50%;
  background: var(--text); color: #fff;
  font-size: 12px; line-height: 1;
  cursor: pointer;
  display: flex; align-items: center; justify-content: center;
}
.attach-err { padding: 0 6px 6px; font-size: 12px; color: var(--danger); }

.composer-bar { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding-top: 6px; border-top: 1px solid var(--border); }
.bar-left { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.bar-right { display: flex; flex: 0 0 auto; }
.tool-btn {
  border: none;
  background: none;
  color: var(--text-faint);
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  display: inline-flex;
}
.tool-btn:hover { background: var(--bg-hover); color: var(--accent); }
.chip {
  border: 1px solid var(--border-strong);
  background: var(--bg-raised);
  color: var(--text-muted);
  border-radius: 999px;
  font-size: 12px;
  padding: 4px 12px;
  cursor: pointer;
  transition: all .15s;
}
.chip:hover { border-color: var(--accent); color: var(--accent); }
.chip.on { background: var(--accent-soft); border-color: var(--accent); color: var(--accent); }
.chip.native { background: rgba(122, 90, 248, .10); border-color: var(--accent-2); color: var(--accent-2); cursor: default; }
.chip.ghost { border-style: dashed; }

.send-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  border-radius: 10px;
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
  transition: background .15s;
}
.send-btn:hover { background: #245fd6; border-color: #245fd6; }
.send-btn:disabled { opacity: .45; cursor: default; background: var(--accent); border-color: var(--accent); }
.send-btn.stop { background: var(--bg-raised); color: var(--danger); border-color: var(--danger); }
.send-btn.stop:hover { background: rgba(224, 72, 79, .08); }
.stop-ico { width: 10px; height: 10px; background: currentColor; border-radius: 2px; }

/* toast */
.toast {
  position: fixed;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(30, 38, 54, .92);
  color: #fff;
  font-size: 13px;
  padding: 9px 18px;
  border-radius: 10px;
  box-shadow: var(--shadow-pop);
  z-index: 200;
  max-width: 70vw;
}
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, -8px); }
.toast-enter-active, .toast-leave-active { transition: all .2s ease; }

/* 孤儿工作流恢复条 / 审批门提示 */
.wf-orphan {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 24px 8px;
  padding: 9px 14px;
  border: 1px solid var(--amber);
  background: rgba(214, 158, 46, .08);
  border-radius: 10px;
  font-size: 12.5px;
  color: var(--text-muted);
}
.wf-orphan-glyph { color: var(--amber); font-size: 15px; }
.wf-orphan-body { flex: 1; min-width: 0; }
.wf-orphan-err { color: var(--danger); margin-top: 3px; }
.wf-orphan-btn {
  flex: 0 0 auto;
  border: 1px solid var(--border-strong);
  background: var(--bg-raised);
  color: var(--text);
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.wf-orphan-btn:hover { border-color: var(--accent); color: var(--accent); }
.wf-orphan-btn.danger:hover { border-color: var(--danger); color: var(--danger); }
.wf-orphan-btn:disabled { opacity: .55; cursor: default; }
.gate-hint {
  flex: 0 0 auto;
  margin: 0 24px 6px;
  text-align: center;
  font-size: 12px;
  color: var(--amber);
}
.composer.blocked { opacity: .7; }
.composer.blocked textarea { pointer-events: none; }
</style>
