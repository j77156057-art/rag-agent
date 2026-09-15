<script setup lang="ts">
// 代码编辑/预览：CodeMirror 6（oneDark）。
// 每个标签页持有独立 EditorState（保留各自 undo 历史），切换标签用 setState；
// 非活动标签的文本取 state.doc，保存时不依赖当前 DOM view。
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { basicSetup } from 'codemirror'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap } from '@codemirror/view'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import type { EditorTab } from '../composables/workbench'
import { useWorkbench } from '../composables/workbench'
import { regionColor, formatMtime, gitState } from '../theme'
import { demoMode } from '../composables/demo'

const props = defineProps<{ tab: EditorTab | null }>()

const {
  saveActive, closeTab, tabs, registerContentGetter, registerDocReplacer, setSelection,
  openSymbolMap,
} = useWorkbench()

// 浅色编辑器主题（壳是浅色，代码区也用白底；语法色走 CM 默认高亮，浅底可读）。
const MONO_FONT = "'Cascadia Code','JetBrains Mono',Consolas,monospace"
const lightEditorTheme = EditorView.theme({
  '&': { height: '100%', fontSize: '12.5px', backgroundColor: '#ffffff', color: '#222b38' },
  '.cm-scroller': { fontFamily: MONO_FONT },
  '.cm-content': { caretColor: '#2f6fed' },
  '&.cm-focused .cm-cursor': { borderLeftColor: '#2f6fed' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection':
    { backgroundColor: 'rgba(47,111,237,.16)' },
  '.cm-gutters': { backgroundColor: '#f6f8fb', color: '#9aa5b6', borderRight: '1px solid #e4e9f2' },
  '.cm-activeLine': { backgroundColor: 'rgba(47,111,237,.05)' },
  '.cm-activeLineGutter': { backgroundColor: '#eef3fc', color: '#2f6fed' },
  '.cm-foldPlaceholder': {
    backgroundColor: '#eef1f7', border: '1px solid #dde3ee', color: '#5a6778',
  },
})

const host = ref<HTMLElement | null>(null)
let view: EditorView | null = null
const states = new Map<number, EditorState>()
const readOnlyComp = new Compartment()

// ---- P2：把当前非空选区上报给选区 AI（含视口坐标，供浮条定位） ----
function publishSelection(v: EditorView, tab: EditorTab) {
  const main = v.state.selection.main
  const text = v.state.sliceDoc(main.from, main.to)
  if (main.empty || !text.trim()) {
    setSelection(null)
    return
  }
  const coords = v.coordsAtPos(main.to)
  if (!coords) {
    setSelection(null)
    return
  }
  setSelection({
    path: tab.path,
    name: tab.name,
    lang: tab.lang,
    writable: tab.writable,
    text,
    from: main.from,
    to: main.to,
    startLine: v.state.doc.lineAt(main.from).number,
    endLine: v.state.doc.lineAt(main.to).number,
    x: coords.right,
    y: coords.bottom,
  })
}

let rafPending = false
function scheduleRepublish() {
  if (rafPending) return
  rafPending = true
  requestAnimationFrame(() => {
    rafPending = false
    if (view && props.tab && !props.tab.loading && !props.tab.error) publishSelection(view, props.tab)
  })
}
function onEditorScrollOrResize() {
  scheduleRepublish()
}

function langExtension(lang: string) {
  switch (lang) {
    case 'python': return python()
    case 'javascript': return javascript()
    case 'jsx': return javascript({ jsx: true })
    case 'typescript': return javascript({ typescript: true })
    case 'tsx': return javascript({ jsx: true, typescript: true })
    case 'json': return json()
    case 'html': return html()
    case 'css': return css()
    case 'markdown': return markdown()
    // gdscript / ini(tscn/tres/cfg) / yaml / toml / csv 等无专用语言包，按纯文本渲染
    default: return []
  }
}

/**
 * CRLF 归一化比较：Windows 仓库（core.autocrlf=true）git checkout 落盘为 CRLF，
 * 而 CodeMirror 建 state 时会把所有换行统一存成 \n，直接逐字符比较会误判 dirty。
 */
function isDocDirty(docText: string, saved: string): boolean {
  const norm = (s: string) => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  return norm(docText) !== norm(saved)
}

function buildState(tab: EditorTab): EditorState {
  return EditorState.create({
    doc: tab.savedContent,
    extensions: [
      basicSetup,
      langExtension(tab.lang),
      lightEditorTheme,
      readOnlyComp.of(EditorState.readOnly.of(!tab.writable)),
      keymap.of([{
        key: 'Mod-s',
        preventDefault: true,
        run: () => {
          void saveActive()
          return true
        },
      }]),
      EditorView.updateListener.of((u) => {
        // CM6 state 不可变：dispatch 后是全新 state 对象，必须回写 Map，
        // 否则非活动标签/保存时 contentGetter 取到的还是旧引用。
        states.set(tab.id, u.state)
        // P2：选区或文档变化时刷新选区快照（文档替换后变光标则隐藏浮条）
        if (u.selectionSet || u.docChanged) publishSelection(u.view, tab)
        if (!u.docChanged) return
        const t = tabs.value.find((x) => x.id === tab.id)
        if (t) t.dirty = isDocDirty(u.state.doc.toString(), t.savedContent)
      }),
    ],
  })
}

function ensureState(tab: EditorTab): EditorState {
  let st = states.get(tab.id)
  if (!st) {
    st = buildState(tab)
    states.set(tab.id, st)
    registerContentGetter(tab.id, () => states.get(tab.id)!.doc.toString())
    registerDocReplacer(tab.id, (content) => replaceTabDoc(tab.id, content))
  }
  return st
}

/**
 * P3：git 回滚/历史恢复后整文档替换。
 * 注意 EditorState.update() 返回的是 Transaction（新状态在 .state 上），不是 State。
 * 活动标签直接 view.dispatch（listener 会自动回写 states）；
 * 非活动标签换 Map 里留存的 EditorState，切回去即为新内容。
 */
function replaceTabDoc(tabId: number, content: string) {
  const old = states.get(tabId)
  if (!old) return
  if (view && (view as unknown as { __tabId?: number }).__tabId === tabId && view.state === old) {
    view.dispatch({ changes: { from: 0, to: old.doc.length, insert: content } })
    return
  }
  states.set(
    tabId,
    old.update({ changes: { from: 0, to: old.doc.length, insert: content } }).state,
  )
}

function syncView() {
  if (!view) return
  // 切换标签 / 进入加载或错误态：旧选区不再有效，隐藏选区浮条
  setSelection(null)
  if (props.tab && !props.tab.loading && !props.tab.error) {
    // 切走前把旧标签的最终 state 落回 Map（listener 已逐次回写，这里兜底）
    const curId = (view as unknown as { __tabId?: number }).__tabId
    if (curId != null && curId !== props.tab.id && states.has(curId)) {
      states.set(curId, view.state)
    }
    ;(view as unknown as { __tabId?: number }).__tabId = props.tab.id
    view.setState(ensureState(props.tab))
  }
}

onMounted(() => {
  view = new EditorView({ parent: host.value!, extensions: [lightEditorTheme] })
  syncView()
  // 编辑器桥：DevTools / 后续选区 AI（P2）经此读取当前 EditorView。
  // view 单例不变，切标签只替换其 state。
  ;(window as unknown as { __docmind_cm?: EditorView }).__docmind_cm = view
  // 选区浮条是 fixed 定位：编辑器滚动或窗口缩放时刷新锚点坐标
  view.scrollDOM.addEventListener('scroll', onEditorScrollOrResize, { passive: true })
  window.addEventListener('resize', onEditorScrollOrResize)
})

onBeforeUnmount(() => {
  view?.scrollDOM.removeEventListener('scroll', onEditorScrollOrResize)
  window.removeEventListener('resize', onEditorScrollOrResize)
  setSelection(null)
  if ((window as unknown as { __docmind_cm?: unknown }).__docmind_cm === view) {
    delete (window as unknown as { __docmind_cm?: EditorView }).__docmind_cm
  }
  view?.destroy()
})

// 切活动 tab / 加载完成 → 挂载对应 state
watch(() => [props.tab?.id, props.tab?.loading, props.tab?.error], syncView)

// 保存成功后（savedContent 变化）重算 dirty
watch(() => props.tab?.savedContent, () => {
  if (!props.tab) return
  const st = states.get(props.tab.id)
  if (st) props.tab.dirty = isDocDirty(st.doc.toString(), props.tab.savedContent)
})

// 标签关闭后回收 state
watch(tabs, (list) => {
  const alive = new Set(list.map((t) => t.id))
  for (const id of [...states.keys()]) {
    if (!alive.has(id)) states.delete(id)
  }
}, { deep: false })

function accent(tab: EditorTab) {
  return regionColor(tab.region)
}
function git(tab: EditorTab) {
  return gitState(tab.tracked, tab.gitDirty)
}
function segments(path: string) {
  return path.split('/')
}
function savedLabel(tab: EditorTab): string {
  if (tab.saving) return '保存中…'
  if (tab.dirty) return '● 未保存'
  if (tab.savedAt) return `已保存 ${new Date(tab.savedAt).toLocaleTimeString('zh-CN', { hour12: false })}`
  return ''
}

// ---- 新手欢迎页：让第一次打开的人 30 秒知道这里能干什么 ----
function focusChat() {
  window.dispatchEvent(new CustomEvent('docmind:focus-chat'))
}
function showSymbolMap() {
  // 演示模式没有项目数据，地图打开也是空的，不触发
  if (demoMode.value) return
  openSymbolMap()
}
</script>

<template>
  <section class="cv">
    <!-- 文件头：面包屑 + 元信息 -->
    <header v-if="tab && !tab.loading && !tab.error" class="cv-head">
      <div class="cv-crumbs">
        <template v-for="(seg, i) in segments(tab.path)" :key="i">
          <span v-if="i" class="cv-sep">/</span>
          <span :class="['cv-seg', { 'cv-seg-region': i === 0 && tab.region, 'cv-seg-last': i === segments(tab.path).length - 1 }]">
            {{ seg }}
          </span>
        </template>
      </div>
      <div class="cv-meta">
        <span v-if="tab.regionName" class="cv-badge cv-region-badge"
              :style="{ color: accent(tab), borderColor: accent(tab) + '66', background: accent(tab) + '14' }">
          {{ tab.regionName }}
        </span>
        <span class="cv-badge cv-lang">{{ tab.lang }}</span>
        <!-- 不在任何 git 仓库（或本机无 git）时不显示徽标，避免每个标签都挂「无 git」噪声 -->
        <span v-if="git(tab).dot !== 'none'" class="cv-badge" :class="`cv-git-${git(tab).dot}`" :title="git(tab).title">
          {{ git(tab).dot === 'untracked' ? '未跟踪' : git(tab).dot === 'dirty' ? '已修改' : '已跟踪' }}
        </span>
        <span v-if="!tab.writable" class="cv-badge cv-readonly" title="契约/受保护文件">只读保护</span>
        <span :class="['cv-savestate', { 'is-dirty': tab.dirty, 'is-saving': tab.saving }]">{{ savedLabel(tab) }}</span>
        <span class="cv-faint">{{ formatMtime(tab.mtime) }}</span>
      </div>
    </header>

    <!-- 加载态 -->
    <div v-if="tab?.loading" class="cv-state">
      <div class="cv-spinner" />
      <p>正在读取 {{ tab.name }}…</p>
    </div>

    <!-- 打开失败 -->
    <div v-else-if="tab?.error" class="cv-state cv-error">
      <svg width="34" height="34" viewBox="0 0 34 34">
        <circle cx="17" cy="17" r="15" fill="none" stroke="#ff6b6b55" stroke-width="1.5" />
        <path d="M17 9 V19" stroke="#ff6b6b" stroke-width="2" stroke-linecap="round" />
        <circle cx="17" cy="24" r="1.3" fill="#ff6b6b" />
      </svg>
      <p class="cv-error-title">无法打开文件 · HTTP {{ tab.errorStatus }}</p>
      <p class="cv-error-msg">{{ tab.error }}</p>
      <button class="cv-close-btn" @click="closeTab(tab.id)">关闭标签</button>
    </div>

    <!-- 新手欢迎页（未打开任何文件时） -->
    <div v-else-if="!tab" class="welcome">
      <div class="welcome-inner">
        <span class="welcome-badge">👋 第一次使用，花 30 秒看一下</span>
        <h2>用大白话指挥 AI 读懂你的项目</h2>
        <p class="welcome-sub">
          这里是 DocMind 代码工作台：不用自己翻代码，直接用中文问 AI——
          「玩家受伤扣多少血在哪算的？」「这个按钮点了为什么没反应？」它会自己搜代码、给答案、标出位置。
          按下面四步开始：
        </p>

        <div class="welcome-grid">
          <a class="welcome-card" href="/">
            <span class="welcome-num">1</span>
            <span>
              <h4>先让 AI「读完」你的项目</h4>
              <p>到 AI 问答首页选择项目文件夹并建立索引。没建索引，AI 就像没读过课本就上考场。</p>
            </span>
          </a>
          <button class="welcome-card" type="button" @click="focusChat">
            <span class="welcome-num">2</span>
            <span>
              <h4>用大白话直接提问</h4>
              <p>点这里，光标会跳到右下角「AI 助手」。试试问：玩家受伤的数值在哪段代码里算的？</p>
            </span>
          </button>
          <button class="welcome-card" type="button" :disabled="demoMode" @click="showSymbolMap">
            <span class="welcome-num">3</span>
            <span>
              <h4>看图秒懂项目结构</h4>
              <p>顶部「代码地图」画出全项目函数在哪定义、谁调用谁；Unity 项目还能查资源引用、标红断链。<template v-if="demoMode">（演示版无真实数据）</template></p>
            </span>
          </button>
          <div class="welcome-card welcome-static">
            <span class="welcome-num">4</span>
            <span>
              <h4>改坏了也能「读档」</h4>
              <p>每次 git 提交都是一个存档点。文件上的「历史版本」可以一键回到任意旧版本，放心改。</p>
            </span>
          </div>
        </div>

        <div class="welcome-foot">
          <span>💡 左侧是项目文件，点击即可查看</span>
          <span>Ctrl+S 保存</span>
          <span>右键文件可新建 / 重命名 / 删除</span>
        </div>
      </div>
    </div>

    <div v-show="tab && !tab.loading && !tab.error" ref="host" class="cv-host" />

    <!-- reindex 警告（不阻断） -->
    <div v-if="tab && !tab.loading && !tab.error && tab.reindexWarn" class="cv-warnbar" :title="tab.reindexWarn">
      {{ tab.reindexWarn }}
    </div>
  </section>
</template>
