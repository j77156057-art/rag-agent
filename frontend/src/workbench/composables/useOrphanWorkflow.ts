// 孤儿工作流探测与恢复：刷新/重开后后端仍有未终结工作流、本地却没有它的审批卡片，
// 会停在审批门且无任何操作入口。挂载/切会话/发送收尾后探测一次，
// 提供「挂回对话（重建卡片）」与「中断」两个出口。工作台与问答首页共用。
import { computed, nextTick, ref } from 'vue'
import { agentApi } from '../api'
import type { WorkflowState } from '../api'
import { demoMode } from './demo'

const WF_STATUS_LABEL: Record<string, string> = {
  generating_options: '正在生成方案', awaiting_choice: '等待选择方案',
  awaiting_research: '等待联网检索', researching: '联网调研中',
  planning: '任务规划中', awaiting_plan_approval: '等待计划审批',
  awaiting_approval: '等待审核', planned: '待执行',
  executing: '子代理执行中', reviewing: '汇总检查中',
  completed: '已完成', failed: '已失败', interrupted: '已中断',
}

export interface UseOrphanWorkflowOptions {
  /** 本地消息流中是否已存在该工作流的卡片（有卡片在管的不算孤儿） */
  hasWorkflowCard: (workflowId: string) => boolean
  /** 把工作流以卡片消息形式追加到当前对话流 */
  appendWorkflowCard: (wf: WorkflowState) => void
  /** 挂回完成后的界面跟随（如滚动到底部） */
  onReattached?: () => void
}

export function useOrphanWorkflow(options: UseOrphanWorkflowOptions) {
  const orphanWf = ref<WorkflowState | null>(null)
  const orphanBusy = ref(false)
  const orphanError = ref('')
  const orphanStatusLabel = computed(() =>
    orphanWf.value ? WF_STATUS_LABEL[orphanWf.value.status] || orphanWf.value.status : '')

  async function detectOrphanWorkflow() {
    if (demoMode.value) return
    try {
      const r = await agentApi.workflowActive()
      const wf = r.ok ? (r.workflow ?? null) : null
      orphanWf.value = wf && !options.hasWorkflowCard(wf.workflow_id) ? wf : null
      orphanError.value = ''
    } catch {
      /* 服务未启动/不可达时不显示恢复条 */
    }
  }
  /** 把孤儿工作流以卡片形式挂回当前对话流；卡片挂载后会自行拉完整状态并订阅 SSE。 */
  async function reattachOrphan() {
    const wf = orphanWf.value
    if (!wf || orphanBusy.value) return
    orphanBusy.value = true
    try {
      orphanWf.value = null
      options.appendWorkflowCard(wf)
      await nextTick(() => options.onReattached?.())
    } finally {
      orphanBusy.value = false
    }
  }
  async function interruptOrphan() {
    const wf = orphanWf.value
    if (!wf || orphanBusy.value) return
    orphanBusy.value = true
    orphanError.value = ''
    try {
      const r = await agentApi.workflowInterrupt(
        wf.workflow_id, '页面刷新后卡片丢失，用户在孤儿恢复条中断')
      if (r.ok === false) throw new Error(r.error || '中断失败')
      orphanWf.value = null
    } catch (e) {
      orphanError.value = (e as { message?: string }).message || '中断失败，请重试'
    } finally {
      orphanBusy.value = false
    }
  }

  return {
    orphanWf, orphanBusy, orphanError, orphanStatusLabel,
    detectOrphanWorkflow, reattachOrphan, interruptOrphan,
  }
}
