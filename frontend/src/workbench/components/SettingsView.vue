<script setup lang="ts">
// 统一设置页：左侧分组导航（用量费用 / 网络搜索 / MCP / 智能体），右侧对应面板。
// 网络搜索与 URL 获取走 settingsApi（复用 /api/config 的保存通道，密钥不回显）；
// MCP 走 mcpApi；智能体为本地预设（localStorage），后续可升级为后端持久化。
import { ref, watch, computed } from 'vue'
import {
  settingsApi, mcpApi, harnessApi,
  WEB_SEARCH_PROVIDERS, WEB_FETCH_PROVIDERS,
  type SettingsConfigInfo, type ModelConfigInfo, type ProviderOption, type McpServer,
  type McpCapabilityCandidate,
  type BudgetStatus, type TraceSummary,
  type McpAutoConnectCandidate, type McpAutoConnectConfig, type McpProbeRes,
} from '../api'
import Icon from './Icon.vue'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

type Tab = 'usage' | 'search' | 'mcp' | 'agent'
const tab = ref<Tab>('usage')

const loading = ref(false)
const errorMsg = ref('')
const savedMsg = ref('')

// ---------------- Token 用量 / 费用控制 ----------------
const usageLoading = ref(false)
const usageSaving = ref(false)
const usageError = ref('')
const usageSaved = ref('')
const budget = ref<BudgetStatus | null>(null)
const traceSummary = ref<TraceSummary | null>(null)
const budgetLimit = ref('0')
const perMinuteCalls = ref('0')
const perMinuteCost = ref('0')

const budgetRemaining = computed(() => {
  const limit = Number(budget.value?.global_limit || 0)
  if (limit <= 0) return null
  return Math.max(0, limit - Number(budget.value?.global_spent || 0))
})
const providerUsage = computed(() => Object.entries(traceSummary.value?.by_provider || {})
  .map(([provider, item]) => ({ provider, ...item, ...providerMeta(provider) }))
  .sort((a, b) => b.tokens - a.tokens))

function providerMeta(provider: string): { label: string; kind: string; simulated: boolean } {
  const key = (provider || '').toLowerCase()
  if (key === 'mock') return { label: 'mock', kind: '离线估算', simulated: true }
  if (key === 'fake') return { label: 'fake', kind: '自动化测试', simulated: true }
  if (key === '?' || !key) return { label: '未知来源', kind: '无用量', simulated: true }
  if (key === 'flow') return { label: '内部工作流', kind: '无模型调用', simulated: true }
  if (key === 'ollama') return { label: 'Ollama', kind: '本地模型', simulated: false }
  if (key === 'llamacpp') return { label: 'llama.cpp', kind: '本地模型', simulated: false }
  return { label: provider, kind: '云端模型', simulated: false }
}

function money(value: number | null | undefined): string {
  return `¥${Number(value || 0).toFixed(4)}`
}
function tokens(value: number | null | undefined): string {
  return Number(value || 0).toLocaleString('zh-CN')
}

async function loadUsage() {
  usageLoading.value = true
  usageError.value = ''
  try {
    const [b, t] = await Promise.all([harnessApi.budget(), harnessApi.trace(1)])
    budget.value = b.status
    traceSummary.value = t.summary
    budgetLimit.value = String(b.status.global_limit || 0)
    perMinuteCalls.value = String(b.status.per_minute_calls_limit || 0)
    perMinuteCost.value = String(b.status.per_minute_cost_limit || 0)
  } catch (e) {
    usageError.value = (e as { message?: string }).message || '读取用量失败'
  } finally {
    usageLoading.value = false
  }
}

async function saveUsage() {
  const values = [budgetLimit.value, perMinuteCalls.value, perMinuteCost.value].map(Number)
  if (values.some(v => !Number.isFinite(v) || v < 0)) {
    usageError.value = '预算和限流必须是不小于 0 的数字；0 表示不限。'
    return
  }
  usageSaving.value = true
  usageError.value = ''
  usageSaved.value = ''
  try {
    const r = await harnessApi.setUsageLimits(values[0], values[1], values[2])
    if (!r.ok) {
      usageError.value = r.error || '保存失败'
      return
    }
    usageSaved.value = '用量控制已保存'
    await loadUsage()
  } catch (e) {
    usageError.value = (e as { message?: string }).message || '保存失败'
  } finally {
    usageSaving.value = false
  }
}

// ---------------- 网络搜索 / URL 获取 ----------------
const cfg = ref<SettingsConfigInfo & ModelConfigInfo | null>(null)
const wsProvider = ref<ProviderOption['value']>('builtin_auto')
const wsApiKey = ref('')
const wsApiUrl = ref('')
const wsPreferBuiltin = ref(false)
const wfProvider = ref<ProviderOption['value']>('builtin')
const wfApiKey = ref('')
const wfApiUrl = ref('')
const showWsKey = ref(false)
const showWfKey = ref(false)
const searchSaving = ref(false)

const wsNeedsKey = computed(() => WEB_SEARCH_PROVIDERS.find(p => p.value === wsProvider.value)?.needs_key)
const wsNeedsUrl = computed(() => WEB_SEARCH_PROVIDERS.find(p => p.value === wsProvider.value)?.needs_url)
const wfNeedsKey = computed(() => WEB_FETCH_PROVIDERS.find(p => p.value === wfProvider.value)?.needs_key)
const wfNeedsUrl = computed(() => WEB_FETCH_PROVIDERS.find(p => p.value === wfProvider.value)?.needs_url)

// ---------------- MCP ----------------
const mcpServers = ref<McpServer[]>([])
const mcpLoading = ref(false)
const mcpError = ref('')
const mcpForm = ref({ key: '', label: '', transport: 'stdio' as 'stdio' | 'http', command: '', args: '', url: '' })
const mcpAdding = ref(false)
const mcpCapabilities = ref<{ active: Record<string, McpCapabilityCandidate>; pending: Record<string, McpCapabilityCandidate> }>({ active: {}, pending: {} })
const mcpDiscovering = ref('')
const mcpWebLearn = ref(true)
const mcpSearch = ref('')
const mcpSearching = ref(false)

// ---------------- P1：MCP 自动连接向导状态机 ----------------
type WizardStep = 1 | 2 | 3 | 4 | 5
type FillState = 'idle' | 'loading' | 'populated' | 'empty' | 'error' | 'edge' | 'registering'
interface DiagNode { step: number; label: string; state: 'done' | 'active' | 'fail' | 'warn' | 'idle'; sub?: string }
interface AcSource { domain: string; trust: 'official' | 'community' | 'unknown'; url: string }

const wizardStep = ref<WizardStep>(1)
const fillState = ref<FillState>('idle')
const acCandidates = ref<McpAutoConnectCandidate[]>([])
const acSelected = ref<McpAutoConnectCandidate | null>(null)
const acProbing = ref(false)
const acProbe = ref<McpProbeRes | null>(null)
const acNeedRegister = ref(false)
const acConfirmOpen = ref(false)
const acConfirmBusy = ref(false)
const acConfirmError = ref('')
const acShowRaw = ref(false)

// 注册代管（C4）：v1 后端诚实降级为 L2（人工回填凭证）
const regOpen = ref(false)
const regTier = ref<'L0' | 'L1' | 'L2'>('L2')
const regTaskId = ref('')
const regUrl = ref('')
const regCredential = ref('')
const regSecretKey = ref('')
const regError = ref('')
const regCommitting = ref(false)

// ---------------- 智能体（本地预设） ----------------
interface AgentPreset { id: string; name: string; model: string; system: string }
const agents = ref<AgentPreset[]>([])
const agentForm = ref({ name: '', model: '', system: '' })
const AGENT_KEY = 'docmind_agent_presets'

function loadAgents() {
  try {
    const raw = localStorage.getItem(AGENT_KEY)
    agents.value = raw ? (JSON.parse(raw) as AgentPreset[]) : []
  } catch { agents.value = [] }
}
function saveAgents() {
  try { localStorage.setItem(AGENT_KEY, JSON.stringify(agents.value)) } catch { /* ignore */ }
}
function addAgent() {
  const name = agentForm.value.name.trim()
  if (!name) return
  agents.value.push({
    id: `agent-${Date.now().toString(16)}`,
    name,
    model: agentForm.value.model.trim(),
    system: agentForm.value.system.trim(),
  })
  saveAgents()
  agentForm.value = { name: '', model: '', system: '' }
}
function removeAgent(id: string) {
  agents.value = agents.value.filter(a => a.id !== id)
  saveAgents()
}

async function loadConfig() {
  if (!props.visible) return
  loading.value = true
  errorMsg.value = ''
  try {
    const c = await settingsApi.get()
    cfg.value = c
    wsProvider.value = c.web_search_provider
    wsApiKey.value = ''
    wsApiUrl.value = c.web_search_api_url || ''
    wsPreferBuiltin.value = c.web_search_prefer_builtin
    wfProvider.value = c.web_fetch_provider
    wfApiKey.value = ''
    wfApiUrl.value = c.web_fetch_api_url || ''
  } catch (e) {
    errorMsg.value = (e as { message?: string }).message || '读取配置失败'
  } finally {
    loading.value = false
  }
}

async function saveSearch() {
  searchSaving.value = true
  errorMsg.value = ''
  savedMsg.value = ''
  try {
    const res = await settingsApi.save({
      web_search_provider: wsProvider.value as SettingsConfigInfo['web_search_provider'],
      web_search_api_key: wsApiKey.value.trim(),
      web_search_api_url: wsApiUrl.value.trim(),
      web_search_prefer_builtin: wsPreferBuiltin.value,
      web_fetch_provider: wfProvider.value as SettingsConfigInfo['web_fetch_provider'],
      web_fetch_api_key: wfApiKey.value.trim(),
      web_fetch_api_url: wfApiUrl.value.trim(),
    })
    if (res.ok === false) {
      errorMsg.value = res.error || '保存失败'
      return
    }
    savedMsg.value = '已保存'
    if (res.warnings?.length) errorMsg.value = res.warnings.join('；')
  } catch (e) {
    errorMsg.value = (e as { message?: string }).message || '保存失败'
  } finally {
    searchSaving.value = false
  }
}

async function loadMcp() {
  mcpLoading.value = true
  mcpError.value = ''
  try {
    const [r, caps] = await Promise.all([mcpApi.servers(), mcpApi.capabilities()])
    mcpServers.value = r.servers || []
    mcpCapabilities.value = { active: caps.active || {}, pending: caps.pending || {} }
  } catch (e) {
    mcpError.value = (e as { message?: string }).message || '读取 MCP 失败'
  } finally {
    mcpLoading.value = false
  }
}

async function discoverMcp(key: string) {
  mcpDiscovering.value = key
  mcpError.value = ''
  try {
    const r = await mcpApi.discover(key, mcpWebLearn.value)
    if (!r.ok) { mcpError.value = r.error || '能力发现失败'; return }
    await loadMcp()
  } catch (e) { mcpError.value = (e as { message?: string }).message || '能力发现失败' }
  finally { mcpDiscovering.value = '' }
}

// 诊断节点：搜索→抓取→抽取→校验→试连→确认（Phase 2 六节点）
const AC_STEPS = ['搜索', '抓取', '抽取', '校验', '试连', '确认'] as const
function buildDiag(over: Partial<Record<number, DiagNode['state']>>, subs: Partial<Record<number, string>> = {}): DiagNode[] {
  return AC_STEPS.map((label, i) => ({ step: i + 1, label, state: over[i + 1] || 'idle', sub: subs[i + 1] }))
}
const acDiag = computed<DiagNode[]>(() => {
  if (fillState.value === 'loading') return buildDiag({ 1: 'active' })
  if (fillState.value === 'error') return buildDiag({ 1: 'done', 2: 'done', 3: 'fail' }, { 3: '命令不安全，已转手动填写' })
  if (fillState.value === 'empty') return buildDiag({ 1: 'done', 2: 'done', 3: 'done', 4: 'warn' }, { 4: '未找到可直接连的能力' })
  if (acCandidates.value.length === 0) return buildDiag({})
  const sel = acSelected.value
  const base: Partial<Record<number, DiagNode['state']>> = { 1: 'done', 2: 'done', 3: 'done' }
  if (sel) base[4] = sel.trust === 'source_untrusted' ? 'warn' : 'done'
  if (acProbing.value) base[5] = 'active'
  else if (acProbe.value) {
    base[5] = acProbe.value.probe_ok ? 'done' : 'fail'
    base[6] = acProbe.value.probe_ok ? 'active' : 'idle'
  }
  const subs: Partial<Record<number, string>> = {}
  if (sel && sel.trust === 'source_untrusted') subs[4] = '来源不可信，请在确认前人工核实'
  if (acProbe.value && !acProbe.value.probe_ok) subs[5] = acProbe.value.error || '连接失败'
  return buildDiag(base, subs)
})

function acStepState(n: WizardStep): 'done' | 'active' | 'idle' | 'fail' {
  if (n === 2 && fillState.value === 'error') return 'fail'
  if (wizardStep.value > n) return 'done'
  if (wizardStep.value === n) return 'active'
  return 'idle'
}

const acTrustTag = (c: McpAutoConnectCandidate | null) => c && c.trust === 'source_untrusted' ? '未知来源' : '官方 / 可信'
const acSources = (c: McpAutoConnectCandidate | null): AcSource[] => {
  if (!c) return []
  const url = c.config.provenance?.url || ''
  const domain = c.config.provenance?.domain || ''
  if (!domain) return []
  return [{ domain, trust: c.trust === 'source_untrusted' ? 'unknown' : 'official', url }]
}
const acResolvedCmd = (c: McpAutoConnectCandidate | null) => c ? [c.config.command, ...(c.config.args || [])].filter(Boolean).join(' ') : ''
const acEnvMasked = (c: McpAutoConnectCandidate | null) => {
  if (!c) return '无'
  const env = c.config.env || {}
  const keys = Object.keys(env)
  if (keys.length === 0) return '无'
  return keys.map(k => `${k}=已填写（脱敏）`).join('  ')
}
const acRawJson = (c: McpAutoConnectCandidate | null) =>
  c ? JSON.stringify({ key: candidateKey(c), ...c.config }, null, 2) : ''
const candidateKey = (c: McpAutoConnectCandidate | null): string => {
  if (!c) return 'server'
  const p = c.config.provenance || {}
  // 可读标识优先取包名/URL 尾段（server-github / mcp-server-git / docker），回退 command
  const args = (c.config.args || []).filter(a => a && !a.startsWith('-'))
  const pkg = args.find(a => a.includes('/') || a.includes('@')) || args[args.length - 1] || ''
  const tail = (pkg.split('/').pop() || '').replace(/^@/, '') || c.config.command || c.config.url || 'x'
  // 后端 save_server 的 key 白名单：仅字母数字 / 下划线 / 连字符，且 ≤40。
  // domain 含点（github.com）等非法字符，这里统一规整，避免「确认落盘」被拒。
  const slug = `${p.domain || 'server'}-${tail}`
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
    .slice(0, 40)
    .replace(/[-_]+$/g, '')
  return slug || 'server'
}

async function autoConnectSearch() {
  const query = mcpSearch.value.trim()
  if (query.length < 2) { mcpError.value = '请输入至少 2 个字符，例如 Godot、Unity、数据库。'; return }
  mcpSearching.value = true
  fillState.value = 'loading'
  wizardStep.value = 2
  mcpError.value = ''
  acCandidates.value = []
  acSelected.value = null
  acProbe.value = null
  try {
    const r = await mcpApi.autoConnectSearch(query, mcpWebLearn.value)
    acCandidates.value = r.candidates || []
    // 「没找到候选」是正常业务结果（ok:false + search_error 说明原因），不是传输失败。
    // 只有「确实没有候选」走 empty（友好引导手动）；有候选才进预览。
    const noCandidate = acCandidates.value.length === 0
    if (noCandidate) {
      fillState.value = 'empty'
      wizardStep.value = 2
      // search_error 是后端给出的可展示原因（未联网/无链接/读取失败/无命令）
      mcpError.value = r.search_error || r.error || ''
    } else {
      fillState.value = 'populated'
      wizardStep.value = 3
      if (r.search_error) mcpError.value = r.search_error
    }
  } catch (e) {
    mcpError.value = (e as { message?: string }).message || '搜索失败'
    fillState.value = 'error'
  } finally { mcpSearching.value = false }
}

async function probeCandidate(c: McpAutoConnectCandidate) {
  acSelected.value = c
  acProbing.value = true
  acProbe.value = null
  wizardStep.value = 4
  mcpError.value = ''
  try {
    const r = await mcpApi.probeCandidate(c.config)
    acProbe.value = r
    if (r.probe_ok) wizardStep.value = 5
  } catch (e) {
    acProbe.value = { ok: false, probe_ok: false, tools: [], error: (e as { message?: string }).message || '连接失败' }
  } finally { acProbing.value = false }
}

function openConfirm() { acConfirmOpen.value = true; acShowRaw.value = false; acConfirmError.value = '' }
function closeConfirm() { acConfirmOpen.value = false }

async function confirmConnect() {
  const c = acSelected.value
  if (!c) return
  acConfirmBusy.value = true
  acConfirmError.value = ''
  try {
    const r = await mcpApi.confirmConnect(candidateKey(c), c.config)
    if (!r.ok) { acConfirmError.value = r.error || '添加失败'; return }
    acConfirmOpen.value = false
    fillState.value = 'idle'
    acCandidates.value = []
    acSelected.value = null
    acProbe.value = null
    wizardStep.value = 1
    await loadMcp()
  } catch (e) {
    acConfirmError.value = (e as { message?: string }).message || '添加失败'
  } finally { acConfirmBusy.value = false }
}

// 注册代管（C4）：先问后端定档（L0/L1/L2），v1 后端诚实降级为 L2；不可达时回退 L2
async function openRegister(c: McpAutoConnectCandidate) {
  acSelected.value = c
  regCredential.value = ''
  regSecretKey.value = c.config.provenance?.domain || 'provider'
  regError.value = ''
  regOpen.value = true
  regTier.value = 'L2'
  regUrl.value = c.config.provenance?.url || ''
  regTaskId.value = `ac-${Date.now().toString(16)}`
  try {
    const r = await mcpApi.registerStart(candidateKey(c), c.config, regSecretKey.value)
    if (r.ok) {
      regTier.value = r.tier
      regUrl.value = r.url || regUrl.value
      regTaskId.value = r.task_id || regTaskId.value
    }
  } catch {
    regTier.value = 'L2' // 后端不可达：诚实降级为 L2（与 v1 设计一致）
  }
}
async function commitRegister() {
  if (!regCredential.value.trim()) { regError.value = '请先填写凭证'; return }
  regCommitting.value = true
  regError.value = ''
  try {
    const r = await mcpApi.registerCommit(regTaskId.value, { [regSecretKey.value]: regCredential.value.trim() })
    if (!r.ok) { regError.value = r.error || '凭证保存失败'; return }
    if (acSelected.value) {
      acSelected.value = {
        ...acSelected.value,
        config: {
          ...acSelected.value.config,
          env: { ...(acSelected.value.config.env || {}), [regSecretKey.value.toUpperCase()]: `@secret:${regSecretKey.value}` },
        },
      }
    }
    regOpen.value = false
    acNeedRegister.value = false
  } catch (e) {
    regError.value = (e as { message?: string }).message || '凭证保存失败'
  } finally { regCommitting.value = false }
}

async function decideMcpCapability(key: string, approved: boolean) {
  mcpError.value = ''
  try {
    const r = await mcpApi.decideCapability(key, approved)
    if (!r.ok) { mcpError.value = r.error || '保存能力失败'; return }
    await loadMcp()
  } catch (e) { mcpError.value = (e as { message?: string }).message || '保存能力失败' }
}

async function addMcp() {
  const { key, label, transport, command, args, url } = mcpForm.value
  if (!key.trim()) return
  mcpAdding.value = true
  mcpError.value = ''
  try {
    const config: Record<string, unknown> = { label: label.trim() || key.trim() }
    if (transport === 'stdio') {
      config.command = command.trim()
      config.args = args.split(/\s+/).filter(Boolean)
    } else {
      config.url = url.trim()
    }
    const r = await mcpApi.save(key.trim(), config)
    if (!r.ok) {
      mcpError.value = r.error || '添加失败'
      return
    }
    mcpForm.value = { key: '', label: '', transport: 'stdio', command: '', args: '', url: '' }
    await loadMcp()
  } catch (e) {
    mcpError.value = (e as { message?: string }).message || '添加失败'
  } finally {
    mcpAdding.value = false
  }
}

async function removeMcp(key: string) {
  mcpError.value = ''
  try {
    const r = await mcpApi.remove(key)
    if (!r.ok) { mcpError.value = r.error || '移除失败'; return }
    await loadMcp()
  } catch (e) {
    mcpError.value = (e as { message?: string }).message || '移除失败'
  }
}

// immediate：组件在首次打开设置时才由 App 异步挂载，挂载即 visible=true，需立即加载
watch(() => props.visible, async (v) => {
  if (!v) return
  await Promise.all([loadConfig(), loadMcp(), loadUsage()])
  loadAgents()
  tab.value = 'usage'
  savedMsg.value = ''
  usageSaved.value = ''
}, { immediate: true })

function close() { emit('close') }
</script>

<template>
  <div v-if="visible" class="sv-mask" @mousedown.self="close">
    <div class="sv-box" role="dialog" aria-modal="true">
      <div class="sv-head">
        <h3 class="sv-title">设置</h3>
        <button class="sv-x" @click="close" title="关闭">×</button>
      </div>
      <div class="sv-body">
        <!-- 左侧分组导航 -->
        <nav class="sv-nav">
          <button class="sv-nav-item" :class="{ on: tab === 'usage' }" @click="tab = 'usage'">用量与费用</button>
          <button class="sv-nav-item" :class="{ on: tab === 'search' }" @click="tab = 'search'">网络搜索</button>
          <button class="sv-nav-item" :class="{ on: tab === 'mcp' }" @click="tab = 'mcp'">MCP</button>
          <button class="sv-nav-item" :class="{ on: tab === 'agent' }" @click="tab = 'agent'">智能体</button>
        </nav>

        <!-- 右侧内容 -->
        <div class="sv-content">
          <!-- Token 用量与费用 -->
          <section v-show="tab === 'usage'" class="sv-panel">
            <div class="sv-section-head">
              <div>
                <h4 class="sv-h4">Token 用量与费用</h4>
                <p class="sv-hint">统计来自逐轮运行账本，不保存对话正文。Token 表示上下文处理量，不等于费用；云端模型按配置单价估算，本地模型费用为 ¥0。</p>
              </div>
              <button class="sv-mini" :disabled="usageLoading" @click="loadUsage">刷新</button>
            </div>

            <div class="sv-meter-grid" aria-label="Token 用量概览">
              <div class="sv-meter">
                <span>输入 Token</span>
                <b>{{ tokens(traceSummary?.prompt_tokens) }}</b>
              </div>
              <div class="sv-meter">
                <span>输出 Token</span>
                <b>{{ tokens(traceSummary?.completion_tokens) }}</b>
              </div>
              <div class="sv-meter">
                <span>总 Token</span>
                <b>{{ tokens(traceSummary?.total_tokens) }}</b>
              </div>
              <div class="sv-meter">
                <span>今日费用</span>
                <b>{{ money(budget?.day_spent) }}</b>
              </div>
              <div class="sv-meter">
                <span>累计费用</span>
                <b>{{ money(budget?.global_spent) }}</b>
              </div>
              <div class="sv-meter">
                <span>预算余额</span>
                <b>{{ budgetRemaining === null ? '不限' : money(budgetRemaining) }}</b>
              </div>
            </div>

            <div v-if="providerUsage.length" class="sv-provider-list">
              <div class="sv-provider-head"><span>模型供应商</span><span>类型</span><span>Token</span><span>费用</span></div>
              <div v-for="item in providerUsage" :key="item.provider" class="sv-provider-row" :class="{ simulated: item.simulated }">
                <b>{{ item.label }}</b>
                <span class="sv-kind">{{ item.kind }}</span>
                <span :title="`输入 ${tokens(item.prompt_tokens)} / 输出 ${tokens(item.completion_tokens)}`">{{ tokens(item.tokens) }}</span>
                <span>{{ money(item.cost_cny) }}</span>
              </div>
            </div>
            <p v-if="providerUsage.some(item => item.simulated && item.tokens > 0)" class="sv-note">
              mock / fake 的 Token 是按文本估算的测试负载，用于验证上下文裁剪与性能，不代表 API 消耗，也不会计费。
            </p>
            <p v-else-if="!usageLoading" class="sv-hint">还没有模型调用记录。</p>

            <div class="sv-sep"></div>
            <h4 class="sv-h4">费用与调用上限</h4>
            <p class="sv-hint">达到累计预算后停止新的模型回合；每分钟限制用于抑制突发调用。所有输入填 0 表示不限。</p>
            <div class="sv-form-grid">
              <label class="sv-field">
                <span>累计预算上限（元）</span>
                <input v-model="budgetLimit" class="sv-input" type="number" min="0" step="0.01" inputmode="decimal" />
              </label>
              <label class="sv-field">
                <span>每分钟调用上限（次）</span>
                <input v-model="perMinuteCalls" class="sv-input" type="number" min="0" step="1" inputmode="numeric" />
              </label>
              <label class="sv-field">
                <span>每分钟费用上限（元）</span>
                <input v-model="perMinuteCost" class="sv-input" type="number" min="0" step="0.01" inputmode="decimal" />
              </label>
            </div>
            <p v-if="usageError" class="sv-err">{{ usageError }}</p>
            <p v-if="usageSaved" class="sv-ok">{{ usageSaved }}</p>
            <div class="sv-actions">
              <button class="sv-btn sv-primary" :disabled="usageSaving || usageLoading" @click="saveUsage">
                {{ usageSaving ? '保存中…' : '保存用量控制' }}
              </button>
            </div>
          </section>

          <!-- 网络搜索 -->
          <section v-show="tab === 'search'" class="sv-panel">
            <h4 class="sv-h4">网络搜索</h4>
            <p class="sv-hint">选择搜索服务商；内置（DDG / 百度 / Bing）无需 Key，API 类需填写 Key 或自建地址。</p>

            <label class="sv-label">搜索服务商</label>
            <select v-model="wsProvider" class="sv-input">
              <option v-for="p in WEB_SEARCH_PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
            <p v-if="wsNeedsKey || wsNeedsUrl" class="sv-hint">{{ WEB_SEARCH_PROVIDERS.find(p => p.value === wsProvider)?.desc }}</p>

            <template v-if="wsNeedsKey">
              <label class="sv-label">API Key
                <span class="sv-key-toggle" @click="showWsKey = !showWsKey">{{ showWsKey ? '隐藏' : '显示' }}</span>
              </label>
              <input v-model="wsApiKey" class="sv-input" :type="showWsKey ? 'text' : 'password'"
                     :placeholder="cfg?.web_search_has_key ? '已保存，留空表示不修改' : 'sk-...'" autocomplete="off" spellcheck="false" />
            </template>
            <template v-if="wsNeedsUrl">
              <label class="sv-label">API 地址（自建实例 / 网关）</label>
              <input v-model="wsApiUrl" class="sv-input" placeholder="https://your-instance" spellcheck="false" />
            </template>

            <label class="sv-toggle-row">
              <input type="checkbox" v-model="wsPreferBuiltin" />
              <span>优先使用模型内置 Web 工具（如模型本身支持联网）</span>
            </label>

            <div class="sv-sep"></div>
            <h4 class="sv-h4">URL 获取（网页正文）</h4>
            <label class="sv-label">URL 获取服务商</label>
            <select v-model="wfProvider" class="sv-input">
              <option v-for="p in WEB_FETCH_PROVIDERS" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
            <template v-if="wfNeedsKey">
              <label class="sv-label">API Key
                <span class="sv-key-toggle" @click="showWfKey = !showWfKey">{{ showWfKey ? '隐藏' : '显示' }}</span>
              </label>
              <input v-model="wfApiKey" class="sv-input" :type="showWfKey ? 'text' : 'password'"
                     :placeholder="cfg?.web_fetch_has_key ? '已保存，留空表示不修改' : 'sk-...'" autocomplete="off" spellcheck="false" />
            </template>
            <template v-if="wfNeedsUrl">
              <label class="sv-label">API 地址</label>
              <input v-model="wfApiUrl" class="sv-input" placeholder="https://your-instance" spellcheck="false" />
            </template>

            <p v-if="errorMsg" class="sv-err">{{ errorMsg }}</p>
            <p v-if="savedMsg" class="sv-ok">{{ savedMsg }}</p>
            <div class="sv-actions">
              <button class="sv-btn sv-primary" :disabled="searchSaving || loading" @click="saveSearch">
                {{ searchSaving ? '保存中…' : '保存' }}
              </button>
            </div>
          </section>

          <!-- MCP -->
          <section v-show="tab === 'mcp'" class="sv-panel" data-wizard="mcp-autoconnect">
            <h4 class="sv-h4">MCP 自动连接</h4>
            <p class="sv-hint">描述你想要的能力，DocMind 会读取官方文档、提取连接命令、试连并请你确认后写入本机。</p>
            <p v-if="mcpError" class="sv-err">{{ mcpError }}</p>

            <!-- C1：自动连接向导主流程（步骤条） -->
            <div class="sv-search-row">
              <input v-model="mcpSearch" class="sv-input" placeholder="搜索需要的能力，例如 Godot、Unity、数据库" @keyup.enter="autoConnectSearch" />
              <button class="sv-btn sv-primary" :disabled="mcpSearching" @click="autoConnectSearch">
                <Icon v-if="mcpSearching" name="loader" :size="16" class="dm-spin" />
                <template v-else><Icon name="zap" :size="16" /> 自动连接</template>
              </button>
            </div>
            <div class="sv-step-rail" data-role="wizard-rail">
              <button class="sv-step" :data-state="acStepState(1)" data-step="1"><Icon name="search" :size="16" />搜索</button>
              <button class="sv-step" :data-state="acStepState(2)" data-step="2"><Icon name="zap" :size="16" />自动填参</button>
              <button class="sv-step" :data-state="acStepState(3)" data-step="3"><Icon name="list" :size="16" />预览</button>
              <button class="sv-step" :data-state="acStepState(4)" data-step="4"><Icon name="activity" :size="16" />试连</button>
              <button class="sv-step" :data-state="acStepState(5)" data-step="5"><Icon name="shield-check" :size="16" />确认落盘</button>
            </div>

            <div class="sv-wizard-body" data-role="wizard-body">
              <!-- 加载中 -->
              <div v-if="fillState === 'loading'" class="sv-ac-state">
                <Icon name="loader" :size="20" class="dm-spin" />
                <span>正在读取官方文档并提取连接命令…</span>
              </div>
              <!-- 无候选 -->
              <div v-else-if="fillState === 'empty'" class="sv-ac-state">
                <Icon name="circle-alert" :size="20" />
                <span>没找到可直接连的能力，换个说法，或用下方「手动添加连接器」。</span>
              </div>
              <!-- 抽取失败 -->
              <div v-else-if="fillState === 'error'" class="sv-ac-state">
                <Icon name="circle-x" :size="20" />
                <span>文档里没找到可信任的命令，已转为手动填写（见下方手动添加）。</span>
              </div>

              <!-- C2：自动填参预览态（候选卡） -->
              <div v-for="c in acCandidates" :key="candidateKey(c)" class="sv-preview" data-role="mcp-preview"
                   :data-fill-state="acSelected === c ? 'registering' : (c.trust === 'source_untrusted' ? 'edge' : 'populated')"
                   v-show="fillState === 'populated' || (acSelected === c)">
                <div class="sv-preview-head">
                  <b>{{ c.config.provenance?.domain || 'server' }}</b>
                  <span class="sv-cap-badge" :data-trust="c.trust === 'source_untrusted' ? 'unknown' : 'official'">{{ acTrustTag(c) }}</span>
                </div>
                <label class="sv-label">将要运行的命令</label>
                <pre class="sv-cmd" data-role="command-preview">{{ acResolvedCmd(c) }}</pre>
                <label class="sv-label">参数</label>
                <pre class="sv-cmd">{{ (c.config.args || []).join(' ') || '无' }}</pre>
                <label class="sv-label">环境变量</label>
                <pre class="sv-cmd" data-role="env-preview">{{ acEnvMasked(c) }}</pre>

                <!-- C6：可信来源域徽标折叠态 -->
                <div class="sv-src-row" data-role="src-badges">
                  <span v-for="s in acSources(c)" :key="s.domain" class="sv-src-badge" :data-trust="s.trust">
                    <Icon name="globe" :size="16" />{{ s.domain }}
                    <span class="sv-src-tag" :data-tone="s.trust">{{ s.trust === 'official' ? '官方' : s.trust === 'community' ? '社区' : '未知' }}</span>
                  </span>
                  <a v-if="c.config.provenance?.url" class="sv-doc-link" :href="c.config.provenance.url" target="_blank" rel="noreferrer">
                    <Icon name="book-open" :size="16" />查看文档
                  </a>
                </div>

                <!-- C5：连接诊断图（试连时） -->
                <svg v-if="acSelected === c" class="mcp-diag" data-role="mcp-diag" viewBox="0 0 680 120" role="img" aria-label="连接诊断">
                  <line v-for="n in 5" :key="'seg'+n" class="mcp-seg"
                        :data-state="acDiag[n-1].state === 'done' || acDiag[n].state === 'done' ? 'done' : 'idle'"
                        :x1="70 + (n-1) * 110" y1="40" :x2="70 + n * 110" y2="40" />
                  <g v-for="(node, i) in acDiag" :key="'node'+i" class="mcp-node" :data-state="node.state" :data-step="node.step"
                     :transform="`translate(${40 + i * 110},40)`">
                    <circle class="mcp-ring" r="16" />
                    <text class="mcp-lbl" y="34">{{ node.label }}</text>
                    <text v-if="node.sub" class="mcp-sub" y="50">{{ node.sub }}</text>
                  </g>
                </svg>

                <div class="sv-row-actions">
                  <button class="sv-btn sv-primary" :disabled="acProbing" @click="probeCandidate(c)">
                    <Icon v-if="acProbing && acSelected === c" name="loader" :size="16" class="dm-spin" />
                    <template v-else>测试连接</template>
                  </button>
                  <button v-if="c.config.command_unresolved || acNeedRegister" class="sv-mini" @click="openRegister(c)">
                    <Icon name="lock" :size="16" />需要凭证
                  </button>
                  <button class="sv-mini" :disabled="acProbing" @click="acSelected === c ? (acSelected = null) : (acSelected = c)">
                    {{ acSelected === c ? '收起' : '编辑' }}
                  </button>
                </div>
                <p v-if="acSelected === c && acProbe && acProbe.probe_ok" class="sv-ok">
                  连上了，发现 {{ acProbe.tools?.length || 0 }} 个工具。
                </p>
                <button v-if="acSelected === c && acProbe && acProbe.probe_ok" class="sv-btn sv-primary sv-block" @click="openConfirm">
                  <Icon name="shield-check" :size="16" />确认添加并启用
                </button>
              </div>
            </div>

            <!-- C3：写盘确认卡（最重要） -->
            <div v-if="acConfirmOpen" class="sv-mask" data-dialog="mcp-confirm" @click.self="closeConfirm">
              <div class="sv-dialog" style="width: 460px">
                <div class="sv-dialog-head">
                  <Icon name="shield-check" :size="24" />
                  <div><h3 class="sv-title">确认添加这个连接器？</h3>
                  <p class="sv-hint">DocMind 会把下面这条配置写入本机并立即启用，之后你可以在对话里调用它的工具。</p></div>
                </div>
                <div class="sv-dialog-body" v-if="acSelected">
                  <label class="sv-label">将要运行的命令</label>
                  <pre class="sv-cmd" data-role="command-preview">{{ acResolvedCmd(acSelected) }}</pre>
                  <p class="sv-list-sub">环境变量：{{ acEnvMasked(acSelected) }}</p>
                  <label class="sv-label">参数来源</label>
                  <div class="sv-src-row" data-role="src-badges">
                    <span v-for="s in acSources(acSelected)" :key="s.domain" class="sv-src-badge" :data-trust="s.trust">
                      <Icon name="globe" :size="16" />{{ s.domain }}
                      <span class="sv-src-tag" :data-tone="s.trust">{{ s.trust === 'official' ? '官方' : s.trust === 'community' ? '社区' : '未知' }}</span>
                    </span>
                  </div>
                  <p class="sv-note" data-tone="neutral">这条命令会在本机启动一个进程来提供工具；它只在你主动调用时才运行，不会在后台自行动作。</p>
                  <p class="sv-note" data-tone="warn" v-if="acSelected.trust === 'source_untrusted'">该来源未被标记为官方渠道，请确认命令与公开文档一致后再启用。</p>
                  <button class="sv-expand" data-action="toggle-config" @click="acShowRaw = !acShowRaw">
                    <Icon name="chevron-down" :size="16" :class="acShowRaw ? 'dm-rot' : ''" />我想先看看完整配置
                  </button>
                  <pre v-show="acShowRaw" class="sv-detail" data-role="raw-config">{{ acRawJson(acSelected) }}</pre>
                  <p v-if="acConfirmError" class="sv-err">{{ acConfirmError }}</p>
                </div>
                <div class="sv-dialog-actions">
                  <button class="sv-mini" data-action="cancel" :disabled="acConfirmBusy" @click="closeConfirm">取消</button>
                  <button class="sv-btn" data-action="add-only" :disabled="acConfirmBusy" @click="confirmConnect">仅添加不启用</button>
                  <button class="sv-btn sv-primary" data-action="confirm-enable" :disabled="acConfirmBusy" @click="confirmConnect">
                    {{ acConfirmBusy ? '写入中…' : '确认添加并启用' }}
                  </button>
                </div>
              </div>
            </div>

            <!-- C4：注册代管弹窗（v1 诚实降级为 L2） -->
            <div v-if="regOpen" class="sv-mask" data-dialog="mcp-register" :data-tier="regTier" @click.self="regOpen = false">
              <div class="sv-dialog" style="width: 440px">
                <div class="sv-dialog-head">
                  <Icon name="lock" :size="24" />
                  <h3 class="sv-title">这一步需要你手动完成</h3>
                </div>
                <div class="sv-dialog-body">
                  <p class="sv-hint">这个连接器无法自动配置——先在官网创建账号并拿到连接凭证（API Key），填好后我帮你写入并测试。</p>
                  <ol class="sv-guide"><li>注册账号</li><li>创建凭证</li><li>复制 Key</li></ol>
                  <a v-if="regUrl" class="sv-btn sv-primary sv-ext" :href="regUrl" target="_blank" rel="noreferrer">
                    <Icon name="external-link" :size="16" />去官网创建凭证
                  </a>
                  <label class="sv-label">API Key / 连接凭证</label>
                  <div class="sv-input-wrap">
                    <Icon name="lock" :size="20" />
                    <input v-model="regCredential" class="sv-input" type="password" :placeholder="'粘贴 API Key'" autocomplete="off" spellcheck="false" />
                  </div>
                  <p class="sv-note" data-tone="neutral">凭证会存进本机钥匙串（OS keychain），仅该连接器调用时使用，不会外传。</p>
                  <p v-if="regError" class="sv-err">{{ regError }}</p>
                </div>
                <div class="sv-dialog-actions">
                  <button class="sv-mini" @click="regOpen = false">取消</button>
                  <button class="sv-btn sv-primary" data-action="continue" :disabled="regCommitting || !regCredential.trim()" @click="commitRegister">
                    {{ regCommitting ? '处理中…' : '写入并测试连接' }}
                  </button>
                </div>
              </div>
            </div>

            <ul class="sv-list" v-if="mcpServers.length">
              <li v-for="s in mcpServers" :key="s.key" class="sv-list-item">
                <div class="sv-list-main">
                  <span class="sv-list-name">{{ s.label || s.key }}</span>
                  <span class="sv-list-sub">{{ s.transport }}{{ s.enabled ? ' · 已启用' : ' · 未启用' }} · {{ (mcpCapabilities.active[s.key]?.domain || s.engine || '未发现能力') }}</span>
                </div>
                <div class="sv-row-actions">
                  <button class="sv-mini" :disabled="mcpDiscovering === s.key || !s.enabled" @click="discoverMcp(s.key)">{{ mcpDiscovering === s.key ? '发现中…' : '发现能力' }}</button>
                  <button class="sv-mini" @click="removeMcp(s.key)">移除</button>
                </div>
              </li>
            </ul>
            <p v-else-if="!mcpLoading" class="sv-hint">暂无 MCP Server。</p>
            <label class="sv-toggle-row"><input v-model="mcpWebLearn" type="checkbox" />允许联网补充工具说明来源（只生成候选，不自动启用）</label>
            <div v-for="(candidate, key) in mcpCapabilities.pending" :key="`pending-${key}`" class="sv-cap-card">
              <div><b>{{ key }} · {{ candidate.domain.toUpperCase() }}</b><span class="sv-list-sub">候选能力：{{ candidate.capabilities.join('、') || '通用 MCP 工具' }} · 置信度 {{ Math.round(candidate.confidence * 100) }}%</span></div>
              <div class="sv-row-actions"><button class="sv-mini sv-approve" @click="decideMcpCapability(key, true)">批准路由</button><button class="sv-mini" @click="decideMcpCapability(key, false)">拒绝</button></div>
            </div>

            <div class="sv-sep"></div>
            <h4 class="sv-h4">手动添加连接器（高级）</h4>
            <p class="sv-hint">自动连接没覆盖时，可手动填写并添加；命令与参数需以官方文档为准。</p>
            <label class="sv-label">标识 key</label>
            <input v-model="mcpForm.key" class="sv-input" placeholder="my-server" spellcheck="false" />
            <label class="sv-label">名称</label>
            <input v-model="mcpForm.label" class="sv-input" placeholder="我的服务" spellcheck="false" />
            <label class="sv-label">传输方式</label>
            <select v-model="mcpForm.transport" class="sv-input">
              <option value="stdio">stdio</option>
              <option value="http">http</option>
            </select>
            <template v-if="mcpForm.transport === 'stdio'">
              <label class="sv-label">命令</label>
              <input v-model="mcpForm.command" class="sv-input" placeholder="uvx" spellcheck="false" />
              <label class="sv-label">参数（空格分隔）</label>
              <input v-model="mcpForm.args" class="sv-input" placeholder="mcp-server-godot" spellcheck="false" />
            </template>
            <template v-else>
              <label class="sv-label">URL</label>
              <input v-model="mcpForm.url" class="sv-input" placeholder="https://.../mcp" spellcheck="false" />
            </template>
            <div class="sv-actions">
              <button class="sv-btn sv-primary" :disabled="mcpAdding" @click="addMcp">
                {{ mcpAdding ? '添加中…' : '添加' }}
              </button>
            </div>
          </section>

          <!-- 智能体 -->
          <section v-show="tab === 'agent'" class="sv-panel">
            <h4 class="sv-h4">智能体（本地预设）</h4>
            <p class="sv-hint">保存常用 Agent 预设（名称 / 模型 / 提示词），便于在对话前快速切换。当前存于本机浏览器。</p>
            <ul class="sv-list" v-if="agents.length">
              <li v-for="a in agents" :key="a.id" class="sv-list-item">
                <div class="sv-list-main">
                  <span class="sv-list-name">{{ a.name }}</span>
                  <span class="sv-list-sub">{{ a.model || '默认模型' }} · {{ (a.system || '').slice(0, 24) }}{{ (a.system || '').length > 24 ? '…' : '' }}</span>
                </div>
                <button class="sv-mini" @click="removeAgent(a.id)">删除</button>
              </li>
            </ul>
            <p v-else class="sv-hint">还没有智能体预设。</p>

            <div class="sv-sep"></div>
            <h4 class="sv-h4">添加智能体</h4>
            <label class="sv-label">名称</label>
            <input v-model="agentForm.name" class="sv-input" placeholder="Cherry 小助手" spellcheck="false" />
            <label class="sv-label">模型（留空=默认）</label>
            <input v-model="agentForm.model" class="sv-input" placeholder="qwen-plus" spellcheck="false" />
            <label class="sv-label">提示词 / 角色设定</label>
            <textarea v-model="agentForm.system" class="sv-input sv-textarea" rows="3" placeholder="你是一个专注于……的助手"></textarea>
            <div class="sv-actions">
              <button class="sv-btn sv-primary" :disabled="!agentForm.name.trim()" @click="addAgent">添加</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sv-mask {
  position: fixed; inset: 0; z-index: 2100;
  background: rgba(20, 24, 33, .34);
  display: flex; align-items: center; justify-content: center;
}
.sv-box {
  width: 760px; max-width: calc(100vw - 32px); height: 78vh; max-height: 720px;
  background: var(--bg, #fff); border: 1px solid var(--border);
  border-radius: 12px; box-shadow: 0 16px 48px rgba(15, 23, 42, .22);
  display: flex; flex-direction: column; overflow: hidden;
}
.sv-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 16px; border-bottom: 1px solid var(--border);
}
.sv-title { margin: 0; font-size: 15px; font-weight: 600; }
.sv-x {
  border: none; background: transparent; font-size: 20px; line-height: 1;
  color: var(--text-muted); cursor: pointer; padding: 0 6px;
}
.sv-x:hover { color: var(--text); }
.sv-body { display: flex; flex: 1; min-height: 0; }
.sv-nav {
  width: 168px; flex: none; border-right: 1px solid var(--border);
  padding: 10px 8px; display: flex; flex-direction: column; gap: 4px;
  background: var(--bg-selected);
}
.sv-nav-item {
  text-align: left; padding: 9px 12px; border: 1px solid transparent; border-radius: 8px;
  background: transparent; color: var(--text); cursor: pointer; font-size: 13px;
}
.sv-nav-item:hover { background: var(--bg-input); }
.sv-nav-item.on { background: var(--bg-input); border-color: var(--accent); color: var(--accent); font-weight: 600; }
.sv-content { flex: 1; min-width: 0; overflow-y: auto; padding: 16px 20px; }
.sv-panel { display: flex; flex-direction: column; gap: 4px; }
.sv-section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
.sv-section-head .sv-hint { margin-bottom: 0; }
.sv-h4 { margin: 0 0 6px; font-size: 14px; font-weight: 600; }
.sv-hint { font-size: 12px; color: var(--text-faint); margin: 2px 0 6px; line-height: 1.5; }
.sv-label {
  font-size: 12px; color: var(--text-muted); margin-top: 10px;
  display: flex; justify-content: space-between; align-items: center;
}
.sv-key-toggle { color: var(--accent); cursor: pointer; user-select: none; }
.sv-key-toggle:hover { text-decoration: underline; }
.sv-input {
  width: 100%; box-sizing: border-box; padding: 7px 10px; font-size: 13px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg-input, #fff); color: var(--text); outline: none;
}
.sv-input:focus { border-color: var(--accent); }
.sv-textarea { resize: vertical; font-family: inherit; }
.sv-meter-grid {
  display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 12px 0 8px; border-block: 1px solid var(--border);
}
.sv-meter { min-width: 0; padding: 12px 10px; border-right: 1px solid var(--border); }
.sv-meter:nth-child(3n) { border-right: 0; }
.sv-meter:nth-child(n+4) { border-top: 1px solid var(--border); }
.sv-meter span { display: block; color: var(--text-faint); font-size: 11px; }
.sv-meter b { display: block; margin-top: 3px; font-size: 17px; font-weight: 650; overflow-wrap: anywhere; }
.sv-provider-list { margin-top: 8px; border-top: 1px solid var(--border); }
.sv-provider-head, .sv-provider-row {
  display: grid; grid-template-columns: minmax(90px, 1fr) 100px 110px 90px;
  gap: 10px; align-items: center; padding: 7px 4px; font-size: 12px;
  border-bottom: 1px solid var(--border);
}
.sv-provider-head { color: var(--text-faint); }
.sv-provider-row span { text-align: right; color: var(--text-muted); }
.sv-provider-head span:not(:first-child) { text-align: right; }
.sv-provider-row .sv-kind { text-align: left; font-size: 11px; }
.sv-provider-row.simulated { background: color-mix(in srgb, var(--bg-selected) 55%, transparent); }
.sv-note { margin: 8px 0 0; padding-left: 9px; border-left: 2px solid var(--accent); color: var(--text-muted); font-size: 11px; line-height: 1.55; }
.sv-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
.sv-field { display: flex; flex-direction: column; gap: 5px; margin-top: 7px; font-size: 12px; color: var(--text-muted); }
.sv-toggle-row { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 13px; color: var(--text); cursor: pointer; }
.sv-toggle-row input { width: 16px; height: 16px; }
.sv-sep { height: 1px; background: var(--border); margin: 16px 0 4px; }
.sv-err { font-size: 12px; color: var(--danger); margin: 8px 0 0; }
.sv-ok { font-size: 12px; color: var(--green); margin: 8px 0 0; }
.sv-actions { display: flex; justify-content: flex-end; margin-top: 14px; }
.sv-btn {
  padding: 7px 18px; font-size: 13px; border-radius: 8px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); cursor: pointer;
}
.sv-btn:hover { border-color: var(--accent); }
.sv-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.sv-primary:disabled { opacity: .5; cursor: default; }
.sv-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.sv-list-item {
  display: flex; align-items: center; justify-content: space-between; gap: 10px;
  padding: 9px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg);
}
.sv-row-actions { display: flex; align-items: center; gap: 6px; flex: none; }
.sv-cap-card { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-top: 8px; padding: 9px 12px; border: 1px solid var(--accent); border-radius: 8px; background: color-mix(in srgb, var(--accent) 7%, var(--bg)); font-size: 12px; }
.sv-cap-card > div:first-child { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.sv-approve { color: var(--accent); }
.sv-search-row { display: grid; grid-template-columns: 1fr auto; gap: 8px; margin: 10px 0; }
.sv-directory-card { margin: 8px 0; padding: 11px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); font-size: 12px; }
.sv-directory-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.sv-directory-head p { margin: 3px 0 0; color: var(--text-muted); }
.sv-directory-card ol { margin: 8px 0; padding-left: 20px; color: var(--text-muted); line-height: 1.65; }
.sv-source-list { display: flex; flex-direction: column; gap: 3px; overflow-wrap: anywhere; }
.sv-source-list a { color: var(--accent); }
.sv-list-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sv-list-name { font-size: 13px; font-weight: 600; }
.sv-list-sub { font-size: 11px; color: var(--text-faint); }
.sv-mini {
  flex: none; font-size: 12px; padding: 4px 12px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); cursor: pointer;
}
.sv-mini:hover { border-color: var(--accent); color: var(--accent); }
.sv-mini:disabled { opacity: .5; cursor: default; }

/* ================= P1：MCP 自动连接向导 ================= */
.sv-step-rail { display: flex; gap: 6px; margin: 10px 0; flex-wrap: wrap; }
.sv-step {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 5px 11px; font-size: 12px; border-radius: var(--radius-pill);
  border: 1px solid var(--border); background: var(--bg); color: var(--text-muted); cursor: default;
}
.sv-step .dm-icon { display: inline-flex; }
.sv-step[data-state="done"] { border-color: var(--diag-done); color: var(--diag-done); }
.sv-step[data-state="active"] { border-color: var(--diag-active); color: var(--diag-active); background: var(--bg-selected); font-weight: 600; }
.sv-step[data-state="fail"] { border-color: var(--diag-fail); color: var(--diag-fail); }
.sv-step[data-state="idle"] { opacity: .75; }

.sv-ac-state { display: flex; align-items: center; gap: 8px; margin: 10px 0; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-hover); font-size: 12px; color: var(--text-muted); }
.sv-ac-state .dm-icon { color: var(--text-faint); flex: none; }

.sv-preview { margin: 10px 0; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--bg-raised); }
.sv-preview-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.sv-preview-head b { font-size: 13px; }
.sv-cap-badge { font-size: 11px; padding: 2px 9px; border-radius: var(--radius-pill); }
.sv-cap-badge[data-trust="official"] { color: var(--trust-verified-fg); background: var(--trust-verified-bg); }
.sv-cap-badge[data-trust="unknown"] { color: var(--trust-unknown-fg); background: var(--trust-unknown-bg); }

.sv-cmd {
  margin: 4px 0 2px; padding: 7px 10px; font-family: var(--font-mono); font-size: 12px; line-height: 1.5;
  background: var(--bg-hover); border: 1px solid var(--border); border-radius: var(--radius-md);
  color: var(--text); white-space: pre-wrap; word-break: break-all; max-height: 160px; overflow: auto;
}

.sv-src-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 6px 0; }
.sv-src-badge { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; padding: 2px 9px; border-radius: var(--radius-pill); border: 1px solid var(--border); color: var(--text-muted); }
.sv-src-badge .dm-icon { color: var(--text-faint); }
.sv-src-badge[data-trust="official"] { color: var(--trust-verified-fg); background: var(--trust-verified-bg); border-color: transparent; }
.sv-src-badge[data-trust="community"] { color: var(--trust-community-fg); background: var(--trust-community-bg); border-color: transparent; }
.sv-src-badge[data-trust="unknown"] { color: var(--trust-unknown-fg); background: var(--trust-unknown-bg); }
.sv-src-tag { font-size: 10px; opacity: .9; }
.sv-doc-link { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; color: var(--accent); text-decoration: none; }
.sv-doc-link:hover { text-decoration: underline; }

/* C5：诊断图 */
.mcp-diag { width: 100%; height: auto; margin: 10px 0; display: block; }
.mcp-seg { stroke-width: 2; }
.mcp-seg[data-state="done"] { stroke: var(--diag-seg-done); }
.mcp-seg[data-state="idle"] { stroke: var(--diag-seg-idle); }
.mcp-ring { fill: var(--bg-raised); stroke-width: 2; }
.mcp-node[data-state="done"] .mcp-ring { stroke: var(--diag-done); }
.mcp-node[data-state="active"] .mcp-ring { stroke: var(--diag-active); }
.mcp-node[data-state="fail"] .mcp-ring { stroke: var(--diag-fail); }
.mcp-node[data-state="warn"] .mcp-ring { stroke: var(--diag-warn); }
.mcp-node[data-state="idle"] .mcp-ring { stroke: var(--diag-idle); }
.mcp-lbl { font: 510 12px var(--font-ui); fill: var(--text); text-anchor: middle; }
.mcp-sub { font: 400 11px var(--font-ui); fill: var(--text-muted); text-anchor: middle; }

/* C3 / C4：确认卡 / 注册卡（复用 .sv-mask） */
.sv-dialog {
  background: var(--bg-raised); border: 1px solid var(--border);
  border-radius: var(--radius-xl); box-shadow: var(--shadow-pop);
  display: flex; flex-direction: column; max-width: calc(100vw - 32px); max-height: 86vh; overflow: hidden;
}
.sv-dialog-head { display: flex; align-items: flex-start; gap: 10px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
.sv-dialog-head .dm-icon { color: var(--accent); flex: none; margin-top: 1px; }
.sv-dialog-head h3 { margin: 0; font-size: 15px; font-weight: 600; }
.sv-dialog-body { padding: 14px 16px; overflow-y: auto; }
.sv-dialog-actions { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px 16px; border-top: 1px solid var(--border); }
.sv-dialog-actions .sv-btn, .sv-dialog-actions .sv-mini { margin-left: 0; }
.sv-expand { display: inline-flex; align-items: center; gap: 5px; margin-top: 10px; font-size: 12px; color: var(--accent); background: transparent; border: none; cursor: pointer; padding: 0; }
.sv-detail { margin: 8px 0 0; padding: 8px 10px; font-family: var(--font-mono); font-size: 11px; background: var(--bg-hover); border: 1px solid var(--border); border-radius: var(--radius-md); white-space: pre-wrap; word-break: break-all; max-height: 180px; overflow: auto; }
.sv-note { margin: 8px 0 0; padding: 8px 10px; border-radius: var(--radius-md); font-size: 11px; line-height: 1.55; }
.sv-note[data-tone="neutral"] { background: var(--bg-hover); color: var(--text-muted); border-left: 2px solid var(--border-strong); }
.sv-note[data-tone="warn"] { background: var(--trust-community-bg); color: var(--trust-community-fg); border-left: 2px solid var(--amber); }
.sv-guide { margin: 8px 0; padding-left: 20px; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.sv-input-wrap { display: flex; align-items: center; gap: 8px; border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg-raised); padding: 0 10px; }
.sv-input-wrap:focus-within { border-color: var(--accent); }
.sv-input-wrap .dm-icon { color: var(--text-faint); flex: none; }
.sv-input-wrap .sv-input { border: none; background: transparent; padding-left: 0; padding-right: 0; }
.sv-input-wrap .sv-input:focus { border-color: transparent; }
.sv-ext { display: inline-flex; align-items: center; gap: 6px; margin: 4px 0; text-decoration: none; }
.sv-block { width: 100%; margin-top: 10px; justify-content: center; }

.dm-spin { animation: dm-spin 1s linear infinite; transform-origin: center; }
.dm-rot { transition: transform var(--motion-base) var(--ease-standard); transform: rotate(180deg); }
@keyframes dm-spin { to { transform: rotate(360deg); } }

@media (max-width: 680px) {
  .sv-box { height: calc(100vh - 20px); max-width: calc(100vw - 20px); }
  .sv-nav { width: 128px; }
  .sv-meter-grid, .sv-form-grid { grid-template-columns: 1fr 1fr; }
  .sv-meter:nth-child(3n) { border-right: 1px solid var(--border); }
  .sv-meter:nth-child(2n) { border-right: 0; }
  .sv-meter:nth-child(n+3) { border-top: 1px solid var(--border); }
  .sv-provider-head, .sv-provider-row { grid-template-columns: minmax(70px, 1fr) 74px 82px 68px; gap: 6px; }
}
</style>
