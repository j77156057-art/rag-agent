<script setup lang="ts">
// P0-2 场景画布：把 Godot .tscn 画成可操作的节点图。
//
// 设计取舍（与原 spike 的「容器嵌套」方案不同，这里说明理由）：
//   * spike 验证的是 Vue Flow 的 group 容器能不能承载"分区+文件"。那个模型到了场景树上
//     会退化成"父节点=容器"，三层以上嵌套时尺寸传播 / extent 钳制 / fitView 都变得脆弱；
//   * 场景开发真正要看的是**空间关系**：position / transform 才是高频操作对象，容器嵌套
//     表达不了这个。所以这里改成 **扁平节点 + 三类边**，并提供两种布局：
//       「层级布局」= DFS 前序的水平树；「空间布局」= 直接按场景坐标落点（可拖拽回写位置）。
//   * 实例（instance）与脚本引用（ExtResource）另外画成"文件卡"，边用不同颜色区分，
//     双击文件卡即在编辑器里打开该文件——场景图和代码因此连成一条线。
import { computed, onMounted, ref, watch } from 'vue'
import {
  VueFlow, applyNodeChanges, applyEdgeChanges, MarkerType,
} from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import type { Node, Edge, NodeChange, EdgeChange } from '@vue-flow/core'
// Vue Flow 的样式不带在包里，必须显式引（漏了不会报错，只会让 .vue-flow__node
// 退化成 position:static —— 画布看起来"有东西"但节点其实堆成一列）。
// 不引 node-resizer（本项目没用到它的缩放功能）。
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { sceneApi } from '../api'
import type { SceneFile, SceneGraph, SceneNode, SceneUndo } from '../api'
import SceneNodeCard from './SceneNodeCard.vue'
import SceneFileCard from './SceneFileCard.vue'

const props = defineProps<{ path: string }>()
const emit = defineEmits<{ (e: 'open-file', rel: string): void }>()

const nodeTypes = { sceneNode: SceneNodeCard, sceneFile: SceneFileCard }
const COLORS = { '2d': '#58a6ff', '3d': '#bc8cff', root: '#e3a83a', instance: '#2ec4b6' }
const EDGE = {
  hierarchy: '#4a5563',
  script: '#d2a8ff',
  instance: '#2ec4b6',
}

const graph = ref<SceneGraph | null>(null)
const loading = ref(false)
const message = ref('')
const messageKind = ref<'info' | 'ok' | 'err'>('info')
const busy = ref(false)

const nodes = ref<Node[]>([])
const edges = ref<Edge[]>([])
const selectedId = ref<string | null>(null)
const selectedIds = ref<string[]>([])
const layoutMode = ref<'hierarchy' | 'space'>('hierarchy')
const showScripts = ref(true)
const showInstances = ref(true)
// 资源引用（贴图 / 材质 / 字体…）默认不画：真实工程里动辄上百个 ext_resource，
// 全铺到画布上会把场景层级淹没。需要排查"这张图被谁引用"时再打开。
const showResources = ref(false)

const undoStack = ref<SceneUndo[]>([])
const redoStack = ref<SceneUndo[]>([])
const spaceScale = ref(1)
/** 刚被本次操作改动过的节点 id -> 高亮态，用于给用户"改到哪了"的即时反馈 */
const flash = ref<Record<string, string>>({})

const scene = computed(() => graph.value)
const nodeById = computed(() => new Map((graph.value?.nodes ?? []).map(n => [n.id, n])))
const selected = computed(() => (selectedId.value ? nodeById.value.get(selectedId.value) ?? null : null))
const canEdit = computed(() => !!graph.value?.guard.writable)

function say(text: string, kind: 'info' | 'ok' | 'err' = 'info') {
  message.value = text
  messageKind.value = kind
}

const ROW = 76
const COL = 296
const FILE_COL = 330

/* ------------------------------------------------------------------ 布局 */
function buildOrder(list: SceneNode[]): { node: SceneNode; depth: number }[] {
  const byParent = new Map<string, SceneNode[]>()
  const ids = new Set(list.map(n => n.id))
  for (const node of list) {
    const key = node.parent && ids.has(node.parent) ? node.parent : '__orphan__'
    if (!byParent.has(key)) byParent.set(key, [])
    byParent.get(key)!.push(node)
  }
  const out: { node: SceneNode; depth: number }[] = []
  const seen = new Set<string>()
  const walk = (id: string, depth: number) => {
    const node = id === '__orphan__' ? null : list.find(n => n.id === id)
    if (node) {
      if (seen.has(node.id)) return
      seen.add(node.id)
      out.push({ node, depth })
    }
    for (const child of byParent.get(id) ?? []) walk(child.id, node ? depth + 1 : depth)
  }
  walk(graph.value?.root_id ?? '.', 0)
  walk('__orphan__', 1)
  for (const node of list) if (!seen.has(node.id)) out.push({ node, depth: 1 })
  return out
}

function layout(): { nodes: Node[]; edges: Edge[]; maxX: number; minY: number } {
  const g = graph.value
  if (!g) return { nodes: [], edges: [], maxX: 0, minY: 0 }
  const ordered = buildOrder(g.nodes)

  // 空间布局：先按场景坐标算包围盒，过大就整体缩放，保证落点仍在可视范围
  let scale = 1
  if (layoutMode.value === 'space') {
    const pts = g.nodes.filter(n => n.position)
    const span = pts.reduce((acc, n) => Math.max(acc, Math.abs(n.position![0]), Math.abs(n.position![1])), 0)
    scale = span > 2400 ? 2400 / span : 1
  }
  spaceScale.value = scale

  const placed = new Map<string, { x: number; y: number }>()
  const overflow = new Map<string, number>()
  const out: Node[] = []
  let row = 0
  for (const { node, depth } of ordered) {
    let pos: { x: number; y: number }
    if (layoutMode.value === 'hierarchy') {
      pos = { x: depth * COL, y: row * ROW }
      row += 1
    } else if (node.position) {
      pos = { x: node.position[0] * scale, y: node.position[1] * scale }
    } else {
      const parent = node.parent ? placed.get(node.parent) : undefined
      const count = (overflow.get(node.parent ?? '') ?? 0) + 1
      overflow.set(node.parent ?? '', count)
      pos = parent ? { x: parent.x, y: parent.y + count * 66 } : { x: 0, y: (row + count) * ROW }
    }
    placed.set(node.id, pos)
    const isRoot = node.id === g.root_id
    const color = isRoot ? COLORS.root : node.instance_id ? COLORS.instance : COLORS[node.space]
    out.push({
      id: node.id,
      type: 'sceneNode',
      position: pos,
      draggable: layoutMode.value === 'space' && !isRoot,
      data: { node, color, flash: flash.value[node.id] ?? '' },
    })
  }

  // 文件卡单独排一列，避免和场景节点抢空间
  const maxX = ordered.reduce((acc, { depth }) => Math.max(acc, depth * COL), 0)
  const visibleFiles = g.files.filter(f => !f.orphan && (
    (f.kind === 'script' && showScripts.value)
    || (f.kind === 'scene' && showInstances.value)
    || (f.kind === 'resource' && showResources.value)))
  visibleFiles.forEach((file, index) => {
    out.push({
      id: file.id,
      type: 'sceneFile',
      position: layoutMode.value === 'space'
        ? { x: (placed.size ? Math.max(...[...placed.values()].map(p => p.x)) : 0) + FILE_COL, y: index * 88 }
        : { x: maxX + FILE_COL, y: index * 88 },
      draggable: true,
      data: { file, color: file.kind === 'instance' ? EDGE.instance : file.kind === 'script' ? EDGE.script : EDGE.hierarchy },
    })
  })

  const fileIds = new Set(visibleFiles.map(f => f.id))
  const edgeList: Edge[] = []
  for (const e of g.edges) {
    if (e.kind !== 'hierarchy' && !fileIds.has(e.target)) continue
    const color = EDGE[e.kind]
    edgeList.push({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.kind === 'hierarchy' ? 'children' : 'ref',
      targetHandle: e.kind === 'hierarchy' ? 'parent' : 'in',
      type: 'smoothstep',
      animated: e.kind !== 'hierarchy',
      style: {
        stroke: color,
        strokeWidth: e.kind === 'hierarchy' ? 1.4 : 1.2,
        strokeDasharray: e.kind === 'hierarchy' ? undefined : e.kind === 'script' ? '5 4' : '2 3',
      },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color },
    })
  }
  return { nodes: out, edges: edgeList, maxX, minY: 0 }
}

function rebuild() {
  const { nodes: list, edges: list2 } = layout()
  nodes.value = list
  edges.value = list2
  if (selectedId.value && !nodeById.value.has(selectedId.value)) selectedId.value = null
}

function onNodesChange(changes: NodeChange[]) {
  nodes.value = applyNodeChanges(changes, nodes.value)
  if (changes.some(c => c.type === 'select' || c.type === 'remove')) {
    selectedIds.value = nodes.value.filter(n => n.selected).map(n => n.id)
    const last = selectedIds.value[selectedIds.value.length - 1]
    if (last) selectedId.value = last
  }
}
function onEdgesChange(changes: EdgeChange[]) {
  edges.value = applyEdgeChanges(changes, edges.value)
}
/**
 * Vue Flow v1 的事件载荷是**单个对象** `{ event, node, nodes, ... }`，不是 `(event, node)`。
 * 这一点不看源码很容易写错：写成 `(_e, n) => n.id` 会在点节点时抛
 * "Cannot read properties of undefined"，而且只在点击时才炸（浏览器冒烟抓到的）。
 * 这里两种形态都兜住，顺带对未来 minor 变更免疫。
 */
function pickNode(args: unknown[]): Node | undefined {
  const looksLikeNode = (value: unknown): value is Node =>
    !!value && typeof value === 'object' && 'id' in value && 'position' in value
  if (looksLikeNode(args[1])) return args[1]
  const first = args[0] as { node?: unknown } | null | undefined
  return looksLikeNode(first?.node) ? (first!.node as Node) : undefined
}

function onNodeClick(...args: unknown[]) {
  const node = pickNode(args)
  if (!node || node.type === 'sceneFile') return
  selectedId.value = node.id
}

/* ------------------------------------------------------------------ 加载 */
async function reload() {
  if (!props.path.trim()) return
  loading.value = true
  try {
    const g = await sceneApi.graph(props.path.trim())
    if (!g.ok) {
      graph.value = null
      nodes.value = []
      edges.value = []
      say(g.error || '加载场景失败', 'err')
      return
    }
    graph.value = g
    rebuild()
    const warn = g.structure_errors?.length
      ? `（结构异常 ${g.structure_errors.length} 项，编辑已锁定）`
      : ''
    say(`已加载 ${g.stats.nodes} 节点 / ${g.stats.files} 外部引用 / ${g.stats.edges} 边${warn}`,
      g.structure_errors?.length ? 'err' : 'ok')
  } catch (e) {
    graph.value = null
    say((e as Error).message, 'err')
  } finally {
    loading.value = false
  }
}

/* ------------------------------------------------------------------ 编辑 */
// undo 与 /api/scene/op 请求体同形，直接展开即可——不要在这里做字段映射，
// 一旦两边字段名漂移，撤销会静默变成空 payload。
async function runOp(payload: Record<string, unknown>, options: { remember?: boolean } = {}): Promise<boolean> {
  if (busy.value) return false
  const g = graph.value
  if (!g) return false
  if (!canEdit.value) {
    say(`该场景不可写入：${g.guard.reason || '只读'}`, 'err')
    return false
  }
  busy.value = true
  try {
    const r = await sceneApi.op({ path: g.path, if_mtime: g.stats.mtime, ...payload })
    if (!r.ok) {
      if (r.stale) {
        say('场景已被外部修改（Godot 编辑器保存过？），请点「重新加载」后再操作。', 'err')
      } else if (r.rolled_back) {
        say('编辑会破坏场景结构，已自动回滚：' + (r.error ?? ''), 'err')
      } else {
        say(r.error || '操作失败', 'err')
      }
      return false
    }
    if (options.remember !== false && r.undo) {
      undoStack.value.push(r.undo)
      redoStack.value = []
      if (undoStack.value.length > 100) undoStack.value.shift()
    }
    if (r.node) {
      flash.value = { ...flash.value, [r.node]: 'ok' }
      window.setTimeout(() => { const next = { ...flash.value }; delete next[r.node!]; flash.value = next; rebuild() }, 900)
    }
    for (const w of r.warnings ?? []) say(w, 'ok')
    await reload()
    if (r.node && nodeById.value.has(r.node)) selectedId.value = r.node
    return true
  } catch (e) {
    say((e as Error).message, 'err')
    return false
  } finally {
    busy.value = false
  }
}

async function undo() {
  const inv = undoStack.value.pop()
  if (!inv) return say('没有可撤销的操作', 'info')
  const g = graph.value
  if (!g) return
  const r = await sceneApi.op({ path: g.path, if_mtime: g.stats.mtime, ...inv })
  if (!r.ok) {
    undoStack.value.push(inv)
    say('撤销失败：' + (r.error ?? ''), 'err')
    return
  }
  if (r.undo) redoStack.value.push(r.undo)
  await reload()
  say('已撤销', 'ok')
}

async function redo() {
  const inv = redoStack.value.pop()
  if (!inv) return say('没有可重做的操作', 'info')
  const g = graph.value
  if (!g) return
  const r = await sceneApi.op({ path: g.path, if_mtime: g.stats.mtime, ...inv })
  if (!r.ok) {
    redoStack.value.push(inv)
    say('重做失败：' + (r.error ?? ''), 'err')
    return
  }
  if (r.undo) undoStack.value.push(r.undo)
  await reload()
  say('已重做', 'ok')
}

/* 拖拽落点 -> 写回 position（只在空间布局下生效，且排除根节点） */
async function onNodeDragStop(...args: unknown[]) {
  const node = pickNode(args)
  if (layoutMode.value !== 'space' || !node || node.type !== 'sceneNode') return
  const target = nodeById.value.get(node.id)
  if (!target || target.id === graph.value?.root_id) {
    rebuild()
    return
  }
  if (target.position_from === 'transform') {
    say(`${target.name} 用 transform 定位，画布拖拽不改写；请在右侧检查器编辑。`, 'info')
    rebuild()
    return
  }
  const scale = spaceScale.value || 1
  const moved = [Math.round(node.position.x / scale), Math.round(node.position.y / scale)]
  const current = target.position ?? [0, 0]
  if (Math.abs(moved[0] - current[0]) < 1 && Math.abs(moved[1] - current[1]) < 1) return
  if (!canEdit.value) return
  await runOp({ op: 'move', node: node.id, position: moved })
  say(`已写回 ${target.name} position = Vector2(${moved[0]}, ${moved[1]})`, 'ok')
}

/* ------------------------------------------------------------------ 检查器动作 */
const draft = ref({ name: '', type: 'Node2D', prop: '', value: '' })
const reparentTo = ref('')

watch(selectedId, () => {
  draft.value.name = selected.value?.name ?? ''
  draft.value.type = selected.value?.space === '3d' ? 'Node3D' : 'Node2D'
  reparentTo.value = ''
})

async function openFile(file: SceneFile) {
  if (file.resolved && file.rel) emit('open-file', file.rel)
}

const addDraft = ref({ name: '', type: 'Node2D' })
async function addChildNew() {
  const node = selected.value
  if (!node) return
  const type = addDraft.value.type.trim()
  const name = addDraft.value.name.trim() || type
  if (!type) return say('请填写节点类型', 'err')
  if (await runOp({ op: 'add', parent: node.id, type, name })) say(`已新增 ${name}`, 'ok')
  addDraft.value.name = ''
}
async function dupNode() {
  const node = selected.value
  if (node) await runOp({ op: 'duplicate', node: node.id })
}
async function delNode() {
  const node = selected.value
  if (!node) return
  if (node.id === graph.value?.root_id) return say('根节点不能删除', 'err')
  if (!window.confirm(`删除节点 ${node.id}（含其全部子孙）？可用「撤销」恢复。`)) return
  await runOp({ op: 'delete', node: node.id })
}
async function renameNode() {
  const node = selected.value
  const name = draft.value.name.trim()
  if (!node || !name || name === node.name) return
  await runOp({ op: 'rename', node: node.id, name })
  draft.value.name = name
}
async function reparentNode() {
  const node = selected.value
  if (!node || !reparentTo.value) return
  await runOp({ op: 'reparent', node: node.id, parent: reparentTo.value })
  reparentTo.value = ''
}
async function setProperty(property: string, value: string) {
  const node = selected.value
  if (!node) return
  await runOp({ op: 'set_props', node: node.id, properties: { [property]: value } })
}
async function removeProperty(property: string) {
  const node = selected.value
  if (!node) return
  await runOp({ op: 'set_props', node: node.id, remove: [property] })
}
async function addProperty() {
  const key = draft.value.prop.trim()
  if (!key) return
  if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return say('属性名必须是合法标识符', 'err')
  await runOp({ op: 'set_props', node: selected.value!.id, properties: { [key]: draft.value.value || 'null' } })
  draft.value.prop = ''
  draft.value.value = ''
}
async function deleteSelected() {
  const ids = selectedIds.value.filter(id => id !== graph.value?.root_id && nodeById.value.has(id))
  if (!ids.length) return say('先用 Shift 框选/点选要删除的节点', 'info')
  if (!window.confirm(`删除选中的 ${ids.length} 个节点（含子孙）？可用「撤销」恢复。`)) return
  for (const id of ids) await runOp({ op: 'delete', node: id })
}

/* 换父候选：排除自身与子孙，避免造成循环 */
const parentCandidates = computed(() => {
  const node = selected.value
  if (!node) return []
  return (graph.value?.nodes ?? [])
    .filter(n => n.id !== node.id && !n.id.startsWith(node.id === '.' ? '\u0000' : node.id + '/'))
    .map(n => ({ id: n.id, label: n.id === '.' ? `${n.name}（根）` : n.id }))
})

function onKeydown(ev: KeyboardEvent) {
  const tag = (ev.target as HTMLElement)?.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
  const mod = ev.ctrlKey || ev.metaKey
  if (mod && ev.key.toLowerCase() === 'z' && !ev.shiftKey) { ev.preventDefault(); void undo() }
  else if (mod && (ev.key.toLowerCase() === 'y' || (ev.shiftKey && ev.key.toLowerCase() === 'z'))) { ev.preventDefault(); void redo() }
  else if (ev.key === 'Delete') { ev.preventDefault(); void deleteSelected() }
}

function miniColor(n: Node) {
  const file = (n.data as { file?: SceneFile })?.file
  if (file) return EDGE.script
  return (n.data as { color?: string })?.color ?? '#3a424d'
}

watch(layoutMode, rebuild)
watch([showScripts, showInstances, showResources], rebuild)
watch(() => props.path, () => { undoStack.value = []; redoStack.value = []; void reload() })
onMounted(reload)

// 暴露给自动化探针（与 spike 一样留一个窄门面，避免探针依赖 Vue Flow store 形状）
function onPaneReady(instance: { findNode: (id: string) => Node | undefined; nodes: Node[] }) {
  ;(window as unknown as { __sceneCanvas: unknown }).__sceneCanvas = {
    findNode: (id: string) => instance.findNode(id),
    nodeList: () => instance.nodes,
    graph: () => graph.value,
    select: (id: string) => { selectedId.value = id },
  }
}

defineExpose({ reload, undo, redo, addChildNew })
</script>

<template>
  <div class="sc-wrap" tabindex="0" @keydown="onKeydown">
    <div class="sc-bar">
      <input
        class="sc-path"
        :value="path"
        readonly
        :title="path"
      />
      <button class="sc-btn" :disabled="loading" @click="reload">{{ loading ? '加载中…' : '重新加载' }}</button>
      <span class="sc-sep" />
      <div class="sc-seg">
        <button :class="{ on: layoutMode === 'hierarchy' }" @click="layoutMode = 'hierarchy'">层级布局</button>
        <button :class="{ on: layoutMode === 'space' }" @click="layoutMode = 'space'">空间布局</button>
      </div>
      <label class="sc-check"><input v-model="showScripts" type="checkbox" />脚本边</label>
      <label class="sc-check"><input v-model="showInstances" type="checkbox" />实例化边</label>
      <label class="sc-check"><input v-model="showResources" type="checkbox" />资源引用</label>
      <span class="sc-sep" />
      <button class="sc-btn" :disabled="!undoStack.length || busy" title="Ctrl+Z" @click="undo">撤销 {{ undoStack.length || '' }}</button>
      <button class="sc-btn" :disabled="!redoStack.length || busy" title="Ctrl+Y" @click="redo">重做 {{ redoStack.length || '' }}</button>
      <button class="sc-btn" :disabled="busy" @click="deleteSelected">删除所选</button>
      <span class="sc-spacer" />
      <span v-if="scene" class="sc-guard" :class="{ ro: !canEdit }">
        <template v-if="!canEdit">🔒 {{ scene.guard.reason || '只读' }}</template>
        <template v-else>
          {{ scene.guard.region_name || '未分区' }}
          <i v-if="scene.guard.tracked" :class="{ dirty: scene.guard.dirty }" :title="scene.guard.dirty ? '有未提交改动' : '已纳入 git'">{{ scene.guard.dirty ? '● 已改动' : '● 已跟踪' }}</i>
          <i v-else class="untracked" title="未纳入 git，误删不可恢复">未跟踪</i>
        </template>
      </span>
    </div>

    <div v-if="message" class="sc-msg" :class="messageKind">{{ message }}</div>

    <div v-if="!scene" class="sc-empty">
      <p>没有可显示的场景。</p>
      <small>在左侧文件树里点开一个 .tscn（例如 scenes/Main.tscn），或在上方填入场景路径后重新加载。</small>
    </div>

    <div v-else class="sc-body">
      <div class="sc-canvas">
        <VueFlow
          :nodes="nodes"
          :edges="edges"
          :node-types="nodeTypes"
          :nodes-draggable="layoutMode === 'space'"
          :min-zoom="0.2"
          :max-zoom="2"
          :snap-to-grid="true"
          :snap-grid="[8, 8]"
          :default-viewport="{ zoom: 0.9 }"
          fit-view-on-init
          @nodes-change="onNodesChange"
          @edges-change="onEdgesChange"
          @node-click="onNodeClick"
          @node-drag-stop="onNodeDragStop"
          @pane-ready="onPaneReady"
        >
          <Background :gap="22" :size="1.4" color="#1c2530" />
          <Controls />
          <MiniMap pannable zoomable :node-color="miniColor" />
        </VueFlow>
        <div v-if="layoutMode === 'space'" class="sc-hint">
          空间布局：拖动节点即写回 <code>position</code>（transform 定位的节点不会被拖动改写）
        </div>
        <div v-else class="sc-hint">
          层级布局：上→下为父子关系；虚线是脚本引用，点线是实例化引用；双击文件卡打开文件
        </div>
      </div>

      <aside class="sc-side">
        <template v-if="selected">
          <div class="sc-side-head">
            <b>{{ selected.name }}</b>
            <span class="sc-side-path">{{ selected.id }}</span>
          </div>
          <div class="sc-side-meta">
            <span>{{ selected.type }}</span>
            <span v-if="selected.overridden" class="sc-tag warn">实例子树覆写</span>
            <span v-if="!canEdit" class="sc-tag">只读</span>
          </div>

          <section class="sc-sec">
            <h4>重命名</h4>
            <div class="sc-row">
              <input v-model="draft.name" :disabled="selected.id === scene.root_id" @keyup.enter="renameNode" />
              <button class="sc-btn" :disabled="!canEdit || selected.id === scene.root_id" @click="renameNode">应用</button>
            </div>
          </section>

          <section class="sc-sec">
            <h4>新增子节点</h4>
            <div class="sc-row">
              <input v-model="addDraft.type" class="sc-w2" placeholder="类型 Node2D" />
              <input v-model="addDraft.name" class="sc-w2" placeholder="名称（留空用类型）" @keyup.enter="addChildNew" />
              <button class="sc-btn" :disabled="!canEdit" @click="addChildNew">＋</button>
            </div>
          </section>

          <section class="sc-sec">
            <h4>换父节点</h4>
            <div class="sc-row">
              <select v-model="reparentTo" :disabled="selected.id === scene.root_id">
                <option value="">选择新父节点…</option>
                <option v-for="c in parentCandidates" :key="c.id" :value="c.id">{{ c.label }}</option>
              </select>
              <button class="sc-btn" :disabled="!canEdit || !reparentTo" @click="reparentNode">移动</button>
            </div>
          </section>

          <section class="sc-sec">
            <h4>操作</h4>
            <div class="sc-row">
              <button class="sc-btn" :disabled="!canEdit || selected.id === scene.root_id" @click="dupNode">复制子树</button>
              <button class="sc-btn danger" :disabled="!canEdit || selected.id === scene.root_id" @click="delNode">删除</button>
            </div>
          </section>

          <section class="sc-sec grow">
            <h4>属性 <small>{{ selected.properties.length }}</small></h4>
            <div class="sc-props">
              <div v-for="p in selected.properties" :key="p.name" class="sc-prop">
                <span class="sc-prop-name" :title="`L${p.line}`">{{ p.name }}</span>
                <input
                  :value="p.value"
                  :class="`k-${p.kind}`"
                  :disabled="!canEdit"
                  @change="setProperty(p.name, ($event.target as HTMLInputElement).value)"
                />
                <button class="sc-x" :disabled="!canEdit" title="删除该属性" @click="removeProperty(p.name)">×</button>
              </div>
            </div>
            <div class="sc-row">
              <input v-model="draft.prop" class="sc-w2" placeholder="新属性名" />
              <input v-model="draft.value" class="sc-w2" placeholder="值" @keyup.enter="addProperty" />
              <button class="sc-btn" :disabled="!canEdit" @click="addProperty">＋</button>
            </div>
          </section>
        </template>

        <template v-else>
          <div class="sc-side-head"><b>场景概览</b></div>
          <div class="sc-stats">
            <div><span>节点</span><b>{{ scene.stats.nodes }}</b></div>
            <div><span>层级深度</span><b>{{ scene.stats.max_depth }}</b></div>
            <div><span>外部引用</span><b>{{ scene.stats.files }}<template v-if="scene.stats.orphans">（{{ scene.stats.orphans }} 个未引用）</template></b></div>
            <div><span>脚本 / 实例化</span><b>{{ scene.stats.scripts }} / {{ scene.stats.instances }}</b></div>
            <div><span>行数</span><b>{{ scene.stats.lines }}</b></div>
            <div><span>引擎空间</span><b>{{ scene.space === '3d' ? '3D' : '2D' }}</b></div>
            <div><span>目录</span><b>{{ scene.path }}</b></div>
          </div>
          <section v-if="scene.files.length" class="sc-sec">
            <h4>外部引用</h4>
            <div class="sc-props">
              <div v-for="f in scene.files" :key="f.id" class="sc-ref" :class="{ ghost: !f.resolved }" @dblclick="openFile(f)">
                <span class="sc-file-chip" :style="{ '--fc': f.kind === 'script' ? EDGE.script : f.kind === 'scene' ? EDGE.instance : EDGE.hierarchy }">{{ f.chip }}</span>
                <span class="sc-ref-name">{{ f.rel || f.raw }}</span>
                <small>{{ f.kind }} · {{ f.used }} 处{{ f.orphan ? ' · 未引用' : '' }}</small>
              </div>
            </div>
          </section>
          <section v-if="scene.structure_errors.length" class="sc-sec">
            <h4 class="err">结构异常（编辑已锁定）</h4>
            <ul class="sc-warn"><li v-for="(w, i) in scene.structure_errors" :key="i">{{ w }}</li></ul>
          </section>
          <section v-else-if="scene.warnings.length" class="sc-sec">
            <h4>解析提示</h4>
            <ul class="sc-warn"><li v-for="(w, i) in scene.warnings" :key="i">{{ w }}</li></ul>
          </section>
        </template>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.sc-wrap { display: flex; flex-direction: column; min-height: 0; flex: 1; outline: none; }
.sc-bar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; padding-bottom: 8px; }
.sc-path { flex: 1; min-width: 160px; max-width: 320px; background: var(--bg); border: 1px solid var(--border); color: var(--text-muted); padding: 5px 8px; border-radius: 5px; font-size: 11px; font-family: var(--font-mono); }
.sc-sep { width: 1px; height: 18px; background: var(--border); margin: 0 3px; }
.sc-spacer { flex: 1; }
.sc-btn { border: 1px solid var(--border-strong); background: transparent; color: var(--text-muted); border-radius: 5px; padding: 5px 9px; font-size: 11px; cursor: pointer; white-space: nowrap; }
.sc-btn:hover:not(:disabled) { color: var(--text); border-color: #315c86; }
.sc-btn:disabled { opacity: .38; cursor: default; }
.sc-btn.danger { border-color: #6a2f2f; color: #e8a0a0; }
.sc-seg { display: flex; border: 1px solid var(--border-strong); border-radius: 5px; overflow: hidden; }
.sc-seg button { background: transparent; border: 0; color: var(--text-muted); padding: 5px 10px; font-size: 11px; cursor: pointer; }
.sc-seg button.on { background: #17304b; color: #b9d8f5; }
.sc-check { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-muted); cursor: pointer; }
.sc-guard { font-size: 11px; color: var(--text-faint); }
.sc-guard i { font-style: normal; margin-left: 4px; color: var(--green); }
.sc-guard i.dirty { color: var(--amber); }
.sc-guard i.untracked { color: var(--text-faint); }
.sc-guard.ro { color: var(--amber); }
.sc-msg { font-size: 11px; border-radius: 5px; padding: 5px 9px; margin-bottom: 7px; border: 1px solid var(--border); color: var(--text-muted); }
.sc-msg.ok { color: var(--green); border-color: #244d33; background: #0f1a14; }
.sc-msg.err { color: #e8a0a0; border-color: #6a2f2f; background: #241414; }
.sc-empty { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--text-faint); border: 1px dashed var(--border); border-radius: 8px; }
.sc-empty small { max-width: 380px; text-align: center; line-height: 1.7; }
.sc-body { flex: 1; display: flex; min-height: 0; gap: 10px; }
.sc-canvas { flex: 1; min-width: 0; position: relative; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; background: #0b0f15; }
.sc-hint { position: absolute; left: 12px; bottom: 12px; background: #0f141ce8; border: 1px solid var(--border); border-radius: 7px; padding: 6px 10px; font-size: 11px; color: var(--text-muted); pointer-events: none; }
.sc-hint code { color: var(--accent); font-family: var(--font-mono); }
.sc-side { width: 306px; flex: 0 0 306px; border: 1px solid var(--border); border-radius: 8px; display: flex; flex-direction: column; min-height: 0; overflow: auto; padding: 9px 10px; gap: 8px; }
.sc-side-head { display: flex; align-items: baseline; gap: 7px; }
.sc-side-head b { font-size: 13px; }
.sc-side-path { font-size: 10px; color: var(--text-faint); font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-side-meta { display: flex; gap: 6px; flex-wrap: wrap; font-size: 10.5px; color: var(--text-muted); }
.sc-tag { border: 1px solid var(--border); border-radius: 3px; padding: 0 5px; }
.sc-tag.warn { color: var(--amber); border-color: #5a5030; }
.sc-sec { display: flex; flex-direction: column; gap: 5px; }
.sc-sec.grow { flex: 1; min-height: 0; }
.sc-sec h4 { margin: 0; font-size: 11px; font-weight: 600; color: var(--text-muted); letter-spacing: .3px; }
.sc-sec h4 small { color: var(--text-faint); font-weight: 400; }
.sc-sec h4.err { color: #e8a0a0; }
.sc-row { display: flex; gap: 5px; align-items: center; }
.sc-row input, .sc-row select { flex: 1; min-width: 0; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-size: 11px; padding: 4px 6px; border-radius: 4px; }
.sc-row input.sc-w2 { flex: 1 1 40%; }
.sc-props { display: flex; flex-direction: column; gap: 3px; max-height: 320px; overflow: auto; }
.sc-prop { display: flex; gap: 5px; align-items: center; }
.sc-prop-name { flex: 0 0 104px; font-size: 10.5px; color: var(--text-muted); font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-prop input { flex: 1; min-width: 0; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-size: 10.5px; padding: 3px 5px; border-radius: 4px; font-family: var(--font-mono); }
.sc-prop input.k-vector, .sc-prop input.k-transform { color: #8fc1f0; }
.sc-prop input.k-ref { color: #d2a8ff; }
.sc-prop input.k-number { color: #7ed492; }
.sc-x { flex: none; background: none; border: 0; color: var(--text-faint); cursor: pointer; font-size: 13px; padding: 0 3px; }
.sc-x:hover { color: var(--danger); }
.sc-stats { display: flex; flex-direction: column; gap: 3px; font-size: 11px; }
.sc-stats div { display: flex; justify-content: space-between; gap: 8px; color: var(--text-muted); }
.sc-stats b { color: var(--text); font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-ref { display: flex; align-items: center; gap: 6px; font-size: 10.5px; padding: 3px 4px; border-radius: 4px; cursor: pointer; }
.sc-ref:hover { background: var(--bg-hover); }
.sc-ref.ghost { opacity: .55; }
.sc-ref-name { flex: 1; font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sc-ref small { color: var(--text-faint); }
.sc-file-chip { flex: none; font-family: var(--font-mono); font-size: 9px; font-weight: 700; color: var(--fc); background: color-mix(in srgb, var(--fc) 14%, transparent); border-radius: 3px; padding: 1px 4px; }
.sc-warn { margin: 0; padding-left: 16px; font-size: 10.5px; color: var(--text-muted); line-height: 1.7; }
</style>

<style>
/* Vue Flow 主题覆盖（非 scoped：这些类由库渲染在组件外层） */
.sc-canvas .vue-flow__node-sceneNode,
.sc-canvas .vue-flow__node-sceneFile { padding: 0 !important; border: none; background: transparent; }
.sc-canvas .vue-flow__attribution { background: transparent !important; color: #5d6878 !important; }
.sc-canvas .vue-flow__controls { box-shadow: none !important; border: 1px solid #1c2530; border-radius: 7px; overflow: hidden; }
.sc-canvas .vue-flow__controls-button { background: #0f141c; border-bottom: 1px solid #1c2530; fill: #8b97a7; width: 24px; height: 24px; }
.sc-canvas .vue-flow__controls-button:hover { background: #131a24; fill: #dbe4ee; }
.sc-canvas .vue-flow__minimap { background: #0f141c !important; border: 1px solid #1c2530; border-radius: 7px; overflow: hidden; }
.sc-canvas .vue-flow__minimap-mask { fill: rgba(11, 15, 21, .72); }
.sc-canvas .vue-flow__edge.selected .vue-flow__edge-path { stroke: #fff; }
.sc-canvas .vue-flow__selection { background: rgba(88, 166, 255, .08); border: 1px solid #315c86; }

.sc-node {
  width: 244px;
  background: #131a24;
  border: 1px solid #243040;
  border-left: 3px solid var(--nc);
  border-radius: 7px;
  padding: 6px 9px;
  cursor: grab;
  transition: border-color .12s ease, box-shadow .12s ease;
}
.sc-node:active { cursor: grabbing; }
.sc-node:hover { border-color: color-mix(in srgb, var(--nc) 55%, #243040); }
.sc-node.sel { box-shadow: 0 0 0 2px color-mix(in srgb, var(--nc) 32%, transparent); border-color: var(--nc); }
.sc-node.dim { opacity: .78; border-left-style: dashed; }
.sc-node.flash { box-shadow: 0 0 0 2px #45c98c88; }
.sc-node.flash.bad { box-shadow: 0 0 0 2px #ff6b6b88; }
.sc-node-head { display: flex; align-items: center; gap: 6px; }
.sc-node-dot { width: 6px; height: 6px; border-radius: 2px; background: var(--nc); flex: none; }
.sc-node-name { font-size: 12.5px; color: #dbe4ee; font-family: Consolas, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-node-pos { margin-left: auto; font-size: 9.5px; color: #5d6878; font-family: Consolas, monospace; flex: none; }
.sc-node-meta { display: flex; align-items: center; gap: 5px; margin-top: 3px; }
.sc-node-type { font-size: 10px; color: #8b97a7; font-family: Consolas, monospace; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-chip { font-size: 9px; border-radius: 3px; padding: 0 4px; border: 1px solid; flex: none; }
.sc-chip.inst { color: #2ec4b6; border-color: #1d5b55; }
.sc-chip.script { color: #d2a8ff; border-color: #4b3a63; }
.sc-chip.group { color: #8b97a7; border-color: #2a3645; }
.sc-chip.over { color: #e3a83a; border-color: #5a5030; }
.sc-handle { width: 7px !important; height: 7px !important; background: #0b0f15 !important; border: 1.5px solid #4a5563 !important; border-radius: 2px; }
.sc-handle.ref { top: 70% !important; }
.sc-handle:hover { background: #58a6ff !important; }

.sc-file {
  display: flex; align-items: center; gap: 8px;
  width: 258px; padding: 6px 9px;
  background: #0f141c; border: 1px solid #1c2530; border-left: 3px solid var(--fc);
  border-radius: 7px; cursor: pointer;
}
.sc-file:hover { border-color: color-mix(in srgb, var(--fc) 50%, #1c2530); }
.sc-file.sel { box-shadow: 0 0 0 2px color-mix(in srgb, var(--fc) 25%, transparent); }
.sc-file.ghost { border-style: dashed; opacity: .7; }
.sc-file-chip { flex: none; font-family: Consolas, monospace; font-size: 9px; font-weight: 700; color: var(--fc); background: color-mix(in srgb, var(--fc) 14%, transparent); border-radius: 3px; padding: 2px 5px; }
.sc-file-meta { min-width: 0; flex: 1; }
.sc-file-name { font-family: Consolas, monospace; font-size: 11.5px; color: #dbe4ee; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-file-sub { font-size: 9.5px; color: #5d6878; }
.sc-file-warn { color: #e3a83a; }
</style>
