// 工作台「选区 AI」域：编辑器选区解释/审查/改写/提问的流式面板，
// 以及改写字级 diff 预览。编辑器标签内容依赖由 workbench 主模块注入。
import { computed, ref, type ComputedRef } from 'vue'
import type { EditorView } from '@codemirror/view'

import { aiApi, getActiveTask, FsApiError } from '../api'
import type { EditorTab } from './workbench-types'

export type AiAction = 'explain' | 'review' | 'rewrite' | 'ask'

/** 编辑器当前非空选区快照（CodeView 上报；坐标为相对视口 fixed）。 */
export interface AiSelection {
  path: string
  name: string
  lang: string
  writable: boolean
  text: string
  from: number
  to: number
  startLine: number
  endLine: number
  /** 选区末端锚点视口坐标，用于浮条定位 */
  x: number
  y: number
}

export interface AiOrigin {
  path: string
  lang: string
  startLine: number
  endLine: number
  from: number
  to: number
  selection: string
  writable: boolean
}

export interface AiTraceItem {
  type: 'thought' | 'action' | 'observation' | 'reflection' | string
  text: string
}

export interface AiTurn {
  id: number
  action: AiAction
  /** 动作标题：解释 / 代码审查 / 改写 / 提问 */
  title: string
  /** ask=用户问题；rewrite=改写要求 */
  instruction: string
  origin: AiOrigin
  status: 'streaming' | 'done' | 'error' | 'stopped'
  /** 流式累积答案（agent: markdown；rewrite: 纯代码） */
  answer: string
  trace: AiTraceItem[]
  replaced: boolean
  error: string | null
}

export interface RewriteDiffState {
  turn: AiTurn
  name: string
  /** 改写要求（可能为空） */
  instruction: string
  /** 编辑器中的原选区文本 */
  oldCode: string
  /** 去围栏后的 AI 结果 */
  newCode: string
  startLine: number
  endLine: number
}

interface AiDeps {
  activeTab: ComputedRef<EditorTab | null>
  contentGetters: Map<number, () => string>
}
let deps!: AiDeps
export function initAiDeps(d: AiDeps) { deps = d }

const AI_ACTION_TITLE: Record<AiAction, string> = {
  explain: '解释选中代码',
  review: '代码审查',
  rewrite: '改写选中代码',
  ask: '就选区提问',
}

const AI_FENCE_LANG: Record<string, string> = {
  python: 'python', javascript: 'javascript', typescript: 'typescript',
  json: 'json', html: 'html', css: 'css', markdown: 'markdown',
  gdscript: 'gdscript',
}

const aiPanelOpen = ref(false)
const turns = ref<AiTurn[]>([])
const activeSelection = ref<AiSelection | null>(null)
/** 浮条点「提问」后，面板进入输入态 */
const askComposing = ref(false)
let turnSeq = 1
let aiAbort: AbortController | null = null

const aiStreaming = computed(() => turns.value.some((t) => t.status === 'streaming'))

function setSelection(sel: AiSelection | null) {
  activeSelection.value = sel
}

function openAiPanel() {
  aiPanelOpen.value = true
}
function closeAiPanel() {
  // 关闭面板不丢历史；若仍在流式则一并停止
  if (aiStreaming.value) stopAi()
  aiPanelOpen.value = false
  askComposing.value = false
}
function clearTurns() {
  if (aiStreaming.value) stopAi()
  turns.value = []
  askComposing.value = false
}

function startAskCompose() {
  askComposing.value = true
  aiPanelOpen.value = true
}

/** 去掉模型偶发多包的一层 markdown 围栏（快通道强约束纯代码，这里双保险）。 */
function stripCodeFence(s: string): string {
  const t = s.trim()
  const m = /^```[A-Za-z0-9_+\-.]*\s*\n([\s\S]*?)\n?```$/.exec(t)
  return m ? m[1].replace(/\s+$/, '') : s.replace(/\s+$/, '')
}

/** 解释 / Review / 自由提问：把选区与任务拼成一条带检索引导的问题，交给 ReAct agent。 */
function buildGroundedQuestion(action: AiAction, sel: AiSelection, instruction?: string): string {
  const fence = '```'
  const fenceLang = AI_FENCE_LANG[sel.lang] || ''
  const header =
    `【选区上下文】文件：${sel.path}（语言：${sel.lang}），` +
    `我在编辑器中选中了第 ${sel.startLine}–${sel.endLine} 行。\n\n` +
    `选中的代码：\n${fence}${fenceLang}\n${sel.text}\n${fence}`
  let task: string
  if (action === 'explain') {
    task =
      '请解释这段选中代码：先用一句话说明它的职责，再分点讲清关键逻辑、输入/输出、状态修改与副作用，' +
      '必要时指出它依赖的项目内其他类/函数。'
  } else if (action === 'review') {
    task =
      '请对这段选中代码做代码审查：找出潜在 bug、边界条件、空值/异常、性能、可读性与命名问题，' +
      '按严重程度从高到低排列；每条给出对应行号、问题原因与具体修改建议（可附最小代码）。' +
      '若没有明显问题，也要明确说明"未发现阻断性问题"并可给出可选改进。'
  } else {
    task = (instruction || '').trim()
  }
  const tail =
    '\n\n选中的代码已经完整贴在上面，请优先直接基于它作答，不要逐个浏览或读取文件。' +
    '只有当确实需要确认选区引用到的外部类/函数/信号的定义或调用点时，才使用 search_code / read_file / grep，' +
    '且工具调用总计不超过 2 次；拿到必要信息后立即给出最终答案，不要反复检索。' +
    '引用外部内容时标注文件路径与行号；只围绕选中片段及其直接相关代码，不要臆造不存在的符号。'
  return `${header}\n\n【任务】${task}${tail}`
}

async function runAi(action: AiAction, instruction?: string): Promise<void> {
  const sel = activeSelection.value
  if (!sel || aiStreaming.value) return
  if (action === 'rewrite' && !sel.writable) return
  const origin: AiOrigin = {
    path: sel.path,
    lang: sel.lang,
    startLine: sel.startLine,
    endLine: sel.endLine,
    from: sel.from,
    to: sel.to,
    selection: sel.text,
    writable: sel.writable,
  }
  const turn: AiTurn = {
    id: turnSeq++,
    action,
    title: AI_ACTION_TITLE[action],
    instruction: (instruction || '').trim(),
    origin,
    status: 'streaming',
    answer: '',
    trace: [],
    replaced: false,
    error: null,
  }
  turns.value = [...turns.value, turn]
  aiPanelOpen.value = true
  askComposing.value = false

  const ac = new AbortController()
  aiAbort = ac
  const live = () => turns.value.find((t) => t.id === turn.id)

  const onEvent = (ev: { type: string; text?: string }) => {
    const t = live()
    if (!t) return
    if (ev.type === 'token' && ev.text) {
      // 快通道（rewrite）单轮直出，token 即纯代码，可逐字显示；
      // agent（解释/Review/提问）每轮 ReAct 都会转发 token（含 Thought/Action 草稿），
      // 不能直接当答案——只在最终 final 事件渲染干净答案，过程靠 trace + thinking 反馈。
      if (action === 'rewrite') t.answer += ev.text
    } else if (ev.type === 'final') {
      // 两通道最终都以 final 给全文：以它为准（agent 通道由此得到干净答案）
      if (typeof ev.text === 'string' && ev.text) t.answer = ev.text
    } else if (ev.type === 'thought' || ev.type === 'action' || ev.type === 'observation' || ev.type === 'reflection') {
      if (ev.text) t.trace.push({ type: ev.type, text: ev.text })
    }
  }

  try {
    if (action === 'rewrite') {
      // 文件全文作上下文（超过快通道上限则省略，仅靠选区）
      const tab = deps.activeTab.value
      let fileCtx = ''
      const getter = tab ? deps.contentGetters.get(tab.id) : null
      if (getter) fileCtx = getter()
      if (fileCtx.length > 56_000) fileCtx = ''
      await aiApi.rewriteSelection(
        {
          path: sel.path,
          lang: sel.lang,
          start_line: sel.startLine,
          end_line: sel.endLine,
          selection: sel.text,
          instruction,
          file_context: fileCtx,
          task_id: getActiveTask().id || undefined,
          task_region: getActiveTask().region || undefined,
          allowed_paths: getActiveTask().allowedPaths,
          engine: window.localStorage.getItem('docmind.engine') || 'godot',
        },
        { onEvent, signal: ac.signal },
      )
    } else {
      const q = buildGroundedQuestion(action, sel, instruction)
      await aiApi.askGrounded(q, { onEvent, signal: ac.signal })
    }
    const t = live()
    if (t) t.status = 'done'
  } catch (e) {
    const t = live()
    if (!t) return
    if ((e as Error).name === 'AbortError') {
      t.status = t.answer ? 'stopped' : 'stopped'
    } else {
      const err = e as FsApiError
      t.status = 'error'
      t.error = err.message || '请求失败'
    }
  } finally {
    if (aiAbort === ac) aiAbort = null
  }
}

function stopAi() {
  aiAbort?.abort()
  aiAbort = null
}

/** 把改写结果替换回编辑器原选区；返回 null=成功，否则为不可替换原因。 */
function applyRewrite(turn: AiTurn): string | null {
  const view = (window as unknown as { __docmind_cm?: EditorView }).__docmind_cm
  const tab = deps.activeTab.value
  if (!view) return '编辑器未就绪。'
  if (!tab || tab.path !== turn.origin.path) return '目标文件不是当前打开的标签，已取消替换。'
  if (!tab.writable) return '该文件为只读保护，不能替换。'
  const o = turn.origin
  // 陈旧坐标护栏：异步往返期间该范围必须仍是原选区文本
  let current: string
  try {
    current = view.state.doc.sliceString(o.from, o.to)
  } catch {
    return '选区坐标已失效，请重新选择后再替换。'
  }
  if (current !== o.selection) {
    return '自 AI 生成后该段代码已被改动，为避免覆盖你的修改，请重新选择再替换。'
  }
  const code = stripCodeFence(turn.answer)
  if (!code.trim()) return 'AI 未返回可替换的代码。'
  view.focus()
  view.dispatch({
    changes: { from: o.from, to: o.to, insert: code },
    selection: { anchor: o.from + code.length },
  })
  turn.replaced = true
  return null
}

async function copyAnswer(turn: AiTurn): Promise<boolean> {
  const text = turn.action === 'rewrite' ? stripCodeFence(turn.answer) : turn.answer
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

// ---------------------------------------------------------------- 改写 diff
const rewriteDiff = ref<RewriteDiffState | null>(null)

function openRewriteDiff(turn: AiTurn) {
  const newCode = stripCodeFence(turn.answer)
  if (!newCode.trim()) return
  rewriteDiff.value = {
    turn,
    name: turn.origin.path.split('/').pop() || turn.origin.path,
    instruction: turn.instruction,
    oldCode: turn.origin.selection,
    newCode,
    startLine: turn.origin.startLine,
    endLine: turn.origin.endLine,
  }
}

function closeRewriteDiff() {
  rewriteDiff.value = null
}

/** 接受差异：复用 applyRewrite 的陈旧坐标/只读护栏；成功关闭弹窗，失败返回原因。 */
function acceptRewriteDiff(): string | null {
  const st = rewriteDiff.value
  if (!st) return null
  const reason = applyRewrite(st.turn)
  if (!reason) rewriteDiff.value = null
  return reason
}

export function useAiSelection() {
  return {
    activeSelection, setSelection,
    aiPanelOpen, openAiPanel, closeAiPanel, clearTurns,
    turns, aiStreaming, askComposing, startAskCompose,
    runAi, stopAi, applyRewrite, copyAnswer,
    rewriteDiff, openRewriteDiff, closeRewriteDiff, acceptRewriteDiff,
  }
}
