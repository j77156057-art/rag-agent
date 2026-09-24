// AI 问答页对话消息模型（与 /api/chat SSE 事件对齐）

import type { WorkflowState } from '../workbench/api'

export type TraceType = 'thought' | 'action' | 'observation' | 'reflection'

/** 挂在助手回合内的工作流卡片引用（与工作台 ChatMsg.workflow 同构） */
export interface AskWorkflowRef {
  workflowId: string
  seed?: Partial<WorkflowState>
}

export interface AskTraceItem {
  type: TraceType
  text: string
  at: number
  /** observation/reflection 回填前一个 action 的耗时（ms） */
  elapsedMs?: number
}

export type AskMsgStatus = 'streaming' | 'done' | 'stopped' | 'error'

export interface AskChatMsg {
  id: number
  role: 'user' | 'assistant'
  text: string
  status: AskMsgStatus
  /** 用户消息附带的图片（objectURL/dataURL） */
  images?: string[]
  trace: AskTraceItem[]
  reasoning: string
  /** SSE notice 事件（提示性通知） */
  notices: string[]
  /** 本回合发起的开发工作流：问答页内直接挂卡片，实时进度/审批无需跳工作台 */
  workflow?: AskWorkflowRef
  startedAt?: number
  finishedAt?: number
  error?: string
}
