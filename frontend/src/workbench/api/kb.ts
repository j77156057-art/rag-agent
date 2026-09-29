// 资料摄取 / 提示词增强 / 上下文查询（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { FsApiError, rawJson, request, withProject } from './_base'
import { ContextUsage } from './sse'
import { getSessionId } from './session'

// ---------------------------------------------------------------- 问答页：资料摄取 / 提示词增强
export interface IngestResult {
  ok: boolean
  chunks?: number
  error?: string
}

export interface EnhancePromptResult {
  ok: boolean
  enhanced?: string
  /** llm=模型重写；local=本地规则兜底 */
  mode?: 'llm' | 'local' | string
  note?: string
  error?: string
}

export const kbApi = {
  /** 上传 PDF/MD/TXT 资料文档入向量库（multipart，字段名 file）。 */
  ingest(file: File): Promise<IngestResult> {
    const fd = new FormData()
    fd.append('file', file, file.name)
    return fetch('/api/ingest', withProject({ method: 'POST', body: fd }))
      .then(async (res) => {
        const body = (await res.json().catch(() => null)) as IngestResult | null
        if (!res.ok && body === null) throw new FsApiError(res.status, `请求失败（HTTP ${res.status}）`)
        return body as IngestResult
      })
  },
}

export const promptApi = {
  /** 把草稿重写为更清晰的提问；无可用模型时后端本地规则兜底（mode=local）。 */
  enhance(prompt: string): Promise<EnhancePromptResult> {
    return rawJson<EnhancePromptResult>('/api/enhance_prompt', { prompt })
  },
}

/** 上下文窗口用量查询（页面刷新后恢复指示用）。session_id 为 query 参数，
 *  与 /api/chat 的 form 字段对应，保证读到的是当前标签页会话的用量。 */
export const contextApi = {
  async get(sessionId = getSessionId()): Promise<ContextUsage | null> {
    const r = await request<{ ok: boolean; active?: boolean } & Partial<ContextUsage>>(
      `/api/context?session_id=${encodeURIComponent(sessionId)}`,
    )
    if (!r.active) return null
    return {
      used_tokens: r.used_tokens ?? 0,
      context_window: r.context_window ?? 0,
      prompt_budget: r.prompt_budget ?? 0,
      percent: r.percent ?? 0,
      level: (r.level as ContextUsage['level']) ?? 'ok',
      history_tokens: r.history_tokens,
      compact_trigger_tokens: r.compact_trigger_tokens,
      compact_percent: r.compact_percent,
    }
  },
}
