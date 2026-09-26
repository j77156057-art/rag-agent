// 模型配置与联网/思考开关：工作台对话台与问答首页共用。
// - modelConfig 拉取/保存后的本地状态；
// - webOn / thinkingOn 持久化在本机 localStorage（两入口共享同一份偏好）；
// - 思考能力三态：native（恒开不可点）/ toggle（用户可切）/ none（不支持）。
import { computed, ref, watch } from 'vue'
import { modelApi } from '../api'
import type { ModelConfigInfo } from '../api'
import { demoMode } from './demo'

export interface UseModelConfigOptions {
  /** 模型设置保存成功后的附加副作用（如清空多轮消息、刷新驻留状态） */
  onSaved?: (info: ModelConfigInfo) => void
}

export function useModelConfig(options: UseModelConfigOptions = {}) {
  const modelConfig = ref<ModelConfigInfo | null>(null)
  const settingsOpen = ref(false)
  // 联网默认关（代码问答不外联），选择持久化在本机
  const webOn = ref(window.localStorage.getItem('docmind.chatWeb') === '1')
  // 深度思考：'1'/'0'，仅对支持思考的模型才发送
  const thinkingOn = ref(window.localStorage.getItem('docmind.chatThinking') === '1')
  watch(webOn, (v) => window.localStorage.setItem('docmind.chatWeb', v ? '1' : '0'))
  watch(thinkingOn, (v) => window.localStorage.setItem('docmind.chatThinking', v ? '1' : '0'))

  const modelLabel = computed(() => {
    if (demoMode.value) return '离线演示模型'
    const c = modelConfig.value
    if (!c) return '模型加载中…'
    const name = c.provider_meta?.[c.llm_provider]?.label || c.llm_provider
    return `${name} · ${c.llm_model || '默认模型'}`
  })
  const thinkingMode = computed(() => modelConfig.value?.capability?.thinking || 'none')
  const thinkingSupported = computed(() => thinkingMode.value === 'native' || thinkingMode.value === 'toggle')
  // native 思考模型（reasoner 类）开关恒开且不可点；toggle 家族才允许用户切
  const thinkingNative = computed(() => thinkingMode.value === 'native')
  const thinkingEffective = computed(() => thinkingNative.value || thinkingOn.value)
  const visionMode = computed(() => modelConfig.value?.capability?.vision || 'unknown')
  const visionSupported = computed(() => visionMode.value !== 'none')
  const visionLabel = computed(() => {
    if (visionMode.value === 'native') return '当前模型直接识图'
    if (visionMode.value === 'harness') return 'Harness 视觉辅助'
    if (visionMode.value === 'none') return '当前模型不支持识图'
    return '识图能力待确认，可由 Harness 辅助'
  })

  async function loadModelConfig() {
    if (demoMode.value) return
    try {
      modelConfig.value = await modelApi.get()
    } catch {
      /* 配置加载失败不阻塞聊天，芯片显示兜底文案 */
    }
  }
  function openSettings() {
    if (demoMode.value) return
    void loadModelConfig().then(() => { settingsOpen.value = true })
  }
  function onModelSaved(info: ModelConfigInfo) {
    modelConfig.value = info
    options.onSaved?.(info)
    // 是否关闭弹窗由弹窗自身决定（有告警时停留，让用户看到告警内容）
  }

  return {
    modelConfig, settingsOpen, webOn, thinkingOn,
    modelLabel, thinkingMode, thinkingSupported, thinkingNative, thinkingEffective,
    visionMode, visionSupported, visionLabel,
    loadModelConfig, openSettings, onModelSaved,
  }
}
