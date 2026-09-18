<script setup lang="ts">
// GPU 协调面板：每卡显存/利用率/温度、近 20 分钟占用迷你曲线、
// 持有者强制回收、FIFO 排队取消、Ollama 空闲卸载秒数设置。
// 组件自管轮询（打开时 5s 一次），不进工作台全局状态。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { gpuApi, modelResidencyApi, type GpuStatus, type ModelStatus } from '../api'

const open = ref(false)
const st = ref<GpuStatus | null>(null)
const models = ref<ModelStatus | null>(null)
const error = ref('')
const busy = ref(false)
const idleInput = ref('')
let timer: number | null = null

const CHART_W = 380
const CHART_H = 56
const LINE_COLORS = ['#5b8def', '#e0a33e', '#7ac47c', '#c47adf', '#45b8c6']

const modeLabel: Record<string, string> = {
  serial: '排队模式（一次只跑一个任务，最稳妥）',
  multi: '多卡模式（每张显卡各跑各的）',
  parallel: '自由模式（不干预，可能互相抢显存）',
}

const summary = computed(() => {
  const m = st.value?.memory
  if (!m) return open.value ? '探测中…' : 'GPU'
  return `${(m.used_mb / 1024).toFixed(1)} / ${(m.total_mb / 1024).toFixed(1)} GB`
})

const busyDot = computed(() => !!st.value?.holders.length)

function gb(mb: number | null | undefined): string {
  if (mb === null || mb === undefined) return '—'
  return `${(mb / 1024).toFixed(1)} GB`
}

function fmtDur(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return ''
  if (sec < 60) return `${Math.round(sec)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}

function ownerLabel(owner: string): string {
  if (owner.startsWith('ollama')) return 'Ollama 模型'
  if (owner.startsWith('comfyui:submit:')) return 'ComfyUI 提交中'
  if (owner.startsWith('comfyui:')) return `ComfyUI 作业 ${owner.slice('comfyui:'.length).slice(0, 10)}`
  return owner
}

interface Line { index: number; points: string; color: string }

const chartLines = computed<Line[]>(() => {
  const samples = st.value?.samples ?? []
  if (samples.length < 2) return []
  const n = samples.length
  const indices = Array.from(new Set(samples.flatMap((p) => p.gpus.map((g) => g.index)))).sort((a, b) => a - b)
  return indices.map((index, ci) => {
    const pts: string[] = []
    samples.forEach((p, i) => {
      const g = p.gpus.find((x) => x.index === index)
      if (!g || !g.total_mb) return
      const x = (i / (n - 1)) * CHART_W
      const y = CHART_H - Math.min(1, g.used_mb / g.total_mb) * CHART_H
      pts.push(`${x.toFixed(1)},${y.toFixed(1)}`)
    })
    return { index, points: pts.join(' '), color: LINE_COLORS[ci % LINE_COLORS.length] }
  })
})

async function refresh() {
  try {
    const data = await gpuApi.status()
    st.value = data
    error.value = ''
  } catch (e) {
    error.value = (e as Error).message || 'GPU 状态获取失败'
  }
  // 本地模型驻留单独取（Ollama 未连接不影响显存面板）
  try {
    models.value = await modelResidencyApi.status()
  } catch {
    models.value = { reachable: false, loaded: [], vram_gb: 0, needs_ollama: false }
  }
}

async function withBusy(fn: () => Promise<void>) {
  if (busy.value) return
  busy.value = true
  try {
    await fn()
    await refresh()
  } finally {
    busy.value = false
  }
}

function cancelQueue(owner: string) {
  return withBusy(async () => {
    const r = await gpuApi.cancel(owner)
    if (!r.ok) error.value = r.error || '取消失败'
  })
}

function forceRelease(owner?: string) {
  const tip = owner ? `强制回收「${ownerLabel(owner)}」的 GPU 租约？\n（不会中断对方进程，但租约会立即转交排队者）`
    : '强制回收全部 GPU 租约？\n（持有者不会被杀进程，队列将立即转交）'
  if (!window.confirm(tip)) return
  return withBusy(async () => {
    const r = await gpuApi.forceRelease(owner)
    if (!r.ok) error.value = r.error || '回收失败'
  })
}

async function unloadModels() {
  if (!window.confirm('立即卸载全部驻留的本地模型以释放显存？\n（下次对话会自动重载，首次会稍慢）')) return
  await withBusy(async () => {
    try {
      const r = await modelResidencyApi.power('off')
      if (!r.ok) error.value = r.error || '卸载失败'
    } catch (e) {
      error.value = (e as Error).message || '卸载失败'
    }
  })
}

async function preloadModels() {
  await withBusy(async () => {
    try {
      const r = await modelResidencyApi.power('on')
      if (!r.ok) error.value = r.error || '预加载失败'
    } catch (e) {
      error.value = (e as Error).message || '预加载失败'
    }
  })
}

async function saveIdle() {
  const v = Number(idleInput.value)
  if (!Number.isFinite(v) || v < 0) {
    error.value = '空闲秒数需为不小于 0 的数字（0=关闭自动卸载）'
    return
  }
  await withBusy(async () => {
    await gpuApi.configure(v)
  })
}

watch(open, async (v) => {
  if (v) {
    idleInput.value = String(st.value?.ollama_idle.unload_seconds ?? 0)
    await refresh()
    timer = window.setInterval(refresh, 5000)
  } else if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
})

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})
</script>

<template>
  <div class="gp">
    <button class="gp-trigger" :class="{ 'gp-busy': busyDot }" title="显卡（GPU）：看显存用了多少、谁在占用、任务排队和空闲自动释放" @click="open = !open">
      <svg width="12" height="12" viewBox="0 0 12 12">
        <rect x="1" y="2.2" width="10" height="7" rx="1" fill="none" stroke="currentColor" stroke-width="0.9" />
        <rect x="2.2" y="3.4" width="2" height="2" rx="0.3" fill="currentColor" opacity="0.5" />
        <rect x="4.7" y="3.4" width="2" height="2" rx="0.3" fill="currentColor" opacity="0.5" />
        <rect x="7.2" y="3.4" width="2" height="2" rx="0.3" fill="currentColor" opacity="0.5" />
        <path d="M2.5 10.2 H4 M8 10.2 H9.5 M3 9.2 V10.4 M9 9.2 V10.4" stroke="currentColor" stroke-width="0.8" />
      </svg>
      <span class="gp-label">{{ summary }}</span>
    </button>

    <Teleport to="body"><template v-if="open">
      <div class="gp-backdrop" @click="open = false" />
      <div class="gp-pop">
        <div class="gp-head">
          <b>显卡使用情况</b>
          <span class="gp-mode">{{ st ? modeLabel[st.mode] || st.mode : '—' }}</span>
          <span class="gp-spacer" />
          <button class="gp-link" :disabled="busy" @click="refresh">刷新</button>
        </div>
        <p v-if="!st?.coordinating" class="gp-warn">当前模式不做租约协调（parallel），排队/回收不生效；采样与空闲卸载仍可用。</p>
        <p v-if="error" class="gp-err">{{ error }}</p>

        <!-- 占用迷你曲线（每卡一条，按显存占用比例） -->
        <div class="gp-chart-wrap">
          <svg class="gp-chart" :viewBox="`0 0 ${CHART_W} ${CHART_H}`" preserveAspectRatio="none">
            <line v-for="y in [0.25, 0.5, 0.75]" :key="y" :x1="0" :x2="CHART_W" :y1="CHART_H * y" :y2="CHART_H * y" class="gp-grid" />
            <polyline v-for="line in chartLines" :key="line.index" :points="line.points"
                      fill="none" :stroke="line.color" stroke-width="1.4" vector-effect="non-scaling-stroke" />
          </svg>
          <div class="gp-legend">
            <i v-for="line in chartLines" :key="line.index">
              <b :style="{ background: line.color }" />卡 {{ line.index }}
            </i>
            <span v-if="!chartLines.length" class="gp-faint">暂无采样（采样间隔 5s，稍后自动出现）</span>
          </div>
        </div>

        <!-- 每卡状态 + 持有者 + 该卡排队 -->
        <div v-for="g in st?.gpus || []" :key="g.index" class="gp-card">
          <div class="gp-card-head">
            <b>卡 {{ g.index }}<em v-if="g.name"> · {{ g.name }}</em></b>
            <span class="gp-faint">
              {{ g.utilization !== null ? Math.round(g.utilization) + '% 利用率' : '利用率 —' }}
              <template v-if="g.temperature_c !== null"> · {{ Math.round(g.temperature_c) }}°C</template>
            </span>
          </div>
          <div class="gp-bar">
            <i v-if="g.total_mb" :style="{ width: Math.min(100, (g.used_mb! / g.total_mb) * 100) + '%' }" />
          </div>
          <div class="gp-bar-row">
            <span>{{ gb(g.used_mb) }} / {{ gb(g.total_mb) }}（空 {{ gb(g.free_mb) }}）</span>
          </div>

          <div v-if="g.holder" class="gp-holder">
            <span class="gp-tag gp-tag-on">占用</span>
            <span class="gp-owner">{{ ownerLabel(g.holder.owner) }}</span>
            <span class="gp-faint" v-if="g.holder.purpose">· {{ g.holder.purpose }}</span>
            <span class="gp-faint">· {{ fmtDur(g.holder.held_for_seconds) }}</span>
            <span v-if="g.holder.lease_ttl" class="gp-faint">· TTL {{ fmtDur(g.holder.lease_ttl) }}</span>
            <span class="gp-spacer" />
            <button class="gp-mini-danger" :disabled="busy" @click="forceRelease(g.holder!.owner)">回收</button>
          </div>
          <div v-for="(q, i) in g.queue" :key="q.owner + i" class="gp-holder gp-wait">
            <span class="gp-tag gp-tag-wait">排队 #{{ i + 1 }}</span>
            <span class="gp-owner">{{ ownerLabel(q.owner) }}</span>
            <span class="gp-faint" v-if="q.waiting_gpu">· 任意卡</span>
            <span class="gp-spacer" />
            <button class="gp-mini" :disabled="busy" @click="cancelQueue(q.owner)">取消排队</button>
          </div>
        </div>
        <div v-if="st?.processes?.length || st?.compute_apps?.length" class="gp-idle">
          <div class="gp-idle-title">进程显存</div>
          <div v-for="p in st?.processes || []" :key="p.pid" class="gp-faint">PID {{ p.pid }} · {{ p.owner }} · GPU {{ p.gpu ?? '—' }} · {{ p.status }}</div>
          <div v-for="p in st?.compute_apps || []" :key="'c'+p.pid" class="gp-faint">计算进程 {{ p.pid }} · {{ p.process_name }} · {{ p.used_mb }} MB</div>
        </div>
        <div v-if="st?.recovery_events?.length" class="gp-idle">
          <div class="gp-idle-title">最近回收事件</div>
          <div v-for="(e,i) in st.recovery_events.slice(-8).reverse()" :key="i" class="gp-faint">{{ e.status || 'recovered' }} · {{ e.owner || 'unknown' }} · GPU {{ e.gpu ?? '—' }}</div>
        </div>

        <!-- Ollama 空闲自动卸载 -->
        <div class="gp-idle">
          <div class="gp-idle-title">Ollama 空闲自动卸载
            <span class="gp-faint" v-if="st">（{{ st.ollama_idle.unload_seconds ? fmtDur(st.ollama_idle.unload_seconds) + ' 后' : '已关闭' }}）</span>
          </div>
          <div class="gp-idle-row">
            <input v-model="idleInput" type="number" min="0" step="10" class="gp-input" />
            <span class="gp-faint">秒（0=关）</span>
            <button class="gp-mini" :disabled="busy" @click="saveIdle">保存</button>
          </div>
          <div v-if="st" class="gp-faint gp-idle-meta">
            最近活动：{{ st.ollama_idle.last_activity_ago === null ? '无' : fmtDur(st.ollama_idle.last_activity_ago) + '前' }}
            · 最近卸载：{{ st.ollama_idle.last_unload_ago === null ? '无' : fmtDur(st.ollama_idle.last_unload_ago) + '前' }}
          </div>
        </div>

        <!-- 本地模型驻留（显存）：一键卸载 / 预加载 -->
        <div class="gp-idle">
          <div class="gp-idle-title">本地模型驻留
            <span class="gp-faint" v-if="models">
              （{{ !models.reachable ? 'Ollama 未连接'
                  : (models.loaded.length ? models.loaded.length + ' 个 · ' + models.vram_gb + ' GB 显存' : '未加载') }}）
            </span>
          </div>
          <div v-for="m in models?.loaded || []" :key="m.name" class="gp-faint gp-model-row">
            <span class="gp-owner">{{ m.name }}</span>
            <span>{{ m.size_vram_gb }} GB 显存</span>
            <span v-if="m.processor"> · {{ m.processor }}</span>
            <span v-if="m.expires_minutes !== null"> · {{ m.expires_minutes }} 分钟后自动卸载</span>
          </div>
          <div class="gp-idle-row">
            <button class="gp-mini" :disabled="busy || !models?.loaded.length" @click="unloadModels">卸载全部（腾显存）</button>
            <button class="gp-mini" :disabled="busy || !models?.needs_ollama" @click="preloadModels">预加载</button>
          </div>
          <div class="gp-faint gp-idle-meta">切换本地模型或跑 ComfyUI 生图/帧动画前，先卸载可避免显存不足。</div>
        </div>

        <div class="gp-foot">
          <span class="gp-faint">排队 {{ st?.queue_length ?? 0 }} · 钩子 {{ st?.hooks.join(', ') || '无' }}</span>
          <span class="gp-spacer" />
          <button class="gp-danger" :disabled="busy || !st?.holders.length" @click="forceRelease()">全部强制回收</button>
        </div>
      </div>
    </template></Teleport>
  </div>
</template>

<style scoped>
.gp { position: relative; }
.gp-trigger {
  display: inline-flex; align-items: center; gap: 5px;
  background: transparent; color: var(--text-muted);
  border: 1px solid var(--border-strong); border-radius: 5px;
  padding: 5px 9px; cursor: pointer; font-size: 11px; white-space: nowrap;
}
.gp-trigger:hover { color: var(--text); }
.gp-busy { border-color: #dfb067; color: #8a5a16; }
.gp-backdrop { position: fixed; inset: 0; z-index: 299; }
.gp-pop {
  position: fixed; right: 16px; top: 64px; width: 420px; max-width: calc(100vw - 32px); max-height: calc(100vh - 80px); overflow-y: auto;
  padding: 12px; background: var(--bg-raised);
  border: 1px solid var(--border-strong); border-radius: 8px;
  z-index: 300; font-size: 12px; line-height: 1.6;
  box-shadow: 0 12px 32px rgba(35, 52, 84, 0.16);
}
.gp-head { display: flex; align-items: center; gap: 8px; }
.gp-head b { color: var(--text); font-size: 13px; }
.gp-mode { color: var(--text-muted); font-size: 11px; }
.gp-spacer { flex: 1; }
.gp-link { background: none; border: none; color: #2f6fed; cursor: pointer; font-size: 11px; padding: 0; }
.gp-warn { margin: 8px 0 0; padding: 6px 8px; border-radius: 5px; background: rgba(200, 129, 28, 0.12); color: #8a5a16; font-size: 11px; }
.gp-err { margin: 8px 0 0; color: #c23a40; font-size: 11px; }
.gp-faint { color: var(--text-muted); font-size: 11px; }

.gp-chart-wrap { margin: 10px 0 4px; }
.gp-chart { width: 100%; height: 56px; display: block; background: #f6f8fb; border-radius: 4px; }
.gp-grid { stroke: var(--border-strong); stroke-width: 0.5; stroke-dasharray: 2 3; }
.gp-legend { display: flex; gap: 10px; margin-top: 3px; font-size: 10px; color: var(--text-muted); }
.gp-legend i { display: inline-flex; align-items: center; gap: 4px; font-style: normal; }
.gp-legend b { width: 8px; height: 3px; border-radius: 1px; display: inline-block; }

.gp-card { margin-top: 10px; padding: 8px 9px; border: 1px solid var(--border-strong); border-radius: 6px; }
.gp-card-head { display: flex; align-items: baseline; gap: 8px; }
.gp-card-head b { font-size: 12px; color: var(--text); font-weight: 600; }
.gp-card-head em { font-style: normal; font-weight: 400; color: var(--text-muted); font-size: 11px; }
.gp-bar { height: 6px; border-radius: 3px; background: rgba(35, 52, 84, 0.08); margin-top: 6px; overflow: hidden; }
.gp-bar i { display: block; height: 100%; border-radius: 3px; background: linear-gradient(90deg, #2f6fed, #6f9cf5); }
.gp-bar-row { display: flex; justify-content: space-between; margin-top: 3px; font-size: 11px; color: var(--text-muted); }

.gp-holder { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 11px; }
.gp-wait .gp-owner { color: var(--text-muted); }
.gp-tag { padding: 0 6px; border-radius: 8px; font-size: 10px; line-height: 16px; }
.gp-tag-on { background: rgba(224, 72, 79, 0.12); color: #c23a40; }
.gp-tag-wait { background: rgba(47, 111, 237, 0.12); color: #2f6fed; }
.gp-owner { color: var(--text); }
.gp-mini { background: transparent; border: 1px solid var(--border-strong); color: var(--text-muted); border-radius: 4px; font-size: 10px; padding: 1px 7px; cursor: pointer; }
.gp-mini:hover:not(:disabled) { color: var(--text); border-color: #2f6fed; }
.gp-mini-danger { background: transparent; border: 1px solid rgba(224, 72, 79, 0.5); color: #c23a40; border-radius: 4px; font-size: 10px; padding: 1px 7px; cursor: pointer; }
.gp-mini-danger:hover:not(:disabled) { background: rgba(224, 72, 79, 0.1); }
button:disabled { opacity: 0.5; cursor: default; }

.gp-idle { margin-top: 12px; padding: 8px 9px; border: 1px solid var(--border-strong); border-radius: 6px; }
.gp-idle-title { font-size: 12px; color: var(--text); }
.gp-idle-row { display: flex; align-items: center; gap: 7px; margin-top: 5px; }
.gp-input { width: 72px; background: var(--bg); border: 1px solid var(--border-strong); border-radius: 4px; color: var(--text); padding: 3px 6px; font-size: 11px; }
.gp-idle-meta { margin-top: 4px; }
.gp-model-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 3px; }

.gp-foot { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
.gp-danger { background: transparent; border: 1px solid rgba(224, 72, 79, 0.5); color: #c23a40; border-radius: 5px; font-size: 11px; padding: 4px 10px; cursor: pointer; }
.gp-danger:hover:not(:disabled) { background: rgba(224, 72, 79, 0.1); }
</style>
