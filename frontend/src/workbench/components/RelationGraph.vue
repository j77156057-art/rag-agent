<script setup lang="ts">
// P1：关系图。全项目类/脚本/场景节点 + 两类有向边：
//   inherits（子 → 父：GDScript extends / Python bases）、mounts（场景 → 挂载脚本）。
// 零第三方依赖：手写力导向布局（预迭代收敛 + rAF 余温），SVG 渲染，滚轮缩放/拖拽平移/节点拖动。
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { fsApi } from '../api'
import type { RelationGraphResp, RelationNode, RelationEdge } from '../api'
import { useWorkbench } from '../composables/workbench'
import { graphNodeStyle, regionColor } from '../theme'

const { relationGraphOpen, closeRelationGraph, jumpToLine } = useWorkbench()

const data = ref<RelationGraphResp | null>(null)
const loading = ref(false)
const error = ref('')
const ready = ref(false)
const query = ref('')
const showExternal = ref(true)
const showInherits = ref(true)
const showMounts = ref(true)
const svgEl = ref<SVGSVGElement | null>(null)

interface Sim {
  id: string
  node: RelationNode
  x: number; y: number; vx: number; vy: number
  w: number; h: number
}
interface SimEdge { a: Sim; b: Sim; e: RelationEdge }
interface Geo { w: number; h: number; label: string; sub: string }

let sim: Sim[] = []
const byId = new Map<string, Sim>()
let simEdges: SimEdge[] = []
const edgeEls = new Map<string, { line: SVGLineElement; label: SVGGElement }>()
const geometry = ref<Record<string, Geo>>({})

// 视口变换（唯一一个每帧响应式更新的绑定，节点/边坐标全部命令式写 DOM）
const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)

let raf = 0
let alpha = 0
let dragSim: Sim | null = null
let panning = false
let panStart = { x: 0, y: 0, px: 0, py: 0 }
let down: { id: string; x: number; y: number; moved: boolean } | null = null

// ------------------------------------------------------------ 数据加载

async function reload() {
  loading.value = true
  error.value = ''
  ready.value = false
  cancelRaf()
  try {
    data.value = await fsApi.relationGraph()
    // 让 loading 态先绘制一帧，避免预迭代布局卡住 spinner
    await nextTick()
    await new Promise((r) => setTimeout(r, 30))
    buildLayout()
    ready.value = true
    await nextTick()
    fitView() // buildLayout 时 svg 还没显示，正式渲染后按真实尺寸再适配一次
    syncDom()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

watch(relationGraphOpen, (open) => {
  if (open) {
    void reload()
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
  } else {
    window.removeEventListener('keydown', onKey)
    window.removeEventListener('pointermove', onPointerMove)
    window.removeEventListener('pointerup', onPointerUp)
    cancelRaf()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('pointermove', onPointerMove)
  window.removeEventListener('pointerup', onPointerUp)
  cancelRaf()
})

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeRelationGraph()
}

// 切换过滤项：保留旧坐标重建
watch([showExternal, showInherits, showMounts], () => {
  if (!data.value || !ready.value) return
  buildLayout()
  nextTick(() => syncDom())
})

// ------------------------------------------------------------ 过滤 / 搜索

const viewNodes = computed<RelationNode[]>(() => {
  const d = data.value
  if (!d) return []
  return showExternal.value ? d.nodes : d.nodes.filter((n) => !n.external)
})

const viewEdges = computed<RelationEdge[]>(() => {
  const d = data.value
  if (!d) return []
  const ids = new Set(viewNodes.value.map((n) => n.id))
  return d.edges.filter(
    (e) =>
      ids.has(e.source) &&
      ids.has(e.target) &&
      (e.kind !== 'inherits' || showInherits.value) &&
      (e.kind !== 'mounts' || showMounts.value),
  )
})

const matched = computed<Set<string>>(() => {
  const out = new Set<string>()
  const q = query.value.trim().toLowerCase()
  if (!q || !data.value) return out
  const words = q.split(/\s+/).filter(Boolean)
  for (const n of data.value.nodes) {
    const hay = `${n.label} ${n.sub} ${n.rel} ${n.doc} ${n.region_name}`.toLowerCase()
    if (words.every((w) => hay.includes(w))) out.add(n.id)
  }
  // 直接邻居也点亮
  for (const e of data.value.edges) {
    if (out.has(e.source)) out.add(e.target)
    if (out.has(e.target)) out.add(e.source)
  }
  return out
})

function nodeDim(n: RelationNode): boolean {
  return query.value.trim() !== '' && !matched.value.has(n.id)
}
function edgeDim(e: RelationEdge): boolean {
  if (query.value.trim() === '') return false
  return !matched.value.has(e.source) || !matched.value.has(e.target)
}

// ------------------------------------------------------------ 布局

let measureCtx: CanvasRenderingContext2D | null = null

function measure(text: string, size: number, bold: boolean): number {
  if (!measureCtx) {
    measureCtx = document.createElement('canvas').getContext('2d')
  }
  if (!measureCtx) return text.length * size * 0.62
  measureCtx.font = `${bold ? '600 ' : ''}${size}px "Segoe UI", "Microsoft YaHei", sans-serif`
  return measureCtx.measureText(text).width
}

function fitText(text: string, maxW: number, size: number, bold: boolean): string {
  if (measure(text, size, bold) <= maxW) return text
  let t = text
  while (t.length > 1 && measure(t + '…', size, bold) > maxW) t = t.slice(0, -1)
  return t + '…'
}

function edgeKey(e: RelationEdge): string {
  return `${e.kind}|${e.source}>${e.target}`
}

function buildLayout() {
  const nodes = viewNodes.value
  const edges = viewEdges.value

  const prev = new Map(sim.map((s) => [s.id, s]))
  sim = []
  byId.clear()
  simEdges = []
  const geo: Record<string, Geo> = {}

  nodes.forEach((n, i) => {
    const st = graphNodeStyle(n.kind)
    const label = fitText(n.label, 200, 12, true)
    const subRaw = n.external ? (n.sub ? `(${n.sub})` : '') : n.sub
    const sub = subRaw ? fitText(subRaw, 200, 9.5, false) : ''
    const labelW = measure(label, 12, true)
    const subW = sub ? measure(sub, 9.5, false) : 0
    const w = Math.max(92, Math.min(250, Math.max(labelW, subW) + 40))
    const h = sub ? 46 : 32
    geo[n.id] = { w, h, label, sub }

    const old = prev.get(n.id)
    let x: number, y: number
    if (old) {
      x = old.x
      y = old.y
    } else {
      const ring = n.external ? 1.9 : 1
      const ang = (i / Math.max(1, nodes.length)) * Math.PI * 2
      const r = (90 + nodes.length * 9) * ring
      x = Math.cos(ang) * r
      y = Math.sin(ang) * r * 0.62
    }
    const s: Sim = { id: n.id, node: n, x, y, vx: 0, vy: 0, w, h }
    sim.push(s)
    byId.set(n.id, s)
  })

  for (const e of edges) {
    const a = byId.get(e.source)
    const b = byId.get(e.target)
    if (a && b) simEdges.push({ a, b, e })
  }

  // 同步预迭代（小图毫秒级；大图最多 1000 文件上限内可接受）
  const iters = sim.length > 400 ? 120 : 260
  for (let i = 0; i < iters; i++) step(1)
  geometry.value = geo
  fitView()
}

function step(a: number) {
  const REP = 52000
  const SPRING = 0.05
  const IDEAL_INH = 150
  const IDEAL_MNT = 190
  const n = sim.length

  for (let i = 0; i < n; i++) {
    const s = sim[i]
    for (let j = i + 1; j < n; j++) {
      const t = sim[j]
      let dx = s.x - t.x
      let dy = s.y - t.y
      let d2 = dx * dx + dy * dy
      if (d2 < 0.01) {
        dx = (Math.random() - 0.5) * 2
        dy = (Math.random() - 0.5) * 2
        d2 = dx * dx + dy * dy
      }
      const d = Math.sqrt(d2)
      const f = (REP / d2) * a
      const ux = dx / d
      const uy = dy / d
      s.vx += ux * f
      s.vy += uy * f
      t.vx -= ux * f
      t.vy -= uy * f
      // 矩形碰撞兜底：重叠时强分离
      const minD = (s.w + t.w) / 4 + 12
      if (d < minD) {
        const push = ((minD - d) * 0.5 * a) / 1
        s.vx += ux * push
        s.vy += uy * push
        t.vx -= ux * push
        t.vy -= uy * push
      }
    }
    // 中心引力（不随 alpha 消失，拖动后也回聚）
    s.vx -= s.x * 0.012
    s.vy -= s.y * 0.012
  }

  for (const { a: sa, b: sb, e } of simEdges) {
    const dx = sb.x - sa.x
    const dy = sb.y - sa.y
    const d = Math.sqrt(dx * dx + dy * dy) || 0.01
    const ideal = e.kind === 'mounts' ? IDEAL_MNT : IDEAL_INH
    const f = (d - ideal) * SPRING * (0.25 + 0.75 * a)
    const ux = dx / d
    const uy = dy / d
    sa.vx += ux * f
    sa.vy += uy * f
    sb.vx -= ux * f
    sb.vy -= uy * f
  }

  for (const s of sim) {
    if (s === dragSim) {
      s.vx = 0
      s.vy = 0
      continue
    }
    s.vx *= 0.82
    s.vy *= 0.82
    const cap = 26
    const v = Math.hypot(s.vx, s.vy)
    if (v > cap) {
      s.vx = (s.vx / v) * cap
      s.vy = (s.vy / v) * cap
    }
    s.x += s.vx
    s.y += s.vy
  }
}

function reheat(v = 0.6) {
  alpha = Math.max(alpha, v)
  if (!raf && ready.value) raf = requestAnimationFrame(loop)
}

function loop() {
  if (alpha <= 0.02 && !dragSim) {
    raf = 0
    syncDom()
    return
  }
  step(alpha)
  alpha *= 0.96
  syncDom()
  raf = requestAnimationFrame(loop)
}

function cancelRaf() {
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  alpha = 0
}

// 边端点收进矩形边界，箭头不扎进节点
function endpoints(sa: Sim, sb: Sim) {
  const dx = sb.x - sa.x
  const dy = sb.y - sa.y
  const d = Math.hypot(dx, dy) || 0.01
  const ux = dx / d
  const uy = dy / d
  const ta = Math.min(sa.w / 2 / Math.abs(ux || 1e-6), sa.h / 2 / Math.abs(uy || 1e-6))
  const tb = Math.min(sb.w / 2 / Math.abs(ux || 1e-6), sb.h / 2 / Math.abs(uy || 1e-6))
  return {
    x1: sa.x + ux * ta,
    y1: sa.y + uy * ta,
    x2: sb.x - ux * tb,
    y2: sb.y - uy * tb,
  }
}

function syncDom() {
  for (const se of simEdges) {
    const els = edgeEls.get(edgeKey(se.e))
    if (!els) continue
    const p = endpoints(se.a, se.b)
    els.line.setAttribute('x1', String(p.x1))
    els.line.setAttribute('y1', String(p.y1))
    els.line.setAttribute('x2', String(p.x2))
    els.line.setAttribute('y2', String(p.y2))
    els.label.setAttribute('transform', `translate(${(p.x1 + p.x2) / 2}, ${(p.y1 + p.y2) / 2})`)
    els.label.style.display = zoom.value < 0.55 ? 'none' : ''
  }
  for (const s of sim) {
    const el = nodeEl(s.id)
    if (el) el.setAttribute('transform', `translate(${s.x - s.w / 2}, ${s.y - s.h / 2})`)
  }
}

// 初始模板绑定读取（首帧 Vue 渲染时用，之后命令式覆盖）
function initialPos(id: string, axis: 'x' | 'y', wh: 'w' | 'h', half: number): number {
  const s = byId.get(id)
  if (!s) return 0
  return axis === 'x' ? s.x - (half ? s[wh] / 2 : 0) : s.y - (half ? s[wh] / 2 : 0)
}
function edgePos(e: RelationEdge, end: 'x1' | 'y1' | 'x2' | 'y2'): number {
  const a = byId.get(e.source)
  const b = byId.get(e.target)
  if (!a || !b) return 0
  const p = endpoints(a, b)
  return p[end]
}

// ------------------------------------------------------------ 视口

function fitView() {
  const svg = svgEl.value
  if (!svg || sim.length === 0) {
    zoom.value = 1
    panX.value = 0
    panY.value = 0
    return
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (const s of sim) {
    minX = Math.min(minX, s.x - s.w / 2)
    maxX = Math.max(maxX, s.x + s.w / 2)
    minY = Math.min(minY, s.y - s.h / 2)
    maxY = Math.max(maxY, s.y + s.h / 2)
  }
  const cw = svg.clientWidth || 900
  const ch = svg.clientHeight || 600
  const bw = Math.max(1, maxX - minX + 80)
  const bh = Math.max(1, maxY - minY + 80)
  const z = Math.min(1.25, Math.max(0.18, Math.min(cw / bw, ch / bh)))
  zoom.value = z
  panX.value = cw / 2 - ((minX + maxX) / 2) * z
  panY.value = ch / 2 - ((minY + maxY) / 2) * z
}

function zoomBy(f: number) {
  const svg = svgEl.value
  if (!svg) return
  const cx = (svg.clientWidth || 900) / 2
  const cy = (svg.clientHeight || 600) / 2
  applyZoom(f, cx, cy)
}

function applyZoom(f: number, cx: number, cy: number) {
  const nz = Math.min(2.5, Math.max(0.18, zoom.value * f))
  const k = nz / zoom.value
  panX.value = cx - (cx - panX.value) * k
  panY.value = cy - (cy - panY.value) * k
  zoom.value = nz
}

function onWheel(e: WheelEvent) {
  if (!ready.value) return
  e.preventDefault()
  const r = svgEl.value!.getBoundingClientRect()
  applyZoom(e.deltaY < 0 ? 1.12 : 1 / 1.12, e.clientX - r.left, e.clientY - r.top)
}

function toWorld(clientX: number, clientY: number) {
  const r = svgEl.value!.getBoundingClientRect()
  return {
    x: (clientX - r.left - panX.value) / zoom.value,
    y: (clientY - r.top - panY.value) / zoom.value,
  }
}

// ------------------------------------------------------------ 交互

function nodeEl(id: string): SVGGElement | undefined {
  return nodeEls.value.get(id)
}
const nodeEls = ref(new Map<string, SVGGElement>())

function setNodeRef(el: Element | unknown, id: string) {
  if (el instanceof SVGGElement) nodeEls.value.set(id, el)
  else nodeEls.value.delete(id)
}

function setEdgeRef(el: Element | unknown, key: string) {
  if (el instanceof SVGGElement) {
    edgeEls.set(key, {
      line: el.querySelector('.rg-line') as SVGLineElement,
      label: el.querySelector('.rg-elabel') as SVGGElement,
    })
  } else {
    edgeEls.delete(key)
  }
}

function onNodeDown(ev: PointerEvent, n: RelationNode) {
  const s = byId.get(n.id)
  if (!s) return
  ev.stopPropagation()
  down = { id: n.id, x: ev.clientX, y: ev.clientY, moved: false }
  dragSim = s
  reheat(0.7)
}

function onBgDown(ev: PointerEvent) {
  if (!ready.value) return
  panning = true
  panStart = { x: ev.clientX, y: ev.clientY, px: panX.value, py: panY.value }
  ;(ev.currentTarget as Element).setPointerCapture?.(ev.pointerId)
}

function onPointerMove(ev: PointerEvent) {
  if (dragSim && down) {
    if (Math.hypot(ev.clientX - down.x, ev.clientY - down.y) > 4) down.moved = true
    const w = toWorld(ev.clientX, ev.clientY)
    dragSim.x = w.x
    dragSim.y = w.y
    dragSim.vx = 0
    dragSim.vy = 0
    reheat(0.5)
  } else if (panning) {
    panX.value = panStart.px + (ev.clientX - panStart.x)
    panY.value = panStart.py + (ev.clientY - panStart.y)
  }
}

async function onPointerUp() {
  const clicked = down
  dragSim = null
  panning = false
  down = null
  if (clicked && !clicked.moved) {
    const n = data.value?.nodes.find((x) => x.id === clicked.id)
    if (n && n.rel) {
      closeRelationGraph()
      await jumpToLine(n.rel, n.line || 1)
    }
  }
}

const viewport = computed(() => `translate(${panX.value}, ${panY.value}) scale(${zoom.value})`)
const stats = computed(() => data.value?.stats)
const styleOf = graphNodeStyle
const colorOf = regionColor
</script>

<template>
  <div v-if="relationGraphOpen" class="rg-overlay" @mousedown.self="closeRelationGraph">
    <section class="rg-panel" role="dialog" aria-label="关系图">
      <header class="rg-top">
        <div class="rg-title">
          <svg width="15" height="15" viewBox="0 0 15 15" aria-hidden="true">
            <circle cx="3.6" cy="4" r="1.7" fill="none" stroke="#58a6ff" stroke-width="1.1" />
            <circle cx="11.4" cy="4" r="1.7" fill="none" stroke="#2ec4b6" stroke-width="1.1" />
            <circle cx="7.5" cy="11.2" r="1.7" fill="none" stroke="#f0883e" stroke-width="1.1" />
            <path d="M5 4.8 L9.6 4.8 M4.6 5.4 L6.6 9.7 M10.4 5.4 L8.4 9.7" stroke="#7d90aa" stroke-width="0.9" />
          </svg>
          <h2>关系图</h2>
          <span v-if="stats" class="rg-stats">
            {{ stats.user_nodes }} 个类/脚本/场景 · {{ stats.external_nodes }} 个外部基类 ·
            {{ stats.edges_by_kind.inherits || 0 }} 继承 · {{ stats.edges_by_kind.mounts || 0 }} 挂载
          </span>
        </div>
        <button class="rg-iconbtn" title="重新布局" @click="reload">
          <svg width="13" height="13" viewBox="0 0 13 13"><path d="M11 6.5 A4.5 4.5 0 1 1 6.5 2 M11 1.4 V4.2 H8.2" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        </button>
        <button class="rg-iconbtn" title="关闭（Esc）" @click="closeRelationGraph">
          <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>
        </button>
      </header>

      <div class="rg-toolbar">
        <div class="rg-search">
          <svg width="12" height="12" viewBox="0 0 12 12"><circle cx="5.2" cy="5.2" r="3.4" fill="none" stroke="currentColor" stroke-width="1.2"/><path d="M7.8 7.8 L10.5 10.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
          <input v-model="query" type="text" placeholder="搜索类 / 脚本 / 场景 / 分区（高亮节点与直接关系）" />
          <kbd v-if="query" class="rg-clear" @click="query = ''">清空</kbd>
        </div>
        <div class="rg-toggles">
          <button :class="['rg-toggle', { off: !showInherits }]" @click="showInherits = !showInherits">
            <i class="rg-tg-line inh" />继承
          </button>
          <button :class="['rg-toggle', { off: !showMounts }]" @click="showMounts = !showMounts">
            <i class="rg-tg-line mnt" />场景挂载
          </button>
          <button :class="['rg-toggle', { off: !showExternal }]" @click="showExternal = !showExternal">
            <i class="rg-tg-ext" />引擎/外部基类
          </button>
        </div>
        <div class="rg-zoom">
          <button title="放大" @click="zoomBy(1.2)">+</button>
          <button title="缩小" @click="zoomBy(1 / 1.2)">−</button>
          <button title="适应窗口" @click="fitView">适应</button>
        </div>
      </div>

      <div class="rg-canvas-wrap">
        <div v-if="loading" class="rg-state"><div class="cv-spinner" /><p>正在分析继承与挂载关系…</p></div>
        <div v-else-if="error" class="rg-state">
          <p class="rg-error">{{ error }}</p>
          <button class="rg-retry" @click="reload">重试</button>
        </div>
        <div v-else-if="ready && viewNodes.length === 0" class="rg-state">
          <p class="rg-faint">当前过滤条件下没有可显示的节点。</p>
        </div>
        <div v-else-if="ready && viewEdges.length === 0 && (!showInherits && !showMounts)" class="rg-state">
          <p class="rg-faint">两类边都已隐藏，打开上方开关查看关系。</p>
        </div>

        <svg
          v-show="ready"
          ref="svgEl"
          class="rg-svg"
          :class="{ grabbing: panning }"
          @pointerdown="onBgDown"
          @wheel="onWheel"
        >
          <defs>
            <marker id="rg-arrow-inh" viewBox="0 0 10 10" refX="8.5" refY="5"
                    markerWidth="9" markerHeight="9" orient="auto-start-reverse">
              <path d="M0 0 L9 5 L0 10 Z" fill="#7fa3d0" />
            </marker>
            <marker id="rg-arrow-mnt" viewBox="0 0 10 10" refX="8.5" refY="5"
                    markerWidth="9" markerHeight="9" orient="auto-start-reverse">
              <path d="M0 0 L9 5 L0 10 Z" fill="#f0883e" />
            </marker>
          </defs>

          <g :transform="viewport">
            <g class="rg-edges">
              <g v-for="e in viewEdges" :key="edgeKey(e)"
                 :ref="(el) => setEdgeRef(el, edgeKey(e))"
                 :class="['rg-edge-g', { dim: edgeDim(e) }]">
                <line
                  class="rg-line"
                  :class="e.kind"
                  :x1="edgePos(e, 'x1')" :y1="edgePos(e, 'y1')"
                  :x2="edgePos(e, 'x2')" :y2="edgePos(e, 'y2')"
                  :marker-end="e.kind === 'mounts' ? 'url(#rg-arrow-mnt)' : 'url(#rg-arrow-inh)'"
                />
                <g class="rg-elabel" :transform="`translate(${edgePos(e, 'x1')}, ${edgePos(e, 'y1')})`">
                  <rect x="-26" y="-8" width="52" height="13" rx="3" class="rg-elabel-bg-inh" v-if="e.kind === 'inherits'" />
                  <rect x="-20" y="-8" width="40" height="13" rx="3" class="rg-elabel-bg-mnt" v-else />
                  <text text-anchor="middle" y="2" :class="['rg-elabel-tx', e.kind]">{{ e.label }}</text>
                </g>
              </g>
            </g>

            <g class="rg-nodes">
              <g
                v-for="n in viewNodes"
                :key="n.id"
                :ref="(el) => setNodeRef(el, n.id)"
                class="rg-node"
                :class="{ ext: n.external, dim: nodeDim(n), clickable: !!n.rel }"
                :transform="`translate(${initialPos(n.id, 'x', 'w', true)}, ${initialPos(n.id, 'y', 'h', true)})`"
                @pointerdown="onNodeDown($event, n)"
              >
                <rect
                  :width="geometry[n.id]?.w || 100"
                  :height="geometry[n.id]?.h || 32"
                  rx="7"
                  :fill="styleOf(n.kind).fill"
                  :stroke="styleOf(n.kind).stroke"
                  :stroke-dasharray="styleOf(n.kind).dashed ? '4 3' : undefined"
                />
                <circle
                  v-if="n.region"
                  class="rg-region-dot"
                  :cx="9" :cy="(geometry[n.id]?.h || 32) / 2" r="3"
                  :fill="colorOf(n.region)"
                />
                <text class="rg-mark" :fill="styleOf(n.kind).stroke"
                      :x="n.region ? 17 : 12"
                      :y="(geometry[n.id]?.h || 32) / 2 + 3.6">
                  {{ styleOf(n.kind).mark }}
                </text>
                <text class="rg-label" :fill="styleOf(n.kind).text" x="30"
                      :y="geometry[n.id]?.sub ? 17 : ((geometry[n.id]?.h || 32) / 2 + 4)">
                  {{ geometry[n.id]?.label }}
                </text>
                <text v-if="geometry[n.id]?.sub" class="rg-sub" x="30"
                      :y="(geometry[n.id]?.h || 32) - 9">
                  {{ geometry[n.id]?.sub }}
                </text>
                <title>{{ n.rel ? `${n.rel}${n.doc ? '\n' + n.doc : ''}` : `${n.label}（${styleOf(n.kind).label}）` }}</title>
              </g>
            </g>
          </g>
        </svg>

        <div v-if="ready && viewNodes.length" class="rg-legend">
          <span><i class="rg-lg rg-lg-class" />命名类</span>
          <span><i class="rg-lg rg-lg-script" />脚本</span>
          <span><i class="rg-lg rg-lg-scene" />场景</span>
          <span><i class="rg-lg rg-lg-ext" />外部基类</span>
          <span class="rg-legend-tip">滚轮缩放 · 拖动平移 · 点节点打开文件</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.rg-overlay {
  position: fixed;
  inset: 0;
  background: rgba(5, 8, 12, 0.66);
  backdrop-filter: blur(2px);
  z-index: 90;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 26px;
}
.rg-panel {
  width: min(1280px, 97vw);
  height: min(820px, 92vh);
  background: var(--bg-raised);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.55);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rg-top {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.rg-title { display: flex; align-items: center; gap: 9px; flex: 1; }
.rg-title h2 { margin: 0; font-size: 14px; font-weight: 600; }
.rg-stats { font-size: 11.5px; color: var(--text-faint); }
.rg-iconbtn {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  width: 26px; height: 26px;
  border-radius: 5px;
  cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
}
.rg-iconbtn:hover { background: var(--bg-hover); color: var(--text); }

.rg-toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 16px;
  border-bottom: 1px solid var(--border);
}
.rg-search {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg);
  border: 1px solid var(--border-strong);
  border-radius: 6px;
  padding: 0 10px;
  height: 30px;
  color: var(--text-faint);
  max-width: 460px;
}
.rg-search:focus-within { border-color: #58a6ff66; box-shadow: 0 0 0 2px #58a6ff18; }
.rg-search input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text);
  font-size: 12.5px;
  font-family: inherit;
  min-width: 0;
}
.rg-clear {
  font-style: normal;
  font-size: 10.5px;
  color: var(--text-muted);
  cursor: pointer;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 6px;
}
.rg-clear:hover { color: var(--text); }
.rg-toggles { display: flex; gap: 6px; }
.rg-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  font-size: 11.5px;
  padding: 3px 10px;
  border-radius: 11px;
  cursor: pointer;
}
.rg-toggle:hover { border-color: var(--border-strong); color: var(--text); }
.rg-toggle.off { opacity: 0.42; }
.rg-tg-line { width: 14px; height: 0; border-top: 2px solid #7fa3d0; display: inline-block; }
.rg-tg-line.mnt { border-top: 2px dashed #f0883e; }
.rg-tg-ext {
  width: 11px; height: 9px;
  border: 1.5px dashed #8b97a7;
  border-radius: 2px;
  display: inline-block;
}
.rg-zoom { margin-left: auto; display: flex; gap: 4px; }
.rg-zoom button {
  min-width: 26px; height: 24px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-muted);
  border-radius: 4px;
  font-size: 11.5px;
  cursor: pointer;
  padding: 0 7px;
}
.rg-zoom button:hover { color: var(--text); border-color: var(--border-strong); }

.rg-canvas-wrap { flex: 1; position: relative; overflow: hidden; background: var(--bg); }
.rg-svg {
  width: 100%;
  height: 100%;
  display: block;
  touch-action: none;
  cursor: grab;
  background-image:
    radial-gradient(circle, rgba(125, 133, 144, 0.10) 1px, transparent 1px);
  background-size: 26px 26px;
}
.rg-svg.grabbing { cursor: grabbing; }

.rg-line { fill: none; stroke-width: 1.3; }
.rg-line.inherits { stroke: #5f7fa6; }
.rg-line.mounts { stroke: #c9762f; stroke-dasharray: 6 4; }
.rg-edge-g.dim { opacity: 0.10; }
.rg-elabel-bg-inh { fill: var(--bg); stroke: #5f7fa655; stroke-width: 0.8; }
.rg-elabel-bg-mnt { fill: var(--bg); stroke: #c9762f55; stroke-width: 0.8; }
.rg-elabel-tx { font-size: 8.5px; }
.rg-elabel-tx.inherits { fill: #9ec2ea; }
.rg-elabel-tx.mounts { fill: #f0a868; }

.rg-node { cursor: default; }
.rg-node.clickable { cursor: pointer; }
.rg-node rect { stroke-width: 1.3; }
.rg-node.clickable:hover rect { stroke-width: 2; filter: brightness(1.18); }
.rg-node.dim { opacity: 0.13; }
.rg-mark { font-family: var(--font-mono); font-size: 10.5px; font-weight: 700; text-anchor: middle; }
.rg-label { font-size: 12px; font-weight: 600; }
.rg-sub { font-size: 9.5px; fill: var(--text-faint); font-family: var(--font-mono); }

.rg-state {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--text-faint);
  font-size: 12.5px;
}
.rg-error { color: #ff8585; }
.rg-retry { border: 1px solid var(--border-strong); background: transparent; color: var(--text); border-radius: 5px; padding: 5px 14px; cursor: pointer; }

.rg-legend {
  position: absolute;
  left: 14px;
  bottom: 12px;
  display: flex;
  align-items: center;
  gap: 14px;
  background: rgba(15, 20, 28, 0.82);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 12px;
  font-size: 10.5px;
  color: var(--text-muted);
  pointer-events: none;
}
.rg-legend span { display: inline-flex; align-items: center; gap: 5px; }
.rg-lg { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.rg-lg-class { background: rgba(88, 166, 255, 0.3); border: 1px solid #58a6ff; }
.rg-lg-script { background: rgba(46, 196, 182, 0.25); border: 1px solid #2ec4b6; }
.rg-lg-scene { background: rgba(240, 136, 62, 0.25); border: 1px solid #f0883e; }
.rg-lg-ext { background: rgba(139, 151, 167, 0.1); border: 1px dashed #8b97a7; }
.rg-legend-tip { color: var(--text-faint); }
</style>
