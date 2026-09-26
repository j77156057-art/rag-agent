<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { agentApi, getProjectId, workflowEvents } from '../api'
import type { VisualFeedbackRecord, WorkflowEvent, WorkflowState, WorkflowSummary } from '../api'
import { blobDataUrl } from '../previewFeedback'
import { capturePreviewFrame, refreshPreviewFrame } from '../previewCapture'
import type { PreviewFeedbackRequest } from '../previewFeedback'
import CockpitApprovalQueue from './CockpitApprovalQueue.vue'

const goal = ref('')
const workflow = ref<WorkflowState | null>(null)
const selectedId = ref('')
const history = ref<WorkflowSummary[]>([])
const error = ref('')
const busy = ref(false)
const feedbackOpenId = ref('')
const feedbackText = ref('')
const liveWidth = ref(100)
const liveHeight = ref(360)
const liveReload = ref<Record<string, number>>({})
const annotationId = ref('')
const feedbackRegion = ref<{ x: number; y: number; width: number; height: number } | null>(null)
interface FeedbackRecord extends VisualFeedbackRecord { sentAt: string }
const feedbackHistory = ref<FeedbackRecord[]>([])
const feedbackSending = ref(false)
const snapshotBusyId = ref('')
const regionOwner = ref('')
const feedbackStatusLabel: Record<string, string> = { pending: '待发送', processing: 'AI 处理中', awaiting_review: '等待检查效果', failed: '处理未完成', accepted: '效果已确认', needs_changes: '需要继续修改', sent: '历史记录，发送状态未确认' }
let resizeState: { startX: number; startY: number; width: number; height: number; containerWidth: number } | null = null
let regionState: { id: string; rect: DOMRect; startX: number; startY: number } | null = null
const liveFrameRefs = new Map<string, HTMLIFrameElement>()
const captureErrors = new Map<string, string>()
const feedbackUpdates = new Map<string, Promise<void>>()
let unsubscribe: (() => void) | null = null
let pollTimer: ReturnType<typeof setInterval> | null = null
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let generation = 0
const statusLabel: Record<string, string> = {
  awaiting_choice: '等待选择方案', generating_options: '正在设计方案', planning: '正在规划',
  planned: '等待启动', awaiting_approval: '等待计划审核', executing: '持续执行中',
  completed: '执行完成，等待验收', failed: '执行失败', interrupted: '已暂停',
  awaiting_research: '等待资料',
}
const pending = computed(() => workflow.value &&
  ['awaiting_choice', 'planned', 'awaiting_approval', 'awaiting_research'].includes(workflow.value.status))
const taskResults = computed<Record<string, Record<string, unknown>>>(() => {
  const report = workflow.value?.results || {}
  return (report.results as Record<string, Record<string, unknown>> | undefined)
    || report as Record<string, Record<string, unknown>>
})
const doneCount = computed(() => Object.values(taskResults.value).filter(r => r.status === 'ok').length)
const recentEvents = computed(() => (workflow.value?.events || []).slice(-12).reverse())
const previewArtifacts = computed(() => workflow.value?.preview?.artifacts || [])
const previewKindLabel: Record<string, string> = {
  code: '代码', text: '文本', diff: '差异', image: '图片', audio: '音频',
  video: '视频', interactive: '实时画面', model: '模型', structured: '结构化数据', binary: '文件', unknown: '证据',
}
const capabilityLabel: Record<string, string> = {
  read_local: '读取项目', read_external: '读取外部资料', write_local: '写入项目',
  write_external: '写入外部服务', exec: '执行命令', network: '联网 / MCP', admin: '管理操作',
}
function leaseIsExpired(lease: WorkflowState['capability_lease']): boolean {
  return !!lease?.expires_at_epoch && Date.now() / 1000 >= lease.expires_at_epoch
}
function leaseStatusLabel(lease: WorkflowState['capability_lease']): string {
  if (!lease) return '未启用'
  if (lease.status === 'released') return '已释放'
  if (leaseIsExpired(lease)) return '已过期'
  return '当前有效'
}
function previewSource(artifact: { uri?: string; path?: string; id?: string }): string {
  const uri = String(artifact.uri || '')
  if (/^https?:\/\//i.test(uri) || /^\/(?!\/)/.test(uri)) return uri
  const id = String(artifact.id || '')
  return id && workflow.value?.workflow_id
    ? `/api/agent/workflow/${encodeURIComponent(workflow.value.workflow_id)}/preview/artifact/${encodeURIComponent(id)}`
    : ''
}
function feedbackStorageKey(id = workflow.value?.workflow_id) {
  return id ? `docmind.cockpit.feedback.${id}` : ''
}
function loadFeedbackHistory(id: string) {
  feedbackHistory.value = []
  try {
    const raw = window.localStorage.getItem(feedbackStorageKey(id))
    const parsed = raw ? JSON.parse(raw) : []
    if (Array.isArray(parsed)) feedbackHistory.value = parsed.filter(row => row && typeof row.note === 'string').slice(0, 20).map(mapFeedback)
  } catch { /* 浏览器存储不可用时只保留当前会话记录 */ }
}
function mapFeedback(item: VisualFeedbackRecord | Record<string, unknown>): FeedbackRecord {
  return {
    ...item as VisualFeedbackRecord,
    id: String(item.id || `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`),
    label: String(item.label || '实时画面'),
    note: String(item.note || ''),
    region: item.region as FeedbackRecord['region'],
    screenshot: !!item.screenshot,
    sent_at: String(item.sent_at || ('sentAt' in item && item.sentAt) || new Date().toISOString()),
    sentAt: String(item.sent_at || ('sentAt' in item && item.sentAt) || new Date().toISOString()),
  }
}
function upsertFeedback(record: FeedbackRecord) {
  const rows = [record, ...feedbackHistory.value.filter(row => row.id !== record.id)]
  feedbackHistory.value = rows.sort((a, b) => b.sentAt.localeCompare(a.sentAt)).slice(0, 20)
  saveFeedbackHistory()
}
function saveFeedbackHistory() {
  const key = feedbackStorageKey()
  if (!key) return
  try { window.localStorage.setItem(key, JSON.stringify(feedbackHistory.value.slice(0, 20))) } catch { /* ignore quota/private mode */ }
}
function liveFrameStyle() {
  return { width: `${liveWidth.value}%`, height: `${liveHeight.value}px` }
}
function startResize(ev: PointerEvent) {
  const handle = ev.currentTarget as HTMLElement
  handle.setPointerCapture(ev.pointerId)
  resizeState = { startX: ev.clientX, startY: ev.clientY, width: liveWidth.value, height: liveHeight.value,
    containerWidth: handle.parentElement?.parentElement?.clientWidth || 300 }
  window.addEventListener('pointermove', resizeLiveFrame)
  window.addEventListener('pointerup', stopResize, { once: true })
}
function resizeLiveFrame(ev: PointerEvent) {
  if (!resizeState) return
  liveWidth.value = Math.max(45, Math.min(100, resizeState.width + (ev.clientX - resizeState.startX) * 100 / resizeState.containerWidth))
  liveHeight.value = Math.max(220, Math.min(720, resizeState.height + ev.clientY - resizeState.startY))
}
function stopResize() {
  resizeState = null
  window.removeEventListener('pointermove', resizeLiveFrame)
  window.removeEventListener('pointerup', stopResize)
}
function setLiveFrameRef(id: string | undefined, value: unknown) {
  if (!id) return
  if (value instanceof HTMLIFrameElement) liveFrameRefs.set(id, value)
  else liveFrameRefs.delete(id)
}
function reloadLive(id: string | undefined) {
  if (!id) return
  liveReload.value = { ...liveReload.value, [id]: (liveReload.value[id] || 0) + 1 }
}
function fullscreenLive(id: string | undefined) {
  if (!id) return
  const frame = liveFrameRefs.get(id)
  void frame?.parentElement?.requestFullscreen?.().catch(() => { error.value = '当前环境未允许全屏查看' })
}
function toggleAnnotation(id: string) {
  regionOwner.value = id
  feedbackRegion.value = null
  annotationId.value = annotationId.value === id ? '' : id
  if (annotationId.value !== id) {
    feedbackRegion.value = null
    regionState = null
  }
}
function beginAnnotation(ev: PointerEvent, id: string) {
  const target = ev.currentTarget as HTMLElement
  const rect = target.getBoundingClientRect()
  regionState = { id, rect, startX: ev.clientX, startY: ev.clientY }
  regionOwner.value = id
  feedbackRegion.value = { x: 0, y: 0, width: 0, height: 0 }
  target.setPointerCapture?.(ev.pointerId)
}
function moveAnnotation(ev: PointerEvent, id: string) {
  if (!regionState || regionState.id !== id) return
  const { rect, startX, startY } = regionState
  const endX = Math.max(rect.left, Math.min(rect.right, ev.clientX))
  const endY = Math.max(rect.top, Math.min(rect.bottom, ev.clientY))
  const left = Math.min(startX, endX) - rect.left
  const top = Math.min(startY, endY) - rect.top
  feedbackRegion.value = {
    x: Math.round((left / rect.width) * 1000) / 10,
    y: Math.round((top / rect.height) * 1000) / 10,
    width: Math.round((Math.abs(endX - startX) / rect.width) * 1000) / 10,
    height: Math.round((Math.abs(endY - startY) / rect.height) * 1000) / 10,
  }
}
function endAnnotation() {
  regionState = null
  annotationId.value = ''
  if (feedbackRegion.value?.width && feedbackRegion.value.height) feedbackOpenId.value = regionOwner.value
}
function regionStyle() {
  const r = feedbackRegion.value
  if (!r) return {}
  return { left: `${r.x}%`, top: `${r.y}%`, width: `${r.width}%`, height: `${r.height}%` }
}
async function captureLiveFrame(id: string): Promise<Blob | null> {
  try {
    const frame = liveFrameRefs.get(id)
    if (!frame) throw new Error('预览尚未显示，请展开画面后重试')
    const image = await capturePreviewFrame(frame)
    captureErrors.delete(id)
    return image
  } catch (e) {
    captureErrors.set(id, (e as Error).message || '当前画面无法截图')
    return null
  }
}
function toggleFeedback(id: string) {
  feedbackOpenId.value = feedbackOpenId.value === id ? '' : id
  if (feedbackOpenId.value !== id) feedbackText.value = ''
}
async function sendFeedback(artifact: { id?: string; label?: string }) {
  const note = feedbackText.value.trim()
  if (!note || !workflow.value || feedbackSending.value) return
  feedbackSending.value = true
  const wid = workflow.value.workflow_id
  const projectId = getProjectId()
  const ticket = generation
  const label = artifact.label || '实时画面'
  const region = regionOwner.value === artifact.id ? feedbackRegion.value : null
  let prompt = [
    `请继续修改当前项目。用户针对自主开发舱中的「${label}」提出反馈：`,
    note,
    region && region.width > 0 && region.height > 0
      ? `用户标注区域（相对画面百分比）：左 ${region.x}%、上 ${region.y}%、宽 ${region.width}%、高 ${region.height}%。请优先检查该区域。`
      : '',
    `当前预览尺寸约为 ${Math.round(liveWidth.value)}% × ${Math.round(liveHeight.value)}px。请先使用可用的截图、运行或项目检查工具核对实际效果，再直接完成修改并验证。工作流：${workflow.value.workflow_id}`,
  ].filter(Boolean).join('\n')
  const screenshot = artifact.id ? await captureLiveFrame(artifact.id) : null
  if (!screenshot) prompt += `\n本次反馈未附截图：${captureErrors.get(artifact.id || '') || '当前画面无法截图'}。请使用项目运行或检查工具确认效果，不要声称已看过画面。`
  const sentAt = new Date().toISOString()
  const record: FeedbackRecord = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    artifact_id: artifact.id, label, note, region: region && region.width > 0 && region.height > 0 ? { ...region } : undefined,
    screenshot: !!screenshot, sentAt, sent_at: sentAt, status: 'pending', snapshots: {},
  }
  try {
    const saved = await agentApi.workflowVisualFeedback(wid, {
      id: record.id, label: record.label, note: record.note, region: record.region,
      artifact_id: record.artifact_id, screenshot: record.screenshot, sent_at: record.sentAt,
    }, projectId)
    if (!saved.ok || !saved.feedback) throw new Error(saved.error || '反馈保存失败')
    Object.assign(record, mapFeedback(saved.feedback))
    if (screenshot) {
      const snapshot = await agentApi.workflowFeedbackSnapshot(wid, record.id, 'before', await blobDataUrl(screenshot), projectId)
      if (!snapshot.ok || !snapshot.feedback) throw new Error(snapshot.error || '截图保存失败')
      Object.assign(record, mapFeedback(snapshot.feedback))
    }
    if (ticket !== generation || projectId !== getProjectId()) return
    upsertFeedback(record)
    dispatchFeedback(record, prompt, screenshot ? [screenshot] : [], wid, projectId)
    feedbackText.value = ''
    feedbackOpenId.value = ''
    feedbackRegion.value = null
  } catch (e) {
    if (ticket === generation) {
      record.detail = (e as Error).message; upsertFeedback(record)
      error.value = `反馈已保留，尚未发送：${record.detail}`
    }
  } finally { feedbackSending.value = false }
}

function dispatchFeedback(record: FeedbackRecord, prompt: string, images: Blob[], wid: string, projectId: string) {
  const ticket = generation
  let reviewStarted = false
  let revision = 0
  const update = async (status: string, detail = '') => {
    const current = ++revision
    Object.assign(record, { status, detail })
    if (workflow.value?.workflow_id === wid && getProjectId() === projectId) upsertFeedback(record)
    const previous = feedbackUpdates.get(record.id) || Promise.resolve()
    const queued = previous.then(async () => {
      try {
        const result = await agentApi.workflowFeedbackStatus(wid, record.id, status, detail, projectId)
        if (!result.ok || !result.feedback) throw new Error(result.error || '反馈状态未同步')
        if (current === revision) Object.assign(record, mapFeedback(result.feedback))
      } catch {
        if (current === revision) record.detail = `${detail}（状态尚未同步，请稍后重试）`
      }
      if (current === revision && workflow.value?.workflow_id === wid && getProjectId() === projectId) upsertFeedback(record)
    })
    feedbackUpdates.set(record.id, queued)
    await queued
    if (feedbackUpdates.get(record.id) === queued) feedbackUpdates.delete(record.id)
  }
  const onStatus: PreviewFeedbackRequest['onStatus'] = (status, detail) => {
    if (status !== 'awaiting_review') { void update(status, detail); return }
    if (reviewStarted) return
    reviewStarted = true
    void (async () => {
      await update('processing', 'AI 回复已结束，正在刷新预览并记录效果')
      const failure = ticket === generation && workflow.value?.workflow_id === wid && getProjectId() === projectId
        ? await captureAfter(record, true)
        : '页面或项目已切换，请回到原工作流记录当前效果'
      await update('awaiting_review', failure
        ? `AI 回复已结束，自动记录未完成：${failure}。请检查效果后手动记录。`
        : '已刷新预览并保存复验画面，请对照检查；截图不代表功能或源码已通过验收')
    })()
  }
  const request: PreviewFeedbackRequest = { prompt, images, projectId, onStatus }
  window.dispatchEvent(new CustomEvent('docmind:send-chat', { detail: request }))
}

async function retryFeedback(record: FeedbackRecord) {
  if (!workflow.value || feedbackSending.value) return
  feedbackSending.value = true
  const wid = workflow.value.workflow_id, projectId = getProjectId(), ticket = generation
  try {
    const saved = await agentApi.workflowVisualFeedback(wid, record as unknown as Record<string, unknown>, projectId)
    if (!saved.ok || !saved.feedback) throw new Error(saved.error || '反馈保存失败')
    Object.assign(record, mapFeedback(saved.feedback))
    const images: Blob[] = []
    if (record.snapshots?.before) {
      const response = await fetch(agentApi.workflowFeedbackSnapshotUrl(wid, record.id, 'before', projectId))
      if (!response.ok) throw new Error('修改前截图读取失败')
      images.push(await response.blob())
    }
    if (ticket !== generation || projectId !== getProjectId()) return
    dispatchFeedback(record, `请根据当前项目的视觉反馈继续修改并验证：\n${record.note}\n工作流：${wid}\n预览：${record.label}\n标注区域百分比：${JSON.stringify(record.region || {})}\n先检查当前项目状态；之前执行的操作不会自动撤销，请避免重复修改。`, images, wid, projectId)
  } catch (e) { if (ticket === generation) error.value = (e as Error).message }
  finally { feedbackSending.value = false }
}

async function captureAfter(record: FeedbackRecord, refresh = false): Promise<string | null> {
  if (!workflow.value || snapshotBusyId.value) return '另一个截图正在保存，请稍后重试'
  const wid = workflow.value.workflow_id, ticket = generation, projectId = getProjectId()
  snapshotBusyId.value = record.id
  try {
    if (refresh) {
      const frame = liveFrameRefs.get(record.artifact_id || '')
      if (!frame) throw new Error('预览尚未显示，请展开画面后重试')
      await refreshPreviewFrame(frame)
    }
    if (ticket !== generation || projectId !== getProjectId()) throw new Error('项目或工作流已切换')
    const image = record.artifact_id ? await captureLiveFrame(record.artifact_id) : null
    if (!image) throw new Error(captureErrors.get(record.artifact_id || '') || '当前画面无法读取截图')
    if (ticket !== generation || projectId !== getProjectId()) throw new Error('项目或工作流已切换')
    const saved = await agentApi.workflowFeedbackSnapshot(wid, record.id, 'after', await blobDataUrl(image), projectId)
    if (!saved.ok || !saved.feedback) throw new Error(saved.error || '截图保存失败')
    if (ticket === generation) {
      const current = feedbackHistory.value.find(item => item.id === record.id) || record
      upsertFeedback({ ...current, snapshots: saved.feedback.snapshots })
    }
    return null
  } catch (e) {
    const message = (e as Error).message
    if (!refresh && ticket === generation) error.value = message
    return message
  }
  finally { snapshotBusyId.value = '' }
}

async function decideFeedback(record: FeedbackRecord, accepted: boolean) {
  if (!workflow.value) return
  const wid = workflow.value.workflow_id, ticket = generation
  try {
    const saved = await agentApi.workflowFeedbackStatus(wid, record.id, accepted ? 'accepted' : 'needs_changes')
    if (!saved.ok || !saved.feedback) throw new Error(saved.error || '决定保存失败')
    if (ticket === generation) upsertFeedback(mapFeedback(saved.feedback))
    if (!accepted && record.artifact_id) { feedbackOpenId.value = record.artifact_id; feedbackText.value = `继续改进：${record.note}` }
  } catch (e) { if (ticket === generation) error.value = (e as Error).message }
}

async function hydrate(id: string, ticket = generation) {
  try {
    const r = await agentApi.workflow(id)
    if (ticket !== generation) return
    if (r.ok && r.workflow) {
      workflow.value = r.workflow
      if (r.workflow.visual_feedback?.length) {
        const server = r.workflow.visual_feedback.map(item => feedbackUpdates.has(item.id)
          ? feedbackHistory.value.find(row => row.id === item.id) || mapFeedback(item)
          : mapFeedback(item))
        const ids = new Set(server.map(item => item.id))
        feedbackHistory.value = [...server, ...feedbackHistory.value.filter(item => !ids.has(item.id))]
          .sort((a, b) => b.sentAt.localeCompare(a.sentAt)).slice(0, 20)
        saveFeedbackHistory()
      } else if (!feedbackHistory.value.length) loadFeedbackHistory(id)
    }
    else error.value = r.error || '无法读取工作流'
  } catch (e) { if (ticket === generation) error.value = (e as Error).message }
}
function scheduleHydrate() {
  if (refreshTimer || !workflow.value) return
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    if (workflow.value) void hydrate(workflow.value.workflow_id)
  }, 350)
}
function select(id: string) {
  generation++
  feedbackOpenId.value = ''; feedbackText.value = ''; feedbackRegion.value = null; annotationId.value = ''; regionOwner.value = ''
  unsubscribe?.()
  selectedId.value = id
  workflow.value = null
  loadFeedbackHistory(id)
  error.value = ''
  const ticket = generation
  void hydrate(id, ticket)
  unsubscribe = workflowEvents(id, 0, {
    onEvent: (event: WorkflowEvent) => {
      if (ticket !== generation) return
      if (event.status && workflow.value) workflow.value = { ...workflow.value, status: event.status }
      scheduleHydrate()
    },
    onDone: scheduleHydrate,
    onError: scheduleHydrate,
  })
}
async function loadList() {
  const ticket = generation
  try {
    const r = await agentApi.workflowList(20)
    if (ticket !== generation) return
    history.value = r.items || []
    if (!selectedId.value && history.value.length) select(history.value[0].workflow_id)
  } catch (e) { error.value = (e as Error).message }
}
function start() {
  const prompt = goal.value.trim()
  if (!prompt) return
  error.value = ''
  window.dispatchEvent(new CustomEvent('docmind:start-workflow', { detail: { prompt } }))
}
function openChat() {
  window.dispatchEvent(new CustomEvent('docmind:focus-chat'))
}
function openMcpSettings() {
  window.dispatchEvent(new CustomEvent('docmind:open-settings', { detail: { tab: 'mcp' } }))
}
async function control(action: 'interrupt' | 'resume') {
  if (!workflow.value || busy.value) return
  busy.value = true
  try {
    const r = action === 'interrupt'
      ? await agentApi.workflowInterrupt(workflow.value.workflow_id)
      : await agentApi.workflowResume(workflow.value.workflow_id)
    if (r.workflow) workflow.value = r.workflow
    else error.value = r.error || '操作失败'
  } catch (e) { error.value = (e as Error).message || '操作失败'
  } finally { busy.value = false }
}

async function rollbackProject() {
  if (!workflow.value || busy.value || !workflow.value.project_checkpoint?.id) return
  if (!window.confirm('将恢复模型执行前的项目文件快照，是否继续？')) return
  busy.value = true
  error.value = ''
  try {
    const r = await agentApi.workflowProjectRollback(workflow.value.workflow_id, true)
    if (r.rollback?.ok) await hydrate(workflow.value.workflow_id)
    else error.value = r.error || '项目快照恢复失败'
  } catch (e) { error.value = (e as Error).message || '项目快照恢复失败' }
  finally { busy.value = false }
}
async function applyRecovery(option: NonNullable<NonNullable<WorkflowState['recovery']>['options']>[number]) {
  if (!workflow.value || busy.value || !option) return
  const action = option.action || ''
  if (action === 'rollback') { await rollbackProject(); return }
  if (action === 'inspect') { openChat(); return }
  if (action !== 'retry_failed') { openChat(); return }
  const taskIds = (option.task_ids || []).filter(Boolean)
  if (!taskIds.length) { openChat(); return }
  if (!window.confirm(`将按顺序重试 ${taskIds.length} 个失败任务，是否继续？`)) return
  busy.value = true
  error.value = ''
  try {
    for (const taskId of taskIds) {
      const r = await agentApi.workflowSubagentRetry(workflow.value.workflow_id, taskId)
      if (!r.workflow) throw new Error(r.error || `任务 ${taskId} 重试失败`)
      workflow.value = r.workflow
    }
    await hydrate(workflow.value.workflow_id)
  } catch (e) { error.value = (e as Error).message || '重试失败任务时发生错误' }
  finally { busy.value = false }
}
async function approvePlan() {
  if (!workflow.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const id = workflow.value.workflow_id
    const first = workflow.value.status === 'awaiting_approval'
      ? await agentApi.workflowApprove(id, true, true)
      : await agentApi.workflowExecute(id)
    if (!first.workflow) { error.value = first.error || '启动失败'; return }
    workflow.value = first.workflow
    if (first.workflow.status === 'awaiting_approval') {
      const next = await agentApi.workflowApprove(id, true, true)
      if (next.workflow) workflow.value = next.workflow
      else error.value = next.error || '批准后启动失败'
    }
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function decide(approved: boolean, note: string) {
  if (!workflow.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await agentApi.workflowAcceptanceDecide(
      workflow.value.workflow_id, approved, note)
    if (r.workflow) workflow.value = r.workflow
    else error.value = r.error || '验收决定保存失败'
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function onStarted(ev: Event) {
  const id = (ev as CustomEvent<{ workflowId?: string }>).detail?.workflowId
  if (id) { goal.value = ''; select(id); void loadList() }
}
function onProjectChanged() {
  generation++
  unsubscribe?.()
  workflow.value = null
  selectedId.value = ''
  history.value = []
  feedbackHistory.value = []
  feedbackOpenId.value = ''; feedbackText.value = ''; feedbackRegion.value = null; annotationId.value = ''; regionOwner.value = ''
  error.value = ''
  void loadList()
}
onMounted(() => {
  void loadList()
  window.addEventListener('docmind:workflow-started', onStarted as EventListener)
  window.addEventListener('docmind:project-changed', onProjectChanged)
  pollTimer = setInterval(() => { if (workflow.value) void hydrate(workflow.value.workflow_id) }, 10000)
})
onBeforeUnmount(() => {
  generation++
  unsubscribe?.()
  if (pollTimer) clearInterval(pollTimer)
  if (refreshTimer) clearTimeout(refreshTimer)
  stopResize()
  window.removeEventListener('docmind:workflow-started', onStarted as EventListener)
  window.removeEventListener('docmind:project-changed', onProjectChanged)
})
</script>

<template>
  <div class="acp">
    <header class="acp-head"><div><h2>自主开发舱</h2><p>描述结果，由模型规划、执行和验证；你审核计划与最终效果。</p></div>
      <span v-if="workflow" class="acp-state">{{ statusLabel[workflow.status] || workflow.status }}</span></header>
    <div class="acp-compose"><textarea v-model="goal" rows="3" placeholder="描述想完成的功能、效果或设计目标…" @keydown.ctrl.enter="start" />
      <button :disabled="!goal.trim()" @click="start">交给模型推进</button></div>
    <p v-if="error" class="acp-error">{{ error }}</p>
    <div v-if="workflow" class="acp-grid">
      <section class="acp-panel"><h3>目标与验收</h3><p class="acp-goal">{{ workflow.request }}</p>
        <div v-if="workflow.selected_option" class="acp-option"><b>当前方案</b><span>{{ workflow.selected_option.title }}</span><small>{{ workflow.selected_option.summary }}</small></div>
        <div v-if="workflow.acceptance_contract?.items?.length" class="acp-list"><b>验收条件 · 第 {{ workflow.acceptance_contract.revision }} 版</b>
          <div v-for="item in workflow.acceptance_contract.items" :key="item.id" class="acp-row"><em>{{ item.required ? '必需' : '可选' }}</em><span>{{ item.statement }}<small>{{ item.method }} · {{ item.evidence.join('、') || '待补证据' }}</small></span></div>
          <p v-if="workflow.acceptance_contract.final_decision === 'accepted'" class="acp-ok">用户已验收通过</p>
          <p v-else-if="workflow.acceptance_contract.final_decision === 'rejected'" class="acp-error">用户未通过：{{ workflow.acceptance_contract.final_note }}</p>
        </div>
      </section>
      <section class="acp-panel"><div class="acp-panel-head"><h3>执行现场</h3><span>{{ doneCount }}/{{ workflow.tasks?.length || 0 }} 个任务完成</span></div>
        <div v-if="workflow.tasks?.length" class="acp-list"><div v-for="task in workflow.tasks" :key="String(task.id)" class="acp-row"><em>{{ String(taskResults[String(task.id)]?.status || task.status || '待执行') }}</em><span>{{ task.task }}<small>{{ taskResults[String(task.id)]?.conclusion || '' }}</small></span></div></div>
        <p v-else class="acp-muted">方案确认后显示模型的任务分工。</p>
        <h3>最近事件</h3><div class="acp-events"><div v-for="event in recentEvents" :key="event.seq || event.ts"><span>{{ event.kind }}</span><small>{{ event.ts }}</small></div></div>
        <h3>通用预览与证据</h3>
        <div v-if="workflow.preview?.changes?.total" class="acp-change-summary">
          <b>{{ workflow.preview.changes.total }} 个文件有变化</b>
          <span>新增 {{ workflow.preview.changes.counts?.added || 0 }}</span>
          <span>修改 {{ workflow.preview.changes.counts?.modified || 0 }}</span>
          <span>删除 {{ workflow.preview.changes.counts?.deleted || 0 }}</span>
        </div>
        <div v-if="previewArtifacts.length" class="acp-previews">
          <details v-for="artifact in previewArtifacts" :key="artifact.id" class="acp-preview">
            <summary><b>{{ previewKindLabel[artifact.kind] || artifact.kind }}</b><span>{{ artifact.label }}</span></summary>
            <p v-if="artifact.summary">{{ artifact.summary }}</p>
            <div v-if="(artifact.kind === 'interactive' || artifact.renderer === 'interactive') && previewSource(artifact)" class="acp-live-frame">
              <div class="acp-live-bar"><i></i><span>实时预览</span><small>拖动右下角调整大小</small><button class="acp-feedback-trigger" @click.stop="reloadLive(artifact.id)">刷新</button><button class="acp-feedback-trigger" @click.stop="fullscreenLive(artifact.id)">全屏</button><button class="acp-feedback-trigger" @click.stop="toggleFeedback(artifact.id)">反馈给 AI</button><button class="acp-feedback-trigger" @click.stop="toggleAnnotation(artifact.id)">{{ annotationId === artifact.id ? '取消标注' : '标注区域' }}</button></div>
              <div class="acp-live-viewport" :style="liveFrameStyle()">
                <iframe :key="`${artifact.id}-${liveReload[artifact.id] || 0}`" :ref="(el) => setLiveFrameRef(artifact.id, el)" :src="previewSource(artifact)" :title="artifact.label" loading="lazy" allow="fullscreen" />
                <div v-if="annotationId === artifact.id" class="acp-annotation-layer" @pointerdown="beginAnnotation($event, artifact.id)" @pointermove="moveAnnotation($event, artifact.id)" @pointerup="endAnnotation" @pointercancel="endAnnotation" />
                <div v-if="regionOwner === artifact.id && feedbackRegion && feedbackRegion.width > 0" class="acp-annotation-box" :style="regionStyle()" />
                <button class="acp-resize-handle" title="拖动调整预览大小" @pointerdown.stop.prevent="startResize" />
              </div>
              <div v-if="feedbackOpenId === artifact.id" class="acp-feedback-pop" @click.stop>
                <div class="acp-feedback-head"><b>告诉 AI 需要怎么改</b><button @click="toggleFeedback(artifact.id)">×</button></div>
                <textarea v-model="feedbackText" rows="3" placeholder="例如：按钮太靠右，移动到画面下方并增大字号…" @keydown.ctrl.enter="sendFeedback(artifact)" />
                <div class="acp-feedback-foot"><small>Ctrl+Enter 发送</small><button :disabled="!feedbackText.trim() || feedbackSending" @click="sendFeedback(artifact)">{{ feedbackSending ? '保存并发送中…' : '发送并修改' }}</button></div>
              </div>
            </div>
            <img v-if="artifact.kind === 'image' && previewSource(artifact)" :src="previewSource(artifact)" :alt="artifact.label" />
            <video v-else-if="artifact.kind === 'video' && previewSource(artifact)" controls :src="previewSource(artifact)" />
            <audio v-else-if="artifact.kind === 'audio' && previewSource(artifact)" controls :src="previewSource(artifact)" />
            <pre v-if="artifact.before || artifact.after">{{ artifact.before ? `变更前：\n${artifact.before}\n\n` : '' }}{{ artifact.after ? `变更后：\n${artifact.after}` : '' }}</pre>
            <small v-if="artifact.path">路径：{{ artifact.path }}</small>
            <small v-if="artifact.change_summary">变更：{{ artifact.change_summary.before_lines || 0 }} 行 → {{ artifact.change_summary.after_lines || 0 }} 行，新增 {{ artifact.change_summary.added_lines || 0 }}，删除 {{ artifact.change_summary.removed_lines || 0 }}</small>
            <small v-if="artifact.evidence?.length">证据：{{ artifact.evidence.join('、') }}</small>
          </details>
        </div>
        <p v-else class="acp-muted">任务完成后，工具可以提交任意类型的文件、资源、差异或验证证据。</p>
        <div v-if="feedbackHistory.length" class="acp-feedback-history">
          <h4>视觉反馈与效果对比</h4>
          <details v-for="item in feedbackHistory" :key="item.id" class="acp-feedback-record" :open="item.id === feedbackHistory[0]?.id">
            <summary><b>{{ item.label }}</b><span>{{ feedbackStatusLabel[item.status || 'pending'] || item.status }}</span></summary>
            <p>{{ item.note }}</p>
            <small>{{ new Date(item.sentAt).toLocaleString('zh-CN', { hour12: false }) }} · {{ item.screenshot ? '附带截图' : '文字反馈，未附截图' }}<span v-if="item.region"> · 已标注区域</span></small>
            <p v-if="item.detail" class="acp-muted">{{ item.detail }}</p>
            <div v-if="item.snapshots?.before || item.snapshots?.after" class="acp-snapshot-pair">
              <figure><figcaption>修改前</figcaption><img v-if="item.snapshots?.before" :src="agentApi.workflowFeedbackSnapshotUrl(workflow.workflow_id, item.id, 'before')" alt="反馈发送时的画面" /><small v-else>未取得截图</small></figure>
              <figure><figcaption>复验画面</figcaption><img v-if="item.snapshots?.after" :src="`${agentApi.workflowFeedbackSnapshotUrl(workflow.workflow_id, item.id, 'after')}&v=${encodeURIComponent(item.snapshots.after.captured_at)}`" alt="记录的当前效果" /><small v-else>AI 回复后会自动刷新并记录；也可手动记录当前效果</small></figure>
            </div>
            <div class="acp-feedback-actions">
              <button v-if="['pending', 'failed', 'needs_changes'].includes(item.status || 'pending')" :disabled="feedbackSending" @click="retryFeedback(item)">重新发送</button>
              <button :disabled="!!snapshotBusyId || !item.artifact_id || item.status === 'processing'" @click="captureAfter(item)">{{ snapshotBusyId === item.id ? '保存截图中…' : '记录当前效果' }}</button>
              <button v-if="item.status === 'awaiting_review' || item.snapshots?.after" @click="decideFeedback(item, true)">效果满意</button>
              <button v-if="item.status === 'awaiting_review' || item.snapshots?.after" @click="decideFeedback(item, false)">继续修改</button>
            </div>
          </details>
        </div>
      </section>
      <aside class="acp-panel"><CockpitApprovalQueue :workflow="workflow" @approve-plan="approvePlan" @final-decision="decide" @open-settings="openMcpSettings" />
        <p v-if="pending">条件编辑和方案选择可在对话卡片中完成。</p>
        <p v-else-if="workflow.status === 'completed'">执行已结束。请对照验收条件、任务结果和实际项目效果做最终验收。</p>
        <p v-else>模型正在使用当前项目内已授权的工具和能力推进任务。</p>
        <button @click="openChat">打开对话与审核卡片</button>
        <div v-if="workflow.recovery?.status === 'required'" class="acp-recovery">
          <div class="acp-recovery-head"><b>失败后的下一步</b><span>需要审核</span></div>
          <p>{{ workflow.recovery.summary || '执行没有通过复核，请选择下一步。' }}</p>
          <small v-if="workflow.recovery.evidence?.uncertain_task_ids?.length" class="acp-recovery-warning">
            有 {{ workflow.recovery.evidence.uncertain_task_ids.length }} 个任务的副作用尚未确认，重试前请先核对外部状态。
          </small>
          <div v-for="option in (workflow.recovery.options || [])" :key="option.id || option.title" class="acp-recovery-option">
            <div><b>{{ option.title }}</b><small>{{ option.detail }}</small></div>
            <button :disabled="busy" @click="applyRecovery(option)">{{ option.action === 'retry_failed' ? '重试这些任务' : option.action === 'rollback' ? '恢复项目快照' : '查看并重新规划' }}</button>
          </div>
        </div>
        <div v-if="workflow.project_checkpoint?.id" class="acp-checkpoint">
          <b>执行前快照</b>
          <small>{{ workflow.project_checkpoint.file_count || 0 }} 个文件 · {{ Math.round((workflow.project_checkpoint.bytes || 0) / 1024) }} KB</small>
          <button :disabled="busy" @click="rollbackProject">恢复执行前版本</button>
        </div>
        <div v-if="workflow.capability_lease" class="acp-lease">
          <div class="acp-lease-head"><b>工具权限租约</b><span :class="leaseStatusLabel(workflow.capability_lease) === '当前有效' ? 'acp-ok' : 'acp-muted'">{{ leaseStatusLabel(workflow.capability_lease) }}</span></div>
          <small>{{ (workflow.capability_lease.capabilities || []).map(item => capabilityLabel[item] || item).join(' · ') }}</small>
          <small v-if="workflow.capability_lease.expires_at">到期：{{ new Date(workflow.capability_lease.expires_at).toLocaleString() }}</small>
        </div>
        <button v-if="workflow.status === 'executing'" :disabled="busy" @click="control('interrupt')">暂停工作流</button>
        <button v-if="workflow.status === 'interrupted'" :disabled="busy" @click="control('resume')">恢复工作流</button>
        <h3>本项目历史</h3><button v-for="item in history" :key="item.workflow_id" class="acp-history" @click="select(item.workflow_id)">{{ item.request || item.workflow_id }}<small>{{ statusLabel[item.status] || item.status }}</small></button>
      </aside>
    </div>
    <div v-else class="acp-empty">本项目还没有工作流。写下目标后，模型会先提出方案和验收条件。</div>
  </div>
</template>

<style scoped>
.acp { flex: 1; min-width: 0; overflow: auto; padding: 22px; background: var(--bg-raised); color: var(--text); }
.acp-head,.acp-panel-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.acp-head h2 { margin: 0; font-size: 19px; }.acp-head p,.acp-panel p { color: var(--text-muted); font-size: 12px; line-height: 1.6; }
.acp-state { border: 1px solid var(--border); border-radius: 99px; padding: 5px 9px; font-size: 11px; white-space: nowrap; }
.acp-compose { display: flex; gap: 8px; margin: 16px 0; }.acp-compose textarea { flex: 1; resize: vertical; min-width: 0; border: 1px solid var(--border); border-radius: 9px; padding: 10px; color: var(--text); background: var(--bg-raised); font: inherit; }
button { border: 1px solid var(--border); background: var(--bg-raised); color: var(--text); border-radius: 7px; padding: 7px 10px; cursor: pointer; font: inherit; font-size: 12px; }
button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }button:disabled { opacity: .5; cursor: default; }.acp-compose button { background: var(--accent); color: white; border-color: var(--accent); }
.acp-grid { display: grid; grid-template-columns: minmax(220px,1fr) minmax(270px,1.3fr) minmax(200px,.8fr); gap: 12px; }.acp-panel { min-width: 0; border: 1px solid var(--border); border-radius: 11px; padding: 13px; display: flex; flex-direction: column; gap: 9px; }.acp-panel h3 { margin: 0; font-size: 13px; }.acp-panel-head span,.acp-muted { color: var(--text-faint); font-size: 11px; }.acp-goal { margin: 0; }.acp-option,.acp-list { display: grid; gap: 8px; }.acp-option small,.acp-row small { display: block; color: var(--text-faint); font-size: 11px; margin-top: 3px; }.acp-row { display: flex; gap: 7px; padding-top: 7px; border-top: 1px solid var(--border); font-size: 12px; line-height: 1.5; }.acp-row em { font-size: 10px; color: var(--accent); font-style: normal; flex: 0 0 auto; }.acp-events { display: grid; gap: 5px; }.acp-events div { display: flex; justify-content: space-between; gap: 7px; font-size: 11px; color: var(--text-muted); }.acp-events small { color: var(--text-faint); }.acp-history { text-align: left; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.acp-history small { display: block; color: var(--text-faint); }.acp-error { color: var(--danger) !important; }.acp-ok { color: var(--green) !important; }.acp-empty { padding: 35px; text-align: center; color: var(--text-faint); font-size: 12px; }
.acp-previews { display: grid; gap: 6px; }.acp-preview { border: 1px solid var(--border); border-radius: 7px; padding: 6px 8px; font-size: 11px; }.acp-preview summary { display: flex; gap: 7px; cursor: pointer; }.acp-preview summary b { color: var(--accent); font-weight: 600; }.acp-preview p { margin: 7px 0; }.acp-preview img,.acp-preview video { display: block; max-width: 100%; max-height: 180px; margin-top: 7px; border-radius: 5px; }.acp-preview audio { width: 100%; margin-top: 7px; }.acp-preview pre { max-height: 150px; overflow: auto; padding: 7px; white-space: pre-wrap; background: var(--bg-hover); }.acp-preview small { display: block; color: var(--text-faint); margin-top: 4px; overflow-wrap: anywhere; }
.acp-change-summary { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; font-size: 11px; color: var(--text-faint); }.acp-change-summary b { color: var(--text); margin-right: 3px; }
.acp-live-frame { position: relative; margin-top: 8px; max-width: 100%; border: 1px solid var(--border); border-radius: 7px; overflow: visible; background: #101521; }.acp-live-bar { display: flex; align-items: center; gap: 6px; padding: 6px 8px; color: #dbe5f5; font-size: 10px; }.acp-live-bar i { width: 7px; height: 7px; border-radius: 50%; background: #43d17a; box-shadow: 0 0 8px #43d17a; }.acp-live-bar small { margin-left: auto; color: #93a4bd; }.acp-live-bar button { padding: 3px 7px; border-color: #52698b; color: #dbe5f5; background: #26344b; font-size: 10px; }.acp-live-viewport { position: relative; max-width: 100%; min-height: 220px; }.acp-live-viewport iframe { display: block; width: 100%; height: 100%; min-height: 220px; border: 0; border-radius: 0 0 7px 7px; background: #000; }.acp-annotation-layer { position: absolute; inset: 0; cursor: crosshair; background: rgba(24, 39, 64, .22); touch-action: none; }.acp-annotation-box { position: absolute; border: 2px solid #55a8ff; background: rgba(85,168,255,.18); pointer-events: none; }.acp-resize-handle { position: absolute; right: -1px; bottom: -1px; width: 18px; height: 18px; padding: 0; border: 0; border-radius: 0 0 7px 0; background: linear-gradient(135deg, transparent 45%, #8fa4c4 46%, #8fa4c4 53%, transparent 54%, transparent 64%, #8fa4c4 65%, #8fa4c4 72%, transparent 73%); cursor: nwse-resize; }.acp-feedback-pop { position: absolute; z-index: 4; top: 35px; right: 8px; width: min(300px, calc(100% - 16px)); padding: 9px; border: 1px solid var(--border-strong); border-radius: 8px; background: var(--bg-raised); box-shadow: 0 12px 32px rgba(0,0,0,.28); }.acp-feedback-head,.acp-feedback-foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; }.acp-feedback-head button { border: 0; background: transparent; padding: 0 3px; font-size: 16px; }.acp-feedback-pop textarea { width: 100%; box-sizing: border-box; margin: 8px 0; resize: vertical; border: 1px solid var(--border); border-radius: 6px; padding: 7px; color: var(--text); background: var(--bg); font: inherit; font-size: 11px; }.acp-feedback-foot small { color: var(--text-faint); font-size: 10px; }.acp-feedback-foot button { padding: 5px 8px; background: var(--accent); color: #fff; border-color: var(--accent); }
.acp-feedback-history { display: grid; gap: 7px; margin-top: 4px; padding-top: 9px; border-top: 1px solid var(--border); }.acp-feedback-history h4 { margin: 0; font-size: 12px; }.acp-feedback-record { display: flex; gap: 7px; padding: 7px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-hover); }.acp-feedback-dot { flex: 0 0 auto; width: 7px; height: 7px; margin-top: 4px; border-radius: 50%; background: var(--amber); }.acp-feedback-record b { font-size: 11px; }.acp-feedback-record p { margin: 3px 0; color: var(--text-muted); font-size: 11px; line-height: 1.45; }.acp-feedback-record small { color: var(--text-faint); font-size: 10px; }
@media (max-width: 1050px) { .acp-grid { grid-template-columns: 1fr 1fr; }.acp-grid aside { grid-column: 1 / -1; } }@media (max-width: 700px) { .acp-grid { grid-template-columns: 1fr; }.acp-grid aside { grid-column: auto; } }
.acp-live-bar { flex-wrap: wrap; }
.acp-live-viewport:fullscreen { width: 100vw !important; height: 100vh !important; background: #000; }
.acp-resize-handle { z-index: 2; touch-action: none; }
.acp-feedback-record { display: block; }
.acp-feedback-record summary { display: flex; justify-content: space-between; gap: 8px; cursor: pointer; font-size: 11px; }
.acp-feedback-record summary span { color: var(--accent); }
.acp-feedback-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.acp-feedback-actions button { padding: 5px 7px; font-size: 11px; }
.acp-snapshot-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
.acp-snapshot-pair figure { margin: 0; padding: 6px; border: 1px solid var(--border); border-radius: 6px; min-width: 0; }
.acp-snapshot-pair figcaption { margin-bottom: 5px; font-size: 11px; color: var(--text-muted); }
.acp-snapshot-pair img { width: 100%; display: block; }
.acp-snapshot-pair small { display: block; }
.acp-checkpoint { display: grid; gap: 4px; padding: 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-hover); font-size: 11px; }
.acp-checkpoint small { color: var(--text-faint); }
.acp-checkpoint button { justify-self: start; color: var(--danger); }
.acp-lease { display: grid; gap: 4px; padding: 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-hover); font-size: 11px; }
.acp-lease-head { display: flex; justify-content: space-between; gap: 8px; }
.acp-lease small { color: var(--text-faint); line-height: 1.45; }
.acp-recovery { display: grid; gap: 7px; padding: 9px; border: 1px solid color-mix(in srgb, var(--danger) 45%, var(--border)); border-radius: 8px; background: color-mix(in srgb, var(--danger) 5%, var(--bg-hover)); font-size: 11px; }
.acp-recovery-head { display: flex; justify-content: space-between; gap: 8px; }
.acp-recovery-head span { color: var(--danger); font-size: 10px; }
.acp-recovery p { margin: 0; }
.acp-recovery-warning { color: var(--amber); line-height: 1.45; }
.acp-recovery-option { display: grid; gap: 6px; padding-top: 7px; border-top: 1px solid var(--border); }
.acp-recovery-option b { display: block; }
.acp-recovery-option small { display: block; margin-top: 3px; color: var(--text-faint); line-height: 1.45; }
.acp-recovery-option button { justify-self: start; padding: 5px 8px; font-size: 11px; }
</style>
