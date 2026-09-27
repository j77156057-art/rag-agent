<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { agentApi } from '../api'
import type { PreviewAdapterInfo, WorkflowAcceptanceReport, WorkflowState } from '../api'

const props = defineProps<{ workflow: WorkflowState }>()
const emit = defineEmits<{ (e: 'activity'): void }>()
const adapters = ref<PreviewAdapterInfo[]>([])
const configured = ref<string[]>([])
const report = ref<WorkflowAcceptanceReport | null>(null)
const selected = ref<string[]>([])
const loading = ref(false)
const error = ref('')
const rollbackBusy = ref(false)

const changedFiles = computed(() => report.value?.preview?.changes?.files || props.workflow.preview?.changes?.files || [])
const evaluation = computed(() => report.value?.evaluation)

async function load() {
  loading.value = true
  error.value = ''
  const [catalog, acceptance] = await Promise.allSettled([
    agentApi.previewAdapters(),
    agentApi.workflowAcceptanceReport(props.workflow.workflow_id),
  ])
  if (catalog.status === 'fulfilled' && catalog.value.ok) {
    adapters.value = catalog.value.adapters || []
    const profileAdapters = props.workflow.project_profile?.preview_adapters || []
    configured.value = profileAdapters.length ? [...profileAdapters] : adapters.value.filter(item => item.available && !item.requires_connector).map(item => item.id)
  }
  if (acceptance.status === 'fulfilled' && acceptance.value.ok) report.value = acceptance.value.report || null
  if ((catalog.status === 'rejected' || (catalog.status === 'fulfilled' && !catalog.value.ok)) &&
      (acceptance.status === 'rejected' || (acceptance.status === 'fulfilled' && !acceptance.value.ok))) {
    error.value = '适配器或验收报告暂时无法读取'
  }
  loading.value = false
}

function toggleAdapter(id: string) {
  configured.value = configured.value.includes(id)
    ? configured.value.filter(item => item !== id)
    : [...configured.value, id]
}

async function saveConfig() {
  const result = await agentApi.configurePreviewAdapters(configured.value)
  if (!result.ok) error.value = result.error || '适配器配置保存失败'
  else emit('activity')
}

function togglePath(path: string) {
  selected.value = selected.value.includes(path)
    ? selected.value.filter(item => item !== path)
    : [...selected.value, path]
}

async function rollback() {
  if (rollbackBusy.value || !selected.value.length || !window.confirm('只恢复选中的文件？此操作需要用户确认。')) return
  rollbackBusy.value = true
  error.value = ''
  try {
    const result = await agentApi.workflowProjectRollback(props.workflow.workflow_id, true, selected.value)
    if (!result.ok) throw new Error(result.error || '局部回滚失败')
    selected.value = []
    emit('activity')
    await load()
  } catch (cause) {
    error.value = (cause as Error).message || '局部回滚失败'
  } finally { rollbackBusy.value = false }
}

watch(() => props.workflow.workflow_id, () => { selected.value = []; void load() })
onMounted(() => { void load() })
</script>

<template>
  <section class="adapter-panel">
    <div class="adapter-head"><b>适配器与验收</b><small>当前项目范围</small><span v-if="loading">读取中…</span></div>
    <p v-if="error" class="adapter-error">{{ error }}</p>
    <div class="adapter-grid">
      <div v-for="item in adapters" :key="item.id" class="adapter-item">
        <input v-if="!item.requires_connector" type="checkbox" :checked="configured.includes(item.id)" @change="toggleAdapter(item.id)" />
        <div><b>{{ item.label || item.id }}</b><small>{{ item.evidence }}</small></div>
        <span :class="item.available ? 'on' : 'off'">{{ item.available ? '可用' : '未连接' }}</span>
      </div>
    </div>
    <button type="button" class="adapter-save" @click="saveConfig">保存项目预览配置</button>
    <div v-if="evaluation" class="acceptance-report">
      <div class="report-head"><b>真实验收报告</b><strong :class="evaluation.passed ? 'ok' : 'bad'">{{ Math.round((evaluation.score || 0) * 100) }}%</strong></div>
      <small>{{ evaluation.passed ? '检查通过' : '仍有检查未通过' }} · {{ evaluation.task_count || 0 }} 个任务 · {{ evaluation.steps || 0 }} 步</small>
      <details v-if="evaluation.checks?.length"><summary>查看检查项</summary><p v-for="(check, index) in evaluation.checks" :key="index" :class="check.ok ? 'ok' : 'bad'">{{ check.ok ? '✓' : '!' }} {{ check.name }}{{ check.detail ? '：' + check.detail : '' }}</p></details>
    </div>
    <div v-if="changedFiles.length" class="rollback-box">
      <div class="report-head"><b>按文件回滚</b><small>只恢复选中的快照文件</small></div>
      <label v-for="file in changedFiles" :key="file.path" class="rollback-file">
        <input type="checkbox" :checked="selected.includes(file.path || '')" :disabled="!file.path" @change="file.path && togglePath(file.path)" />
        <span>{{ file.path }}</span><small>{{ file.kind || 'modified' }}</small>
      </label>
      <button type="button" :disabled="rollbackBusy || !selected.length" @click="rollback">{{ rollbackBusy ? '恢复中…' : `恢复选中文件（${selected.length}）` }}</button>
    </div>
  </section>
</template>

<style scoped>
.adapter-panel { display: grid; gap: 8px; padding: 10px; border: 1px solid var(--border); border-radius: 9px; background: var(--bg-hover); font-size: 11px; }
.adapter-head, .report-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.adapter-head small, .adapter-head span, .report-head small, .adapter-item small { color: var(--text-faint); }
.adapter-error { margin: 0; color: var(--danger); }
.adapter-grid { display: grid; gap: 5px; }
.adapter-item { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 6px 7px; border: 1px solid var(--border); border-radius: 6px; }
.adapter-item div { display: grid; gap: 2px; min-width: 0; }.adapter-item small { overflow-wrap: anywhere; }
.adapter-item > span { flex: 0 0 auto; }.on, .ok { color: var(--green); }.off, .bad { color: var(--danger); }
.acceptance-report, .rollback-box { display: grid; gap: 5px; border-top: 1px solid var(--border); padding-top: 8px; }
.acceptance-report > small { color: var(--text-muted); }.acceptance-report details { border-top: 1px solid var(--border); padding-top: 5px; }.acceptance-report p { margin: 4px 0 0; }
.report-head strong { font-size: 18px; }.rollback-file { display: flex; align-items: center; gap: 6px; }.rollback-file span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }.rollback-file small { color: var(--text-faint); }
.rollback-box button { justify-self: start; border: 1px solid var(--accent); border-radius: 6px; padding: 5px 8px; color: #fff; background: var(--accent); cursor: pointer; font: inherit; }.rollback-box button:disabled { opacity: .5; cursor: default; }
.adapter-save { justify-self: start; border: 1px solid var(--border); border-radius: 6px; padding: 5px 8px; color: var(--text); background: var(--bg-raised); cursor: pointer; font: inherit; }
</style>
