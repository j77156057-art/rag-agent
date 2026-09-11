<script setup lang="ts">
// P3：分区可视化。上方为依赖方向 DAG（dep → 依赖方，按依赖深度分层），
// 下方为每区状态卡（目录/独立 git/分支/脏标记/文件数/依赖/导出/校验器），
// 顶部横幅展示契约校验结果（依赖无环、导出文件存在）。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useWorkbench } from '../composables/workbench'
import { regionColor } from '../theme'
import type { RegionInfo } from '../api'

const {
  regionMapOpen, regionMap, openRegionMap, closeRegionMap, locateRegion,
} = useWorkbench()

const selectedKey = ref<string | null>(null)

const byKey = computed(() => new Map(regionMap.value.regions.map((r) => [r.key, r])))

function depName(key: string): string {
  return byKey.value.get(key)?.name || key
}

// ---------------------------------------------------------------- DAG 分层布局
const NODE_W = 168
const NODE_H = 46
const GAP_X = 76
const GAP_Y = 16
const PAD = 10

interface LaidEdge { x1: number; y1: number; x2: number; y2: number }
interface DagLayout {
  pos: Map<string, { x: number; y: number }>
  edges: LaidEdge[]
  width: number
  height: number
}

const layout = computed<DagLayout>(() => {
  const regs = regionMap.value.regions
  const present = new Set(regs.map((r) => r.key))
  const depthCache = new Map<string, number>()
  const depthOf = (k: string, stack: Set<string>): number => {
    const cached = depthCache.get(k)
    if (cached != null) return cached
    if (stack.has(k)) return 0 // 环保护（契约会报错）
    const r = byKey.value.get(k)
    const deps = (r?.depends_on || []).filter((d) => present.has(d))
    const d = deps.length
      ? 1 + Math.max(...deps.map((x) => depthOf(x, new Set(stack).add(k))))
      : 0
    depthCache.set(k, d)
    return d
  }

  const cols: string[][] = []
  for (const r of regs) {
    // 初始栈必须为空：环检测发生在递归下钻时（栈里放的是祖先），
    // 若把节点自身放入初始栈，守卫会立即命中导致所有节点深度恒为 0。
    const d = depthOf(r.key, new Set())
    ;(cols[d] ||= []).push(r.key)
  }
  cols.forEach((keys) => keys.sort())

  const pos = new Map<string, { x: number; y: number }>()
  let maxRows = 0
  cols.forEach((keys, ci) => {
    maxRows = Math.max(maxRows, keys.length)
    keys.forEach((k, ri) => {
      pos.set(k, { x: PAD + ci * (NODE_W + GAP_X), y: PAD + ri * (NODE_H + GAP_Y) })
    })
  })

  const edges: LaidEdge[] = []
  for (const r of regs) {
    for (const dep of r.depends_on || []) {
      const a = pos.get(dep)
      const b = pos.get(r.key)
      if (a && b) edges.push({ x1: a.x + NODE_W, y1: a.y + NODE_H / 2, x2: b.x, y2: b.y + NODE_H / 2 })
    }
  }

  const width = PAD * 2 + Math.max(1, cols.length) * NODE_W + Math.max(0, cols.length - 1) * GAP_X
  const height = PAD * 2 + Math.max(1, maxRows) * NODE_H + Math.max(0, maxRows - 1) * GAP_Y
  return { pos, edges, width, height }
})

function nodePos(key: string) {
  return layout.value.pos.get(key) || { x: 0, y: 0 }
}
function edgePath(e: LaidEdge): string {
  const mx = (e.x1 + e.x2) / 2
  return `M ${e.x1} ${e.y1} C ${mx} ${e.y1}, ${mx} ${e.y2}, ${e.x2 - 2} ${e.y2}`
}

function selectNode(key: string) {
  selectedKey.value = key
  document.getElementById(`rm-card-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

function verifyText(r: RegionInfo): string {
  const v = (r.verify || '').trim()
  if (!v) return '未配置校验'
  if (v === 'builtin:py') return 'Python 语法检查'
  if (v === 'builtin:json') return 'JSON/TOML 校验'
  return v
}

function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeRegionMap()
}
watch(regionMapOpen, (open) => {
  if (open) {
    selectedKey.value = null
    window.addEventListener('keydown', onKey)
  } else window.removeEventListener('keydown', onKey)
})
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <div v-if="regionMapOpen" class="rm-overlay" @mousedown.self="closeRegionMap">
    <section class="rm-panel" role="dialog" aria-label="分区可视化">
      <header class="rm-top">
        <div class="rm-title">
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
            <rect x="1.2" y="2" width="4.6" height="3.4" rx="0.8" fill="none" stroke="#58a6ff" stroke-width="1" />
            <rect x="8.4" y="2" width="4.6" height="3.4" rx="0.8" fill="none" stroke="#45c98c" stroke-width="1" />
            <rect x="4.8" y="9.2" width="4.6" height="3.4" rx="0.8" fill="none" stroke="#bc8cff" stroke-width="1" />
            <path d="M5.8 3.7 L8.4 3.7 M3.5 5.4 L6 9.2 M10.7 5.4 L8.2 9.2" stroke="#3a4658" stroke-width="0.9" />
          </svg>
          <h2>分区可视化</h2>
          <span class="rm-root" :title="regionMap.codeRoot">{{ regionMap.codeRoot }}</span>
        </div>
        <div class="rm-top-actions">
          <button class="rm-refresh" :disabled="regionMap.loading" @click="void openRegionMap()">
            <svg width="12" height="12" viewBox="0 0 12 12" :class="{ spinning: regionMap.loading }">
              <path d="M10.2 6 A4.2 4.2 0 1 1 6 1.8 A4.2 4.2 0 0 1 9.6 3.4 M9.6 1.4 V3.4 H7.6"
                    fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            刷新
          </button>
          <button class="rm-iconbtn" title="关闭（Esc）" @click="closeRegionMap">
            <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" /></svg>
          </button>
        </div>
      </header>

      <div v-if="regionMap.loading && !regionMap.regions.length" class="rm-state"><div class="cv-spinner" /><p>正在读取分区状态…</p></div>
      <div v-else-if="regionMap.error" class="rm-state"><p class="rm-error">{{ regionMap.error }}</p></div>

      <div v-else class="rm-body">
        <!-- 契约校验横幅 -->
        <div class="rm-contract" :class="regionMap.contracts?.ok ? 'ok' : 'bad'">
          <template v-if="regionMap.contracts">
            <svg v-if="regionMap.contracts.ok" width="13" height="13" viewBox="0 0 13 13">
              <circle cx="6.5" cy="6.5" r="5.4" fill="none" stroke="#45c98c" stroke-width="1" />
              <path d="M4 6.6 L5.8 8.3 L9 4.8" fill="none" stroke="#45c98c" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <svg v-else width="13" height="13" viewBox="0 0 13 13">
              <circle cx="6.5" cy="6.5" r="5.4" fill="none" stroke="#ff8e8e" stroke-width="1" />
              <path d="M6.5 3.4 V7.2" stroke="#ff8e8e" stroke-width="1.2" stroke-linecap="round" />
              <circle cx="6.5" cy="9.2" r="0.7" fill="#ff8e8e" />
            </svg>
            <div class="rm-contract-text">
              <strong>{{ regionMap.contracts.ok ? '契约校验通过' : `契约校验发现 ${regionMap.contracts.errors.length} 个问题` }}</strong>
              <ul v-if="!regionMap.contracts.ok">
                <li v-for="(err, i) in regionMap.contracts.errors" :key="i">{{ err }}</li>
              </ul>
            </div>
          </template>
          <span v-else class="rm-contract-off">契约校验不可用：{{ regionMap.contractsError }}</span>
        </div>

        <!-- 依赖方向 DAG -->
        <section class="rm-section">
          <h3>依赖方向 <em>箭头由被依赖分区指向依赖方（只允许单向依赖）</em></h3>
          <div class="rm-dag-scroll">
            <svg :width="layout.width" :height="layout.height" class="rm-dag">
              <defs>
                <marker id="rm-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                  <path d="M0 0 L8 4 L0 8 Z" fill="#46536a" />
                </marker>
              </defs>
              <path
                v-for="(e, i) in layout.edges" :key="i"
                :d="edgePath(e)" fill="none" stroke="#46536a" stroke-width="1.1"
                marker-end="url(#rm-arrow)"
              />
              <g
                v-for="r in regionMap.regions" :key="r.key"
                :transform="`translate(${nodePos(r.key).x},${nodePos(r.key).y})`"
                class="rm-node"
                :class="{ missing: !r.exists, selected: selectedKey === r.key }"
                @click="selectNode(r.key)"
              >
                <rect
                  :width="NODE_W" :height="NODE_H" rx="7"
                  :stroke="regionColor(r.key)" :fill="regionColor(r.key) + '14'"
                  stroke-width="1.1"
                />
                <circle cx="11" cy="15" r="3.4" :fill="regionColor(r.key)" />
                <text x="20" y="19" class="rm-node-name">{{ r.name }}</text>
                <text x="11" y="36" class="rm-node-dir">{{ r.dir }}/</text>
              </g>
            </svg>
          </div>
        </section>

        <!-- 分区状态卡 -->
        <section class="rm-section">
          <h3>分区状态 <em>点击卡片可在左侧文件树中定位</em></h3>
          <div class="rm-cards">
            <article
              v-for="r in regionMap.regions" :key="r.key"
              :id="`rm-card-${r.key}`"
              class="rm-card"
              :class="{ missing: !r.exists, selected: selectedKey === r.key }"
              @click="r.exists && locateRegion(r.dir)"
              :title="r.exists ? `在文件树中定位 ${r.dir}/` : '该分区目录尚未创建'"
            >
              <span class="rm-card-bar" :style="{ background: regionColor(r.key) }" />
              <div class="rm-card-main">
                <div class="rm-card-head">
                  <span class="rm-card-name">
                    <i class="rm-card-dot" :style="{ background: regionColor(r.key) }" />{{ r.name }}
                  </span>
                  <code class="rm-card-dir">{{ r.dir }}/</code>
                </div>
                <p class="rm-card-desc">{{ r.desc || '（无描述）' }}</p>
                <div class="rm-card-badges">
                  <span v-if="!r.exists" class="rm-badge rm-badge-missing">目录未创建</span>
                  <template v-else>
                    <span class="rm-badge rm-badge-files">{{ r.files }} 个文件</span>
                    <span v-if="r.git" class="rm-badge rm-badge-git">
                      git · {{ r.branch || '?' }}
                    </span>
                    <span v-else class="rm-badge rm-badge-nogit">无独立仓库</span>
                    <span v-if="r.dirty" class="rm-badge rm-badge-dirty">有未提交改动</span>
                  </template>
                </div>
                <div class="rm-card-meta">
                  <span class="rm-meta-label">依赖</span>
                  <span v-if="!r.depends_on.length" class="rm-mata-faint">无（基础分区）</span>
                  <span v-for="dep in r.depends_on" :key="dep" class="rm-chip">
                    <i :style="{ background: regionColor(dep) }" />{{ depName(dep) }}
                  </span>
                </div>
                <div class="rm-card-meta">
                  <span class="rm-meta-label">导出</span>
                  <span v-if="!r.exports.length" class="rm-mata-faint">无对外接口文件</span>
                  <code v-for="ex in r.exports" :key="ex" class="rm-export">{{ ex }}</code>
                </div>
                <div class="rm-card-meta">
                  <span class="rm-meta-label">校验</span>
                  <span class="rm-mata-faint">{{ verifyText(r) }}</span>
                </div>
              </div>
            </article>
          </div>
        </section>
      </div>
    </section>
  </div>
</template>

<style scoped>
.rm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(5, 8, 12, 0.62);
  backdrop-filter: blur(2px);
  z-index: 92;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 30px;
}
.rm-panel {
  width: min(1020px, 96vw);
  height: min(700px, 90vh);
  background: var(--bg-raised);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  box-shadow: 0 24px 70px rgba(0, 0, 0, 0.55);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.rm-top {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}
.rm-title { display: flex; align-items: center; gap: 9px; flex: 1; min-width: 0; }
.rm-title h2 { margin: 0; font-size: 14px; font-weight: 600; }
.rm-root {
  font-size: 11px;
  color: var(--text-faint);
  font-family: 'Cascadia Code', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rm-top-actions { display: flex; gap: 6px; align-items: center; flex: 0 0 auto; }
.rm-refresh {
  display: inline-flex; align-items: center; gap: 5px;
  font-size: 11.5px; padding: 4px 10px; border-radius: 6px;
  border: 1px solid #2b3543; background: transparent; color: #aeb9c8; cursor: pointer;
}
.rm-refresh:hover:not(:disabled) { background: #1a2230; }
.rm-refresh:disabled { opacity: 0.5; cursor: default; }
.rm-iconbtn {
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  width: 26px; height: 26px;
  border-radius: 5px;
  cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
}
.rm-iconbtn:hover { background: var(--bg-hover); color: var(--text); }
.spinning { animation: rm-spin 1s linear infinite; }
@keyframes rm-spin { to { transform: rotate(360deg); } }

.rm-body { flex: 1 1 auto; overflow-y: auto; padding: 14px 16px 22px; }

.rm-contract {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  border-radius: 8px;
  padding: 9px 12px;
  font-size: 12px;
  margin-bottom: 16px;
}
.rm-contract.ok { background: #12332333; border: 1px solid #2c6a48; color: #9fe0b6; }
.rm-contract.bad { background: #3a1c2044; border: 1px solid #6e3238; color: #ffb4b4; }
.rm-contract svg { flex: 0 0 auto; margin-top: 1px; }
.rm-contract-text { min-width: 0; }
.rm-contract-text strong { font-size: 12.5px; }
.rm-contract-text ul { margin: 5px 0 0; padding-left: 18px; line-height: 1.7; }
.rm-contract-off { color: var(--text-faint); font-size: 11.5px; }

.rm-section { margin-bottom: 18px; }
.rm-section h3 {
  margin: 0 0 9px;
  font-size: 12px;
  font-weight: 600;
  color: #c6d0de;
}
.rm-section h3 em {
  font-style: normal;
  font-weight: 400;
  font-size: 11px;
  color: var(--text-faint);
  margin-left: 8px;
}

.rm-dag-scroll {
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #0a0e14;
  padding: 4px;
}
.rm-dag { display: block; min-width: 100%; }
.rm-node { cursor: pointer; }
.rm-node rect { transition: stroke-width 0.12s; }
.rm-node:hover rect, .rm-node.selected rect { stroke-width: 2; }
.rm-node.missing rect { stroke-dasharray: 4 3; fill-opacity: 0.35; }
.rm-node-name {
  fill: #dce4ee;
  font-size: 11.5px;
  font-weight: 600;
  font-family: inherit;
}
.rm-node-dir {
  fill: #6b7686;
  font-size: 9.5px;
  font-family: 'Cascadia Code', Consolas, monospace;
}
.rm-node.missing .rm-node-name { fill: #7d8590; }

.rm-cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(310px, 1fr));
  gap: 10px;
}
.rm-card {
  position: relative;
  display: flex;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: #0f151d;
  overflow: hidden;
  cursor: pointer;
}
.rm-card:hover { border-color: #304055; background: #121925; }
.rm-card.selected { border-color: #58a6ff77; box-shadow: 0 0 0 1px #58a6ff44 inset; }
.rm-card.missing { cursor: default; opacity: 0.72; }
.rm-card-bar { flex: 0 0 3px; }
.rm-card-main { flex: 1 1 auto; min-width: 0; padding: 9px 11px 10px; }
.rm-card-head { display: flex; align-items: baseline; gap: 8px; margin-bottom: 3px; }
.rm-card-name { font-size: 12.5px; font-weight: 600; color: #dde4ee; display: inline-flex; align-items: center; gap: 6px; }
.rm-card-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.rm-card-dir {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
  color: #6b7686;
}
.rm-card-desc {
  margin: 0 0 7px;
  font-size: 11.5px;
  line-height: 1.55;
  color: #8b97a8;
}
.rm-card-badges { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 7px; }
.rm-badge {
  font-size: 10.5px;
  line-height: 17px;
  padding: 0 7px;
  border-radius: 9px;
  border: 1px solid #26313f;
  color: #9aa7b9;
}
.rm-badge-files { color: #8fc1ff; border-color: #2c4a6b; background: #58a6ff0e; }
.rm-badge-git { color: #7fd49a; border-color: #2f6b48; background: #45c98c0e; }
.rm-badge-nogit { color: #8a8126; }
.rm-badge-dirty { color: #e0a05c; border-color: #6e4d26; background: #d98a3d12; }
.rm-badge-missing { color: #ff9d9d; border-color: #6e3238; background: #ff6b6b0e; }
.rm-card-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px 7px;
  font-size: 11px;
  margin-top: 4px;
}
.rm-meta-label {
  flex: 0 0 auto;
  font-size: 10px;
  color: #5f6b7d;
  width: 30px;
}
.rm-mata-faint { color: #5f6b7d; }
.rm-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: #aeb9c8;
  background: #151c27;
  border: 1px solid #232e3d;
  border-radius: 9px;
  padding: 1px 8px 1px 7px;
}
.rm-chip i { width: 6px; height: 6px; border-radius: 50%; display: inline-block; }
.rm-export {
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 10.5px;
  color: #e6c07b;
  background: #1d2230;
  border-radius: 4px;
  padding: 0 5px;
}

.rm-state {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  font-size: 12.5px;
  color: var(--text-faint);
}
.rm-error { color: #ff8e8e; }
</style>
