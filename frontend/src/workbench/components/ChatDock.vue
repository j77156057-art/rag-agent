<script setup lang="ts">
// P0 底部 AI 对话台：全局代码问答/定位入口（ReAct agent，SSE）。
// - 答案中的文件引用渲染为可点击卡片：跳转代码行 + 文件树展开闪烁 + 分区高亮；
// - 头部「引擎」弹层：MCP 服务器连接状态（godot-ai stdio / unity / unreal HTTP）
//   与 godot-ai 插件安装引导（安装前必须用户确认）。
import { nextTick, ref, watch, onBeforeUnmount } from 'vue'
import { useWorkbench, askConfirm, askAlert } from '../composables/workbench'
import { aiApi, mcpApi } from '../api'
import type { McpServer } from '../api'
import type { SseEvent } from '../api'
import { mdToHtml, extractFileRefs } from '../markdown'
import type { FileRef } from '../markdown'

const {
  nodeExists, revealPath, jumpToLine,
} = useWorkbench()

// ---------------------------------------------------------------- 对话状态
interface ChatMsg {
  id: number
  role: 'user' | 'assistant'
  text: string
  status: 'streaming' | 'done' | 'error' | 'stopped'
  trace: { type: string; text: string }[]
  error?: string
}

let msgSeq = 1
const messages = ref<ChatMsg[]>([])
const input = ref('')
const sending = ref(false)
let abortCtl: AbortController | null = null

const scroller = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)

// ---------------------------------------------------------------- 折叠
const collapsed = ref(window.localStorage.getItem('docmind.chatDockCollapsed') === '1')
watch(collapsed, (v) => window.localStorage.setItem('docmind.chatDockCollapsed', v ? '1' : '0'))
function toggleDock() {
  collapsed.value = !collapsed.value
  if (!collapsed.value) nextTick(() => inputEl.value?.focus())
}

const QUICK_PROMPTS = [
  '玩家角色的数值配置在哪些文件？',
  '角色行为逻辑代码在哪里？',
  '项目入口场景和主循环在哪？',
  '帮我梳理这个项目的代码结构',
]

// ---------------------------------------------------------------- 发送 / 停止
async function send(text?: string) {
  const q = (text ?? input.value).trim()
  if (!q || sending.value) return
  input.value = ''
  messages.value.push({ id: msgSeq++, role: 'user', text: q, status: 'done', trace: [] })
  const turn: ChatMsg = { id: msgSeq++, role: 'assistant', text: '', status: 'streaming', trace: [] }
  messages.value.push(turn)
  sending.value = true
  const ac = new AbortController()
  abortCtl = ac
  await nextTick(scrollToBottom)

  const live = () => messages.value.find((m) => m.id === turn.id)
  const onEvent = (ev: SseEvent) => {
    const t = live()
    if (!t) return
    if (ev.type === 'final' && typeof ev.text === 'string' && ev.text) {
      t.text = ev.text
    } else if (ev.type === 'thought' || ev.type === 'action' ||
               ev.type === 'observation' || ev.type === 'reflection') {
      if (ev.text) t.trace.push({ type: ev.type, text: ev.text })
    }
    void nextTick(scrollToBottom)
  }

  try {
    await aiApi.askGrounded(q, { onEvent, signal: ac.signal })
    const t = live()
    if (t) t.status = t.text ? 'done' : 'stopped'
  } catch (e) {
    const t = live()
    if (!t) return
    if ((e as Error).name === 'AbortError') {
      t.status = 'stopped'
      if (!t.text) t.text = '（已停止）'
    } else {
      t.status = 'error'
      t.error = (e as { message?: string }).message || '请求失败'
    }
  } finally {
    sending.value = false
    abortCtl = null
    await nextTick(scrollToBottom)
  }
}

function stop() {
  abortCtl?.abort()
  abortCtl = null
}

function onKeydown(ev: KeyboardEvent) {
  if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
    ev.preventDefault()
    void send()
  }
}

function clearMessages() {
  if (sending.value) stop()
  messages.value = []
}

function scrollToBottom() {
  const el = scroller.value
  if (el) el.scrollTop = el.scrollHeight
}

// ---------------------------------------------------------------- 答案引用卡片
function refsOf(msg: ChatMsg): FileRef[] {
  // 只对真实存在于文件树中的路径生成卡片（过滤幻觉引用）
  return extractFileRefs(msg.text).filter((r) => nodeExists(r.path))
}

function answerHtml(msg: ChatMsg): string {
  return mdToHtml(msg.text || '')
}

async function openRef(r: FileRef) {
  await jumpToLine(r.path, r.line || 1)
  revealPath(r.path)
}

const TRACE_LABEL: Record<string, string> = {
  thought: '思考', action: '工具调用', observation: '观察', reflection: '反思',
}
const traceOpen = ref<Set<number>>(new Set())
function toggleTrace(id: number) {
  const next = new Set(traceOpen.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  traceOpen.value = next
}

// ---------------------------------------------------------------- 引擎 / MCP 弹层
const enginePopOpen = ref(false)
const servers = ref<McpServer[]>([])
const probing = ref<string | null>(null)
const probeResults = ref<Record<string, { ok: boolean; tool_count?: number; error?: string }>>({})
const addon = ref<Awaited<ReturnType<typeof mcpApi.addonStatus>> | null>(null)
const installing = ref(false)

async function refreshEnginePop() {
  try {
    const [s, a] = await Promise.allSettled([mcpApi.servers(), mcpApi.addonStatus()])
    if (s.status === 'fulfilled') servers.value = s.value.servers
    if (a.status === 'fulfilled') addon.value = a.value
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
    probeResults.value[key] = { ok: !!r.ok && !(r as { error?: string }).error, tool_count: r.tool_count, error: r.error }
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
function connectorGuide(s: McpServer) {
  if (s.key.includes('godot')) return '让 AI 读取场景、节点和运行日志；请先打开 Godot 项目。'
  if (s.key.includes('unity')) return '让 AI 查看 Unity 场景和组件；需先启动 Unity 并运行 MCP 插件。'
  if (s.key.includes('unreal')) return '让 AI 查询 Unreal 资产、Actor 和蓝图；需先启动 Editor Bridge。'
  return '让 AI 连接外部工具并调用其能力。'
}
</script>

<template>
  <section class="cd-dock" :class="{ 'cd-collapsed': collapsed }">
    <header class="cd-head" @click="toggleDock">
      <span class="cd-chevron" :class="{ rotated: !collapsed }">
        <svg width="9" height="9" viewBox="0 0 9 9"><path d="M2 1.5 L5.5 4.5 L2 7.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </span>
      <span class="cd-title">AI 助手</span>
      <span class="cd-hint">问代码 · 找文件 · 定位角色数值与行为逻辑</span>
      <span v-if="sending" class="cd-live">检索中<span class="cd-dots">…</span></span>
      <span class="cd-spacer" />
      <button
        class="cd-btn"
        :class="{ 'cd-btn-on': enginePopOpen }"
        title="引擎 / MCP 连接（godot-ai / Unity / Unreal）"
        @click.stop="enginePopOpen = !enginePopOpen"
      >
        <svg width="14" height="14" viewBox="0 0 14 14">
          <path d="M5 1.5 H1.5 V5 M9 1.5 H12.5 V5 M5 12.5 H1.5 V9 M9 12.5 H12.5 V9" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          <circle cx="7" cy="7" r="2.1" fill="none" stroke="currentColor" stroke-width="1.2"/>
        </svg>
        <span>引擎</span>
      </button>
      <button class="cd-btn" title="清空对话" @click.stop="clearMessages">
        <svg width="13" height="13" viewBox="0 0 13 13"><path d="M2.5 3.2 H10.5 M5.2 3.2 V2 Q5.2 1.5 5.7 1.5 H7.3 Q7.8 1.5 7.8 2 V3.2 M3.4 3.2 L3.8 11 Q3.8 11.6 4.4 11.6 H8.6 Q9.2 11.6 9.2 11 L9.6 3.2" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </header>

    <!-- 引擎 / MCP 弹层 -->
    <!-- 点击外部关闭：透明遮罩截获弹层外点击 -->
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
          <span class="cd-dot" :class="resultOf(s)?.ok ? 'cd-dot-ok' : (!s.enabled ? 'cd-dot-off' : 'cd-dot-idle')" />
          <span class="cd-server-name">{{ s.label || s.key }}</span>
          <span class="cd-server-meta">{{ s.transport === 'stdio' ? '本机插件' : '本机服务' }}</span>
          <span class="cd-spacer" />
          <button class="cd-mini" :disabled="probing === s.key || !s.enabled" @click="probe(s.key)">
            {{ probing === s.key ? '连接中…' : (resultOf(s.key) ? '重试' : '连接') }}
          </button>
        </div>
        <div class="cd-server-guide">{{ connectorGuide(s) }}</div>
        <div v-if="resultOf(s.key)" class="cd-server-result">
          <span v-if="resultOf(s.key)?.ok" class="cd-ok-text">
            已连接 · {{ resultOf(s.key)?.tool_count }} 个工具可用
          </span>
          <span v-else class="cd-err-text" :title="resultOf(s.key)?.error">
            {{ resultOf(s.key)?.error || '连接失败（确认对应引擎/插件已运行）' }}
          </span>
        </div>
        <div v-if="s.help && !resultOf(s.key)" class="cd-server-help">{{ s.help }}</div>
      </div>
    </div>

    <template v-if="!collapsed">
      <div ref="scroller" class="cd-body">
        <div v-if="messages.length === 0" class="cd-empty">
          <p class="cd-empty-title">直接问整个代码库，AI 会定位到具体文件和行号</p>
          <div class="cd-quicks">
            <button v-for="q in QUICK_PROMPTS" :key="q" class="cd-quick" @click="send(q)">{{ q }}</button>
          </div>
        </div>

        <div v-for="m in messages" :key="m.id" class="cd-msg" :class="`cd-msg-${m.role}`">
          <div v-if="m.role === 'user'" class="cd-user-bubble">{{ m.text }}</div>
          <template v-else>
            <div v-if="m.text" class="ai-md cd-answer" v-html="answerHtml(m)" />
            <div v-else-if="m.status === 'streaming'" class="cd-thinking">
              正在检索代码库并组织回答<span class="cd-dots">…</span>
            </div>
            <div v-if="m.status === 'error'" class="cd-error">⚠ {{ m.error }}</div>
            <div v-if="m.trace.length" class="cd-trace">
              <button class="cd-trace-head" @click="toggleTrace(m.id)">
                {{ traceOpen.has(m.id) ? '▾' : '▸' }} 检索轨迹（{{ m.trace.length }}）
              </button>
              <div v-if="traceOpen.has(m.id)" class="cd-trace-body">
                <div v-for="(t, i) in m.trace" :key="i" class="cd-trace-item">
                  <span class="cd-trace-tag">{{ TRACE_LABEL[t.type] || t.type }}</span>
                  <span class="cd-trace-text">{{ t.text }}</span>
                </div>
              </div>
            </div>
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

      <footer class="cd-inputbar">
        <textarea
          ref="inputEl"
          v-model="input"
          class="cd-input"
          rows="2"
          placeholder="提问：角色数值在哪 / 解释这段逻辑 / 这个报错怎么改…（Ctrl+Enter 发送）"
          @keydown="onKeydown"
        />
        <button v-if="sending" class="cd-send cd-stop" @click="stop">停止</button>
        <button v-else class="cd-send" :disabled="!input.trim()" @click="send()">发送</button>
      </footer>
    </template>
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
  max-height: 46vh;
}
.cd-collapsed { max-height: none; }

.cd-head {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 32px;
  padding: 0 10px;
  cursor: pointer;
  user-select: none;
}
.cd-head:hover { background: var(--bg-hover); }
.cd-chevron { display: flex; color: var(--text-muted); transition: transform .15s; }
.cd-chevron.rotated { transform: rotate(90deg); }
.cd-title { font-size: 12px; font-weight: 600; color: var(--text); }
.cd-hint { font-size: 11px; color: var(--text-faint); }
.cd-live { font-size: 11px; color: var(--amber); margin-left: 4px; }
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
  position: absolute;
  left: 10px; bottom: 38px;
  width: 460px; max-width: calc(100vw - 24px);
  max-height: 60vh; overflow-y: auto;
  background: var(--bg-raised);
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  box-shadow: 0 12px 32px rgba(0,0,0,.5);
  padding: 10px 12px;
  z-index: 60;
}
.cd-pop-title { font-size: 12px; font-weight: 600; color: var(--text); margin-bottom: 8px; }
.cd-pop-subtitle { font-size: 11px; color: var(--text-dim); line-height: 1.5; margin-bottom: 8px; }
.cd-addon {
  border: 1px solid var(--border); border-radius: 7px;
  padding: 8px 10px; margin-bottom: 10px; background: #0c121a;
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
.cd-thinking, .cd-error { font-size: 12px; color: var(--text-muted); padding: 4px 0; }
.cd-error { color: var(--danger); }

.cd-trace { margin-top: 5px; }
.cd-trace-head {
  border: none; background: none; padding: 0; cursor: pointer;
  font-size: 11px; color: var(--text-faint);
}
.cd-trace-head:hover { color: var(--text-muted); }
.cd-trace-body { margin-top: 4px; border-left: 2px solid var(--border); padding-left: 8px; display: flex; flex-direction: column; gap: 3px; }
.cd-trace-item { font-size: 11px; color: var(--text-faint); display: flex; gap: 6px; }
.cd-trace-tag { flex: 0 0 52px; color: var(--text-muted); }
.cd-trace-text { white-space: pre-wrap; word-break: break-all; max-height: 70px; overflow: hidden; }

.cd-refs { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 7px; }
.cd-ref {
  display: inline-flex; align-items: center; gap: 4px;
  height: 22px; padding: 0 8px;
  border: 1px solid var(--border-strong); border-radius: 5px;
  background: #0d151f; color: var(--accent);
  font-family: var(--font-mono); font-size: 11px; cursor: pointer;
}
.cd-ref:hover { background: var(--bg-selected); border-color: var(--accent); }
.cd-ref-line { color: var(--amber); }

/* 输入区 */
.cd-inputbar { display: flex; gap: 8px; align-items: flex-end; padding: 8px 12px 10px; }
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
  border: 1px solid var(--accent); background: var(--accent);
  color: #08121f; font-size: 12px; font-weight: 600; cursor: pointer;
}
.cd-send:disabled { opacity: .4; cursor: default; }
.cd-stop { background: transparent; color: var(--danger); border-color: var(--danger); }
</style>
