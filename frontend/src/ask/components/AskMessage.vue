<script setup lang="ts">
// 单条对话气泡：用户消息（含图片）/ 助手消息。
// 助手消息支持 SSE 流式渲染：深度思考折叠块、ReAct 轨迹（分析/动作/观察/复核）、
// Markdown 正文、文件行号引用 chips（点击打开只读源码弹窗）。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { mdToHtml, extractFileRefs } from '../../workbench/markdown'
import WorkflowCard from '../../workbench/components/WorkflowCard.vue'
import type { AskChatMsg, AskTraceItem } from '../types'

const props = defineProps<{ msg: AskChatMsg }>()
const emit = defineEmits<{
  (e: 'openFile', path: string, line: number): void
  (e: 'wfGate', open: boolean): void
  (e: 'wfActivity'): void
}>()

const html = computed(() => mdToHtml(props.msg.text || ''))
const refs = computed(() => extractFileRefs(props.msg.text || ''))
const streaming = computed(() => props.msg.status === 'streaming')

// ---------------- 深度思考折叠 ----------------
const reasonOpen = ref(false)
let reasonManual = false
watch(() => props.msg.reasoning, (v) => {
  if (v && streaming.value && !reasonManual) reasonOpen.value = true
})
watch(() => props.msg.status, (s) => {
  if (s !== 'streaming' && !reasonManual) reasonOpen.value = false
})
function toggleReason() {
  reasonManual = true
  reasonOpen.value = !reasonOpen.value
}

// ---------------- ReAct 轨迹折叠 ----------------
const openSteps = ref<Set<number>>(new Set())
function stepOpen(i: number) {
  return openSteps.value.has(i) || (streaming.value && i === props.msg.trace.length - 1)
}
function toggleStep(i: number) {
  const next = new Set(openSteps.value)
  if (next.has(i)) next.delete(i)
  else next.add(i)
  openSteps.value = next
}

const TRACE_LABEL: Record<string, string> = {
  thought: '分析', action: '工具', observation: '观察', reflection: '复核',
}
const TOOL_NAMES: Record<string, string> = {
  search_code: '检索代码', search_knowledge: '检索知识库', read_file: '读取文件',
  grep: '定位代码', run_command: '运行命令', game_playtest: '运行校验',
  web_search: '网页搜索', web_fetch: '读取网页', dev_mcp_call: '调用 MCP',
  apply_edit: '修改文件', create_file: '创建文件',
  delegate: '委派子任务', orchestrate: '编排任务',
}
function oneLine(s: string, n = 80): string {
  const t = s.trim().replace(/\s+/g, ' ')
  return t.length > n ? t.slice(0, n) + '…' : t
}
function stepTitle(item: AskTraceItem): string {
  const raw = item.text.trim()
  if (item.type === 'action') {
    const tool = /^([\w.-]+)\s*\(/.exec(raw)?.[1]
    if (tool) return TOOL_NAMES[tool] || tool
  }
  return TRACE_LABEL[item.type] || item.type
}
function stepSummary(item: AskTraceItem): string {
  const raw = item.text.trim()
  if (item.type === 'action') {
    const tool = /^([\w.-]+)\s*\(([^)]*)\)/.exec(raw)
    if (tool) return oneLine(tool[2].replace(/^['"]|['"]$/g, '') || tool[1], 72)
  }
  if (item.type === 'observation') {
    const len = raw.replace(/\s+/g, '').length
    return len > 0 ? `返回 ${len} 个字符` : '返回结果'
  }
  return oneLine(raw, 72)
}
function fmtElapsed(ms?: number): string {
  if (!ms) return ''
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

// start_workflow 工具行与其观察由工作流卡片承载，轨迹里不重复展示
const visibleTrace = computed(() => {
  const hidden = new Set<number>()
  props.msg.trace.forEach((item, i) => {
    if (item.type === 'action' && /^start_workflow\s*\(/.test(item.text.trim())) {
      hidden.add(i)
      if (props.msg.trace[i + 1]?.type === 'observation') hidden.add(i + 1)
    }
  })
  return props.msg.trace
    .map((item, index) => ({ item, index }))
    .filter((x) => !hidden.has(x.index))
})

// 流式阶段指示：把「取证在跑」做成看得见的状态行 + 秒表（对齐工作台的确定感）
const now = ref(Date.now())
let phaseTimer = 0
watch(streaming, (on) => {
  if (on) {
    now.value = Date.now()
    if (!phaseTimer) phaseTimer = window.setInterval(() => { now.value = Date.now() }, 1000)
  } else if (phaseTimer) {
    clearInterval(phaseTimer)
    phaseTimer = 0
  }
}, { immediate: true })
onBeforeUnmount(() => { if (phaseTimer) clearInterval(phaseTimer) })

const elapsedLabel = computed(() => {
  if (!props.msg.startedAt) return ''
  const end = props.msg.finishedAt || now.value
  const s = Math.max(0, Math.round((end - props.msg.startedAt) / 1000))
  return s >= 1 ? `${s}s` : ''
})

const phaseLabel = computed(() => {
  const t = props.msg
  if (t.workflow?.workflowId) return '工作流进行中，在下方卡片查看进度与审批'
  const last = [...t.trace].reverse().find((x) => x.text.trim())
  if (last) {
    if (last.type === 'thought') return '正在分析问题'
    if (last.type === 'action') return '正在调用工具：' + stepTitle(last)
    if (last.type === 'observation') return '正在阅读结果、组织回答'
    if (last.type === 'reflection') return '正在复核思路'
  }
  if (t.reasoning) return '正在深度思考'
  return 'AI 正在翻代码'
})
</script>

<template>
  <div class="msg" :class="msg.role">
    <div v-if="msg.role === 'user'" class="msg-row user-row">
      <div class="bubble user-bubble">
        <div v-if="msg.images?.length" class="user-imgs">
          <img v-for="(src, i) in msg.images" :key="i" :src="src" alt="附件图片">
        </div>
        <div v-if="msg.text" class="user-text">{{ msg.text }}</div>
      </div>
    </div>

    <div v-else class="msg-row ai-row">
      <div class="ai-avatar" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="16" height="16"><path fill="#fff" d="M12 2l2.4 5.6L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.6-1.4z"/></svg>
      </div>
      <div class="ai-col">
        <!-- 深度思考 -->
        <div v-if="msg.reasoning" class="reason">
          <button class="reason-head" @click="toggleReason">
            <span class="reason-dot" :class="{ on: streaming }"></span>
            深度思考
            <span class="chev" :class="{ open: reasonOpen }">⌄</span>
          </button>
          <div v-if="reasonOpen" class="reason-body">{{ msg.reasoning }}</div>
        </div>

        <!-- ReAct 轨迹 -->
        <div v-if="visibleTrace.length" class="trace">
          <div v-for="row in visibleTrace" :key="row.index" class="step">
            <button class="step-head" @click="toggleStep(row.index)">
              <span class="step-tag" :class="row.item.type">{{ stepTitle(row.item) }}</span>
              <span class="step-summary">{{ stepSummary(row.item) }}</span>
              <span v-if="fmtElapsed(row.item.elapsedMs)" class="step-time">{{ fmtElapsed(row.item.elapsedMs) }}</span>
              <span class="chev" :class="{ open: stepOpen(row.index) }">⌄</span>
            </button>
            <pre v-if="stepOpen(row.index)" class="step-body">{{ row.item.text.trim() }}</pre>
          </div>
        </div>

        <!-- 工作流卡片：进度/方案选择/审批都在问答页内完成 -->
        <WorkflowCard
          v-if="msg.workflow?.workflowId"
          :key="msg.workflow.workflowId"
          :workflow-id="msg.workflow.workflowId"
          :seed="msg.workflow.seed"
          @activity="emit('wfActivity')"
          @gate="(open: boolean) => emit('wfGate', open)"
        />

        <!-- 正文 -->
        <div class="bubble ai-bubble">
          <div v-if="!msg.text && streaming" class="typing" :title="`已等待 ${elapsedLabel || '0s'}`">
            <span class="typing-phase">{{ phaseLabel }}</span>
            <span v-if="elapsedLabel" class="typing-time">{{ elapsedLabel }}</span>
            <span class="td"></span><span class="td"></span><span class="td"></span>
          </div>
          <div v-else-if="msg.text" class="answer" v-html="html"></div>
          <div v-if="msg.status === 'stopped' && msg.text" class="flag stopped">已停止</div>
          <div v-if="msg.status === 'error'" class="flag error">{{ msg.error || '请求失败' }}</div>
        </div>

        <!-- 文件引用 -->
        <div v-if="refs.length" class="refs">
          <button
            v-for="(r, i) in refs" :key="i"
            class="ref-chip"
            :title="r.path + (r.line ? ':' + r.line : '')"
            @click="emit('openFile', r.path, r.line)"
          >
            <svg viewBox="0 0 24 24" width="13" height="13"><path fill="currentColor" d="M6 2h8l4 4v16H6V2zm7 1.5V7h3.5L13 3.5zM8 11h8v1.5H8V11zm0 3.5h8V16H8v-1.5zm0 3.5h5v1.5H8V18z"/></svg>
            <span class="ref-path">{{ r.path }}</span><span v-if="r.line" class="ref-line">:{{ r.line }}</span>
          </button>
        </div>

        <!-- 通知 -->
        <div v-for="(n, i) in msg.notices" :key="'n' + i" class="notice">{{ n }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg-row { display: flex; gap: 10px; }
.user-row { justify-content: flex-end; }

.bubble {
  border-radius: 14px;
  padding: 10px 14px;
  max-width: 100%;
}
.user-bubble {
  max-width: min(560px, 82%);
  background: var(--accent);
  color: #fff;
  border-bottom-right-radius: 4px;
  box-shadow: 0 4px 14px rgba(47, 111, 237, .18);
}
.user-text { white-space: pre-wrap; word-break: break-word; }
.user-imgs { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
.user-imgs img {
  max-width: 180px;
  max-height: 180px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, .3);
}

.ai-avatar {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}
.ai-col { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.ai-bubble {
  background: var(--bg-raised);
  border: 1px solid var(--border);
  border-top-left-radius: 4px;
  padding: 12px 16px;
  box-shadow: 0 2px 8px rgba(35, 52, 84, .05);
  width: fit-content;
  max-width: 100%;
}
.answer { word-break: break-word; }
.answer :deep(.md-link) { color: var(--accent); }

.typing { color: var(--text-muted); font-size: 13px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.typing-phase { color: var(--text); font-weight: 500; }
.typing-time {
  font-family: var(--font-mono); font-size: 11.5px; color: var(--text-faint);
  background: var(--bg-hover); border-radius: 99px; padding: 1px 8px;
}
.td {
  width: 5px; height: 5px; border-radius: 50%; background: var(--text-faint);
  display: inline-block; animation: td-blink 1.2s infinite;
}
.td ~ .td { animation-delay: .2s; }
.td ~ .td ~ .td { animation-delay: .4s; }
@keyframes td-blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }

.flag { margin-top: 8px; font-size: 12px; }
.flag.stopped { color: var(--text-faint); }
.flag.error { color: var(--danger); }

/* 深度思考 */
.reason {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: rgba(122, 90, 248, .05);
  overflow: hidden;
}
.reason-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  border: none;
  background: none;
  padding: 8px 12px;
  font-size: 12.5px;
  color: var(--accent-2);
  cursor: pointer;
  text-align: left;
}
.reason-head:hover { background: rgba(122, 90, 248, .07); }
.reason-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-faint); }
.reason-dot.on { background: var(--accent-2); animation: reason-pulse 1.1s infinite; }
@keyframes reason-pulse { 0%, 100% { opacity: .35; } 50% { opacity: 1; } }
.reason-body {
  padding: 2px 12px 10px 27px;
  font-size: 12.5px;
  color: var(--text-muted);
  white-space: pre-wrap;
  max-height: 260px;
  overflow: auto;
}

/* 轨迹 */
.trace { display: flex; flex-direction: column; gap: 4px; }
.step {
  border: 1px solid var(--border);
  border-radius: 9px;
  background: var(--bg-raised);
  overflow: hidden;
}
.step-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 12.5px;
  text-align: left;
}
.step-head:hover { background: var(--bg-hover); }
.step-tag {
  flex: 0 0 auto;
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--bg-selected);
  color: var(--accent);
}
.step-tag.observation { background: rgba(28, 158, 102, .12); color: var(--green); }
.step-tag.reflection { background: rgba(200, 129, 28, .12); color: var(--amber); }
.step-tag.thought { background: rgba(122, 90, 248, .12); color: var(--accent-2); }
.step-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--text-muted);
}
.step-time { flex: 0 0 auto; color: var(--text-faint); font-size: 11px; font-family: var(--font-mono); }
.step-body {
  margin: 0;
  padding: 8px 12px;
  border-top: 1px dashed var(--border);
  max-height: 280px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-muted);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--bg-hover);
}

/* 文件引用 chips */
.refs { display: flex; flex-wrap: wrap; gap: 6px; }
.ref-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 320px;
  border: 1px solid var(--border);
  background: var(--bg-raised);
  border-radius: 8px;
  padding: 3px 9px;
  font-size: 12px;
  color: var(--accent);
  cursor: pointer;
  transition: border-color .15s, background .15s;
}
.ref-chip:hover { border-color: var(--accent); background: var(--accent-soft); }
.ref-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ref-line { color: var(--text-muted); font-family: var(--font-mono); }

.notice {
  font-size: 12px;
  color: var(--text-muted);
  background: var(--bg-hover);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 6px 10px;
}

.chev { color: var(--text-faint); transition: transform .15s; }
.chev.open { transform: rotate(180deg); }
</style>
