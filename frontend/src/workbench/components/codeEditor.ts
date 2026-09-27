// CodeMirror 6 运行时工厂：所有 CM 依赖集中在本模块，由 CodeView 在首次打开文件时
// 动态 import。工作台概览首屏不加载 650KB 的 vendor-codemirror。
// CodeView 仍保留 EditorView/EditorState 的「类型」引用（类型编译后擦除，无运行时成本）。
import { basicSetup } from 'codemirror'
import { Compartment, EditorState } from '@codemirror/state'
import { EditorView, keymap, type ViewUpdate } from '@codemirror/view'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { json } from '@codemirror/lang-json'
import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { markdown } from '@codemirror/lang-markdown'
import type { Ref } from 'vue'
import type { EditorTab } from '../composables/workbench-types'

// 浅色编辑器主题（壳是浅色，代码区也用白底；语法色走 CM 默认高亮，浅底可读）。
const MONO_FONT = "'Cascadia Code','JetBrains Mono',Consolas,monospace"
export const lightEditorTheme = EditorView.theme({
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
    backgroundColor: '#eef1f7', border: '1px solid #dde3ee', color: '#5a6788',
  },
})

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

export interface EditorStateHooks {
  saveActive: () => void
  tabs: Ref<EditorTab[]>
  /** 每次事务后把新 EditorState 回写到 CodeView 的 Map（含纯选区变化）。 */
  commitState: (tab: EditorTab, state: EditorState) => void
  publishSelection: (view: EditorView, tab: EditorTab) => void
}

/** CRLF 归一化比较（Windows autocrlf 仓库下 CM 统一存 \n，直接比会误判 dirty）。 */
function isDocDirty(docText: string, saved: string): boolean {
  const norm = (s: string) => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  return norm(docText) !== norm(saved)
}

export function buildEditorState(tab: EditorTab, hooks: EditorStateHooks): EditorState {
  // 每个 state 一个独立 Compartment（当前无动态切换只读，仅作唯一占位）
  const readOnlyComp = new Compartment()
  return EditorState.create({
    doc: tab.draftContent ?? tab.savedContent,
    extensions: [
      basicSetup,
      langExtension(tab.lang),
      lightEditorTheme,
      readOnlyComp.of(EditorState.readOnly.of(!tab.writable)),
      keymap.of([{
        key: 'Mod-s',
        preventDefault: true,
        run: () => {
          hooks.saveActive()
          return true
        },
      }]),
      EditorView.updateListener.of((u: ViewUpdate) => {
        // CM6 state 不可变：dispatch 后是全新 state 对象，必须回写 Map，
        // 否则非活动标签/保存时 contentGetter 取到的还是旧引用。
        hooks.commitState(tab, u.state)
        // P2：选区或文档变化时刷新选区快照（文档替换后变光标则隐藏浮条）
        hooks.publishSelection(u.view, tab)
        if (!u.docChanged) return
        const t = hooks.tabs.value.find((x) => x.id === tab.id)
        if (t) {
          t.draftContent = u.state.doc.toString()
          t.dirty = isDocDirty(t.draftContent, t.savedContent)
        }
      }),
    ],
  })
}

export function createEditorView(host: HTMLElement): EditorView {
  return new EditorView({ parent: host, extensions: [lightEditorTheme] })
}
