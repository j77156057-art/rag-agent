<template>
  <div class="ag-panel">
    <!-- 就绪状态条 -->
    <div class="ag-status" :class="statusClass">
      <template v-if="!status">正在检测本地 ComfyUI…</template>
      <template v-else-if="status.online && status.image.ready && status.video.ready">
        <b>● 本地模型就绪</b>
        <span>Z-Image 生图 · MiniMax H3 图生视频 · ComfyUI 已连接</span>
      </template>
      <template v-else-if="status.online">
        <b>● ComfyUI 已连接，但模型不完整</b>
        <span v-if="status.image.missing.length">缺生图权重：{{ status.image.missing.join('、') }}</span>
        <span v-if="status.video.missing.length">缺视频链路：{{ status.video.missing.join('、') }}</span>
      </template>
      <template v-else>
        <b>○ 未检测到运行中的 ComfyUI</b>
        <span>{{ status.online_error || '本机 8188 端口无响应' }}</span>
        <span v-if="status.comfy_root">已发现安装目录：{{ status.comfy_root }}</span>
        <button type="button" class="ag-mini" :disabled="starting" @click="startComfy">
          {{ starting ? '启动中…' : '启动 ComfyUI' }}
        </button>
      </template>
      <button type="button" class="ag-mini ag-refresh-btn" @click="refreshStatus">重新检测</button>
    </div>

    <div class="ag-cols">
      <!-- ============================ 文生图 ============================ -->
      <section class="ag-card">
        <header class="ag-card-head">
          <h3>本地生图 · Z-Image Turbo</h3>
          <span class="ag-tag">Apache-2.0 · 约 8 步出图</span>
        </header>

        <label class="ag-field">
          <span>画面描述（英文效果更好）</span>
          <textarea v-model="imgForm.prompt" rows="3"
            placeholder="a cute slime monster, game asset, soft rim light, clean background"></textarea>
        </label>
        <label class="ag-field">
          <span>反向提示</span>
          <input v-model="imgForm.negative" type="text">
        </label>
        <div class="ag-row3">
          <label class="ag-field">
            <span>画幅</span>
            <select v-model="imgForm.ratio" @change="applyRatio">
              <option value="1:1">方形 1:1</option>
              <option value="3:4">竖版 3:4</option>
              <option value="4:3">横版 4:3</option>
              <option value="9:16">手机竖屏 9:16</option>
              <option value="16:9">宽屏 16:9</option>
            </select>
          </label>
          <label class="ag-field"><span>宽</span><input v-model.number="imgForm.width" type="number" step="32" min="256" max="1536"></label>
          <label class="ag-field"><span>高</span><input v-model.number="imgForm.height" type="number" step="32" min="256" max="1536"></label>
        </div>
        <div class="ag-row3">
          <label class="ag-field"><span>步数</span><input v-model.number="imgForm.steps" type="number" min="1" max="30"></label>
          <label class="ag-field"><span>种子</span>
            <span class="ag-seed"><input v-model.number="imgForm.seed" type="number"><button type="button" class="ag-mini" @click="randomSeed">随机</button></span>
          </label>
          <label class="ag-field"><span>数量</span>
            <select v-model.number="imgForm.batch"><option :value="1">1</option><option :value="2">2</option><option :value="3">3</option><option :value="4">4</option></select>
          </label>
        </div>

        <button type="button" class="ag-primary" :disabled="!canImage" @click="submitImage">
          {{ current && current.type === 'image' && current.status !== 'completed' && current.status !== 'failed'
             ? '生成中…' : '生成图片' }}
        </button>
        <p v-if="imgFormError" class="ag-err">{{ imgFormError }}</p>

        <div v-if="imageResults.length" class="ag-result-head">本次结果（已入素材库 assets/generated/images）</div>
        <div class="ag-wall">
          <figure v-for="(im, i) in imageResults" :key="i" class="ag-shot">
            <img :src="im.src" alt="生成结果">
            <figcaption>
              <button type="button" class="ag-mini" @click="useAsFrame(im)">用作帧动画首帧</button>
            </figcaption>
          </figure>
        </div>
      </section>

      <!-- ============================ 图生帧动画 ============================ -->
      <section class="ag-card">
        <header class="ag-card-head">
          <h3>图生帧动画 · MiniMax H3</h3>
          <span class="ag-tag">真视频模型 · 抽 12fps/≤64帧</span>
        </header>

        <div class="ag-frame-box">
          <div class="ag-frame-preview">
            <img v-if="frame.previewSrc" :src="frame.previewSrc" alt="首帧">
            <span v-else>未选首帧（可用工作流默认图）</span>
          </div>
          <div class="ag-frame-ops">
            <p class="ag-frame-state">
              <template v-if="frame.mode === 'gen'">来自左侧生图结果</template>
              <template v-else-if="frame.mode === 'library'">来自素材库：{{ frame.path }}</template>
              <template v-else-if="frame.mode === 'upload'">本地上传：{{ frame.name }}</template>
              <template v-else>将使用工作流自带示例首帧</template>
            </p>
            <button type="button" class="ag-mini" @click="openPicker">从素材库选图</button>
            <label class="ag-mini ag-file-label">
              上传本地图<input type="file" accept="image/png,image/jpeg,image/webp" @change="onUploadFile">
            </label>
            <button type="button" class="ag-mini" @click="clearFrame" :disabled="frame.mode === 'none'">清除</button>
          </div>
        </div>

        <label class="ag-field">
          <span>动作描述（英文效果更好）</span>
          <textarea v-model="animForm.prompt" rows="3"
            placeholder="the character runs in place, smooth idle-to-run cycle, side view, motion blur"></textarea>
        </label>
        <div class="ag-row3">
          <label class="ag-field"><span>时长(秒)</span><input v-model.number="animForm.duration" type="number" min="1" max="15" step="0.5"></label>
          <label class="ag-field"><span>抽帧 fps</span><input v-model.number="animForm.fps" type="number" min="1" max="30"></label>
          <label class="ag-field"><span>最多帧数</span><input v-model.number="animForm.maxFrames" type="number" min="1" max="128"></label>
        </div>
        <div class="ag-row2">
          <label class="ag-field"><span>种子</span>
            <span class="ag-seed"><input v-model.number="animForm.seed" type="number"><button type="button" class="ag-mini" @click="animForm.seed = Math.floor(Math.random()*1e9)">随机</button></span>
          </label>
          <label class="ag-check">
            <input v-model="animForm.turbo" type="checkbox"> 8 步加速 LoRA（快，画质略降）
          </label>
        </div>

        <button type="button" class="ag-primary" :disabled="!canAnim" @click="submitAnimation">
          {{ current && current.type === 'animation' && current.status !== 'completed' && current.status !== 'failed'
             ? '视频生成/抽帧中…' : '生成帧动画' }}
        </button>
        <p v-if="animFormError" class="ag-err">{{ animFormError }}</p>

        <div v-if="animResult" class="ag-anim-result">
          <SpritePlayer :src="animResult.sheetSrc" :cols="animResult.cols" :rows="animResult.rows"
            :frame-w="animResult.fw" :frame-h="animResult.fh" :fps="animResult.fps"
            :frames="animResult.frames" />
          <p class="ag-anim-meta">
            {{ animResult.frames }} 帧 · {{ animResult.cols }}×{{ animResult.rows }} 图集 ·
            {{ animResult.fw }}×{{ animResult.fh }} · {{ animResult.fps }}fps
          </p>
          <p class="ag-anim-hint">已写入 assets/generated/animations，含 PNG 序列 + SpriteSheet，可在「我的素材库」查看。</p>
        </div>
      </section>
    </div>

    <!-- 当前任务进度 -->
    <div v-if="current" class="ag-job" :class="'job-' + current.status">
      <div class="ag-job-head">
        <b>{{ current.type === 'image' ? '本地生图任务' : '帧动画任务' }}</b>
        <span class="ag-job-state">{{ stateText(current.status) }} · {{ current.phase }}</span>
        <span class="ag-spacer"></span>
        <button v-if="current.status === 'running' || current.status === 'queued'" type="button"
          class="ag-mini ag-cancel" @click="cancelCurrent">取消</button>
      </div>
      <div class="ag-bar"><i :style="{ width: Math.max(3, current.progress) + '%' }"></i></div>
      <p v-if="current.status === 'failed'" class="ag-err">{{ current.error || '生成失败' }}</p>
      <p v-else-if="current.type === 'animation'" class="ag-job-note">
        视频模型较慢（通常 1–5 分钟），完成后会自动抽帧并拼图集，可离开本页稍后回来。
      </p>
    </div>

    <!-- 素材库选首帧 -->
    <div v-if="pickerOpen" class="ag-modal-mask" @click.self="pickerOpen = false">
      <div class="ag-modal">
        <header><b>从素材库选首帧</b><button type="button" class="ag-x" @click="pickerOpen = false">×</button></header>
        <p v-if="pickerLoading" class="ag-loading">加载中…</p>
        <div v-else class="ag-picker-grid">
          <button v-for="it in pickerItems" :key="it.path" type="button" class="ag-pick" @click="pickLibrary(it)">
            <img :src="assetsApi.rawUrl(it.path)" :alt="it.name">
            <span>{{ it.name }}</span>
          </button>
          <p v-if="!pickerItems.length" class="ag-empty">素材库里还没有图片，先用左侧生图。</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  assetsApi, comfyApi, genApi, FsApiError,
  type GenJob, type GenStatus, type LibraryItem,
} from '../api'
import { demoMode, demoGenImages, demoGenSheet } from '../composables/demo'
import SpritePlayer from './SpritePlayer.vue'

const emit = defineEmits<{ done: [] }>()

const RATIO_SIZE: Record<string, [number, number]> = {
  '1:1': [512, 512], '3:4': [512, 672], '4:3': [672, 512],
  '9:16': [448, 768], '16:9': [768, 448],
}

const status = ref<GenStatus | null>(null)
const starting = ref(false)

const imgForm = reactive({
  prompt: '', negative: 'blurry, low quality', ratio: '1:1',
  width: 512, height: 512, steps: 8, seed: 42, batch: 1,
})
const animForm = reactive({
  prompt: '', duration: 5, seed: 1, fps: 12, maxFrames: 64, turbo: true,
})
const imageResults = ref<{ src: string; path?: string }[]>([])
const imgFormError = ref('')
const animFormError = ref('')

type FrameMode = 'none' | 'gen' | 'library' | 'upload'
const frame = reactive<{ mode: FrameMode; path: string; name: string; previewSrc: string }>({
  mode: 'none', path: '', name: '', previewSrc: '',
})
const animResult = ref<{ sheetSrc: string; cols: number; rows: number; fw: number; fh: number;
                         fps: number; frames: number } | null>(null)

const current = ref<GenJob | null>(null)
let timer = 0

const pickerOpen = ref(false)
const pickerLoading = ref(false)
const pickerItems = ref<LibraryItem[]>([])

const canImage = computed(() =>
  !!imgForm.prompt.trim() && !busy('image'))
const canAnim = computed(() =>
  !!animForm.prompt.trim() && !busy('animation'))
function busy(type: 'image' | 'animation') {
  const c = current.value
  return !!c && c.type === type && c.status !== 'completed' && c.status !== 'failed'
}
const statusClass = computed(() => {
  const s = status.value
  if (!s) return 'ag-wait'
  if (s.online && s.image.ready && s.video.ready) return 'ag-ok'
  if (s.online) return 'ag-warn'
  return 'ag-off'
})

function randomSeed() { imgForm.seed = Math.floor(Math.random() * 1e9) }
function applyRatio() {
  const wh = RATIO_SIZE[imgForm.ratio]
  if (wh) { imgForm.width = wh[0]; imgForm.height = wh[1] }
}
function stateText(s: string) {
  return { queued: '排队中', running: '运行中', completed: '已完成', failed: '失败', canceling: '取消中' }[s] || s
}

async function refreshStatus() {
  if (demoMode.value) {
    status.value = {
      ok: true, online: true, online_error: '', comfy_root: 'D:\\ComfyUI（演示）', models_dir: '',
      image: { ready: true, missing: [] }, video: { ready: true, missing: [] },
      workflows: { i2v: 'minimax_h3_i2v.json', t2v: 'minimax_h3_t2v.json' },
    }
    return
  }
  try {
    status.value = await genApi.status()
  } catch (e) {
    status.value = {
      ok: false, online: false, online_error: (e as FsApiError).message, comfy_root: '', models_dir: '',
      image: { ready: false, missing: [] }, video: { ready: false, missing: [] },
      workflows: { i2v: '', t2v: '' },
    }
  }
}

async function startComfy() {
  starting.value = true
  try {
    const r = await comfyApi.start()
    if (!r.ok) throw new Error(r.error || '启动失败')
    // 等待服务起来（加载节点可能要十几秒）
    for (let i = 0; i < 30; i++) {
      await new Promise((res) => setTimeout(res, 2000))
      await refreshStatus()
      if (status.value?.online) break
    }
  } catch (e) {
    if (status.value) status.value.online_error = (e as Error).message
  } finally {
    starting.value = false
  }
}

// ---------------------------------------------------------------- 任务轮询
function stopTimer() { if (timer) { clearInterval(timer); timer = 0 } }

function followJob(jobId: string, onDone: (j: GenJob) => void) {
  stopTimer()
  const tick = async () => {
    try {
      const r = await genApi.job(jobId)
      current.value = r.job
      if (r.job.status === 'completed' || r.job.status === 'failed') {
        stopTimer()
        onDone(r.job)
      }
    } catch (e) {
      stopTimer()
      if (current.value) {
        current.value.status = 'failed'
        current.value.error = (e as FsApiError).message
      }
    }
  }
  timer = window.setInterval(tick, 2000)
  void tick()
}

function submitImage() {
  imgFormError.value = ''
  if (demoMode.value) {
    fakeJob('image', ['排队中', '本地生成图片中'], 1400, (j) => {
      j.result = { paths: ['demo-1', 'demo-2'] }
      demoGenImages.forEach((src) => imageResults.value.unshift({ src }))
    })
    return
  }
  genApi.image({
    prompt: imgForm.prompt.trim(), negative_prompt: imgForm.negative,
    width: imgForm.width, height: imgForm.height, steps: imgForm.steps,
    seed: imgForm.seed, batch_size: imgForm.batch,
  }).then((r) => {
    if (!r.ok || !r.job_id) throw new Error(r.error || '提交失败')
    followJob(r.job_id, (j) => {
      const paths = (j.result?.paths as string[]) || []
      paths.forEach((p) => imageResults.value.unshift({ src: assetsApi.rawUrl(p), path: p }))
      emit('done')
    })
  }).catch((e) => { imgFormError.value = (e as FsApiError).message })
}

function submitAnimation() {
  animFormError.value = ''
  if (demoMode.value) {
    fakeJob('animation', ['上传首帧', 'H3 视频模型生成中（较慢）', '抽帧', '拼精灵图集并入库'], 2200, (j) => {
      j.result = { sheet: 'demo-sheet', frame_count: demoGenSheet.frames, cols: demoGenSheet.cols,
                  rows: demoGenSheet.rows, fps: demoGenSheet.fps }
      animResult.value = {
        sheetSrc: demoGenSheet.src, cols: demoGenSheet.cols, rows: demoGenSheet.rows,
        fw: demoGenSheet.fw, fh: demoGenSheet.fh, fps: demoGenSheet.fps, frames: demoGenSheet.frames,
      }
    })
    return
  }
  const payload: Parameters<typeof genApi.animation>[0] = {
    prompt: animForm.prompt.trim(), duration: animForm.duration, seed: animForm.seed,
    fps: animForm.fps, max_frames: animForm.maxFrames, turbo: animForm.turbo,
  }
  if (frame.mode === 'library' || frame.mode === 'gen') payload.first_frame_path = frame.path
  else if (frame.mode === 'upload') payload.first_frame_name = frame.name
  genApi.animation(payload).then((r) => {
    if (!r.ok || !r.job_id) throw new Error(r.error || '提交失败')
    followJob(r.job_id, (j) => {
      const res = j.result || {}
      animResult.value = {
        sheetSrc: assetsApi.rawUrl(String(res.sheet || '')),
        cols: Number(res.cols) || 1, rows: Number(res.rows) || 1,
        fw: Number(res.frame_width) || 0, fh: Number(res.frame_height) || 0,
        fps: Number(res.fps) || animForm.fps, frames: Number(res.frame_count) || 0,
      }
      emit('done')
    })
  }).catch((e) => { animFormError.value = (e as FsApiError).message })
}

function fakeJob(type: 'image' | 'animation', phases: string[], totalMs: number,
                 finish: (j: GenJob) => void) {
  const j: GenJob = {
    id: 'demo', type, status: 'running', phase: phases[0], progress: 0, prompt_id: '',
    prompt: '', result: null, error: '', created_at: '',
  }
  current.value = j
  const started = performance.now()
  stopTimer()
  timer = window.setInterval(() => {
    const pct = Math.min(99, Math.round((performance.now() - started) / totalMs * 100))
    j.progress = pct
    j.phase = phases[Math.min(phases.length - 1, Math.floor((pct / 100) * phases.length))]
    if (pct >= 99) {
      stopTimer()
      j.status = 'completed'; j.progress = 100; j.phase = '完成'
      finish(j)
    }
  }, 120)
}

async function cancelCurrent() {
  const c = current.value
  if (!c || demoMode.value) { if (c) { stopTimer(); c.status = 'failed'; c.error = '已取消' } return }
  try { await genApi.cancel(c.id) } catch { /* 状态由下次轮询收敛 */ }
}

// ---------------------------------------------------------------- 首帧
function useAsFrame(im: { src: string; path?: string }) {
  frame.mode = 'gen'; frame.path = im.path || ''; frame.name = ''; frame.previewSrc = im.src
}
async function openPicker() {
  if (demoMode.value) {
    pickerItems.value = []
    pickerOpen.value = true
    return
  }
  pickerOpen.value = true
  pickerLoading.value = true
  try {
    const r = await assetsApi.library()
    pickerItems.value = r.dirs.flatMap((d) => d.items)
      .filter((it) => it.kind === 'image' || it.kind === 'texture')
  } finally {
    pickerLoading.value = false
  }
}
function pickLibrary(it: LibraryItem) {
  frame.mode = 'library'; frame.path = it.path; frame.name = ''
  frame.previewSrc = assetsApi.rawUrl(it.path)
  pickerOpen.value = false
}
async function onUploadFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  const localUrl = URL.createObjectURL(file)
  if (demoMode.value) {
    frame.mode = 'upload'; frame.name = file.name; frame.path = ''; frame.previewSrc = localUrl
    return
  }
  try {
    const name = await genApi.uploadFrame(file)
    frame.mode = 'upload'; frame.name = name; frame.path = ''; frame.previewSrc = localUrl
  } catch (err) {
    animFormError.value = (err as FsApiError).message
  } finally {
    input.value = ''
  }
}
function clearFrame() {
  frame.mode = 'none'; frame.path = ''; frame.name = ''; frame.previewSrc = ''
}

onMounted(refreshStatus)
onBeforeUnmount(stopTimer)
</script>

<style scoped>
.ag-panel { display: flex; flex-direction: column; gap: 12px; padding: 14px 16px; overflow-y: auto; flex: 1 1 auto; min-height: 0; }

.ag-status {
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px;
  border: 1px solid var(--border); border-left-width: 3px; border-radius: 6px;
  padding: 8px 12px; font-size: 12px; color: var(--text-muted); background: var(--bg-soft, #fafbfc);
}
.ag-status b { font-size: 12.5px; }
.ag-status.ag-ok { border-left-color: #2e9e5b; }
.ag-status.ag-ok b { color: #2e9e5b; }
.ag-status.ag-warn { border-left-color: #d39418; }
.ag-status.ag-warn b { color: #b57b0e; }
.ag-status.ag-off { border-left-color: #b0b6bf; }
.ag-refresh-btn { margin-left: auto; }

.ag-cols { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
.ag-card {
  border: 1px solid var(--border); border-radius: 8px; padding: 14px;
  display: flex; flex-direction: column; gap: 10px; background: var(--panel, #fff);
}
.ag-card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.ag-card-head h3 { margin: 0; font-size: 14px; }
.ag-tag { font-size: 10.5px; color: var(--text-faint); }

.ag-field { display: flex; flex-direction: column; gap: 4px; font-size: 11.5px; color: var(--text-muted); }
.ag-field textarea, .ag-field input, .ag-field select {
  font: inherit; font-size: 12px; color: var(--text);
  border: 1px solid var(--border); border-radius: 5px; padding: 6px 8px; background: #fff;
  width: 100%; box-sizing: border-box;
}
.ag-field textarea { resize: vertical; }
.ag-row3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }
.ag-row2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; align-items: end; }
.ag-seed { display: flex; gap: 4px; }
.ag-seed input { flex: 1 1 auto; }
.ag-check { display: flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--text-muted); padding-bottom: 6px; }

.ag-primary {
  border: 1px solid #2560d4; background: #2f6fed; color: #fff; border-radius: 6px;
  padding: 8px 12px; font-size: 13px; cursor: pointer;
}
.ag-primary:disabled { opacity: .5; cursor: not-allowed; }
.ag-mini {
  border: 1px solid var(--border); background: #fff; color: var(--text);
  border-radius: 5px; padding: 3px 9px; font-size: 11.5px; cursor: pointer; white-space: nowrap;
}
.ag-mini:disabled { opacity: .45; cursor: not-allowed; }
.ag-file-label { display: inline-flex; align-items: center; justify-content: center; }
.ag-file-label input { display: none; }
.ag-err { color: #d0464a; font-size: 11.5px; margin: 0; }

.ag-result-head { font-size: 11.5px; color: var(--text-muted); margin-top: 2px; }
.ag-wall { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }
.ag-shot { margin: 0; border: 1px solid var(--border); border-radius: 6px; overflow: hidden; background: #fff; }
.ag-shot img { display: block; width: 100%; aspect-ratio: 1; object-fit: cover; }
.ag-shot figcaption { padding: 5px; display: flex; justify-content: center; }

.ag-frame-box { display: flex; gap: 10px; align-items: stretch; }
.ag-frame-preview {
  width: 120px; min-height: 84px; border: 1px dashed var(--border); border-radius: 6px;
  display: flex; align-items: center; justify-content: center; overflow: hidden;
  color: var(--text-faint); font-size: 10.5px; text-align: center; padding: 4px; flex: 0 0 auto;
}
.ag-frame-preview img { max-width: 100%; max-height: 120px; }
.ag-frame-ops { display: flex; flex-direction: column; gap: 6px; justify-content: center; flex: 1 1 auto; }
.ag-frame-state { margin: 0; font-size: 11px; color: var(--text-muted); word-break: break-all; }

.ag-anim-result { display: flex; flex-direction: column; align-items: center; gap: 6px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; background: #fafbfc; }
.ag-anim-meta { margin: 0; font-size: 11.5px; color: var(--text); }
.ag-anim-hint { margin: 0; font-size: 10.5px; color: var(--text-faint); text-align: center; }

.ag-job { border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 7px; }
.ag-job.job-completed { border-color: #2e9e5b; }
.ag-job.job-failed { border-color: #d0464a; }
.ag-job-head { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.ag-job-state { color: var(--text-muted); }
.ag-spacer { flex: 1 1 auto; }
.ag-cancel { border-color: #d88; color: #c23a40; }
.ag-bar { height: 6px; border-radius: 3px; background: #e7ebf0; overflow: hidden; }
.ag-bar i { display: block; height: 100%; background: #2f6fed; transition: width .3s ease; }
.ag-job-note { margin: 0; font-size: 10.5px; color: var(--text-faint); }

.ag-modal-mask { position: fixed; inset: 0; background: rgba(20,30,48,.32); display: flex; align-items: center; justify-content: center; z-index: 60; }
.ag-modal { width: min(720px, 92vw); max-height: 80vh; display: flex; flex-direction: column; background: #fff; border-radius: 8px; border: 1px solid var(--border); overflow: hidden; }
.ag-modal header { display: flex; align-items: center; padding: 10px 14px; border-bottom: 1px solid var(--border); }
.ag-modal header b { flex: 1 1 auto; font-size: 13px; }
.ag-x { border: none; background: none; font-size: 18px; cursor: pointer; color: var(--text-muted); }
.ag-loading, .ag-empty { padding: 20px; text-align: center; color: var(--text-faint); font-size: 12px; }
.ag-picker-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; padding: 12px 14px; overflow-y: auto; }
.ag-pick { border: 1px solid var(--border); border-radius: 6px; background: #fff; padding: 0; cursor: pointer; overflow: hidden; display: flex; flex-direction: column; }
.ag-pick img { width: 100%; aspect-ratio: 1; object-fit: cover; display: block; }
.ag-pick span { font-size: 10.5px; padding: 4px; color: var(--text-muted); word-break: break-all; }

@media (max-width: 1024px) {
  .ag-cols { grid-template-columns: 1fr; }
}
</style>
