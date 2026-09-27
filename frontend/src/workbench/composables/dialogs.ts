// 全局模态对话框（确认 / 输入 / 警告 / 保存冲突）：模块级单例状态，
// AppDialog.vue 负责渲染；任意模块可 await askConfirm 等得到用户选择。
import { ref } from 'vue'

export interface ConfirmSpec {
  kind: 'confirm'
  title: string
  message: string
  detail?: string
  confirmText?: string
  danger?: boolean
  resolve: (ok: boolean) => void
}
export interface PromptSpec {
  kind: 'prompt'
  title: string
  message?: string
  defaultValue: string
  placeholder?: string
  resolve: (v: string | null) => void
}
export interface AlertSpec {
  kind: 'alert'
  title: string
  message: string
  detail?: string
  resolve: () => void
}
export interface ConflictSpec {
  kind: 'conflict'
  path: string
  serverMtime: number
  resolve: (overwrite: boolean) => void
}
export type DialogSpec = ConfirmSpec | PromptSpec | AlertSpec | ConflictSpec

export const dialog = ref<DialogSpec | null>(null)

export function askConfirm(spec: Omit<ConfirmSpec, 'kind' | 'resolve'>): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'confirm', resolve }
  })
}
export function askPrompt(spec: Omit<PromptSpec, 'kind' | 'resolve'>): Promise<string | null> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'prompt', resolve }
  })
}
export function askAlert(spec: Omit<AlertSpec, 'kind' | 'resolve'>): Promise<void> {
  return new Promise((resolve) => {
    dialog.value = { ...spec, kind: 'alert', resolve: () => resolve() }
  })
}
/** 保存时服务端文件已被改动的覆盖确认（仅编辑会话内部使用） */
export function askConflict(path: string, serverMtime: number): Promise<boolean> {
  return new Promise((resolve) => {
    dialog.value = { kind: 'conflict', path, serverMtime, resolve }
  })
}

export function resolveDialog(value?: boolean | string | null) {
  const d = dialog.value
  if (!d) return
  dialog.value = null
  if (d.kind === 'confirm') d.resolve(value === true)
  else if (d.kind === 'prompt') d.resolve((value as string | null) ?? null)
  else if (d.kind === 'alert') d.resolve()
  else d.resolve(value === true)
}
