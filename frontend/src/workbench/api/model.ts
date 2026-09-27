// 模型配置 / 能力画像 / 视觉理解（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { projectRequest, rawJson, request, withProject } from './_base'

// ---------------------------------------------------------------- 模型设置（/api/config）
export interface ProviderMeta {
  label: string
  base_url: string
  default_model: string
  needs_key: boolean
  cloud: boolean
}

export interface ModelCapability {
  context_window: number
  /** native=模型天生推理 / toggle=可开关 / none=不支持 / unknown=待确认 */
  thinking: 'native' | 'toggle' | 'none' | 'unknown' | string
  /** native=原生图片 / harness=Harness 视觉辅助 / none=不支持 / unknown=待确认 */
  vision: 'native' | 'harness' | 'none' | 'unknown' | string
  /** native=原生视频 / frames=Harness 抽帧 / none=不支持 / unknown=待确认 */
  video: 'native' | 'frames' | 'none' | 'unknown' | string
  cloud: boolean
}

export const visionApi = {
  analyzeVideo(file: File): Promise<{ ok: boolean; context?: string[]; info?: { frame_count?: number }; error?: string }> {
    const fd = new FormData()
    fd.append('file', file, file.name || 'clip.mp4')
    return fetch('/api/vision/video', withProject({ method: 'POST', body: fd }))
      .then(async (res) => (await res.json()) as { ok: boolean; context?: string[]; info?: { frame_count?: number }; error?: string })
  },
}

export interface OllamaStatus {
  reachable: boolean
  needed_models?: string[]
  present_models?: string[]
  missing_models?: string[]
  guidance?: string
}

export interface ModelConfigInfo {
  ok?: boolean
  llm_provider: string
  llm_model: string
  embedding_provider: string
  has_key: boolean
  providers: string[]
  provider_meta: Record<string, ProviderMeta>
  custom_base_url: string
  capability: ModelCapability
  ollama_status?: OllamaStatus
  /** 用户手填的窗口覆盖；0/缺省 = 自动（实时探测或内置画像） */
  context_window_override?: number
  capability_override?: { thinking?: string; vision?: string; video?: string }
  /** 当前生效窗口来源：custom 手填 / probe Ollama 实时探测 / profile 内置画像 */
  context_source?: 'custom' | 'probe' | 'profile'
  /** AI 越界访问模式：safe=仅限项目内；high=允许受控越界读写（须配置白名单） */
  external_access_mode?: 'safe' | 'high'
  // 以下字段问答页（/）使用，后端 /api/config 始终返回；工作台类型里保留为可选
  /** 已入库资料文档文件名列表（/api/ingest 上传的 PDF/MD/TXT） */
  ingested_files?: string[]
  /** 当前代码库切片条数 */
  code_sources?: number
  /** 当前代码库根目录 */
  code_root?: string
  /** AI 改文件前是否需要人工确认 */
  edit_confirm?: boolean
  /** 模型选择模式：fixed=固定模型；auto=按任务复杂度自动在本地/云端间路由 */
  llm_mode?: 'fixed' | 'auto'
  /** 自动模式下复杂任务使用的云端预设 id */
  auto_cloud_preset_id?: string
  /** 已保存的命名模型预设（含当前项目是否已存 Key） */
  model_presets?: ModelPreset[]
}

/** 命名模型预设：一键切换整套云端/本地配置（Key 存项目密钥库，不回显） */
export interface ModelPreset {
  id: string
  label: string
  provider: string
  model: string
  base_url: string
  /** 0=自动探测/画像 */
  context_window: number
  created_at?: string
  updated_at?: string
  /** 当前项目下该预设是否已保存 API Key */
  has_key?: boolean
}

export interface SaveModelReq {
  provider: string
  model?: string
  api_key?: string
  base_url?: string
  /** >0 设置窗口覆盖；0 清除覆盖回到自动；undefined=不改动 */
  context_window?: number | null
  thinking_capability?: string
  vision_capability?: string
  video_capability?: string
  /** AI 越界访问模式：'safe' | 'high'；undefined=不改动 */
  external_access_mode?: 'safe' | 'high'
  /** 模型选择模式：'fixed' | 'auto'；undefined=不改动 */
  llm_mode?: 'fixed' | 'auto'
  /** 自动模式云端预设 id（'' 可清空） */
  auto_cloud_preset_id?: string
}

export interface SavePresetReq {
  id?: string
  label: string
  provider: string
  model?: string
  base_url?: string
  context_window?: number | null
  api_key?: string
}

/** 联网识别出的候选窗口（附带出处片段，由用户判断后采用） */
export interface ContextCandidate {
  tokens: number
  evidence: string
}

export interface ContextLookupResult {
  ok: boolean
  error?: string
  query?: string
  best?: number
  candidates?: ContextCandidate[]
}

export interface OllamaProbeResult {
  ok: boolean
  error?: string
  model?: string
  context_window?: number
}

export const modelApi = {
  get(): Promise<ModelConfigInfo> {
    return request('/api/config')
  },
  save(req: SaveModelReq): Promise<ModelConfigInfo & { warnings?: string[]; model_error?: string; error?: string }> {
    // 后端配置保存失败是 HTTP 200 + {ok:false, model_error?}：必须走 rawJson 原样返回失败体，
    // 否则通用 request 会把真实原因吞成「请求失败（HTTP 200）」，model_error 分支永不触发。
    return rawJson<ModelConfigInfo & { warnings?: string[]; model_error?: string; error?: string }>('/api/config', req)
  },
  /** 实时探测本机已安装 Ollama 模型的真实上下文窗口 */
  probeOllama(model: string): Promise<OllamaProbeResult> {
    return rawJson<OllamaProbeResult>(`/api/ollama/probe?model=${encodeURIComponent(model)}`)
  },
  /** 联网搜索模型公开的上下文窗口（返回候选列表，不自动写配置） */
  lookupModelContext(provider: string, model: string): Promise<ContextLookupResult> {
    return rawJson<ContextLookupResult>('/api/model_context_lookup', { provider, model })
  },
  /** 列出命名模型预设 */
  listPresets(): Promise<{ ok: boolean; presets: ModelPreset[] }> {
    return request('/api/model_presets')
  },
  /** 新增/更新预设（api_key 可选，随预设存入当前项目密钥库） */
  savePreset(req: SavePresetReq): Promise<{ ok: boolean; presets?: ModelPreset[]; error?: string }> {
    return rawJson('/api/model_presets', req)
  },
  /** 删除预设 */
  deletePreset(id: string): Promise<{ ok: boolean; presets?: ModelPreset[]; error?: string }> {
    return projectRequest(`/api/model_presets/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  /** 一键激活预设（等同在弹窗里填好整套参数后点保存并切换） */
  activatePreset(id: string): Promise<ModelConfigInfo & { error?: string }> {
    return rawJson(`/api/model_presets/${encodeURIComponent(id)}/activate`, {})
  },
}
