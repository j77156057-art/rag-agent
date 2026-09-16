// 工作台共享状态（模块级单例）：文件树、多标签编辑、保存/冲突、新建/改名/删除流程、
// 对话框与右键菜单。组件只负责渲染与转发事件。
import { computed, ref, shallowRef } from 'vue'
import { EditorView } from '@codemirror/view'
import { aiApi, fsApi, regionsApi, semanticApi, FsApiError } from '../api'
import type {
  TreeNode, TreeResp, GitCommit, RegionInfo, ContractsResp,
  SemanticTagRecord, LocateResp, RegionCard,
} from '../api'

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

/** 点击 AI 答案引用时，文件树展开祖先目录并闪烁目标行（nonce 保证同一路径重复触发也有动画）。 */
const treeReveal = ref<{ path: string; nonce: number } | null>(null)

function nodeExists(path: string): boolean {
  return !!(tree.value && findNode(tree.value.nodes, path))
}

/** AI 答案引用：选中目标并请求文件树展开/闪烁（节点可能尚不存在，由树侧自行判空）。 */
function revealPath(path: string) {
  selectedPath.value = path
  treeReveal.value = { path, nonce: Date.now() }
}

// ================================================================ 阶段 1：语义标签 + 大白话定位
/** 路径 → 业务标签记录（供文件树徽章与定位结果展示）。 */
const tagMap = shallowRef<Record<string, SemanticTagRecord>>({})
const tagLoading = ref(false)
const tagError = ref('')
const tagMeta = ref({ total: 0, tagged: 0, pending: 0, stale: 0 })

/** 定位态：locatePaths 中的文件在树里持续高亮，直到清空查询。 */
const locateQuery = ref('')
const locateLoading = ref(false)
const locatePaths = ref<Set<string>>(new Set())
const locateResult = shallowRef<LocateResp | null>(null)

async function loadTags() {
  try {
    const r = await semanticApi.tags()
    if (r.error) {
      tagError.value = r.error
      return
    }
    tagMap.value = r.files || {}
    tagMeta.value = { total: r.total_files, tagged: r.tagged_files, pending: r.pending, stale: r.stale }
    tagError.value = ''
  } catch {
    /* 标签是增强层：加载失败不打扰主流程 */
  }
}

/** 调模型增量打标签；剩余文件可再次点击直到 pending=0。 */
async function refreshTags(limit = 60) {
  tagLoading.value = true
  tagError.value = ''
  try {
    const r = await semanticApi.refreshTags(limit)
    if (r.error) { tagError.value = r.error; return r }
    tagMap.value = r.files || {}
    tagMeta.value = { total: r.total_files, tagged: r.tagged_files, pending: r.pending, stale: r.stale }
    return r
  } finally {
    tagLoading.value = false
  }
}

async function runLocate(q: string) {
  const query = q.trim()
  locateQuery.value = query
  if (!query) {
    clearLocate()
    return
  }
  locateLoading.value = true
  try {
    const r = await semanticApi.locate(query)
    locateResult.value = r
    locatePaths.value = new Set((r.files || []).map((f) => f.path))
  } finally {
    locateLoading.value = false
  }
}

function clearLocate() {
  locateQuery.value = ''
  locateResult.value = null
  locatePaths.value = new Set()
}

/** 命中某文件：打开并跳到符号所在行，同时让文件树展开/闪烁，定位高亮保留。 */
async function openLocateFile(hit: { path: string; line: number | null }) {
  revealPath(hit.path)
  await jumpToLine(hit.path, hit.line || 1)
}

/** 命中某分区：展开并闪烁该分区文件夹。 */
function openLocateRegion(dir: string) {
  revealPath(dir.replace(/\\/g, '/').replace(/\/+$/, ''))
}

// ================================================================ 分区卡片（概览驾驶舱共享）
const regionCards = shallowRef<RegionCard[]>([])
const regionCardsLoading = ref(false)

/** 概览页分区卡：统计文件数/未提交/最近提交；增强层，失败静默。 */
async function loadRegionCards(force = false) {
  if (!force && regionCardsLoading.value) return
  if (!tree.value?.regions_enabled) {
    regionCards.value = []
    return
  }
  regionCardsLoading.value = true
  try {
    const r = await semanticApi.regionCards()
    regionCards.value = r.regions || []
  } catch {
    /* 分区卡是导航增强层：读取失败保留旧数据，不弹错打扰 */
  } finally {
    regionCardsLoading.value = false
  }
}

/** 离线演示态由 App 注入示例卡片（composable 不反向依赖 demo 模块）。 */
function seedDemoRegionCards(cards: RegionCard[]) {
  regionCards.value = cards
}

// ================================================================ 工作区视图
// Godot 式工作区切换：概览驾驶舱 / 代码编辑 / 素材中心。画布、运行为后续阶段预留标签。
export type WorkspaceView = 'overview' | 'code' | 'assets'
const workspace = ref<WorkspaceView>('overview')

function setWorkspace(v: WorkspaceView) {
  // 没有打开的文件时，代码工作区无内容可看，忽略切换
  if (v === 'code' && !tabs.value.length) return
  workspace.value = v
}

// ---------------------------------------------------------------- 画布 / 运行：顶层 tab 联动 SceneRuntimePanel
// 顶层「画布 / 运行」是真实功能入口（场景画布 / 运行游戏），点击打开弹窗并切到对应 tab。
export type RuntimePanelTab = 'play' | 'scene' | 'timeline'
const runtimeOpen = ref(false)
const runtimeTab = ref<RuntimePanelTab>('play')

function openRuntime(t: RuntimePanelTab) {
  runtimeTab.value = t
  runtimeOpen.value = true
}
function closeRuntime() {
  runtimeOpen.value = false
}

// ---------------------------------------------------------------- 最近打开的文件
interface RecentFile { root: string; path: string; name: string }
const RECENT_KEY = 'docmind:recent-files'
const RECENT_MAX = 12

function readRecent(): RecentFile[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(v) ? v as RecentFile[] : []
  } catch {
    return []
  }
}

/** 只列当前项目根下的最近文件，切换项目不串味。 */
const recentFiles = computed<RecentFile[]>(() => {
  const root = tree.value?.code_root
  if (!root) return []
  return readRecent().filter((r) => r.root === root)
})

function pushRecent(path: string) {
  const root = tree.value?.code_root
  if (!root) return
  const name = path.split('/').pop() || path
  const list = readRecent().filter((r) => !(r.root === root && r.path === path))
  list.unshift({ root, path, name })
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* 隐私模式等场景写入失败可忽略 */
  }
}

async function openRecent(path: string) {
  await openPath(path)
}

/** tab.id -> 取当前编辑器文本（由 CodeView 注册，保存时取最新内容） */
const contentGetters = new Map<number, () => string>()

/**
 * tab.id -> 整文档替换器（由 CodeView 注册）。
 * P3 git 回滚/历史恢复后，磁盘内容在编辑器之外被改写：活动标签走 view.dispatch，
 * 非活动标签要直接替换其留存的 EditorState，否则切回去还是旧文本。
 */
const docReplacers = new Map<number, (content: string) => void>()

export function registerDocReplacer(id: number, fn: (content: string) => void) {
  docReplacers.set(id, fn)
}

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
    void loadTags()  // 业务标签是增强层，静默加载（失败不弹错）
    void loadRegionCards()  // 概览驾驶舱分区卡，同样静默
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
  pushRecent(path)
  workspace.value = 'code'  // 打开文件即进入代码工作区
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

const unityGraphOpen = ref(false)

function openUnityGraph() {
  unityGraphOpen.value = true
}
function closeUnityGraph() {
  unityGraphOpen.value = false
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
  workspace.value = 'code'  // 点编辑器标签即回到代码工作区
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
  docReplacers.delete(id)
  const next = tabs.value.filter((t) => t.id !== id)
  tabs.value = next
  if (activeId.value === id) {
    const neighbor = next[Math.min(idx, next.length - 1)] ?? null
    activeId.value = neighbor ? neighbor.id : null
    selectedPath.value = neighbor ? neighbor.path : null
    if (!neighbor) workspace.value = 'overview'  // 最后一个标签关闭 → 回到概览
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
    const taskId = window.localStorage.getItem('docmind.activeTaskId') || ''
    const resp = await fsApi.save(tab.path, content, overwrite ? null : tab.mtime, true, taskId)
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
        {
          label: '放弃未提交修改',
          separatorBefore: true,
          disabled: !(node.tracked === true && node.dirty === true),
          run: () => revertPath(node.path, node.name),
        },
        {
          label: '历史版本…',
          disabled: node.tracked !== true,
          run: () => openHistory(node.path, node.name),
        },
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

// ================================================================ P3：git 回滚 + 历史版本
/** git 改写磁盘后，把最新内容同步回可能已打开的标签（活动/非活动标签都要换）。 */
async function resyncTabAfterGit(path: string, mtime: number): Promise<void> {
  const tab = tabs.value.find((t) => t.path === path)
  if (!tab) return
  const f = await fsApi.read(path)
  tab.mtime = mtime || f.mtime
  tab.savedContent = f.content
  tab.tracked = f.tracked
  tab.gitDirty = f.dirty
  tab.dirty = false
  tab.savedAt = Date.now()
  tab.reindexWarn = null
  docReplacers.get(tab.id)?.(f.content)
}

/** P3：放弃单个文件的全部未提交改动（含暂存与编辑器未保存内容），恢复到 HEAD。 */
async function revertPath(path: string, name?: string): Promise<void> {
  const label = name ?? path.split('/').pop() ?? path
  const tab = tabs.value.find((t) => t.path === path)
  const ok = await askConfirm({
    title: `放弃「${label}」的未提交修改？`,
    message: '文件将恢复到上次提交（HEAD）时的内容。',
    detail: [
      tab?.dirty ? '· 编辑器里尚未保存的改动也会一并丢弃。' : '',
      '· 已暂存（git add）的改动同样撤销。',
      '· 只影响这一个文件，提交历史不受影响。',
    ].filter(Boolean).join('\n'),
    confirmText: '回滚',
    danger: true,
  })
  if (!ok) return
  try {
    const r = await fsApi.revert(path)
    if (!r.reverted) {
      await askAlert({ title: '无需回滚', message: '该文件没有未提交改动，内容与上次提交一致。' })
      return
    }
    await resyncTabAfterGit(path, r.mtime)
    await loadTree(path)
  } catch (e) {
    const err = e as FsApiError
    await askAlert({
      title: '回滚失败',
      message: err.message,
      detail: err.status === 403
        ? '契约文件/受保护文件禁止在工作台回滚。'
        : err.status ? `HTTP ${err.status}` : undefined,
    })
  }
}

export interface HistoryState {
  path: string
  name: string
  loading: boolean
  error: string | null
  commits: GitCommit[]
  selected: GitCommit | null
  previewLoading: boolean
  previewError: string | null
  preview: string
  restoring: boolean
}
const history = ref<HistoryState | null>(null)

async function openHistory(path: string, name?: string): Promise<void> {
  history.value = {
    path, name: name ?? path.split('/').pop() ?? path,
    loading: true, error: null, commits: [],
    selected: null, previewLoading: false, previewError: null, preview: '', restoring: false,
  }
  try {
    const r = await fsApi.gitlog(path, 50)
    if (!history.value || history.value.path !== path) return
    history.value.commits = r.commits
    history.value.loading = false
  } catch (e) {
    const err = e as FsApiError
    if (history.value) {
      history.value.loading = false
      history.value.error = err.message
    }
  }
}

function closeHistory(): void {
  history.value = null
}

async function selectHistoryVersion(c: GitCommit): Promise<void> {
  const h = history.value
  if (!h || h.restoring) return
  h.selected = c
  h.previewLoading = true
  h.previewError = null
  try {
    const r = await fsApi.gitShow(h.path, c.full_hash)
    // 异步往返期间用户可能点了别的提交/关了弹窗
    if (history.value !== h || h.selected?.full_hash !== c.full_hash) return
    h.preview = r.content
  } catch (e) {
    const err = e as FsApiError
    if (history.value === h && h.selected?.full_hash === c.full_hash) {
      h.previewError = err.message
    }
  } finally {
    if (history.value === h) h.previewLoading = false
  }
}

async function restoreSelectedVersion(): Promise<void> {
  const h = history.value
  if (!h || !h.selected || h.restoring) return
  const c = h.selected
  const ok = await askConfirm({
    title: '恢复为该历史版本？',
    message: `「${h.name}」将恢复为提交 ${c.hash} 时的内容。`,
    detail: '只改写工作区文件，不会改动提交历史；恢复后仍是未提交状态，不满意可再点「回滚」撤销。',
    confirmText: '恢复此版本',
    danger: true,
  })
  if (!ok) return
  h.restoring = true
  try {
    const r = await fsApi.restoreAt(h.path, c.full_hash)
    await resyncTabAfterGit(h.path, r.mtime)
    await loadTree(h.path)
    history.value = null
  } catch (e) {
    const err = e as FsApiError
    await askAlert({
      title: err.status === 422 ? '语法校验未通过，恢复已取消' : '恢复历史版本失败',
      message: err.message,
      detail: err.status ? `HTTP ${err.status}` : undefined,
    })
    h.restoring = false
  }
}

// ================================================================ P2：选区 AI
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
      const tab = activeTab.value
      let fileCtx = ''
      const getter = tab ? contentGetters.get(tab.id) : null
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
          task_id: window.localStorage.getItem('docmind.activeTaskId') || undefined,
          task_region: window.localStorage.getItem('docmind.activeTaskRegion') || undefined,
          allowed_paths: (window.localStorage.getItem('docmind.activeTaskAllowedPaths') || '').split(',').map((x) => x.trim()).filter(Boolean),
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
  const tab = activeTab.value
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

// ================================================================ P3：AI 改写字级 diff 预览
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

// ================================================================ P3：分区可视化
export interface RegionMapState {
  loading: boolean
  error: string | null
  codeRoot: string
  regions: RegionInfo[]
  /** 契约校验结果；独立请求，失败时为 null（不影响分区状态展示） */
  contracts: ContractsResp | null
  contractsError: string | null
}

const regionMapOpen = ref(false)
const regionMap = ref<RegionMapState>({
  loading: false,
  error: null,
  codeRoot: '',
  regions: [],
  contracts: null,
  contractsError: null,
})

/** 契约校验独立拉取（HTTP 200+{ok:false} 是正常业务结果），失败只置错误文案。 */
async function refreshContracts() {
  try {
    regionMap.value.contracts = await regionsApi.contracts()
    regionMap.value.contractsError = null
  } catch (e) {
    regionMap.value.contracts = null
    regionMap.value.contractsError = (e as FsApiError).message || '契约校验不可用。'
  }
}

async function openRegionMap() {
  regionMapOpen.value = true
  regionMap.value = {
    loading: true, error: null,
    codeRoot: regionMap.value.codeRoot,
    regions: regionMap.value.regions,
    contracts: regionMap.value.contracts,
    contractsError: regionMap.value.contractsError,
  }
  try {
    const r = await regionsApi.list()
    regionMap.value.codeRoot = r.code_root
    regionMap.value.regions = r.regions
    regionMap.value.error = null
  } catch (e) {
    regionMap.value.error = (e as FsApiError).message || '分区信息加载失败。'
  }
  // 契约校验单独失败不拖垮整块面板
  await refreshContracts()
  regionMap.value.loading = false
}

function closeRegionMap() {
  regionMapOpen.value = false
}

/** 点击分区卡片：在文件树中选中该分区目录（分区均为顶层目录，默认展开）并关闭面板。 */
function locateRegion(dir: string) {
  selectedPath.value = dir
  regionMapOpen.value = false
}

/** 正在执行分区写操作的 key（创建/补齐按钮 spinner），同一时刻只允许一个 */
const busyRegionKey = ref<string | null>(null)

/** missing 卡片一键创建：确认后建目录 + 导出桩/README，并就地刷新分区状态/契约/文件树。 */
async function createRegion(r: RegionInfo): Promise<boolean> {
  const detail = [
    r.exports.length
      ? `将生成导出接口桩：${r.exports.join('、')}`
      : '该分区无对外接口文件，仅创建目录与 README.md',
    '不会改动其它分区，也不会重写 regions.json。',
  ].join('\n')
  const confirmed = await askConfirm({
    title: `创建分区：${r.name}`,
    message: `将在代码库根目录下创建 ${r.dir}/`,
    detail,
    confirmText: '创建',
  })
  if (!confirmed) return false
  busyRegionKey.value = r.key
  try {
    const resp = await regionsApi.createRegion(r.key)
    regionMap.value.regions = resp.regions
    // 面板保持打开：卡片与 DAG 节点就地翻为「已存在」，契约横幅同步更新
    await Promise.all([refreshContracts(), loadTree()])
    if (resp.git_warning) {
      await askAlert({ title: `分区「${r.name}」已创建`, message: resp.git_warning })
    }
    return true
  } catch (e) {
    await askAlert({ title: '创建分区失败', message: (e as FsApiError).message || '未知错误。' })
    return false
  } finally {
    busyRegionKey.value = null
  }
}

/** 已存在卡片补齐缺失导出桩：确认后只生成缺失文件，就地刷新分区状态/契约/文件树。 */
async function fillRegionExports(r: RegionInfo): Promise<boolean> {
  const confirmed = await askConfirm({
    title: `补齐导出桩：${r.name}`,
    message: `将在 ${r.dir}/ 下生成缺失的导出接口文件`,
    detail: [`将生成：${r.missing_exports.join('、')}`, '仅补缺失文件，不改动其它内容，也不会自动提交。'].join('\n'),
    confirmText: '补齐',
  })
  if (!confirmed) return false
  busyRegionKey.value = r.key
  try {
    const resp = await regionsApi.fillExports(r.key)
    regionMap.value.regions = resp.regions
    await Promise.all([refreshContracts(), loadTree()])
    return true
  } catch (e) {
    await askAlert({ title: '补齐导出桩失败', message: (e as FsApiError).message || '未知错误。' })
    return false
  } finally {
    busyRegionKey.value = null
  }
}

export function useWorkbench() {
  return {
    // state
    tree, treeLoading, treeError, gitActive,
    tabs, activeId, activeTab, selectedPath,
    dialog, ctxMenu,
    // 工作区视图（概览驾驶舱 / 代码）+ 分区卡 + 最近文件
    workspace, setWorkspace,
    regionCards, regionCardsLoading, loadRegionCards, seedDemoRegionCards,
    recentFiles, openRecent,
    // tree
    loadTree, openNode, openPath,
    // AI 引用定位：文件树展开 + 闪烁
    treeReveal, nodeExists, revealPath,
    // P1 符号地图 / 行跳转
    jumpToLine, symbolMapOpen, openSymbolMap, closeSymbolMap,
    relationGraphOpen, openRelationGraph, closeRelationGraph,
    // P1-2 Unity GUID 引用图
    unityGraphOpen, openUnityGraph, closeUnityGraph,
    // tabs
    activateTab, closeTab, saveTab, saveActive, registerContentGetter, registerDocReplacer,
    // P3 git 回滚 / 历史版本
    revertPath, history, openHistory, closeHistory, selectHistoryVersion, restoreSelectedVersion,
    // fs ops
    createAt, renameNode, deleteNode,
    // menus / dialogs
    openNodeMenu, openRootMenu, closeContextMenu, resolveDialog,
    // P2 选区 AI
    activeSelection, setSelection,
    aiPanelOpen, openAiPanel, closeAiPanel, clearTurns,
    turns, aiStreaming, askComposing, startAskCompose,
    runAi, stopAi, applyRewrite, copyAnswer,
    // P3 改写 diff 预览
    rewriteDiff, openRewriteDiff, closeRewriteDiff, acceptRewriteDiff,
    // P3 分区可视化
    regionMapOpen, regionMap, openRegionMap, closeRegionMap, locateRegion,
    busyRegionKey, createRegion, fillRegionExports,
    // 阶段 1 语义标签 + 大白话定位
    tagMap, tagLoading, tagError, tagMeta,
    loadTags, refreshTags,
    locateQuery, locateLoading, locatePaths, locateResult,
    runLocate, clearLocate, openLocateFile, openLocateRegion,
    // 顶层「画布 / 运行」tab：联动 SceneRuntimePanel
    runtimeOpen, runtimeTab, openRuntime, closeRuntime,
  }
}
