<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { agentApi, mcpApi } from '../api'
import type { AgentApproval, CockpitApprovalRequest, McpCapabilityCandidate, WorkflowState } from '../api'

const props = defineProps<{ workflow: WorkflowState | null }>()
const emit = defineEmits<{
  (e: 'approve-plan'): void
  (e: 'final-decision', approved: boolean, note: string): void
  (e: 'open-settings'): void
}>()
type QueueItem = {
  id: string; source: 'plan' | 'final' | 'gate' | 'external' | 'mcp'
  title: string; summary: string; risk: 'L2' | 'L3' | 'review'
  gate?: CockpitApprovalRequest; external?: AgentApproval; mcp?: McpCapabilityCandidate
}
const gates = ref<CockpitApprovalRequest[]>([])
const external = ref<AgentApproval[]>([])
const mcpPending = ref<Record<string, McpCapabilityCandidate>>({})
const selected = ref<QueueItem | null>(null)
const note = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
let pollTimer: ReturnType<typeof setInterval> | null = null
let generation = 0
const actionLabels: Record<string, string> = {
  install_tool: '安装项目工具', apply_regions: '应用分区方案',
  commit_region: '提交分区改动', commit_all: '提交项目改动',
  rollback_changeset: '回滚变更集', rollback_skill: '回滚技能',
  update_skill: '更新技能', mcp_server: '添加或修改 MCP 连接',
  mcp_capability: '启用 MCP 能力',
}
const items = computed<QueueItem[]>(() => {
  const rows: QueueItem[] = []
  const wf = props.workflow
  if (wf && ['planned', 'awaiting_approval'].includes(wf.status))
    rows.push({ id: `plan:${wf.workflow_id}`, source: 'plan', title: '审核执行计划',
      summary: `${wf.tasks?.length || 0} 项任务 · ${wf.acceptance_contract?.items?.length || 0} 条验收条件`, risk: 'review' })
  for (const gate of gates.value) rows.push({ id: gate.id, source: 'gate',
    title: actionLabels[gate.action] || gate.action, summary: gate.target, risk: gate.risk, gate })
  for (const row of external.value.filter(x => x.status === 'pending'))
    rows.push({ id: row.id, source: 'external', title: '修改项目外文件',
      summary: row.summary || row.paths?.join('、') || row.id, risk: 'L3', external: row })
  for (const [key, candidate] of Object.entries(mcpPending.value))
    if (!gates.value.some(g => g.action === 'mcp_capability' && g.target === key))
      rows.push({ id: `mcp:${key}`, source: 'mcp', title: `启用 ${candidate.domain || key} MCP 能力`,
        summary: `${candidate.tool_count || 0} 个工具 · ${candidate.best_for || key}`, risk: 'L2', mcp: candidate })
  if (wf?.status === 'completed' && wf.acceptance_contract?.final_decision === 'pending')
    rows.push({ id: `final:${wf.workflow_id}`, source: 'final', title: '最终验收',
      summary: '检查实际效果、任务结果和验收条件', risk: 'review' })
  return rows.sort((a, b) => Number(b.risk === 'L3') - Number(a.risk === 'L3'))
})

async function refresh() {
  const ticket = generation
  const results = await Promise.allSettled([
    agentApi.approvalRequests(), agentApi.approvals(), mcpApi.capabilities(),
  ])
  if (ticket !== generation) return
  if (results[0].status === 'fulfilled' && results[0].value.ok) gates.value = results[0].value.items || []
  if (results[1].status === 'fulfilled' && results[1].value.ok) external.value = results[1].value.approvals || []
  if (results[2].status === 'fulfilled' && results[2].value.ok) mcpPending.value = results[2].value.pending || {}
}
function review(item: QueueItem) { selected.value = item; error.value = ''; note.value = '' }
function close() { if (!busy.value) selected.value = null }
async function decide(approved: boolean) {
  const item = selected.value
  if (!item || busy.value) return
  if (item.source === 'plan') { emit('approve-plan'); close(); return }
  if (item.source === 'final') { emit('final-decision', approved, note.value); close(); return }
  if (item.source === 'mcp' || item.gate?.action === 'mcp_server' || item.gate?.action === 'mcp_capability') {
    emit('open-settings'); close(); return
  }
  busy.value = true
  error.value = ''
  try {
    const r = item.source === 'gate'
      ? await agentApi.decideApprovalRequest(item.id, approved)
      : await agentApi.decide(item.id, approved ? 'approved' : 'rejected')
    if (!r.ok) { error.value = ('error' in r ? String(r.error || '') : '') || '审批未保存'; return }
    notice.value = approved ? '已记录批准。请让模型重试被阻断的步骤。' : '已拒绝该操作。'
    selected.value = null
    await refresh()
  } catch (e) { error.value = (e as Error).message || '审批失败' }
  finally { busy.value = false }
}
function onProjectChanged() {
  generation++
  gates.value = []; external.value = []; mcpPending.value = {}; selected.value = null
  void refresh()
}
onMounted(() => {
  void refresh()
  pollTimer = setInterval(() => { void refresh() }, 8000)
  window.addEventListener('docmind:project-changed', onProjectChanged)
})
onBeforeUnmount(() => {
  generation++
  if (pollTimer) clearInterval(pollTimer)
  window.removeEventListener('docmind:project-changed', onProjectChanged)
})
watch(() => props.workflow?.status, () => { void refresh() })
</script>

<template>
  <section class="caq">
    <header><h3>审核队列</h3><span>{{ items.length }} 项待处理</span></header>
    <p v-if="notice" class="caq-notice">{{ notice }}</p>
    <p v-if="!items.length" class="caq-empty">当前没有待审核动作。</p>
    <button v-for="item in items" :key="item.id" class="caq-item" @click="review(item)">
      <span class="caq-risk" :class="`caq-${item.risk}`">{{ item.risk === 'review' ? '审核' : item.risk }}</span>
      <span><b>{{ item.title }}</b><small>{{ item.summary }}</small></span>
      <span class="caq-arrow">›</span>
    </button>
    <Teleport to="body"><div v-if="selected" class="caq-mask" @click.self="close">
      <div class="caq-dialog" role="dialog" aria-modal="true" :aria-label="selected.title">
        <header><h3>{{ selected.title }}</h3><button :disabled="busy" @click="close">×</button></header>
        <p class="caq-detail">{{ selected.summary }}</p>
        <template v-if="selected.source === 'plan'">
          <h4>计划任务</h4><div v-for="task in workflow?.tasks || []" :key="String(task.id)" class="caq-line">{{ task.id }} · {{ task.task }}</div>
          <h4>验收条件</h4><div v-for="criterion in workflow?.acceptance_contract?.items || []" :key="criterion.id" class="caq-line">{{ criterion.required ? '必须' : '可选' }} · {{ criterion.statement }}</div>
        </template>
        <template v-else-if="selected.source === 'final'"><p>确认必需条件和实际效果均达到目标后再通过。</p>
          <textarea v-model="note" rows="3" placeholder="未通过时写明需要继续修改的效果" /></template>
        <template v-else-if="selected.source === 'external'"><h4>影响路径</h4><div v-for="path in selected.external?.paths || []" :key="path" class="caq-line">{{ path }}</div><pre v-if="selected.external?.diff">{{ selected.external.diff }}</pre></template>
        <template v-else-if="selected.source === 'mcp'"><p>连接器：{{ selected.mcp?.server }}。能力启用只能在设置页确认。</p><div class="caq-line">{{ selected.mcp?.capabilities.join('、') }}</div></template>
        <template v-else-if="selected.gate"><div class="caq-line">操作：{{ selected.gate.action }}</div><div class="caq-line">对象：{{ selected.gate.target }}</div><p v-if="selected.gate.action.startsWith('mcp_')">MCP 连接与能力只能在设置页确认。</p><p v-else>批准后模型需重新调用该操作；本次确认仅绑定上方对象，有效期由原审批门控制。</p></template>
        <p v-if="error" class="caq-error">{{ error }}</p>
        <footer><button :disabled="busy" @click="close">返回</button>
          <button v-if="selected.source === 'mcp' || selected.gate?.action.startsWith('mcp_')" @click="decide(false)">打开 MCP 设置</button>
          <template v-else-if="selected.source === 'plan'"><button :disabled="busy" @click="decide(true)">批准计划并执行</button></template>
          <template v-else><button :disabled="busy || (selected.source === 'final' && !note.trim())" @click="decide(false)">拒绝 / 未通过</button><button :disabled="busy" @click="decide(true)">批准 / 通过</button></template>
        </footer>
      </div>
    </div></Teleport>
  </section>
</template>

<style scoped>
.caq { display: grid; align-content: start; gap: 8px; }.caq header { display: flex; justify-content: space-between; align-items: center; gap: 10px; }.caq h3 { margin: 0; font-size: 13px; }.caq header span,.caq-empty { color: var(--text-faint); font-size: 11px; }.caq-empty { margin: 0; }.caq-item { width: 100%; display: flex; align-items: start; gap: 8px; text-align: left; border: 1px solid var(--border); border-radius: 8px; padding: 9px; background: var(--bg-raised); color: var(--text); cursor: pointer; }.caq-item:hover { border-color: var(--accent); }.caq-item > span:nth-child(2) { min-width: 0; flex: 1; display: grid; gap: 3px; }.caq-item b { font-size: 12px; }.caq-item small { color: var(--text-faint); overflow-wrap: anywhere; }.caq-risk { font-size: 10px; color: var(--accent); }.caq-L3 { color: var(--danger); }.caq-arrow { color: var(--text-faint); }.caq-notice { color: var(--green); margin: 0; font-size: 11px; }
.caq-mask { position: fixed; inset: 0; z-index: 1200; display: grid; place-items: center; background: rgba(20,30,48,.4); }.caq-dialog { width: min(560px,calc(100vw - 32px)); max-height: 80vh; overflow: auto; box-sizing: border-box; display: grid; gap: 10px; padding: 18px; border-radius: 12px; background: var(--bg-raised); color: var(--text); box-shadow: var(--shadow-pop); }.caq-dialog header button { border: 0; background: transparent; font-size: 20px; cursor: pointer; color: var(--text-muted); }.caq-dialog h4 { margin: 4px 0 0; font-size: 12px; }.caq-dialog p { margin: 0; font-size: 12px; color: var(--text-muted); }.caq-line { border-top: 1px solid var(--border); padding-top: 6px; font-size: 12px; overflow-wrap: anywhere; }.caq-detail { overflow-wrap: anywhere; }.caq-dialog pre { max-height: 220px; overflow: auto; padding: 8px; border: 1px solid var(--border); font-size: 11px; }.caq-dialog textarea { width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-raised); color: var(--text); }.caq-dialog footer { display: flex; justify-content: flex-end; gap: 7px; }.caq-dialog footer button { padding: 6px 9px; border: 1px solid var(--border); border-radius: 7px; background: var(--bg-raised); color: var(--text); cursor: pointer; }.caq-dialog footer button:disabled { opacity: .5; cursor: default; }.caq-error { color: var(--danger) !important; }
</style>
