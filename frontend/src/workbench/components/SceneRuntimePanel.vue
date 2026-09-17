<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted, defineAsyncComponent } from 'vue'
import { runtimeApi, engineApi, playApi, sceneApi, getProjectId } from '../api'
import type { WebTemplates, WebExportResult, DesktopHost, EmbedRect } from '../api'
import { useWorkbench } from '../composables/workbench'
// 画布与时间线都引了重依赖（@vue-flow 约 243KB / gzip 79KB），
// 用异步组件延迟到真正切到对应 tab 再加载，工作台首屏体积不受影响。
const SceneCanvas = defineAsyncComponent(() => import('./SceneCanvas.vue'))
const RuntimeTimeline = defineAsyncComponent(() => import('./RuntimeTimeline.vue'))

const { jumpToLine, openPath, activeTab, runtimeOpen, runtimeTab } = useWorkbench()
const open = ref(false)
const tab = ref<'play' | 'scene' | 'timeline'>('play')

// 顶层「画布 / 运行」tab 与弹窗联动：外部（WorkspaceTabs）请求打开时同步到本地状态，
// 本地关闭/切 tab 也写回共享态，保证顶层 tab 高亮与实际面板一致。
watch(runtimeOpen, v => { if (open.value !== v) open.value = v })
watch(runtimeTab, v => { if (tab.value !== v) tab.value = v })
watch(open, v => { if (runtimeOpen.value !== v) runtimeOpen.value = v })

/* ---------------- 试玩器 ---------------- */
const iframeEl = ref<HTMLIFrameElement | null>(null)
const iframeUrl = ref('')
const iframeNonce = ref(0)
const exporting = ref(false)
const exportMsg = ref('')
const lastExport = ref<WebExportResult | null>(null)
const tpl = ref<WebTemplates | null>(null)
const installing = ref(false)

/* ---- 原生引擎：独立窗口 / 嵌入工作台 ----
   嵌入的意义是"游戏跑在工作台窗口里"，边玩边让 AI 改代码；
   独立窗口模式下 Web 工作台和游戏窗口是两个窗口，来回切很别扭。 */
const nativeRunning = ref(false)
const desktop = ref<DesktopHost | null>(null)
const embedState = ref<'off' | 'embedded' | 'failed'>('off')
const embedMsg = ref('')
const engineViewport = ref<HTMLElement | null>(null)
// 默认开：桌面端启动时"游戏嵌进工作台"才是预期行为；浏览器模式下会自动禁用并提示
const autoEmbed = ref(localStorage.getItem('docmind.autoEmbed') !== '0')
let lastRect = ''

watch(autoEmbed, v => localStorage.setItem('docmind.autoEmbed', v ? '1' : '0'))

const desktopReady = computed(() => !!desktop.value?.desktop)
const hostDpi = computed(() => desktop.value?.dpi || 96)

async function refreshDesktop() {
  try { desktop.value = await engineApi.host() } catch { desktop.value = null }
}

/**
 * 把"引擎视窗"元素换算成宿主客户区的物理像素矩形。
 *
 * 比例用 宿主客户区宽 / 页面视口宽 求，而不是直接用 devicePixelRatio：
 * 窗口缩放、WebView 缩放、多屏不同 DPI 混用时 DPR 并不保证等于这个比例，
 * 而比例一错引擎窗口就整体错位（150% 缩放下尤其明显）。
 */
function viewportRect(): EmbedRect | null {
  const el = engineViewport.value
  if (!el) return null
  const box = el.getBoundingClientRect()
  if (box.width < 40 || box.height < 40) return null
  const clientW = desktop.value?.client?.width
  const scale = clientW && window.innerWidth > 0
    ? clientW / window.innerWidth
    : (window.devicePixelRatio || 1)
  return {
    x: Math.round(box.left * scale),
    y: Math.round(box.top * scale),
    width: Math.round(box.width * scale),
    height: Math.round(box.height * scale),
  }
}

/** 布局变化后同步引擎视窗；矩形没变就不重复请求。 */
async function syncEngineRect(force = false) {
  if (embedState.value !== 'embedded') return
  const rect = viewportRect()
  if (!rect) return
  const key = `${rect.x},${rect.y},${rect.width},${rect.height}`
  if (!force && key === lastRect) return
  lastRect = key
  try { await engineApi.place(rect) } catch { /* 下一次布局变化会对齐 */ }
}

let resizeTimer: number | undefined
function onWindowResize() {
  if (resizeTimer) window.clearTimeout(resizeTimer)
  resizeTimer = window.setTimeout(async () => {
    await refreshDesktop()      // 客户区变了，比例要重算
    await syncEngineRect()
  }, 120)
}

// DPI 变化（拖到不同缩放的显示器 / WebView 缩放）在窗口尺寸不变时不会触发 resize，
// 但宿主客户区物理像素会变 → 嵌入比例要重算、引擎视窗要重新 place，否则画面错位。
// 用 matchMedia(resolution) 精确捕获 DPI 跳变（每次变化需重建监听，因为查询串含当前 DPI）。
let dpiMq: MediaQueryList | null = null
function onDpiChange() {
  void onWindowResize()
  watchDpi()
}
function watchDpi() {
  dpiMq?.removeEventListener('change', onDpiChange)
  dpiMq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`)
  dpiMq.addEventListener('change', onDpiChange)
}

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
  try {
    const st = await engineApi.status()
    nativeRunning.value = !!st.running
    // 刷新页面后要如实反映"现在是不是嵌着的"，不能只认本地变量
    embedState.value = st.embedded ? 'embedded' : 'off'
    if (st.embedded && st.embed_dpi) embedMsg.value = `已嵌入工作台（${st.host_dpi || st.embed_dpi} DPI 宿主）`
  } catch { /* ignore */ }
}

async function nativeStart() {
  exportMsg.value = ''
  embedMsg.value = ''
  const rect = autoEmbed.value && desktopReady.value ? viewportRect() : null
  try {
    const r = await engineApi.start('godot', !!rect, rect)
    nativeRunning.value = !!r.running
    if (!r.running) { exportMsg.value = r.error || '启动失败'; return }
    if (rect && r.embedded) {
      embedState.value = 'embedded'
      lastRect = `${rect.x},${rect.y},${rect.width},${rect.height}`
      const size = r.embed?.width && r.embed?.height ? `${r.embed.width}×${r.embed.height}` : '按视窗'
      embedMsg.value = `已嵌入工作台（${size}，宿主 ${r.embed?.host_dpi || hostDpi.value} DPI）`
      await engineApi.focusEngine(true).catch(() => {})
    } else if (rect) {
      embedState.value = 'failed'
      embedMsg.value = '引擎已启动但嵌入失败：' + (r.embed_error || '未知原因') + '（已退化为独立窗口）'
    } else {
      embedState.value = 'off'
      exportMsg.value = desktopReady.value
        ? '桌面游戏窗口已启动（独立窗口）'
        : '桌面游戏窗口已启动；浏览器模式下无法嵌入工作台'
    }
  } catch (e) { exportMsg.value = (e as Error).message }
}

async function nativeStop() {
  await engineApi.stop().catch(() => { /* 停不掉也要把界面状态归位 */ })
  nativeRunning.value = false
  embedState.value = 'off'
  embedMsg.value = ''
  lastRect = ''
}

async function nativeDetach() {
  try {
    const r = await engineApi.detach()
    embedState.value = 'off'
    lastRect = ''
    embedMsg.value = ''
    exportMsg.value = r.was_embedded ? '已解除嵌入，引擎回到独立窗口（进程仍在运行）' : '引擎当前未嵌入'
  } catch (e) { embedMsg.value = (e as Error).message }
}

async function nativeEmbed() {
  // 仅把已运行的引擎重新嵌进工作台（不重启进程）：切走 play tab 被 detach 后回来用。
  if (!desktopReady.value) return
  const rect = autoEmbed.value ? viewportRect() : null
  if (!rect) return
  try {
    const r = await engineApi.embed(rect)
    if (r.ok && r.embedded) {
      embedState.value = 'embedded'
      await syncEngineRect(true)
      embedMsg.value = `已嵌入工作台（${r.width || '?'}×${r.height || '?'}）`
      await engineApi.focusEngine(true).catch(() => {})
    } else {
      embedState.value = 'off'
      embedMsg.value = (r as { error?: string }).error || '重新嵌入未完成'
    }
  } catch (e) { embedMsg.value = (e as Error).message }
}

function onViewportFocus() {
  // 点引擎视窗区主动把键盘焦点交还游戏（最佳努力；引擎子窗口在顶层时系统点击也会自动聚焦）。
  if (embedState.value === 'embedded') void engineApi.focusEngine().catch(() => {})
}

async function nativeFocus() {
  try {
    const r = await engineApi.focusEngine()
    embedMsg.value = r.ok ? '已把键盘焦点交给游戏窗口' : ('聚焦失败：' + (r.error || '系统拒绝设置前台窗口'))
  } catch (e) { embedMsg.value = (e as Error).message }
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
const showHistory = ref(false)
const captureSince = ref(Date.now())
const eventStatus = ref('等待事件')
const visibleEvents = computed(() => events.value.filter(e => showHistory.value || Date.parse(e.timestamp) >= captureSince.value))
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
  if (ev.source !== iframeEl.value?.contentWindow || ev.origin !== window.location.origin) return
  const d = ev.data
  if (!d || d.source !== 'docmind-runtime') return
  const eid = String(d.eid ?? '')
  eventStatus.value = '收到试玩事件'
  upsert({ eid, type: d.type, data: d.data ?? {}, timestamp: new Date().toISOString(), source: 'web' })
  runtimeApi.append([{ eid, type: d.type, data: d.data ?? {} }]).catch(() => {})
}

let pollEvTimer: number | undefined
async function pollEvents() {
  const project = getProjectId()
  try {
    const r = await runtimeApi.events()
    if (project !== getProjectId()) return
    ;(r.events || []).forEach(upsert)
    eventStatus.value = '已同步 · 仅显示实际收到的事件'
  } catch { eventStatus.value = '事件服务未连接' }
}
function startTimers() {
  stopTimers()
  captureSince.value = Date.now()
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
async function clearEvents() {
  // 只清本地数组的话，下一次轮询会把后端存量事件又拉回来（"清空后点一下又出来"）。
  // 必须同时清后端：scope='all' 连引擎日志的续读游标一起归零，否则 godot 抓取的事件照样复现。
  if (!window.confirm('清空全部运行时事件（含已抓取的引擎日志）？此操作不可撤销。')) return
  try { await runtimeApi.clear('all') } catch { eventStatus.value = '清空失败，请重试'; return }
  events.value = []
  seen.clear()
}

/* ---------------- 场景画布（P0-2：Vue Flow 转正） ---------------- */
// pathInput 是正在输入的路径，scenePath 是「已提交、值得去解析」的路径——
// 分开是为了避免每敲一个字符就触发一次后端解析。
const pathInput = ref('')
const scenePath = ref('')
const sceneMessage = ref('')
function loadScene() {
  const value = pathInput.value.trim()
  if (!value) { sceneMessage.value = '请先填写场景路径，例如 scenes/Main.tscn'; return }
  sceneMessage.value = ''
  scenePath.value = value
}
/** 打开「场景画布」时若还没指定场景，自动加载项目主场景（project.godot 的 run/main_scene），
 *  避免出现"一块空画布、不知道该填什么"的困惑。用户已有输入/已加载则不覆盖。 */
async function ensureSceneLoaded() {
  if (scenePath.value || pathInput.value.trim()) return
  try {
    const r = await sceneApi.main()
    if (r.ok && r.scene) {
      pathInput.value = r.scene
      sceneMessage.value = ''
      scenePath.value = r.scene
    }
  } catch { /* 拿不到主场景就保持空态提示，不打扰用户 */ }
}
/** 编辑器里打开 .tscn 时自动同步到画布——点开场景就能看结构，不用再手抄路径 */
watch(() => activeTab.value?.path, (next) => {
  if (next && next.toLowerCase().endsWith('.tscn')) {
    pathInput.value = next
    sceneMessage.value = ''
    scenePath.value = next
  }
})

/** 画布/时间线里双击文件卡 -> 在工作台编辑器打开（带行号时定位） */
function openFromScene(rel: string, line?: number) {
  if (line && line > 0) void jumpToLine(rel, line)
  else void openPath(rel)
}

watch(open, v => {
  if (v) {
    void refreshTemplates()
    void refreshNativeStatus()
    void refreshDesktop()
    startTimers()
    if (tab.value === 'scene') void ensureSceneLoaded()
  } else {
    stopTimers()
    // 弹窗一关，那块"引擎视窗"就不存在了；继续嵌着只会让引擎画在工作台别的位置上
    if (embedState.value === 'embedded') void nativeDetach()
  }
})

// 切走试玩 tab 同理：视窗元素被 v-if 摘掉，必须解除；切回来若引擎还在跑且开着自动嵌入，自动重新嵌回
watch(tab, v => {
  if (v !== 'play') {
    if (embedState.value === 'embedded') void nativeDetach()
  } else if (nativeRunning.value && autoEmbed.value && embedState.value !== 'embedded' && desktopReady.value) {
    void nativeEmbed()
  }
  // 切到场景画布且还没加载场景时，自动带出项目主场景
  if (v === 'scene') void ensureSceneLoaded()
})

function resetProject() {
  stopTimers()
  events.value = []
  seen.clear()
  showHistory.value = false
  eventStatus.value = '等待事件'
  iframeUrl.value = ''
  lastExport.value = null
  exportMsg.value = ''
  scenePath.value = ''
  pathInput.value = ''
  sceneMessage.value = ''
  nativeRunning.value = false
}
onMounted(() => {
  window.addEventListener('docmind:project-changed', resetProject)
  window.addEventListener('message', onWindowMessage)
  window.addEventListener('resize', onWindowResize)
  watchDpi()
})
onUnmounted(() => {
  window.removeEventListener('docmind:project-changed', resetProject)
  window.removeEventListener('message', onWindowMessage)
  window.removeEventListener('resize', onWindowResize)
  dpiMq?.removeEventListener('change', onDpiChange)
  dpiMq = null
  if (resizeTimer) window.clearTimeout(resizeTimer)
  stopTimers()
})
</script>

<template>
  <div class="sr-panel">
    <button class="sr-trigger" title="一键导出并在工作台里运行游戏，边玩边看日志和场景" @click="open = true">▶ 运行游戏</button>
    <template v-if="open">
      <div class="pb-mask" @click.self="open = false" />
      <div class="pb-pop">
        <div class="pb-head">
          <div class="pb-tabs">
            <button :class="{ on: tab === 'play' }" @click="tab = 'play'">🎮 Web 试玩</button>
            <button :class="{ on: tab === 'scene' }" @click="tab = 'scene'">🗺 场景画布</button>
            <button :class="{ on: tab === 'timeline' }" @click="tab = 'timeline'">⏱ 运行时时间线</button>
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
              <label
                class="pb-check"
                :class="{ off: !desktopReady }"
                :title="desktopReady
                  ? '嵌入后游戏画面直接跑在这块区域里，工作台界面照常可用'
                  : '浏览器模式下无法嵌入原生窗口，请用桌面端启动 DocMind（desktop.py / 桌面快捷方式）'"
              >
                <input v-model="autoEmbed" type="checkbox" :disabled="!desktopReady" />
                嵌入工作台
              </label>
              <button v-if="!nativeRunning" class="pb-btn" @click="nativeStart">🖥 桌面窗口启动</button>
              <template v-else>
                <span class="pb-chip" :class="embedState">{{ embedState === 'embedded' ? '已嵌入' : embedState === 'failed' ? '嵌入失败' : '独立窗口' }}</span>
                <button v-if="embedState === 'embedded'" class="pb-btn" title="把键盘焦点交给游戏窗口（点过工作台之后要还回去）" @click="nativeFocus">聚焦</button>
                <button v-if="embedState === 'embedded'" class="pb-btn" title="引擎回到独立窗口，进程继续运行" @click="nativeDetach">解除嵌入</button>
                <button class="pb-btn warn" @click="nativeStop">停止桌面窗口</button>
              </template>
            </div>

            <div v-if="exportMsg" class="pb-msg" :class="{ err: exportMsg.includes('失败') }">{{ exportMsg }}</div>
            <div v-if="embedMsg" class="pb-msg" :class="{ err: embedState === 'failed' }">{{ embedMsg }}</div>
            <div v-if="!desktopReady" class="pb-hintline">
              当前是浏览器模式：原生引擎只能开独立窗口。用桌面端启动 DocMind 后，勾上「嵌入工作台」即可让游戏跑在这块区域里。
            </div>

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

            <!-- 原生引擎嵌入时，Window 子窗口会盖在这一块矩形上（ref 用于算它的坐标） -->
            <div ref="engineViewport" class="pb-framewrap" @mousedown="onViewportFocus">
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
              <div><span>{{ eventStatus }}</span><label><input v-model="showHistory" type="checkbox" /> 历史事件</label><button class="pb-link" @click="clearEvents">清空</button><button class="pb-link" @click="pollEvents">刷新</button></div>
            </div>
            <div class="pb-tl">
              <div v-if="!visibleEvents.length" class="pb-tl-empty">本次尚未收到事件。游戏需接入运行时通信桥并主动上报；可勾选历史事件查看此前记录。</div>
              <div v-for="e in [...visibleEvents].reverse()" :key="e.key" class="pb-ev" :class="evClass(e.type)" :title="e.timestamp">
                <span class="pb-ev-time">{{ evTime(e) }}</span>
                <span class="pb-ev-type">{{ e.type }}</span>
                <span class="pb-ev-src">{{ e.source }}</span>
                <span v-if="evData(e)" class="pb-ev-data">{{ evData(e) }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- ================= 场景画布 tab（P0-2） ================= -->
        <div v-else-if="tab === 'scene'" class="pb-body">
          <div class="pb-main">
            <div class="pb-toolbar">
              <input v-model="pathInput" class="pb-path" placeholder="scenes/Main.tscn（支持任意 .tscn 路径）"
                     @keyup.enter="loadScene" />
              <button class="pb-btn primary" @click="loadScene">加载场景</button>
              <small class="pb-hint">在左侧文件树里点开 .tscn 会自动带到这里；画布上可改节点、属性与位置，全部可撤销。</small>
            </div>
            <div v-if="sceneMessage" class="pb-msg err">{{ sceneMessage }}</div>
            <SceneCanvas v-if="scenePath" :path="scenePath" @open-file="openFromScene" />
            <div v-else class="pb-empty2">
              填入场景路径并点「加载场景」<br />
              <small>也可在左侧文件树双击任意 .tscn 直接打开画布</small>
            </div>
          </div>
        </div>

        <!-- ================= 运行时时间线 tab（P1-1） ================= -->
        <div v-else class="pb-body">
          <div class="pb-main">
            <RuntimeTimeline @open-file="openFromScene" />
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.sr-panel { position: relative; }
.sr-trigger { border: 1px solid var(--border-strong); background: transparent; color: var(--text-muted); border-radius: 5px; padding: 5px 9px; cursor: pointer; }
.sr-trigger:hover { color: var(--text); border-color: #b9d0f5; }

.pb-mask { position: fixed; inset: 0; background: rgba(38, 52, 77, 0.38); z-index: 65; }
.pb-pop { position: fixed; z-index: 66; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 1360px; max-width: 96vw; height: 840px; max-height: 92vh; background: var(--bg-raised); border: 1px solid var(--border-strong); border-radius: 10px; box-shadow: 0 24px 70px rgba(35, 52, 84, 0.16); display: flex; flex-direction: column; overflow: hidden; }
.pb-head { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--border); }
.pb-tabs { display: flex; gap: 6px; }
.pb-tabs button { background: transparent; border: 1px solid transparent; color: var(--text-muted); padding: 5px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.pb-tabs button.on { background: linear-gradient(180deg, #3b7ef2, #2f6fed); border-color: #2560d4; color: #fff; }
.pb-x { background: none; border: 0; color: var(--text-muted); font-size: 18px; cursor: pointer; }
.pb-body { flex: 1; display: flex; min-height: 0; }
.pb-main { flex: 1; display: flex; flex-direction: column; min-width: 0; padding: 10px 12px; gap: 8px; }
.pb-side { width: 320px; border-left: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; }
.pb-side-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 10px; border-bottom: 1px solid var(--border); font-size: 12px; }
.pb-side-head > div { display: flex; align-items: center; gap: 8px; }
.pb-live { color: #c23a40; font-size: 10px; letter-spacing: 1px; }
.pb-link { background: none; border: 0; color: var(--text-muted); font-size: 11px; cursor: pointer; }
.pb-link:hover { color: var(--text); }

.pb-toolbar { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pb-sep { width: 1px; height: 20px; background: var(--border); margin: 0 4px; }
.pb-btn { border: 1px solid var(--border-strong); background: transparent; color: var(--text-muted); border-radius: 5px; padding: 5px 10px; font-size: 12px; cursor: pointer; }
.pb-btn:hover:not(:disabled) { color: var(--text); border-color: #b9d0f5; }
.pb-btn:disabled { opacity: .4; cursor: default; }
.pb-btn.primary { background: linear-gradient(180deg, #3b7ef2, #2f6fed); border-color: #2560d4; color: #fff; }
.pb-btn.warn { border-color: rgba(224, 72, 79, 0.5); color: #c23a40; }
.pb-path { flex: 1; min-width: 200px; background: var(--bg); border: 1px solid var(--border); color: var(--text); padding: 6px 8px; border-radius: 5px; font-size: 12px; }

.pb-msg { font-size: 11px; color: var(--green); background: #e9f7f0; border: 1px solid #bfe2d0; border-radius: 5px; padding: 6px 9px; max-height: 90px; overflow: auto; white-space: pre-wrap; }
.pb-msg.err { color: #c23a40; background: #fdecec; border-color: #eeb7ba; }

.pb-notpl { margin-top: 4px; border: 1px dashed #dfb067; background: #fdf7ea; border-radius: 7px; padding: 14px; display: flex; flex-direction: column; gap: 9px; font-size: 12px; color: var(--text-muted); }
.pb-notpl b { color: #8a5a16; font-size: 13px; }
.pb-notpl small { color: var(--text-faint); line-height: 1.6; }
.pb-dl { display: flex; flex-direction: column; gap: 4px; }
.pb-dl-bar { height: 7px; border-radius: 4px; background: rgba(35, 52, 84, 0.12); overflow: hidden; }
.pb-dl-bar i { display: block; height: 100%; background: linear-gradient(90deg, #2f6fed, #6f9cf5); transition: width .3s; }

.pb-framewrap { flex: 0 1 auto; width: min(100%, 860px); height: 480px; min-height: 260px; border: 1px solid var(--border); border-radius: 7px; overflow: hidden; background: #fcfdff; position: relative; }
.pb-frame { width: 100%; height: 100%; border: 0; display: block; background: #fcfdff; }
.pb-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 10px; color: var(--text-faint); font-size: 14px; text-align: center; padding: 30px; line-height: 1.8; }
.pb-empty small { font-size: 11px; max-width: 380px; }

.pb-tl { flex: 1; overflow: auto; padding: 4px 0; font: 11px var(--font-mono); }
.pb-tl-empty { padding: 18px 12px; color: var(--text-faint); font-family: var(--font-base, inherit); line-height: 1.7; }
.pb-ev { display: flex; align-items: baseline; gap: 7px; padding: 4px 10px; border-bottom: 1px solid rgba(35, 52, 84, 0.08); }
.pb-ev-time { color: var(--text-faint); flex: 0 0 62px; }
.pb-ev-type { font-weight: 700; }
.pb-ev-src { color: var(--text-faint); font-size: 9px; border: 1px solid var(--border); border-radius: 3px; padding: 0 4px; }
.pb-ev-data { color: var(--text-muted); }
.pb-ev.good .pb-ev-type { color: #1c9e66; }
.pb-ev.bad .pb-ev-type { color: #c23a40; }
.pb-ev.mid .pb-ev-type { color: #2f6fed; }
.pb-ev.sys .pb-ev-type { color: var(--text-faint); }

.pb-hint { color: var(--text-faint); font-size: 10.5px; }
.pb-check { display: flex; align-items: center; gap: 4px; font-size: 11px; color: var(--text-muted); cursor: pointer; white-space: nowrap; }
.pb-check.off { opacity: .5; cursor: not-allowed; }
.pb-chip { font-size: 10px; border: 1px solid var(--border-strong); border-radius: 4px; padding: 2px 6px; color: var(--text-muted); }
.pb-chip.embedded { color: #146c48; border-color: #8fd4b3; background: #e9f7f0; }
.pb-chip.failed { color: #c23a40; border-color: #eeb7ba; background: #fdecec; }
.pb-hintline { font-size: 10.5px; color: var(--text-faint); line-height: 1.7; }
.pb-empty2 { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--text-faint); border: 1px dashed var(--border); border-radius: 8px; text-align: center; line-height: 1.8; }
.pb-empty2 small { font-size: 11px; }
</style>
