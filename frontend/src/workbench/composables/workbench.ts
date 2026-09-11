// 工作台共享状态（模块级单例）：文件树、多标签编辑、保存/冲突、新建/改名/删除流程、
// 对话框与右键菜单。组件只负责渲染与转发事件。
import { computed, ref, shallowRef } from 'vue'
import { EditorView } from '@codemirror/view'
import { fsApi, FsApiError } from '../api'
import type { TreeNode, TreeResp } from '../api'

// ---------------------------------------------------------------- 标签页
export interface EditorTab {
  id: number
  path: string
  name: string
  lang: string
  region: string | null
  regionName: string | null
  mtime: number
  writable: boolean
  /** 只读快照：建编辑器时作为初始 doc，保存成功后更新 */
  savedContent: string
  dirty: boolean
  loading: boolean
  error: string | null
  errorStatus: number | null
  saving: boolean
  savedAt: number | null
  tracked: boolean | null
  gitDirty: boolean | null
  reindexWarn: string | null
}

let tabSeq = 1

/**
 * 契约文件只读名单（与后端 workbench_fs 写删保护名单保持一致）。
 * 后端 tree 仍返 writable:true，由前端在打开时兜底锁定；保存接口另有 403 兜底。
 */
const CONTRACT_READONLY = new Set([
  'regions.json',
  'DOCMIND_RULES.md',
  'DEV_INDEX.md',
  'dev_changesets.jsonl',
])

// ---------------------------------------------------------------- 对话框
interface ConfirmSpec {
  kind: 'confirm'
  title: string
  message: string
  detail?: string
  confirmText?: string
  danger?: boolean
  resolve: (ok: boolean) => void
}
interface PromptSpec {
  kind: 'prompt'
  title: string
  message?: string
  defaultValue: string
  placeholder?: string
  resolve: (v: string | null) => void
}
interface AlertSpec {
  kind: 'alert'
  title: string
  message: string
  detail?: string
  resolve: () => void
}
interface ConflictSpec {
  kind: 'conflict'
  path: string
  serverMtime: number
  resolve: (overwrite: boolean) => void
}
export type DialogSpec = ConfirmSpec | PromptSpec | AlertSpec | ConflictSpec

// ---------------------------------------------------------------- 右键菜单
export interface MenuItem {
  label: string
  danger?: boolean
  disabled?: boolean
  separatorBefore?: boolean
  run: () => void
}
export interface MenuPos { x: number; y: number; items: MenuItem[] }

// ---------------------------------------------------------------- 状态
const tree = shallowRef<TreeResp | null>(null)
const treeLoading = ref(false)
const treeError = ref<FsApiError | null>(null)

const tabs = ref<EditorTab[]>([])
const activeId = ref<number | null>(null)
const selectedPath = ref<string | null>(null)

const dialog = ref<DialogSpec | null>(null)
const ctxMenu = ref<MenuPos | null>(null)

/** tab.id -> 取当前编辑器文本（由 CodeView 注册，保存时取最新内容） */
const contentGetters = new Map<number, () => string>()

const activeTab = computed<EditorTab | null>(
  () => tabs.value.find((t) => t.id === activeId.value) ?? null,
)

/**
 * 当前代码库是否处于 git 管理下：树中任一文件节点带非空 tracked 即为是。
 * 目录节点后端不返 tracked；本机未装 git 或项目未 init 时整树皆 null。
 * 用于删除文案：无 git 上下文时不使用「未跟踪/未纳入 git」这类指责式措辞。
 */
const gitActive = computed(() => {
  if (!tree.value) return false
  for (const n of walkNodes(tree.value.nodes)) {
    if (n.type === 'file' && n.tracked !== undefined && n.tracked !== null) return true
  }
  return false
})

// ---------------------------------------------------------------- 对话框 API
export function askConfirm(spec: Omit<ConfirmSpec, 'kind' | 'resolve'>): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'confirm', resolve }
  })
}
export function askPrompt(spec: Omit<PromptSpec, 'kind' | 'resolve'>): Promise<string | null> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'prompt', resolve }
  })
}
export function askAlert(spec: Omit<AlertSpec, 'kind' | 'resolve'>): Promise<void> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'alert', resolve: () => resolve() }
  })
}
function askConflict(path: string, serverMtime: number): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.value = { kind: 'conflict', path, serverMtime, resolve }
  })
}

export function resolveDialog(value?: boolean | string) {
  const d = dialog.value
  if (!d) return
  dialog.value = null
  if (d.kind === 'confirm') d.resolve(value === true)
  else if (d.kind === 'prompt') d.resolve((value as string | null) ?? null)
  else if (d.kind === 'alert') d.resolve()
  else d.resolve(value === true)
}

// ---------------------------------------------------------------- 树辅助
function* walkNodes(nodes: TreeNode[]): Generator<TreeNode> {
  for (const n of nodes) {
    yield n
    if (n.children?.length) yield* walkNodes(n.children)
  }
}

function findNode(nodes: TreeNode[], path: string): TreeNode | null {
  for (const n of nodes) {
    if (n.path === path) return n
    if (n.children?.length) {
      const hit = findNode(n.children, path)
      if (hit) return hit
    }
  }
  return null
}

function parentPath(p: string): string | null {
  const i = p.lastIndexOf('/')
  return i > 0 ? p.slice(0, i) : null
}

async function loadTree(selectPath?: string | null) {
  treeLoading.value = true
  treeError.value = null
  try {
    tree.value = await fsApi.tree()
    syncTabGitStates()
    if (selectPath !== undefined) selectedPath.value = selectPath
  } catch (e) {
    tree.value = null
    treeError.value = e as FsApiError
  } finally {
    treeLoading.value = false
  }
}

function syncTabGitStates() {
  if (!tree.value) return
  for (const tab of tabs.value) {
    const node = findNode(tree.value.nodes, tab.path)
    if (node) {
      tab.tracked = node.tracked ?? null
      tab.gitDirty = node.dirty ?? null
    }
  }
}

// ---------------------------------------------------------------- 打开 / 切换 / 关闭
async function openPath(path: string, preferWritable?: boolean) {
  const existing = tabs.value.find((t) => t.path === path)
  if (existing) {
    activeId.value = existing.id
    selectedPath.value = path
    return
  }
  const node = tree.value ? findNode(tree.value.nodes, path) : null
  const contractLocked = CONTRACT_READONLY.has(path.split('/').pop() || path)
  const tab: EditorTab = {
    id: tabSeq++,
    path,
    name: path.split('/').pop() || path,
    lang: node?.lang ?? 'text',
    region: node?.region ?? null,
    regionName: node?.region_name ?? null,
    mtime: 0,
    writable: preferWritable ?? node?.writable ?? true,
    savedContent: '',
    dirty: false,
    loading: true,
    error: null,
    errorStatus: null,
    saving: false,
    savedAt: null,
    tracked: node?.tracked ?? null,
    gitDirty: node?.dirty ?? null,
    reindexWarn: null,
  }
  tabs.value = [...tabs.value, tab]
  // 必须取回 reactive 代理：异步回来后在 raw 对象上写属性不会触发渲染更新
  const live = tabs.value[tabs.value.length - 1]
  activeId.value = tab.id
  selectedPath.value = path
  try {
    const f = await fsApi.read(path)
    live.lang = f.lang
    live.region = f.region
    live.regionName = f.region_name
    live.mtime = f.mtime
    live.tracked = f.tracked
    live.gitDirty = f.dirty
    // 树节点 writable 优先（file 接口对契约文件恒返 writable:true）
    if (node) live.writable = node.writable
    if (contractLocked) live.writable = false
    live.savedContent = f.content
    live.loading = false
  } catch (e) {
    const err = e as FsApiError
    live.loading = false
    live.error = err.message
    live.errorStatus = err.status
  }
}

function openNode(node: TreeNode) {
  if (node.type === 'file') void openPath(node.path, node.writable)
}

// ---------------------------------------------------------------- P1：符号跳转 / 地图开关

const symbolMapOpen = ref(false)

function openSymbolMap() {
  symbolMapOpen.value = true
}
function closeSymbolMap() {
  symbolMapOpen.value = false
}

const relationGraphOpen = ref(false)

function openRelationGraph() {
  relationGraphOpen.value = true
}
function closeRelationGraph() {
  relationGraphOpen.value = false
}

/** 打开文件（必要时等待异步加载与 CM 挂载）并把光标定位/滚动到指定行。 */
async function jumpToLine(path: string, line: number): Promise<void> {
  await openPath(path)
  const target = Math.max(1, line | 0)
  await new Promise<void>((resolve) => {
    const t0 = Date.now()
    const tick = () => {
      const view = (window as unknown as { __docmind_cm?: EditorView }).__docmind_cm
      const tab = activeTab.value
      if (view && tab && tab.path === path && !tab.loading && !tab.error) {
        // view 单例：确认它已切换到该标签的 doc（新标签内容加载完成后才 setState）
        const ready = tab.savedContent !== '' || view.state.doc.lines > 0
        if (ready && view.state.doc.lines >= target - 1) {
          const ln = Math.min(target, view.state.doc.lines)
          const pos = view.state.doc.line(ln).from
          view.focus()
          view.dispatch({
            selection: { anchor: pos },
            effects: EditorView.scrollIntoView(pos, { y: 'center' }),
          })
          resolve()
          return
        }
      }
      if (Date.now() - t0 > 5000) {
        resolve()
        return
      }
      setTimeout(tick, 50)
    }
    tick()
  })
}

function activateTab(id: number) {
  activeId.value = id
  const tab = tabs.value.find((t) => t.id === id)
  if (tab) selectedPath.value = tab.path
}

async function closeTab(id: number) {
  const idx = tabs.value.findIndex((t) => t.id === id)
  if (idx < 0) return
  const tab = tabs.value[idx]
  if (tab.dirty) {
    const ok = await askConfirm({
      title: '放弃未保存的改动？',
      message: `「${tab.name}」有尚未保存的修改，关闭后将丢失。`,
      confirmText: '放弃改动并关闭',
      danger: true,
    })
    if (!ok) return
  }
  contentGetters.delete(id)
  const next = tabs.value.filter((t) => t.id !== id)
  tabs.value = next
  if (activeId.value === id) {
    const neighbor = next[Math.min(idx, next.length - 1)] ?? null
    activeId.value = neighbor ? neighbor.id : null
    selectedPath.value = neighbor ? neighbor.path : null
  }
}

export function registerContentGetter(id: number, fn: () => string) {
  contentGetters.set(id, fn)
}

// ---------------------------------------------------------------- 保存
async function saveTab(id: number, overwrite = false): Promise<boolean> {
  const tab = tabs.value.find((t) => t.id === id)
  if (!tab || !tab.writable || tab.saving) return false
  const getter = contentGetters.get(id)
  if (!getter) return false
  const content = getter()
  tab.saving = true
  tab.reindexWarn = null
  try {
    const resp = await fsApi.save(tab.path, content, overwrite ? null : tab.mtime)
    tab.mtime = resp.mtime
    tab.savedContent = content
    tab.dirty = false
    tab.savedAt = Date.now()
    tab.reindexWarn = resp.reindex_warnings?.[0] ?? null
    void loadTree(tab.path)
    return true
  } catch (e) {
    const err = e as FsApiError
    if (err.status === 409 && typeof err.extra.server_mtime === 'number') {
      const doOverwrite = await askConflict(tab.path, err.extra.server_mtime as number)
      if (doOverwrite) {
        // 注意：外层 finally 尚未执行，saving 仍为 true，递归前必须先释放重入锁
        tab.saving = false
        return saveTab(id, true)
      }
      return false
    }
    await askAlert({
      title: err.status === 422 ? '语法校验未通过，写入已取消' : '保存失败',
      message: err.message,
      detail: err.status ? `HTTP ${err.status}` : undefined,
    })
    return false
  } finally {
    tab.saving = false
  }
}

function saveActive() {
  if (activeId.value != null) return saveTab(activeId.value)
  return Promise.resolve(false)
}

// ---------------------------------------------------------------- 新建
const NAME_BAD = /[\\/:*?"<>|]/

async function createAt(parent: TreeNode | null, kind: 'file' | 'folder') {
  const under = parent ? parent.path : '根目录'
  const name = await askPrompt({
    title: kind === 'file' ? '新建文件' : '新建文件夹',
    message: `位置：${under}`,
    defaultValue: kind === 'file' ? '新文件.gd' : '新文件夹',
    placeholder: '名称（可含 / 创建多级）',
  })
  if (name == null) return
  const trimmed = name.trim().replace(/^\/+|\/+$/g, '')
  if (!trimmed) return
  // 新建允许含 / 创建多级目录；单段内仍禁止 Windows 非法字符与 .. 段
  const segs = trimmed.split('/')
  const SEG_BAD = /[\\:*?"<>|]/
  if (segs.some((s) => !s || s === '..' || SEG_BAD.test(s))) {
    await askAlert({ title: '名称不合法', message: '不能包含 \\ : * ? " < > |、空路径段或 .. 段。' })
    return
  }
  const rel = parent ? `${parent.path}/${trimmed}` : trimmed
  try {
    const resp = await fsApi.create(rel, kind)
    await loadTree(resp.path)
    if (kind === 'file') await openPath(resp.path)
  } catch (e) {
    const err = e as FsApiError
    await askAlert({ title: '创建失败', message: err.message, detail: err.status ? `HTTP ${err.status}` : undefined })
  }
}

// ---------------------------------------------------------------- 改名
async function renameNode(node: TreeNode) {
  const dir = parentPath(node.path)
  const name = await askPrompt({
    title: node.type === 'dir' ? '重命名文件夹' : '重命名文件',
    message: dir ? `位置：${dir}` : '位置：根目录',
    defaultValue: node.name,
  })
  if (name == null) return
  const trimmed = name.trim()
  if (!trimmed || trimmed === node.name) return
  if (NAME_BAD.test(trimmed) || trimmed.includes('..')) {
    await askAlert({ title: '名称不合法', message: '不能包含 \\ / : * ? " < > | 或 .. 段。' })
    return
  }
  const newPath = dir ? `${dir}/${trimmed}` : trimmed
  try {
    await fsApi.rename(node.path, newPath)
    const openTab = tabs.value.find((t) => t.path === node.path)
    if (openTab) {
      openTab.path = newPath
      openTab.name = trimmed
    }
    await loadTree(newPath)
    if (activeId.value && openTab) selectedPath.value = newPath
  } catch (e) {
    const err = e as FsApiError
    await askAlert({
      title: '重命名失败',
      message: err.message,
      detail: err.status === 403 ? '分区契约保护：跨分区移动需走 dev_refactor 审批流。' : err.status ? `HTTP ${err.status}` : undefined,
    })
  }
}

// ---------------------------------------------------------------- 删除（多级确认）
async function deleteNode(node: TreeNode) {
  const isDir = node.type === 'dir'
  // 目录节点不返 tracked，用树级信号判断是否处于 git 项目
  const inGit = isDir ? gitActive.value : node.tracked !== null && node.tracked !== undefined
  let recursive = false
  let force = false
  // 第一轮预确认（让 tracked 文件/普通目录也有一次明确确认）
  let detail: string | undefined
  if (isDir) {
    detail = inGit
      ? '已纳入 git 的内容可由 git 恢复；未跟踪文件删除后不可恢复。'
      : '该项目未启用 git 版本管理（未安装 git 或未初始化仓库），删除后不可恢复。'
  } else if (node.tracked === false) {
    detail = '该文件未纳入 git，删除后不可恢复。'
  } else if (node.tracked) {
    detail = '已纳入 git，内容可由 git 恢复。'
  } else {
    detail = '该项目未启用 git 版本管理（未安装 git 或未初始化仓库），删除后不可恢复。'
  }
  const preOk = await askConfirm({
    title: isDir ? `删除文件夹「${node.name}」？` : `删除文件「${node.name}」？`,
    message: isDir ? '文件夹将被删除。' : '文件将被删除。',
    detail,
    confirmText: '删除',
    danger: true,
  })
  if (!preOk) return

  for (let guard = 0; guard < 3; guard++) {
    try {
      await fsApi.delete(node.path, recursive, force)
      // 关闭已打开的对应 tab（含目录下的子 tab）
      const prefix = node.path + '/'
      const dead = tabs.value.filter((t) => t.path === node.path || (isDir && t.path.startsWith(prefix)))
      for (const t of dead) {
        contentGetters.delete(t.id)
        if (activeId.value === t.id) {
          activeId.value = null
          selectedPath.value = null
        }
      }
      if (dead.length) tabs.value = tabs.value.filter((t) => !dead.includes(t))
      if (selectedPath.value === node.path || (isDir && selectedPath.value?.startsWith(prefix))) {
        selectedPath.value = null
      }
      await loadTree(selectedPath.value)
      return
    } catch (e) {
      const err = e as FsApiError
      if (err.status !== 409) {
        await askAlert({
          title: '删除失败',
          message: err.message,
          detail: err.status === 403 ? '契约文件/受保护目录禁止在工作台删除。' : err.status ? `HTTP ${err.status}` : undefined,
        })
        return
      }
      const extra = err.extra as Record<string, unknown>
      // 未跟踪文件/目录 → force
      const untrackedCount = Number(extra.untracked_count ?? 0)
      if (untrackedCount > 0 || (!isDir && !extra.count)) {
        const n = untrackedCount || 1
        const ok = await askConfirm(inGit
          ? {
              title: '包含未纳入 git 的内容，删除不可恢复',
              message: isDir
                ? `文件夹中有 ${n} 个未跟踪文件，删除后无法通过 git 恢复。`
                : '该文件未纳入 git，删除后不可恢复。',
              detail: typeof extra.untracked_sample === 'object'
                ? (extra.untracked_sample as string[]).slice(0, 6).join('\n')
                : undefined,
              confirmText: '仍要永久删除',
              danger: true,
            }
          : {
              // 无 git 环境/非 git 项目：中性措辞，只陈述不可逆事实
              title: '删除后不可恢复',
              message: isDir
                ? `文件夹中有 ${n} 个文件，当前项目未启用 git 版本管理，删除后无法恢复。`
                : '当前项目未启用 git 版本管理，文件删除后无法恢复。',
              detail: typeof extra.untracked_sample === 'object'
                ? (extra.untracked_sample as string[]).slice(0, 6).join('\n')
                : undefined,
              confirmText: '仍要永久删除',
              danger: true,
            })
        if (!ok) return
        force = true
        continue
      }
      // 非空目录 → recursive
      const count = Number(extra.count ?? 0)
      if (count > 0) {
        const ok = await askConfirm({
          title: '文件夹非空',
          message: `「${node.name}」内有 ${count} 个项目，将一并删除。`,
          detail: (extra.sample as string[] | undefined)?.slice(0, 6).join('\n'),
          confirmText: '递归删除全部',
          danger: true,
        })
        if (!ok) return
        recursive = true
        continue
      }
      await askAlert({ title: '删除失败', message: err.message })
      return
    }
  }
}

// ---------------------------------------------------------------- 右键菜单
function openNodeMenu(ev: MouseEvent, node: TreeNode) {
  const isDir = node.type === 'dir'
  const items: MenuItem[] = isDir
    ? [
        { label: '新建文件', run: () => createAt(node, 'file') },
        { label: '新建文件夹', run: () => createAt(node, 'folder') },
        { label: '重命名', separatorBefore: true, run: () => renameNode(node) },
        { label: '删除', danger: true, run: () => deleteNode(node) },
      ]
    : [
        { label: node.writable ? '打开' : '只读打开', run: () => openNode(node) },
        { label: '重命名', separatorBefore: true, run: () => renameNode(node) },
        { label: '删除', danger: true, run: () => deleteNode(node) },
      ]
  ctxMenu.value = { x: ev.clientX, y: ev.clientY, items }
}

function openRootMenu(ev: MouseEvent) {
  ctxMenu.value = {
    x: ev.clientX,
    y: ev.clientY,
    items: [
      { label: '新建文件', run: () => createAt(null, 'file') },
      { label: '新建文件夹', run: () => createAt(null, 'folder') },
    ],
  }
}

function closeContextMenu() {
  ctxMenu.value = null
}

export function useWorkbench() {
  return {
    // state
    tree, treeLoading, treeError, gitActive,
    tabs, activeId, activeTab, selectedPath,
    dialog, ctxMenu,
    // tree
    loadTree, openNode, openPath,
    // P1 符号地图 / 行跳转
    jumpToLine, symbolMapOpen, openSymbolMap, closeSymbolMap,
    relationGraphOpen, openRelationGraph, closeRelationGraph,
    // tabs
    activateTab, closeTab, saveTab, saveActive, registerContentGetter,
    // fs ops
    createAt, renameNode, deleteNode,
    // menus / dialogs
    openNodeMenu, openRootMenu, closeContextMenu, resolveDialog,
  }
}
