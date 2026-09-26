// 工作流人工审批门的前端互斥状态：工作台对话台与问答首页共用。
// 卡片（WorkflowCard）打开方案选择/审批门时上抛 gate=true，期间锁住发送等操作；
// 离开对话前可选地确认并自动中断未终结工作流，避免卡片卸载后后端停在门里成为孤儿。
import { computed, ref } from 'vue'
import { agentApi } from '../api'
import { askConfirm } from './workbench'

export function useWorkflowGate(getActiveIds?: () => string[]) {
  // 正打开人工审批门的工作流集合：此时锁定对话台操作区（发送/工具/头部按钮），
  // 但不锁聊天滚动与编辑器——审批可以边看代码边做
  const gateOpenIds = ref<Set<string>>(new Set())
  const gateBlocking = computed(() => gateOpenIds.value.size > 0)
  function onWfGate(workflowId: string, open: boolean) {
    const next = new Set(gateOpenIds.value)
    if (open) next.add(workflowId); else next.delete(workflowId)
    gateOpenIds.value = next
  }
  /** 卡片随消息卸载时同步清门（如清空对话） */
  function resetGates() {
    gateOpenIds.value = new Set()
  }

  /**
   * 离开当前对话（切换/新建/清空）前调用：有未终结工作流时必须先征得同意，
   * 并在用户确认后尽力中断它们——否则卡片随消息卸载，后端工作流会停在
   * 审批门成为无法操作的孤儿。文件改动不会回滚，需在文案里明示。
   * 未提供 getActiveIds 时视为无活跃工作流，直接放行。
   */
  async function confirmLeaveWorkflows(): Promise<boolean> {
    const ids = getActiveIds ? getActiveIds() : []
    if (!ids.length) return true
    const ok = await askConfirm({
      title: '工作流仍在进行',
      message: `有 ${ids.length} 个工作流正在等待审批或执行中。离开后将无法继续操作，系统会自动中断它（工作流已产生的文件改动不会自动回滚）。确定离开？`,
      confirmText: '中断并离开',
    })
    if (!ok) return false
    await Promise.allSettled(ids.map(id =>
      agentApi.workflowInterrupt(id, '用户离开当前对话，自动中断')))
    return true
  }

  return { gateOpenIds, gateBlocking, onWfGate, resetGates, confirmLeaveWorkflows }
}
