<script setup lang="ts">
// P0 底部 AI 对话台：全局代码问答/定位入口（ReAct agent，SSE）。
// - 答案中的文件引用渲染为可点击卡片：跳转代码行 + 文件树展开闪烁 + 分区高亮；
// - 头部「引擎」弹层：MCP 服务器连接状态（godot-ai stdio / unity / unreal HTTP）
//   与 godot-ai 插件安装引导（安装前必须用户确认）。
import { nextTick, ref, watch, onMounted, onBeforeUnmount } from 'vue'
import { useWorkbench, askConfirm, askAlert } from '../composables/workbench'
import { aiApi, mcpApi } from '../api'
import type { McpServer } from '../api'
import type { SseEvent } from '../api'
import { mdToHtml, extractFileRefs } from '../markdown'
import type { FileRef } from '../markdown'
import { demoMode } from '../composables/demo'

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
  if (demoMode.value) {
    await nextTick(scrollToBottom)
    await demoAnswer(turn.id, q)
    sending.value = false
    await nextTick(scrollToBottom)
    return
  }
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
function onFocusChat(ev?: Event) {
  const q = (ev as CustomEvent<{ q?: string }> | undefined)?.detail?.q
  collapsed.value = false
  nextTick(() => {
    if (q) input.value = q
    inputEl.value?.focus()
  })
}
onMounted(() => window.addEventListener('docmind:focus-chat', onFocusChat as EventListener))
onBeforeUnmount(() => window.removeEventListener('docmind:focus-chat', onFocusChat as EventListener))

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
  <section class="cd-dock" :class="{ 'cd-collapsed': collapsed }">
    <header class="cd-head" @click="toggleDock">
      <span class="cd-chevron" :class="{ rotated: !collapsed }">
        <svg width="9" height="9" viewBox="0 0 9 9"><path d="M2 1.5 L5.5 4.5 L2 7.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </span>
      <span class="cd-title">AI 助手</span>
      <span class="cd-hint">用大白话问代码：数值在哪 · 逻辑怎么走 · 报错怎么改</span>
      <span v-if="sending" class="cd-live">AI 正在查代码<span class="cd-dots">…</span></span>
      <span class="cd-spacer" />
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

    <template v-if="!collapsed">
      <div ref="scroller" class="cd-body">
        <div v-if="messages.length === 0" class="cd-empty">
          <p class="cd-empty-title">用大白话提问，AI 自己搜代码，并把答案定位到具体文件和行号 👇</p>
          <div class="cd-quicks">
            <button v-for="q in QUICK_PROMPTS" :key="q" class="cd-quick" @click="send(q)">{{ q }}</button>
          </div>
        </div>

        <div v-for="m in messages" :key="m.id" class="cd-msg" :class="`cd-msg-${m.role}`">
          <div v-if="m.role === 'user'" class="cd-user-bubble">{{ m.text }}</div>
          <template v-else>
            <div v-if="m.text" class="ai-md cd-answer" v-html="answerHtml(m)" />
            <div v-else-if="m.status === 'streaming'" class="cd-thinking">
              AI 正在翻代码、组织回答<span class="cd-dots">…</span>
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
  box-shadow: 0 14px 38px rgba(35,52,84,.2);
  padding: 10px 12px;
  z-index: 60;
}
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
  border: 1px solid #c8dcfa; border-radius: 5px;
  background: #f3f8ff; color: var(--accent);
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
  border: 1px solid #2560d4; background: linear-gradient(180deg,#3b7ef2,#2f6fed);
  color: #fff; font-size: 12px; font-weight: 600; cursor: pointer;
}
.cd-send:disabled { opacity: .4; cursor: default; }
.cd-stop { background: transparent; color: var(--danger); border-color: var(--danger); }
</style>
