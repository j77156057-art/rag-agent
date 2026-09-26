// /api/chat 问答流式入口（aiApi）（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { SelectionAiRequest, SseStreamHandlers, postSse } from './sse'
import { getSessionId } from './session'

export const aiApi = {
  /** 解释 / Review / 自由提问：走 ReAct agent（可 search_code/read_file/grep，引用文件行号）。
   *  web/thinking：逐请求的联网搜索 / 深度思考开关（后端默认均为关/模型默认）。
   *  session_id：当前标签页会话，用于隔离并发请求的逐请求开关。 */
  askGrounded(
    question: string,
    h: SseStreamHandlers,
    opts: { web?: boolean; thinking?: boolean | null; images?: Blob[] } = {},
  ): Promise<void> {
    const fd = new FormData()
    fd.append('question', question)
    fd.append('session_id', getSessionId())
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
