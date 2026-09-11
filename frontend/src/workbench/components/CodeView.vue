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
import { oneDark } from '@codemirror/theme-one-dark'
import type { EditorTab } from '../composables/workbench'
import { useWorkbench } from '../composables/workbench'
import { regionColor, formatMtime, gitState } from '../theme'

const props = defineProps<{ tab: EditorTab | null }>()

const { saveActive, closeTab, tabs, registerContentGetter, setSelection } = useWorkbench()

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

function buildState(tab: EditorTab): EditorState {
  return EditorState.create({
    doc: tab.savedContent,
    extensions: [
      basicSetup,
      langExtension(tab.lang),
      oneDark,
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
        if (t) t.dirty = u.state.doc.toString() !== t.savedContent
      }),
      EditorView.theme({
        '&': { height: '100%', fontSize: '12.5px' },
        '.cm-scroller': { fontFamily: "'Cascadia Code','JetBrains Mono',Consolas,monospace" },
        '.cm-gutters': { background: '#0b0f15', borderRight: '1px solid #1b2330' },
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
  }
  return st
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
  view = new EditorView({ parent: host.value!, extensions: [oneDark] })
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
  if (st) props.tab.dirty = st.doc.toString() !== props.tab.savedContent
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

    <!-- 空态 -->
    <div v-else-if="!tab" class="cv-state cv-empty">
      <svg width="46" height="46" viewBox="0 0 46 46" fill="none">
        <rect x="8" y="6" width="30" height="34" rx="3" stroke="#2b3543" stroke-width="1.5" />
        <path d="M14 16 H32 M14 22 H32 M14 28 H25" stroke="#2b3543" stroke-width="1.5" stroke-linecap="round" />
      </svg>
      <p class="cv-empty-title">从左侧文件树选择文件</p>
      <p class="cv-empty-hint">点击打开，Ctrl+S 保存；右键文件或文件夹可新建 / 重命名 / 删除</p>
    </div>

    <div v-show="tab && !tab.loading && !tab.error" ref="host" class="cv-host" />

    <!-- reindex 警告（不阻断） -->
    <div v-if="tab && !tab.loading && !tab.error && tab.reindexWarn" class="cv-warnbar" :title="tab.reindexWarn">
      {{ tab.reindexWarn }}
    </div>
  </section>
</template>
