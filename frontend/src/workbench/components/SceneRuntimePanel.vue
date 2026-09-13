<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'
import { fsApi, runtimeApi, engineApi, playApi } from '../api'
import type { WebTemplates, WebExportResult } from '../api'
import { useWorkbench } from '../composables/workbench'

const { jumpToLine } = useWorkbench()
const open = ref(false)
const tab = ref<'play' | 'scene'>('play')

/* ---------------- 试玩器 ---------------- */
const iframeEl = ref<HTMLIFrameElement | null>(null)
const iframeUrl = ref('')
const iframeNonce = ref(0)
const exporting = ref(false)
const exportMsg = ref('')
const lastExport = ref<WebExportResult | null>(null)
const tpl = ref<WebTemplates | null>(null)
const installing = ref(false)
const nativeRunning = ref(false)

async function refreshTemplates() {
  try { tpl.value = await playApi.templates() } catch { /* 忽略 */ }
}

async function doExport() {
  exporting.value = true
  exportMsg.value = '正在导出 Web 版本（首次约 30–90 秒，含脚本编译）…'
  try {
    const r = await playApi.exportWeb()
    lastExport.value = r
    if (r.ok && r.url) {
      iframeUrl.value = r.url
      iframeNonce.value += 1
      const injected = r.html_injected ? '，通信桥已注入' : ''
      exportMsg.value = `导出成功（${r.elapsed}s${injected}），试玩已加载`
    } else if (r.error === 'missing_templates') {
      exportMsg.value = '缺少 Godot Web 导出模板，请先安装（右侧/下方按钮）。'
      tpl.value = r.templates ?? tpl.value
    } else {
      exportMsg.value = '导出失败：' + (r.output?.trim().slice(-500) || r.message || r.error || '未知错误')
    }
  } catch (e) {
    exportMsg.value = (e as Error).message
  } finally {
    exporting.value = false
  }
}

async function doInstallTemplates() {
  if (!window.confirm('将从 Godot 官方 GitHub 下载导出模板包（约 600–800MB，仅提取 Web 模板约 60MB 落盘），是否继续？')) return
  installing.value = true
  exportMsg.value = '模板下载已开始…'
  try {
    await playApi.installTemplates()
    pollInstall()
  } catch (e) {
    installing.value = false
    exportMsg.value = '安装启动失败：' + (e as Error).message
  }
}

async function pollInstall() {
  await refreshTemplates()
  const st = tpl.value?.install
  if (st && ['starting', 'downloading', 'extracting'].includes(st.state)) {
    window.setTimeout(pollInstall, 1500)
    return
  }
  installing.value = false
  if (st?.state === 'done') exportMsg.value = '模板安装完成，点击「导出并试玩」。'
  else if (st?.state === 'error') exportMsg.value = '模板安装失败：' + st.error
}

function reloadFrame() { iframeNonce.value += 1 }
function openExternal() { if (iframeUrl.value) window.open(iframeUrl.value, '_blank') }
function cmdReload() {
  iframeEl.value?.contentWindow?.postMessage({ source: 'docmind-cmd', cmd: 'reload' }, '*')
}
async function refreshNativeStatus() {
  try { nativeRunning.value = (await engineApi.status()).running } catch { /* ignore */ }
}
async function nativeStart() {
  try {
    const r = await engineApi.start('godot', false)
    nativeRunning.value = !!r.running
    exportMsg.value = r.running ? '桌面游戏窗口已启动（事件将进入时间线）' : '启动失败'
  } catch (e) { exportMsg.value = (e as Error).message }
}
async function nativeStop() {
  await engineApi.stop()
  nativeRunning.value = false
}

function frameSrc() {
  return iframeUrl.value ? `${iframeUrl.value}?v=${iframeNonce.value}` : ''
}

const installPct = (st?: WebTemplates['install']) => {
  if (!st || !st.total) return 0
  return Math.min(100, Math.round((st.downloaded / st.total) * 100))
}
const fmtMB = (n: number) => `${(n / 1048576).toFixed(1)} MB`

/* ---------------- 事件时间线 ---------------- */
interface Ev { key: string; type: string; source: string; timestamp: string; data: Record<string, unknown>; eid: string }
const events = ref<Ev[]>([])
const seen = new Set<string>()

function evKey(e: Record<string, unknown>) {
  return String((e.eid as string) || (e.id as string) || '')
}
function upsert(e: Record<string, unknown>) {
  const key = evKey(e)
  if (!key || seen.has(key)) return
  seen.add(key)
  events.value.push({
    key,
    type: String(e.type ?? 'event'),
    source: String(e.source ?? ''),
    timestamp: String(e.timestamp ?? ''),
    data: (e.data ?? {}) as Record<string, unknown>,
    eid: String(e.eid ?? ''),
  })
  if (events.value.length > 300) {
    const drop = events.value.splice(0, events.value.length - 300)
    drop.forEach(d => seen.delete(d.key))
  }
}

function onWindowMessage(ev: MessageEvent) {
  const d = ev.data
  if (!d || d.source !== 'docmind-runtime') return
  const eid = String(d.eid ?? '')
  upsert({ eid, type: d.type, data: d.data ?? {}, timestamp: new Date().toISOString(), source: 'web' })
  runtimeApi.append([{ eid, type: d.type, data: d.data ?? {} }]).catch(() => {})
}

let pollEvTimer: number | undefined
async function pollEvents() {
  try {
    const r = await runtimeApi.events()
    ;(r.events || []).forEach(upsert)
  } catch { /* ignore */ }
}
function startTimers() {
  void pollEvents()
  pollEvTimer = window.setInterval(pollEvents, 3000)
}
function stopTimers() {
  if (pollEvTimer) window.clearInterval(pollEvTimer)
  pollEvTimer = undefined
}

function evClass(t: string) {
  if (t.startsWith('__')) return 'sys'
  if (/damage|hit|defeat|die|death/i.test(t)) return 'bad'
  if (/ready|spawn|start|heal/i.test(t)) return 'good'
  return 'mid'
}
function evTime(e: Ev) {
  if (!e.timestamp) return ''
  const d = new Date(e.timestamp)
  return isNaN(d.getTime()) ? e.timestamp.slice(11, 19) : d.toLocaleTimeString('zh-CN', { hour12: false })
}
function evData(e: Ev) {
  const keys = Object.keys(e.data || {})
  if (!keys.length) return ''
  return keys.map(k => `${k}=${String((e.data as Record<string, unknown>)[k])}`).join('  ')
}
function clearEvents() {
  events.value = []
  seen.clear()
}

/* ---------------- 场景检查（原有功能，搬到第二 tab） ---------------- */
const path = ref('')
const nodes = ref<{ name: string; type: string; parent: string; line: number; script?: string; properties?: { name: string; value: string; line: number }[]; x?: number; y?: number }[]>([])
const message = ref('')
const selected = ref<typeof nodes.value[number] | null>(null)
async function loadScene() {
  try {
    const r = await fsApi.sceneTree(path.value)
    nodes.value = (r.nodes as typeof nodes.value).map((n, i) => ({ ...n, x: 40 + (i % 4) * 120, y: 35 + Math.floor(i / 4) * 70 }))
    message.value = `已加载 ${nodes.value.length} 个节点`
  } catch (e) { message.value = (e as Error).message }
}
async function savePosition(n: typeof nodes.value[number]) {
  await fsApi.setSceneProperty(path.value, n.name, 'position', `Vector2(${Math.round(n.x || 0)}, ${Math.round(n.y || 0)})`)
  message.value = `已保存 ${n.name} 位置`
}
function dragStart(ev: PointerEvent, n: typeof nodes.value[number]) {
  selected.value = n
  const x = ev.clientX, y = ev.clientY, ox = n.x || 0, oy = n.y || 0
  const move = (e: PointerEvent) => { n.x = ox + e.clientX - x; n.y = oy + e.clientY - y }
  const up = () => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    void savePosition(n)
  }
  window.addEventListener('pointermove', move)
  window.addEventListener('pointerup', up)
}

watch(open, v => {
  if (v) {
    void refreshTemplates()
    void refreshNativeStatus()
    startTimers()
  } else {
    stopTimers()
  }
})

onMounted(() => window.addEventListener('message', onWindowMessage))
onUnmounted(() => {
  window.removeEventListener('message', onWindowMessage)
  stopTimers()
})
</script>

<template>
  <div class="sr-panel">
    <button class="sr-trigger" @click="open = true">▶ 试玩器</button>
    <template v-if="open">
      <div class="pb-mask" @click.self="open = false" />
      <div class="pb-pop">
        <div class="pb-head">
          <div class="pb-tabs">
            <button :class="{ on: tab === 'play' }" @click="tab = 'play'">🎮 Web 试玩</button>
            <button :class="{ on: tab === 'scene' }" @click="tab = 'scene'">场景检查</button>
          </div>
          <button class="pb-x" @click="open = false">×</button>
        </div>

        <!-- ================= 试玩 tab ================= -->
        <div v-if="tab === 'play'" class="pb-body">
          <div class="pb-main">
            <div class="pb-toolbar">
              <button class="pb-btn primary" :disabled="exporting" @click="doExport">
                {{ exporting ? '导出中…' : (iframeUrl ? '重新导出并试玩' : '导出并试玩') }}
              </button>
              <button class="pb-btn" :disabled="!iframeUrl" @click="cmdReload" title="热重载游戏场景（不刷新页面）">↻ 重载场景</button>
              <button class="pb-btn" :disabled="!iframeUrl" @click="reloadFrame">刷新框架</button>
              <button class="pb-btn" :disabled="!iframeUrl" @click="openExternal">新标签打开</button>
              <span class="pb-sep" />
              <button v-if="!nativeRunning" class="pb-btn" @click="nativeStart">🖥 桌面窗口启动</button>
              <button v-else class="pb-btn warn" @click="nativeStop">停止桌面窗口</button>
            </div>

            <div v-if="exportMsg" class="pb-msg" :class="{ err: exportMsg.includes('失败') }">{{ exportMsg }}</div>

            <div v-if="tpl && !tpl.installed && !iframeUrl" class="pb-notpl">
              <b>未检测到 Godot Web 导出模板</b>
              <span>引擎版本：{{ tpl.version }}<br />目录：{{ tpl.template_dir }}</span>
              <div v-if="installing && tpl.install" class="pb-dl">
                <div class="pb-dl-bar"><i :style="{ width: installPct(tpl.install) + '%' }" /></div>
                <small>
                  {{ tpl.install.state === 'extracting' ? '正在解压提取 Web 模板…' : `下载中 ${fmtMB(tpl.install.downloaded)} / ${fmtMB(tpl.install.total)}（${installPct(tpl.install)}%）` }}<template v-if="tpl.install.mirror"> · 源：{{ tpl.install.mirror.replace(/^https?:\/\//, '') }}</template>
                </small>
              </div>
              <button class="pb-btn primary" :disabled="installing" @click="doInstallTemplates">
                {{ installing ? '安装中…' : '下载并安装 Web 模板' }}
              </button>
              <small>也可先用右侧「桌面窗口启动」走原生试玩（无需模板）。</small>
            </div>

            <div v-else-if="tpl?.install && ['downloading','extracting','starting'].includes(tpl.install.state)" class="pb-notpl">
              <b>模板安装中</b>
              <div class="pb-dl">
                <div class="pb-dl-bar"><i :style="{ width: installPct(tpl.install) + '%' }" /></div>
                <small>{{ tpl.install.state === 'extracting' ? '解压中…' : `${fmtMB(tpl.install.downloaded)} / ${fmtMB(tpl.install.total)}` }}<template v-if="tpl.install.mirror"> · {{ tpl.install.mirror.replace(/^https?:\/\//, '') }}</template></small>
              </div>
            </div>

            <div class="pb-framewrap">
              <iframe
                v-if="iframeUrl"
                ref="iframeEl"
                :src="frameSrc()"
                class="pb-frame"
                allow="autoplay; fullscreen; gamepad"
              />
              <div v-else class="pb-empty">
                点击「导出并试玩」<br />
                <small>Godot 工程将导出为 WebAssembly 并在此画布内运行，支持边玩边让 AI 修改代码</small>
              </div>
            </div>
          </div>

          <div class="pb-side">
            <div class="pb-side-head">
              <b>运行时事件时间线</b>
              <div><span class="pb-live">● LIVE</span><button class="pb-link" @click="clearEvents">清空</button><button class="pb-link" @click="pollEvents">刷新</button></div>
            </div>
            <div class="pb-tl">
              <div v-if="!events.length" class="pb-tl-empty">暂无事件。启动游戏后，掉血/死亡等事件会实时出现在这里。</div>
              <div v-for="e in [...events].reverse()" :key="e.key" class="pb-ev" :class="evClass(e.type)">
                <span class="pb-ev-time">{{ evTime(e) }}</span>
                <span class="pb-ev-type">{{ e.type }}</span>
                <span class="pb-ev-src">{{ e.source }}</span>
                <span v-if="evData(e)" class="pb-ev-data">{{ evData(e) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- ================= 场景检查 tab ================= -->
        <div v-else class="pb-body">
          <div class="pb-main">
            <div class="pb-toolbar">
              <input v-model="path" class="pb-path" placeholder="scenes/Main.tscn" />
              <button class="pb-btn primary" @click="loadScene">加载场景树</button>
              <button class="pb-btn" @click="pollEvents">刷新事件</button>
            </div>
            <div v-if="message" class="pb-msg">{{ message }}</div>
            <div v-if="nodes.length" class="sr-canvas">
              <div
                v-for="n in nodes"
                :key="`${n.line}-${n.name}`"
                class="sr-card"
                :class="{ sel: selected?.name === n.name }"
                :style="{ left: `${n.x}px`, top: `${n.y}px` }"
                @pointerdown="dragStart($event, n)"
                @click="selected = n"
              >
                <b>◈ {{ n.name }}</b><small>{{ n.type }}</small>
              </div>
            </div>
            <div v-if="selected" class="sr-inspector">
              <b>{{ selected.name }} 属性</b>
              <div v-for="p in selected.properties || []" :key="p.name">
                <span>{{ p.name }}</span>
                <input v-model="p.value" @change="fsApi.setSceneProperty(path, selected!.name, p.name, p.value)" />
              </div>
              <button v-if="selected.script" @click="jumpToLine(selected!.script!, selected!.line)">打开脚本 {{ selected.script }}</button>
            </div>
          </div>
          <div class="pb-side">
            <div class="pb-side-head"><b>运行时事件</b><button class="pb-link" @click="pollEvents">刷新</button></div>
            <div class="pb-tl">
              <div v-for="e in [...events].reverse()" :key="e.key" class="pb-ev" :class="evClass(e.type)">
                <span class="pb-ev-time">{{ evTime(e) }}</span>
                <span class="pb-ev-type">{{ e.type }}</span>
                <span v-if="evData(e)" class="pb-ev-data">{{ evData(e) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sr-panel { position: relative; }
.sr-trigger { border: 1px solid var(--border-strong); background: transparent; color: var(--text-muted); border-radius: 5px; padding: 5px 9px; cursor: pointer; }
.sr-trigger:hover { color: var(--text); border-color: #315c86; }

.pb-mask { position: fixed; inset: 0; background: #0009; z-index: 65; }
.pb-pop { position: fixed; z-index: 66; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 1140px; max-width: 94vw; height: 760px; max-height: 90vh; background: var(--bg-raised); border: 1px solid var(--border-strong); border-radius: 10px; box-shadow: 0 24px 70px #000c; display: flex; flex-direction: column; overflow: hidden; }
.pb-head { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.pb-tabs { display: flex; gap: 6px; }
.pb-tabs button { background: transparent; border: 1px solid transparent; color: var(--text-muted); padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.pb-tabs button.on { background: #17304b; border-color: #315c86; color: #b9d8f5; }
.pb-x { background: none; border: 0; color: var(--text-muted); font-size: 18px; cursor: pointer; }
.pb-body { flex: 1; display: flex; min-height: 0; }
.pb-main { flex: 1; display: flex; flex-direction: column; min-width: 0; padding: 10px 12px; gap: 8px; }
.pb-side { width: 320px; border-left: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
.pb-side-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; }
.pb-side-head > div { display: flex; align-items: center; gap: 8px; }
.pb-live { color: #e06060; font-size: 10px; letter-spacing: 1px; }
.pb-link { background: none; border: 0; color: var(--text-muted); font-size: 11px; cursor: pointer; }
.pb-link:hover { color: var(--text); }

.pb-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pb-sep { width: 1px; height: 20px; background: var(--border); margin: 0 4px; }
.pb-btn { border: 1px solid var(--border-strong); background: transparent; color: var(--text-muted); border-radius: 5px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
.pb-btn:hover:not(:disabled) { color: var(--text); border-color: #315c86; }
.pb-btn:disabled { opacity: .4; cursor: default; }
.pb-btn.primary { background: #17304b; border-color: #315c86; color: #b9d8f5; }
.pb-btn.warn { border-color: #7a3a3a; color: #e8a0a0; }
.pb-path { flex: 1; min-width: 200px; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 6px 8px; border-radius: 5px; font-size: 12px; }

.pb-msg { font-size: 11px; color: var(--green); background: #0f1a14; border: 1px solid #244d33; border-radius: 5px; padding: 6px 9px; max-height: 90px; overflow: auto; white-space: pre-wrap; }
.pb-msg.err { color: #e8a0a0; background: #241414; border-color: #6a2f2f; }

.pb-notpl { margin-top: 4px; border: 1px dashed #5a5030; background: #1c1a12; border-radius: 7px; padding: 14px; display: flex; flex-direction: column; gap: 9px; font-size: 12px; color: var(--text-muted); }
.pb-notpl b { color: #e8cf8a; font-size: 13px; }
.pb-notpl small { color: var(--text-faint); line-height: 1.6; }
.pb-dl { display: flex; flex-direction: column; gap: 4px; }
.pb-dl-bar { height: 7px; border-radius: 4px; background: #0008; overflow: hidden; }
.pb-dl-bar i { display: block; height: 100%; background: linear-gradient(90deg, #3a7bd5, #5ea0ff); transition: width .3s; }

.pb-framewrap { flex: 1; min-height: 0; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; background: #000; position: relative; }
.pb-frame { width: 100%; height: 100%; border: 0; display: block; background: #000; }
.pb-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-faint); font-size: 14px; text-align: center; padding: 30px; line-height: 1.8; }
.pb-empty small { font-size: 11px; max-width: 380px; }

.pb-tl { flex: 1; overflow: auto; padding: 4px 0; font: 11px var(--font-mono); }
.pb-tl-empty { padding: 18px 12px; color: var(--text-faint); font-family: var(--font-base, inherit); line-height: 1.7; }
.pb-ev { display: flex; align-items: baseline; gap: 7px; padding: 4px 10px; border-bottom: 1px solid #ffffff08; }
.pb-ev-time { color: var(--text-faint); flex: 0 0 62px; }
.pb-ev-type { font-weight: 700; }
.pb-ev-src { color: var(--text-faint); font-size: 9px; border: 1px solid var(--border); border-radius: 3px; padding: 0 4px; }
.pb-ev-data { color: var(--text-muted); }
.pb-ev.good .pb-ev-type { color: #7ed492; }
.pb-ev.bad .pb-ev-type { color: #f08a8a; }
.pb-ev.mid .pb-ev-type { color: #8fc1f0; }
.pb-ev.sys .pb-ev-type { color: var(--text-faint); }

.sr-canvas { flex: 1; position: relative; border: 1px solid var(--border); border-radius: 6px; margin-top: 6px; background: repeating-linear-gradient(0deg, transparent 0 34px, #ffffff08 35px), repeating-linear-gradient(90deg, transparent 0 59px, #ffffff08 60px); min-height: 300px; }
.sr-card { position: absolute; width: 105px; padding: 7px; background: #142235; border: 1px solid #315c86; border-radius: 5px; cursor: grab; font-size: 11px; }
.sr-card.sel { border-color: #e8c267; box-shadow: 0 0 0 1px #e8c26755; }
.sr-card small { display: block; color: var(--text-faint); margin-top: 4px; }
.sr-inspector { margin-top: 7px; padding: 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 11px; max-height: 200px; overflow: auto; }
.sr-inspector div { display: flex; gap: 5px; margin-top: 5px; align-items: center; }
.sr-inspector span { width: 110px; color: var(--text-muted); }
.sr-inspector input { flex: 1; background: var(--bg); border: 1px solid var(--border); color: var(--text); font-size: 10px; padding: 3px 5px; border-radius: 4px; }
.sr-inspector button { margin-top: 7px; background: #17304b; border: 1px solid #315c86; color: #b9d8f5; border-radius: 4px; padding: 4px 8px; cursor: pointer; font-size: 11px; }
</style>
