// AI 工具流编排器（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { rawJson, request } from './_base'

// ---------------------------------------------------------------- 阶段 3b：AI 工具流编排器
// 对应后端 flows.py：/api/flows（列表/保存/删除）+ /api/flows/run-step（单步受控执行）。
// 节点动作都在「受控动作白名单」内（后端强制），前端只负责编排与展示。
export interface FlowField {
  name: string
  label: string
  kind: 'region' | 'relpath' | 'changeset' | 'message' | 'text'
  required: boolean
  placeholder: string
}
export interface FlowAction {
  action: string
  label: string
  glyph: string
  cat: string
  mutating: boolean
  summary: string
  fields: FlowField[]
}
export interface FlowNode {
  id: string
  action: string
  label: string
  params: Record<string, string>
  x?: number
  y?: number
}
export interface FlowEdge { source: string; target: string }
export interface FlowDef {
  id: string
  name: string
  desc: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  created_at?: string
  updated_at?: string
}
/** 单步执行结果（与后端 execute_step 返回同构）。 */
export interface FlowStepResult {
  ok: boolean
  action: string
  status: 'ok' | 'fail'
  output: string
  detail?: Record<string, unknown> | null
  error?: string
  latency_ms?: number
  trace_written?: boolean
  /** 前端补充：参数字数 / 返回字数（trace step 同构用） */
  arg_chars?: number
  obs_chars?: number
}

export const flowsApi = {
  list(): Promise<{ ok: boolean; flows: FlowDef[] }> {
    return request<{ ok: boolean; flows: FlowDef[] }>('/api/flows')
  },
  /** 保存流程：走 rawJson 原样返回失败体（读 error/errors），不把 ok:false 抛错。 */
  save(flow: FlowDef): Promise<{ ok: boolean; flow?: FlowDef; error?: string; errors?: string[] }> {
    return rawJson('/api/flows', flow)
  },
  remove(id: string): Promise<{ ok: boolean; deleted?: string; error?: string }> {
    return rawJson('/api/flows/delete', { id })
  },
  /** 服务端执行单个受控步骤；失败也当数据返回（读 output/error 分支）。 */
  runStep(payload: {
    action: string
    params?: Record<string, string>
    run_id?: string
    flow_id?: string
    flow_name?: string
    node_id?: string
    finish?: boolean
  }): Promise<FlowStepResult> {
    return rawJson<FlowStepResult>('/api/flows/run-step', payload)
  },
}
