// /api/chat 问答流式入口（aiApi）（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { SelectionAiRequest, SseStreamHandlers, postSse } from './sse'
import { getSessionId } from './session'
import type { ChatUiContext } from '../previewFeedback'

export const aiApi = {
  /** 解释 / Review / 自由提问：走 ReAct agent（可 search_code/read_file/grep，引用文件行号）。
   *  web/thinking：逐请求的联网搜索 / 深度思考开关（后端默认均为关/模型默认）。
   *  session_id：当前标签页会话，用于隔离并发请求的逐请求开关。 */
  askGrounded(
    question: string,
    h: SseStreamHandlers,
    opts: { web?: boolean; thinking?: boolean | null; images?: Blob[]; sessionId?: string; uiContext?: ChatUiContext; visualTimeline?: { at: string; observation: string }[]; workflowId?: string; feedbackId?: string } = {},
  ): Promise<void> {
    const fd = new FormData()
    fd.append('question', question)
    fd.append('session_id', opts.sessionId || getSessionId())
    if (opts.uiContext) fd.append('ui_context', opts.uiContext)
    // 语音轮次同样要看画面（「边看边聊」），所以与 cockpit_live_vision 一样带上时间线。
    if ((opts.uiContext === 'cockpit_live_vision' || opts.uiContext === 'cockpit_voice_turn')
      && opts.visualTimeline?.length) {
      fd.append('visual_timeline', JSON.stringify(opts.visualTimeline.slice(-5)))
    }
    // 桌面视觉复验：后端只在 ui_context=desktop_visual_review 时读取这两个编号对账。
    if (opts.uiContext === 'desktop_visual_review') {
      if (opts.workflowId) fd.append('workflow_id', opts.workflowId)
      if (opts.feedbackId) fd.append('feedback_id', opts.feedbackId)
    }
    fd.append('web_mode', opts.web ? '1' : '0')
    if (opts.thinking === true) fd.append('thinking_mode', '1')
    else if (opts.thinking === false) fd.append('thinking_mode', '0')
    // 多模态：附带图片（后端按 filename 扩展名 + 文件头魔数双重校验）
    for (const img of opts.images || []) {
      fd.append('images', img, (img as File).name || 'image.png')
    }
    return postSse('/api/chat', { method: 'POST', body: fd }, h)
  },
  /** 改写：直连 LLM 快通道，强约束只产出可直接替换的纯代码。 */
  rewriteSelection(req: SelectionAiRequest, h: SseStreamHandlers): Promise<void> {
    return postSse(
      '/api/selection_ai',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...req, mode: 'rewrite' }),
      },
      h,
    )
  },
}

export const voiceApi = {
  status() { return fetch('/api/voice/status').then(r => r.json() as Promise<{ browser_fallback: boolean; stt_configured: boolean; tts_configured: boolean }>) },
  async transcribe(blob: Blob, language = ''): Promise<string> {
    const fd = new FormData(); fd.append('file', blob, 'voice.webm'); fd.append('language', language)
    const res = await fetch('/api/voice/transcribe', { method: 'POST', body: fd })
    const body = await res.json() as { ok?: boolean; text?: string; error?: string }
    if (!res.ok || body.ok === false) throw new Error(body.error || '语音识别失败')
    return body.text || ''
  },
  async dialogue(text: string, mainResult = '', forward = true): Promise<{ text: string; forward_to_main?: boolean }> {
    const res = await fetch('/api/voice/dialogue', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, main_result: mainResult, forward }),
    })
    const body = await res.json() as { ok?: boolean; text?: string; error?: string; forward_to_main?: boolean }
    if (!res.ok || body.ok === false) throw new Error(body.error || '语音协作失败')
    return { text: body.text || '', forward_to_main: body.forward_to_main }
  },
  async speech(text: string, voice = ''): Promise<Blob> {
    const res = await fetch('/api/voice/speech', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text, voice }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => ({})) as { error?: string }
      throw new Error(body.error || '语音合成失败')
    }
    return res.blob()
  },
}
