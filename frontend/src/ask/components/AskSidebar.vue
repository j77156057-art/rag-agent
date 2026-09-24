<script setup lang="ts">
// AI 问答页左侧栏：品牌、当前项目（只读状态 + 跳转开发台）、资料文档上传、模型与显存。
// 全部为只读/轻操作；代码建索引等重操作统一收口到代码工作台。
import { computed, ref } from 'vue'
import type { ModelConfigInfo, ModelStatus, ProjectInfo } from '../../workbench/api'

const props = defineProps<{
  projects: ProjectInfo[]
  currentPid: string
  config: ModelConfigInfo | null
  power: ModelStatus | null
  demo: boolean
  ingestBusy: boolean
  ingestMsg: string
}>()
const emit = defineEmits<{
  (e: 'activate', pid: string): void
  (e: 'ingest', file: File): void
  (e: 'power', action: 'on' | 'off'): void
  (e: 'openSettings'): void
}>()

const fileInput = ref<HTMLInputElement | null>(null)

const current = computed(() =>
  props.projects.find((p) => p.project_id === props.currentPid) || null)
const files = computed(() => props.config?.ingested_files || [])
const slices = computed(() => props.config?.code_sources || 0)

function pickFile() {
  fileInput.value?.click()
}
function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  const f = input.files?.[0]
  if (f) emit('ingest', f)
  input.value = ''
}

function shortPath(p: string, n = 34): string {
  return p.length > n ? p.slice(0, n - 2) + '…' : p
}

const residentNames = computed(() => (props.power?.loaded || []).map((m) => m.name).join('、'))
</script>

<template>
  <aside class="ask-side">
    <div class="brand">
      <div class="brand-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="17" height="17"><path fill="#fff" d="M12 2l2.4 5.6L20 9l-4 4 1 6-5-3-5 3 1-6-4-4 5.6-1.4z"/></svg>
      </div>
      <div class="brand-text">
        <div class="brand-name">DocMind</div>
        <div class="brand-sub">AI 问答 · 读代码答问题</div>
      </div>
    </div>

    <!-- 模型与显存 -->
    <section class="card">
      <div class="card-title">
        模型
        <button class="gear" title="模型设置" @click="emit('openSettings')">
          <svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M12 8a4 4 0 100 8 4 4 0 000-8zm9 4a7.5 7.5 0 00-.13-1.4l2-1.56-2-3.46-2.37.96a7.6 7.6 0 00-2.4-1.4L15.8 2h-4l-.3 2.74a7.6 7.6 0 00-2.4 1.4l-2.37-.96-2 3.46 2 1.56a7.6 7.6 0 000 2.8l-2 1.56 2 3.46 2.37-.96a7.6 7.6 0 002.4 1.4l.3 2.74h4l.3-2.74a7.6 7.6 0 002.4-1.4l2.37.96 2-3.46-2-1.56c.09-.46.13-.92.13-1.4z"/></svg>
        </button>
      </div>
      <div class="model-line">
        <span class="model-k">回答</span>
        <b>{{ demo ? 'qwen2.5:7b（示例）' : ((config?.llm_provider || '-') + (config?.llm_model ? ' · ' + config.llm_model : '')) }}</b>
      </div>
      <div class="model-line">
        <span class="model-k">检索</span>
        <b>{{ demo ? 'local（示例）' : (config?.embedding_provider || '-') }}</b>
      </div>

      <div v-if="!demo" class="power">
        <template v-if="!power">
          <div class="status-row"><span class="status-dot"></span>显存状态读取中…</div>
        </template>
        <template v-else-if="!power.reachable">
          <div class="status-row">
            <span class="status-dot"></span>
            {{ power.needs_ollama ? 'Ollama 未运行，无本地模型驻留' : '云端模型，不占本机显存' }}
          </div>
        </template>
        <template v-else-if="!power.loaded.length">
          <div class="status-row"><span class="status-dot"></span>模型未驻留，发消息时自动加载</div>
          <button v-if="power.needs_ollama" class="btn ghost block sm" @click="emit('power', 'on')">预加载模型</button>
        </template>
        <template v-else>
          <div class="status-row">
            <span class="status-dot on"></span>
            驻留中：<b>{{ residentNames }}</b> · {{ power.vram_gb }} GB
          </div>
          <button class="btn ghost block sm" @click="emit('power', 'off')">卸载模型 · 释放约 {{ power.vram_gb }} GB</button>
        </template>
      </div>

      <button v-if="!demo" class="btn block sm" @click="emit('openSettings')">模型设置</button>
    </section>

    <!-- 当前项目 -->
    <section class="card">
      <div class="card-title">当前项目</div>
      <select
        v-if="projects.length > 1"
        class="project-select"
        :value="currentPid"
        @change="emit('activate', ($event.target as HTMLSelectElement).value)"
      >
        <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.name }}</option>
      </select>
      <div v-else class="project-name">{{ demo ? '示例游戏项目' : (current?.name || '未选择项目') }}</div>
      <div v-if="current?.root" class="project-root" :title="current.root">{{ shortPath(current.root) }}</div>

      <div class="status-row">
        <span class="status-dot" :class="{ on: !demo && slices > 0 }"></span>
        <template v-if="demo">
          已索引 <b>1,284</b> 条代码切片
        </template>
        <template v-else-if="slices > 0">
          已索引 <b>{{ slices }}</b> 条代码切片
        </template>
        <template v-else>代码索引为空</template>
      </div>
      <a class="card-link" href="/workbench">打开代码工作台，管理项目与索引 →</a>
    </section>

    <!-- 资料文档 -->
    <section class="card">
      <div class="card-title">
        资料文档
        <span class="card-count">{{ demo ? 3 : files.length }}</span>
      </div>
      <p class="card-hint">上传 PDF / Markdown / TXT，AI 回答前会先检索资料。</p>
      <button class="btn block" :disabled="demo || ingestBusy" @click="pickFile">
        {{ ingestBusy ? '正在摄取…' : '上传资料文档' }}
      </button>
      <input ref="fileInput" type="file" accept=".pdf,.md,.txt" hidden @change="onFileChange">
      <div v-if="ingestMsg" class="ingest-msg" :class="{ err: ingestMsg.includes('失败') }">{{ ingestMsg }}</div>
      <div class="file-list">
        <template v-if="demo">
          <div v-for="n in ['数值设计案.pdf', '战斗规则.md', '常见问题.txt']" :key="n" class="file-item">
            <span class="file-ic">{{ n.split('.').pop()!.slice(0, 3).toUpperCase() }}</span>
            <span class="file-nm">{{ n }}</span>
          </div>
        </template>
        <template v-else>
          <div v-for="n in files" :key="n" class="file-item">
            <span class="file-ic">{{ (n.split('.').pop() || '?').slice(0, 3).toUpperCase() }}</span>
            <span class="file-nm" :title="n">{{ n }}</span>
          </div>
          <div v-if="!files.length" class="file-empty">尚未上传文档</div>
        </template>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.ask-side {
  flex: 0 0 296px;
  width: 296px;
  height: 100vh;
  overflow-y: auto;
  padding: 18px 14px 24px;
  background: var(--bg-raised);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.brand { display: flex; align-items: center; gap: 10px; padding: 2px 4px 6px; }
.brand-mark {
  width: 32px; height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--accent), var(--accent-2));
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 12px rgba(47, 111, 237, .25);
}
.brand-name { font-size: 15px; font-weight: 700; letter-spacing: .2px; }
.brand-sub { font-size: 11.5px; color: var(--text-faint); }

.card {
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 12px 13px;
  background: var(--bg-raised);
}
.card-title {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.card-count {
  margin-left: auto;
  background: var(--bg-selected);
  color: var(--accent);
  border-radius: 999px;
  font-size: 11px;
  padding: 0 7px;
  line-height: 18px;
}
.card-hint { margin: 0 0 8px; font-size: 12px; color: var(--text-faint); line-height: 1.55; }
.card-link {
  display: block;
  margin-top: 8px;
  font-size: 12px;
  color: var(--accent);
  text-decoration: none;
}
.card-link:hover { text-decoration: underline; }

.project-name { font-size: 14px; font-weight: 600; word-break: break-all; }
.project-select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border-strong);
  border-radius: 8px;
  background: var(--bg-raised);
  font-size: 13px;
  color: var(--text);
}
.project-root {
  margin-top: 3px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-faint);
}
.status-row {
  margin-top: 8px;
  font-size: 12.5px;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  gap: 7px;
}
.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--border-strong);
  flex: 0 0 auto;
}
.status-dot.on { background: var(--green); box-shadow: 0 0 0 3px rgba(28, 158, 102, .14); }

.ingest-msg { margin-top: 8px; font-size: 12px; color: var(--green); word-break: break-all; }
.ingest-msg.err { color: var(--danger); }
.file-list { margin-top: 8px; display: flex; flex-direction: column; gap: 5px; max-height: 200px; overflow-y: auto; }
.file-item { display: flex; align-items: center; gap: 8px; font-size: 12.5px; }
.file-ic {
  flex: 0 0 auto;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: 5px;
  padding: 1px 5px;
}
.file-nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.file-empty { font-size: 12px; color: var(--text-faint); padding: 2px 0; }

.model-line { display: flex; gap: 8px; font-size: 12.5px; padding: 2px 0; }
.model-k { flex: 0 0 34px; color: var(--text-faint); }
.model-line b { font-weight: 500; word-break: break-all; }
.gear {
  margin-left: auto;
  border: none;
  background: none;
  color: var(--text-faint);
  cursor: pointer;
  padding: 3px;
  border-radius: 6px;
  display: flex;
}
.gear:hover { background: var(--bg-hover); color: var(--text); }
.power { margin: 8px 0; padding-top: 8px; border-top: 1px dashed var(--border); display: flex; flex-direction: column; gap: 7px; }

.btn {
  border: 1px solid var(--accent);
  background: var(--accent);
  color: #fff;
  border-radius: 9px;
  padding: 8px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: background .15s, border-color .15s;
}
.btn:hover { background: #245fd6; border-color: #245fd6; }
.btn:disabled { opacity: .55; cursor: default; }
.btn.ghost { background: var(--bg-raised); color: var(--text); border-color: var(--border-strong); }
.btn.ghost:hover { background: var(--bg-hover); }
.btn.block { width: 100%; }
.btn.sm { padding: 5px 10px; font-size: 12.5px; }
</style>
