<script setup lang="ts">
// 模型设置弹窗：选择预设厂商或任意 OpenAI 兼容端点（base_url + Key + 模型名）。
// 保存后后端即时重建 Agent LLM 客户端；能力画像（思考/上下文窗口）回传父组件，
// 用于刷新输入栏的模型标签与「深度思考」开关状态。
import { ref, watch, computed } from 'vue'
import { modelApi } from '../api'
import type { ModelConfigInfo, ContextLookupResult } from '../api'

const props = defineProps<{ visible: boolean; config: ModelConfigInfo | null }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'saved', info: ModelConfigInfo): void
}>()

const provider = ref('mock')
const model = ref('')
const apiKey = ref('')
const baseUrl = ref('')
const showKey = ref(false)
const saving = ref(false)
const errorMsg = ref('')
const warnings = ref<string[]>([])

// 上下文窗口：手填覆盖（空字符串=自动：Ollama 实时探测/内置画像）
const ctxWindow = ref('')
const ctxBusy = ref<'probe' | 'lookup' | ''>('')
const ctxMsg = ref('')
const ctxMsgOk = ref(false)
const lookupResult = ref<ContextLookupResult | null>(null)

const meta = computed(() => props.config?.provider_meta?.[provider.value])
const isCustom = computed(() => provider.value === 'custom')
const isOllama = computed(() => provider.value === 'ollama')
const ollamaModels = computed<string[]>(() =>
  isOllama.value ? (props.config?.ollama_status?.present_models ?? []) : [])
const modelPlaceholder = computed(() => meta.value?.default_model || '模型名称')
const ctxModelReady = computed(() => !!model.value.trim())

// 当前已保存模型的窗口来源标签
const sourceLabel = computed(() => {
  switch (props.config?.context_source) {
    case 'custom': return '窗口：手填'
    case 'probe': return '窗口：实时探测'
    default: return '窗口：内置画像'
  }
})

function fmtK(v: number): string {
  if (!v) return '0'
  if (v < 1024) return String(v)
  return v >= 1048576 ? `${(v / 1048576).toFixed(v % 1048576 ? 1 : 0)}M` : `${Math.round(v / 1024)}k`
}

const ctxWindowK = computed(() => {
  const n = Number(ctxWindow.value)
  return ctxWindow.value && n > 0 ? `≈ ${fmtK(n)}` : ''
})

// 弹窗每次打开时用服务端最新配置回填表单
watch(
  () => props.visible,
  (v) => {
    if (!v || !props.config) return
    errorMsg.value = ''
    warnings.value = []
    provider.value = props.config.llm_provider || 'mock'
    model.value = props.config.llm_model || ''
    apiKey.value = ''
    baseUrl.value = props.config.custom_base_url || ''
    ctxWindow.value = props.config.context_window_override ? String(props.config.context_window_override) : ''
    ctxBusy.value = ''
    ctxMsg.value = ''
    lookupResult.value = null
  },
)

// 切换厂商：模型名沿用该厂商默认（用户可改）；自定义端点清空 key 输入；
// 窗口覆盖按「厂商/模型」持久化，切厂商时重置为待填状态
watch(provider, (p) => {
  errorMsg.value = ''
  ctxMsg.value = ''
  lookupResult.value = null
  ctxWindow.value = ''
  const m = props.config?.provider_meta?.[p]
  if (p !== 'custom') model.value = m?.default_model || ''
})

function onMaskDown() {
  if (!saving.value) emit('close')
}

// Ollama /api/show 实时探测模型真实窗口：成功后仅填入输入框，保存才生效
async function probeContext() {
  const name = model.value.trim()
  if (!name || ctxBusy.value) return
  ctxBusy.value = 'probe'
  ctxMsg.value = ''
  try {
    const res = await modelApi.probeOllama(name)
    if (res.ok && res.context_window) {
      ctxWindow.value = String(res.context_window)
      ctxMsgOk.value = true
      ctxMsg.value = `实时探测成功：窗口 ${res.context_window.toLocaleString()} tokens（≈ ${fmtK(res.context_window)}），保存后生效。`
    } else {
      ctxMsgOk.value = false
      ctxMsg.value = res.error || '探测失败，可改用联网查询或手动填写。'
    }
  } catch (e) {
    ctxMsgOk.value = false
    ctxMsg.value = (e as { message?: string }).message || '探测失败，可改用联网查询或手动填写。'
  } finally {
    ctxBusy.value = ''
  }
}

// 联网搜索模型公开窗口：结果只展示候选与出处，用户确认后点「采用」填入
async function lookupContext() {
  const name = model.value.trim()
  if (!name || ctxBusy.value) return
  ctxBusy.value = 'lookup'
  ctxMsg.value = ''
  lookupResult.value = null
  try {
    lookupResult.value = await modelApi.lookupModelContext(provider.value, name)
  } catch (e) {
    ctxMsgOk.value = false
    ctxMsg.value = (e as { message?: string }).message || '联网查询失败。'
  } finally {
    ctxBusy.value = ''
  }
}

function useCandidate(tokens: number) {
  ctxWindow.value = String(tokens)
}

async function save() {
  errorMsg.value = ''
  warnings.value = []
  // 窗口范围前端先拦一道（与后端 1024~2097152 保持一致）
  let ctxVal = 0
  const rawCtx = ctxWindow.value.trim()
  if (rawCtx) {
    ctxVal = Number(rawCtx)
    if (!Number.isInteger(ctxVal) || ctxVal < 1024 || ctxVal > 2_097_152) {
      errorMsg.value = '上下文窗口需为 1024 ~ 2097152 tokens 的整数；不需要自定义时请清空输入框。'
      return
    }
  }
  saving.value = true
  try {
    const res = await modelApi.save({
      provider: provider.value,
      model: model.value.trim(),
      // 留空=不修改已保存的 Key，避免每次切换都要重输
      api_key: apiKey.value.trim(),
      base_url: isCustom.value ? baseUrl.value.trim() : '',
      // 空=0：清除覆盖回到自动（Ollama 实时探测/内置画像）
      context_window: ctxVal,
    })
    if (res.model_error) {
      errorMsg.value = `模型探活失败：${res.model_error}`
      return
    }
    // 保存已生效；告警（如缺 Key）非致命，弹窗保持打开让用户看完再手动关
    warnings.value = res.warnings || []
    ctxWindow.value = res.context_window_override ? String(res.context_window_override) : ''
    emit('saved', res)
    if (!warnings.value.length) emit('close')
  } catch (e) {
    errorMsg.value = (e as { message?: string }).message || '保存失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div v-if="visible" class="ms-mask" @mousedown.self="onMaskDown">
    <div class="ms-box" role="dialog" aria-modal="true">
      <h3 class="ms-title">模型设置</h3>

      <label class="ms-label">模型服务</label>
      <select v-model="provider" class="ms-input" :disabled="saving">
        <option v-for="p in props.config?.providers || []" :key="p" :value="p">
          {{ props.config?.provider_meta?.[p]?.label || p }}
        </option>
      </select>

      <template v-if="isCustom">
        <label class="ms-label">接口地址（OpenAI 兼容，到 /v1 一级）</label>
        <input
          v-model="baseUrl"
          class="ms-input"
          placeholder="https://your-host/v1"
          spellcheck="false"
          :disabled="saving"
        />
      </template>
      <div v-else-if="meta?.base_url" class="ms-endpoint" :title="meta.base_url">{{ meta.base_url }}</div>

      <label class="ms-label">模型名称</label>
      <input
        v-model="model"
        class="ms-input"
        :placeholder="modelPlaceholder"
        list="ms-ollama-models"
        spellcheck="false"
        :disabled="saving"
      />
      <datalist id="ms-ollama-models">
        <option v-for="m in ollamaModels" :key="m" :value="m" />
      </datalist>
      <div v-if="isOllama && props.config?.ollama_status && !props.config.ollama_status.reachable"
           class="ms-hint ms-hint-err">
        未检测到本机 Ollama 服务，保存前请先启动它（模型切换时会做一次真实探活）。
      </div>

      <label class="ms-label">上下文窗口（tokens）</label>
      <div class="ms-ctx-row">
        <input
          v-model="ctxWindow"
          class="ms-input ms-ctx-input"
          type="number" min="1024" max="2097152" step="1024"
          placeholder="留空=自动（Ollama 实时探测 / 内置画像）"
          spellcheck="false"
          :disabled="saving"
        />
        <span v-if="ctxWindowK" class="ms-ctx-k">{{ ctxWindowK }}</span>
      </div>
      <div class="ms-ctx-btns">
        <button v-if="isOllama" type="button" class="ms-mini"
                :disabled="!!ctxBusy || saving || !ctxModelReady"
                title="调用本机 Ollama /api/show 读取该模型的真实 context_length"
                @click="probeContext">
          {{ ctxBusy === 'probe' ? '探测中…' : '实时探测' }}
        </button>
        <button type="button" class="ms-mini"
                :disabled="!!ctxBusy || saving || !ctxModelReady"
                title="联网搜索该模型公开的上下文窗口，结果仅供参考，确认后再采用"
                @click="lookupContext">
          {{ ctxBusy === 'lookup' ? '查询中…' : '联网查询' }}
        </button>
        <span class="ms-ctx-note">未知模型可手动填写；手填值优先于探测与画像</span>
      </div>
      <p v-if="ctxMsg" class="ms-hint" :class="{ 'ms-hint-err': !ctxMsgOk }">{{ ctxMsg }}</p>
      <div v-if="lookupResult?.candidates?.length" class="ms-lookup">
        <p class="ms-lookup-title">联网识别到的候选窗口（点击数字采用，出处仅供参考）：</p>
        <div v-for="(c, i) in lookupResult.candidates" :key="c.tokens + '-' + i" class="ms-lookup-item">
          <button type="button" class="ms-lookup-num"
                  :class="{ 'ms-lookup-best': c.tokens === lookupResult.best }"
                  :disabled="saving" @click="useCandidate(c.tokens)">
            {{ c.tokens.toLocaleString() }} <span class="ms-lookup-k">≈{{ Math.round(c.tokens / 1024) }}k</span>
          </button>
          <span class="ms-lookup-ev" :title="c.evidence">{{ c.evidence }}</span>
        </div>
      </div>
      <p v-else-if="lookupResult && !lookupResult.ok" class="ms-hint ms-hint-err">{{ lookupResult.error }}</p>

      <template v-if="meta?.needs_key || isCustom">
        <label class="ms-label">
          API Key
          <span class="ms-key-toggle" @click="showKey = !showKey">{{ showKey ? '隐藏' : '显示' }}</span>
        </label>
        <input
          v-model="apiKey"
          class="ms-input"
          :type="showKey ? 'text' : 'password'"
          :placeholder="props.config?.has_key ? '已保存，留空表示不修改' : 'sk-...'"
          spellcheck="false"
          autocomplete="off"
          :disabled="saving"
        />
      </template>

      <p v-if="errorMsg" class="ms-hint ms-hint-err">{{ errorMsg }}</p>
      <p v-for="(w, i) in warnings" :key="i" class="ms-hint">{{ w }}</p>

      <div class="ms-cap" title="以下为当前已保存模型的能力，保存切换后自动刷新">
        <span class="ms-cap-cap-title">当前模型能力</span>
        <span class="ms-cap-tag">上下文 {{ Math.round((props.config?.capability?.context_window || 0) / 1024) }}k</span>
        <span class="ms-cap-tag">{{ sourceLabel }}</span>
        <span class="ms-cap-tag" v-if="props.config?.capability?.thinking === 'native'">深度思考·模型内置</span>
        <span class="ms-cap-tag" v-else-if="props.config?.capability?.thinking === 'toggle'">深度思考·可开关</span>
        <span class="ms-cap-tag ms-cap-off" v-else>不支持深度思考</span>
        <span class="ms-cap-tag" v-if="props.config?.capability?.cloud">云端</span>
        <span class="ms-cap-tag ms-cap-off" v-else>本地</span>
      </div>
      <p class="ms-tip">切换模型会清空当前对话上下文；Key 使用系统凭据加密存储，仅保存在本机项目目录。</p>

      <div class="ms-actions">
        <button class="ms-btn" :disabled="saving" @click="emit('close')">{{ warnings.length ? '知道了' : '取消' }}</button>
        <button v-if="!warnings.length" class="ms-btn ms-primary"
                :disabled="saving || (isCustom && (!baseUrl.trim() || !model.trim()))"
                @click="save">
          {{ saving ? '保存中…' : '保存并切换' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ms-mask {
  position: fixed; inset: 0; z-index: 2000;
  background: rgba(20, 24, 33, .32);
  display: flex; align-items: center; justify-content: center;
}
.ms-box {
  width: 460px; max-width: calc(100vw - 32px);
  background: var(--bg, #fff);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, .18);
  padding: 18px 20px 16px;
  display: flex; flex-direction: column; gap: 6px;
}
.ms-title { margin: 0 0 6px; font-size: 15px; font-weight: 600; }
.ms-label {
  font-size: 12px; color: var(--text-muted); margin-top: 8px;
  display: flex; justify-content: space-between; align-items: center;
}
.ms-key-toggle { color: var(--accent); cursor: pointer; user-select: none; }
.ms-key-toggle:hover { text-decoration: underline; }
.ms-input {
  width: 100%; box-sizing: border-box;
  padding: 7px 10px; font-size: 13px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-input, #fff); color: var(--text);
  outline: none;
}
.ms-input:focus { border-color: var(--accent); }
.ms-endpoint {
  font-size: 11px; color: var(--text-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  padding: 2px 2px 0;
}
.ms-hint { font-size: 12px; color: #b45309; margin: 6px 0 0; }
.ms-hint-err { color: #dc2626; }
/* 上下文窗口手填 + 探测/联网查询 */
.ms-ctx-row { display: flex; align-items: center; gap: 8px; }
.ms-ctx-input { flex: 1; }
.ms-ctx-k {
  font-size: 11px; color: var(--text-faint); white-space: nowrap;
  min-width: 42px; text-align: right;
}
.ms-ctx-btns { display: flex; align-items: center; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
.ms-mini {
  font-size: 12px; padding: 4px 12px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text);
  cursor: pointer;
}
.ms-mini:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.ms-mini:disabled { opacity: .5; cursor: default; }
.ms-ctx-note { font-size: 11px; color: var(--text-faint); }
.ms-lookup {
  margin-top: 6px; border: 1px solid var(--border); border-radius: 8px;
  padding: 8px 10px; display: flex; flex-direction: column; gap: 6px;
  background: var(--bg-selected);
}
.ms-lookup-title { margin: 0; font-size: 11px; color: var(--text-muted); }
.ms-lookup-item { display: flex; align-items: baseline; gap: 8px; }
.ms-lookup-num {
  flex: none; font-size: 12px; cursor: pointer;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--bg); color: var(--text);
  padding: 3px 10px;
}
.ms-lookup-num:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.ms-lookup-num:disabled { opacity: .5; cursor: default; }
.ms-lookup-best { border-color: var(--accent); color: var(--accent); font-weight: 600; }
.ms-lookup-k { font-weight: 400; opacity: .7; }
.ms-lookup-ev {
  font-size: 11px; color: var(--text-faint); line-height: 1.4;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
}
.ms-cap { display: flex; gap: 6px; margin-top: 10px; flex-wrap: wrap; align-items: center; }
.ms-cap-cap-title { font-size: 11px; color: var(--text-faint); margin-right: 2px; }
.ms-cap-tag {
  font-size: 11px; color: var(--text-muted);
  border: 1px solid var(--border); border-radius: 999px;
  padding: 2px 9px; background: var(--bg-selected);
}
.ms-cap-off { opacity: .65; }
.ms-tip { font-size: 11px; color: var(--text-faint); margin: 8px 0 0; line-height: 1.5; }
.ms-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.ms-btn {
  padding: 7px 16px; font-size: 13px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text);
  cursor: pointer;
}
.ms-btn:hover { border-color: var(--accent); }
.ms-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.ms-primary:disabled { opacity: .5; cursor: default; }
</style>
