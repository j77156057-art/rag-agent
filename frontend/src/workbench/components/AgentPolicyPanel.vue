<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { agentApi } from '../api'

const open = ref(false)
const route = ref('local')
const complexity = ref(0)
const auto = ref(false)
const connectors = ref<{ key: string; label: string; enabled: boolean }[]>([])
const externalPath = ref('')
const approval = ref(false)
const msg = ref('')
const approvals = ref<any[]>([])
const gateRequests = ref<any[]>([])
let gateTimer: ReturnType<typeof setInterval> | undefined

async function load() {
  try {
    const [routing, connectorResult, approvalResult, gateResult] = await Promise.all([
      agentApi.routing(), agentApi.connectors(), agentApi.approvals(), agentApi.approvalRequests(),
    ])
    auto.value = !!routing.auto_cloud_enabled
    connectors.value = connectorResult.connectors || []
    approvals.value = approvalResult.approvals || []
    gateRequests.value = gateResult.items || []
  } catch (error) { msg.value = '设置加载失败：' + (error as Error).message }
}

async function request() {
  if (!externalPath.value) { msg.value = '请填写目标文件路径'; return }
  try {
    if (!approval.value) {
      const result = await agentApi.requestApproval([externalPath.value], '外部文件修改申请')
      if (result.approval) approvals.value.push(result.approval)
      msg.value = '已创建审批请求'
      return
    }
    const result = await agentApi.permission({ path: externalPath.value, allow_external: true, approved: true })
    msg.value = result.recorded ? '已记录授权' : (result.reason || '失败')
  } catch (error) { msg.value = '授权请求失败：' + (error as Error).message }
}

async function decide(id: string, status: string) {
  try { await agentApi.decide(id, status); await load() }
  catch (error) { msg.value = '审批失败：' + (error as Error).message }
}

async function decideGate(id: string, approved: boolean) {
  try {
    const result = await agentApi.decideApprovalRequest(id, approved)
    if (!result.ok) throw new Error(result.error || '审批失败')
    await load()
  } catch (error) { msg.value = '工具动作审批失败：' + (error as Error).message }
}

function gateLabel(row: any) {
  const action = String(row.target || '').split(':')[1] || '动作'
  const labels: Record<string, string> = { click: '点击', type: '输入文字', drag: '拖拽', key: '按键', save: '保存' }
  return `${row.action === 'desktop_action' ? '桌面操作' : '工具动作'} · ${labels[action] || action}（参数已绑定）`
}

function onRoute(event: Event) {
  const detail = (event as CustomEvent).detail
  route.value = detail?.route || 'local'
  complexity.value = detail?.complexity || 0
}

onMounted(() => {
  load()
  gateTimer = setInterval(load, 3000)
  window.addEventListener('docmind-agent-route', onRoute)
})
onBeforeUnmount(() => {
  if (gateTimer) clearInterval(gateTimer)
  window.removeEventListener('docmind-agent-route', onRoute)
})

function show() { open.value = true }
defineExpose({ show })
</script>

<template>
  <div class="ap">
    <button class="ap-trigger" title="AI 在哪运行：本地显卡 / 云端自动切换，以及对外操作授权" @click="open = !open">
      <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true"><circle cx="6.5" cy="6.5" r="2.05" stroke="currentColor" stroke-width="1.05"/><path d="M6.5 1.3 V2.7 M6.5 10.3 V11.7 M1.3 6.5 H2.7 M10.3 6.5 H11.7 M2.8 2.8 L3.8 3.8 M9.2 9.2 L10.2 10.2 M10.2 2.8 L9.2 3.8 M3.8 9.2 L2.8 2.8" stroke="currentColor" stroke-width="1.05" stroke-linecap="round"/></svg>
      <span class="ap-label">AI 设置 · {{ route === 'local' ? '本地' : route }}</span>
    </button>
    <Teleport to="body">
      <div v-if="open" class="ap-backdrop" @click="open = false"></div>
      <div v-if="open" class="ap-pop" role="dialog" aria-label="AI 设置">
        <b>AI 在哪运行</b>
        <div>当前：{{ route === 'local' ? '本地显卡（免费）' : route }} · 本轮复杂度 {{ complexity }}</div>
        <div>难题自动转云端：{{ auto ? '已启用' : '已关闭' }}</div>
        <b v-if="gateRequests.length" class="ap-heading">待处理的工具动作</b>
        <div v-for="gate in gateRequests" :key="gate.id" class="ap-approval">
          <div>{{ gateLabel(gate) }}</div><small>请求 {{ gate.id }} · 风险 {{ gate.risk || 'L2' }}</small>
          <button @click="decideGate(gate.id, true)">批准</button><button @click="decideGate(gate.id, false)">拒绝</button>
        </div>
        <b class="ap-heading">外接工具</b>
        <div v-if="!connectors.length" class="ap-empty">暂无外接工具（游戏引擎等在顶栏「工具 → 游戏引擎」里连接）</div>
        <div v-for="connector in connectors" :key="connector.key">{{ connector.label || connector.key }} · {{ connector.enabled ? '可用' : '未启用' }}</div>
        <b class="ap-heading">改项目外文件的授权</b>
        <input v-model="externalPath" placeholder="项目文件夹之外的路径" />
        <label><input v-model="approval" type="checkbox" /> 我已确认，允许修改</label><button @click="request">提交授权</button><small>{{ msg }}</small>
        <div v-for="item in approvals" :key="item.id" class="ap-approval">
          <div>{{ item.id }} · {{ item.status === 'pending' ? '待批准' : item.status }}</div><div>{{ item.summary }}</div>
          <pre v-if="item.diff">{{ item.diff }}</pre><button v-if="item.status === 'pending'" @click="decide(item.id, 'approved')">批准</button><button v-if="item.status === 'pending'" @click="decide(item.id, 'rejected')">拒绝</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.ap{position:relative}.ap-trigger{display:inline-flex;align-items:center;gap:5px;background:transparent;color:var(--text-muted);border:1px solid var(--border-strong);border-radius:5px;padding:5px 9px;cursor:pointer;white-space:nowrap}.ap-trigger:hover{color:var(--text);border-color:var(--accent)}.ap-icon{font-size:12px}.ap-backdrop{position:fixed;inset:0;z-index:299;background:#0002}.ap-pop{position:fixed;right:16px;top:64px;max-height:calc(100vh - 90px);max-width:calc(100vw - 48px);overflow:auto;width:256px;padding:11px 12px;background:var(--bg-raised);border:1px solid var(--border-strong);border-radius:10px;box-shadow:0 14px 38px rgba(35,52,84,.2);z-index:300;font-size:11px;line-height:1.8}.ap-pop b{display:block;color:var(--text)}.ap-heading{margin-top:6px}.ap-empty{color:var(--text-faint)}.ap-pop input[type=text],.ap-pop input:not([type]){width:100%;box-sizing:border-box;margin:3px 0;padding:5px 7px;background:var(--bg);border:1px solid var(--border);border-radius:5px;color:var(--text);font:inherit}.ap-pop label{display:flex;gap:5px;align-items:center;color:var(--text-muted)}.ap-pop button{margin-top:5px;background:linear-gradient(180deg,#3b7ef2,#2f6fed);border:1px solid #2560d4;color:#fff;border-radius:5px;padding:4px 11px;font-size:11px;cursor:pointer}.ap-pop small{display:block;color:var(--green);margin-top:3px}.ap-approval{margin-top:6px;padding:6px 8px;background:var(--bg-hover);border:1px solid var(--border);border-radius:6px}.ap-approval pre{max-height:90px;overflow:auto;margin:4px 0;font:10px var(--font-mono)}.ap-approval button{margin-right:6px}
</style>
