<script setup lang="ts">
// 统一设置页：左侧分组导航（网络搜索 / MCP / 智能体），右侧对应面板。
// 网络搜索与 URL 获取走 settingsApi（复用 /api/config 的保存通道，密钥不回显）；
// MCP 走 mcpApi；智能体为本地预设（localStorage），后续可升级为后端持久化。
import { ref, watch, computed } from 'vue'
import {
  settingsApi, mcpApi,
  WEB_SEARCH_PROVIDERS, WEB_FETCH_PROVIDERS,
  type SettingsConfigInfo, type ModelConfigInfo, type ProviderOption, type McpServer,
} from '../api'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

type Tab = 'search' | 'mcp' | 'agent'
const tab = ref<Tab>('search')

const loading = ref(false)
const errorMsg = ref('')
const savedMsg = ref('')

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
    const r = await mcpApi.servers()
    mcpServers.value = r.servers || []
  } catch (e) {
    mcpError.value = (e as { message?: string }).message || '读取 MCP 失败'
  } finally {
    mcpLoading.value = false
  }
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

watch(() => props.visible, async (v) => {
  if (!v) return
  await loadConfig()
  await loadMcp()
  loadAgents()
  tab.value = 'search'
  savedMsg.value = ''
})

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
          <button class="sv-nav-item" :class="{ on: tab === 'search' }" @click="tab = 'search'">网络搜索</button>
          <button class="sv-nav-item" :class="{ on: tab === 'mcp' }" @click="tab = 'mcp'">MCP</button>
          <button class="sv-nav-item" :class="{ on: tab === 'agent' }" @click="tab = 'agent'">智能体</button>
        </nav>

        <!-- 右侧内容 -->
        <div class="sv-content">
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
          <section v-show="tab === 'mcp'" class="sv-panel">
            <h4 class="sv-h4">MCP 连接器</h4>
            <p class="sv-hint">管理内置与自定义 MCP Server；引擎连接器在对话中由 Agent 自动选择调用。</p>
            <p v-if="mcpError" class="sv-err">{{ mcpError }}</p>
            <ul class="sv-list" v-if="mcpServers.length">
              <li v-for="s in mcpServers" :key="s.key" class="sv-list-item">
                <div class="sv-list-main">
                  <span class="sv-list-name">{{ s.label || s.key }}</span>
                  <span class="sv-list-sub">{{ s.transport }}{{ s.enabled ? ' · 已启用' : ' · 未启用' }}</span>
                </div>
                <button class="sv-mini" @click="removeMcp(s.key)">移除</button>
              </li>
            </ul>
            <p v-else-if="!mcpLoading" class="sv-hint">暂无 MCP Server。</p>

            <div class="sv-sep"></div>
            <h4 class="sv-h4">添加连接器</h4>
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
.sv-toggle-row { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 13px; color: var(--text); cursor: pointer; }
.sv-toggle-row input { width: 16px; height: 16px; }
.sv-sep { height: 1px; background: var(--border); margin: 16px 0 4px; }
.sv-err { font-size: 12px; color: #dc2626; margin: 8px 0 0; }
.sv-ok { font-size: 12px; color: #16a34a; margin: 8px 0 0; }
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
.sv-list-main { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.sv-list-name { font-size: 13px; font-weight: 600; }
.sv-list-sub { font-size: 11px; color: var(--text-faint); }
.sv-mini {
  flex: none; font-size: 12px; padding: 4px 12px; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); cursor: pointer;
}
.sv-mini:hover { border-color: var(--accent); color: var(--accent); }
</style>