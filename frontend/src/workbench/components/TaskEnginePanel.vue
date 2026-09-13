<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { engineApi, taskApi, comfyApi } from '../api'
import { useWorkbench } from '../composables/workbench'
const { jumpToLine } = useWorkbench()
const open = ref(false), title = ref(''), region = ref(''), files = ref(''), result = ref(''), impact = ref<string[]>([]), taskId = ref(''), tasks = ref<Record<string, unknown>[]>([])
const running = ref(false), busy = ref(false), logs = ref<string[]>([]), errors = ref<{path:string;line:number;message:string}[]>([]), comfyUrl = ref('http://127.0.0.1:8188'), comfyState = ref('未检测'), workflow = ref(''), comfyResult = ref(''), promptId = ref(''), outputs = ref<{filename?:string;subfolder?:string;type?:string}[]>([]), comfyTemplates = ref<{id:string;name:string;model:string;kind:string;workflow?:string}[]>([])
async function loadTasks() { try { tasks.value = (await taskApi.list()).tasks.slice(-5).reverse() } catch {} }
async function refresh() { try { running.value = (await engineApi.status()).running; const r = await engineApi.logs(); logs.value = r.lines.slice(-8); errors.value = r.errors } catch {} }
async function analyze() {
  busy.value = true; result.value = ''
  try { const r = await taskApi.impact({ title: title.value, region: region.value, files: files.value.split(/[,\n]/).map(x => x.trim()).filter(Boolean), allowed_paths: region.value ? [region.value] : [] }); impact.value = r.files || []; result.value = `影响文件 ${impact.value.length} 个` } catch (e) { result.value = (e as Error).message } finally { busy.value = false }
}
async function saveTask() { busy.value = true; try { const r = await taskApi.create({ title: title.value, region: region.value, files: files.value.split(/[,\n]/).map(x => x.trim()).filter(Boolean), allowed_paths: region.value ? [region.value] : [], status: 'open' }); taskId.value = String(r.task?.id || ''); if (taskId.value) { localStorage.setItem('docmind.activeTaskId', taskId.value); localStorage.setItem('docmind.activeTaskRegion', region.value); localStorage.setItem('docmind.activeTaskAllowedPaths', region.value) } result.value = r.ok ? `任务已保存 ${taskId.value}` : (r.error || '保存失败'); await loadTasks() } catch (e) { result.value = (e as Error).message } finally { busy.value = false } }
async function createBranch() { try { const r=await taskApi.branch({id:taskId.value,title:title.value}); result.value=r.ok?`已切换分支 ${r.branch}`:(r.error||'创建分支失败') } catch(e){ result.value=(e as Error).message } }
async function verifyTask() { busy.value = true; try { const r = await taskApi.verify({ id: taskId.value, title: title.value, region: region.value, files: files.value.split(/[,\n]/).map(x => x.trim()).filter(Boolean), allowed_paths: region.value ? [region.value] : [] }); result.value = r.ok ? '任务验证通过' : '任务验证失败：请查看检查结果' } catch (e) { result.value = (e as Error).message } finally { busy.value = false } }
async function toggleEngine() { busy.value = true; try { const r = running.value ? await engineApi.stop() : await engineApi.start(); running.value = 'running' in r ? !!r.running : false; await refresh() } catch (e) { result.value = (e as Error).message } finally { busy.value = false } }
async function verifyEngine() { busy.value = true; try { const r = await engineApi.verify(); result.value = r.ok ? 'Godot 校验通过' : (r.error || 'Godot 校验失败') } catch (e) { result.value = (e as Error).message } finally { busy.value = false } }
onMounted(refresh); onMounted(loadTasks); onMounted(async () => { try { comfyTemplates.value = (await comfyApi.templates()).templates } catch {} })
let timer: number | undefined
onMounted(() => { timer = window.setInterval(refresh, 3000) })
import { onBeforeUnmount } from 'vue'
onBeforeUnmount(() => { if (timer) window.clearInterval(timer) })
async function checkComfy() { try { const r = await comfyApi.status(comfyUrl.value); comfyState.value = r.available ? '可用' : '不可用' } catch { comfyState.value = '不可用' } }
async function queueComfy() { try { const w = JSON.parse(workflow.value); const r = await comfyApi.queue(w, comfyUrl.value); promptId.value = String(r.response?.prompt_id || ''); comfyResult.value = r.ok ? `已提交 ${promptId.value}` : (r.error || '提交失败') } catch { comfyResult.value = 'Workflow JSON 无效' } }
async function pollComfy() { if (!promptId.value) return; const r = await comfyApi.history(promptId.value, comfyUrl.value); outputs.value = r.outputs || []; comfyResult.value = r.done ? `生成完成（${outputs.value.length} 个结果）` : '生成中' }
async function importOutput(o: Record<string, unknown>) { const x = await comfyApi.import(promptId.value, o, comfyUrl.value); comfyResult.value = x.ok ? `已导入 ${x.path}` : (x.error || '导入失败') }
async function loadComfyTemplate(id: string) { const r = await comfyApi.template(id); if (r.ok && r.workflow) { workflow.value = JSON.stringify(r.workflow, null, 2); comfyResult.value = `已加载模板（${r.format || 'api'}）` } else comfyResult.value = r.error || '模板加载失败' }
</script>
<template>
  <div class="te-panel">
    <button class="te-trigger" @click="open = !open">任务 / 引擎</button>
    <div v-if="open" class="te-pop">
      <div class="te-head"><b>区域任务</b><button @click="open=false">×</button></div>
      <div v-if="tasks.length" class="te-tasks"><span v-for="t in tasks" :key="String(t.id)">{{ t.title }} · {{ t.status }}</span></div>
      <input v-model="title" placeholder="任务目标，例如：修改玩家受击逻辑" />
      <input v-model="region" placeholder="分区，例如 behaviors" />
      <textarea v-model="files" placeholder="文件路径，每行或逗号分隔" />
      <button class="te-action" :disabled="busy" @click="saveTask">保存任务</button><button class="te-action" :disabled="busy" @click="createBranch">任务分支</button><button class="te-action" :disabled="busy" @click="analyze">分析影响范围</button><button class="te-action" :disabled="busy" @click="verifyTask">执行验证</button>
      <p v-if="result" class="te-result">{{ result }}</p>
      <ul v-if="impact.length" class="te-list"><li v-for="p in impact" :key="p">{{ p }}</li></ul>
      <div class="te-engine"><span :class="{ live: running }" /> Godot {{ running ? '运行中' : '未运行' }} <button @click="verifyEngine">校验</button><button @click="toggleEngine">{{ running ? '停止' : '启动' }}</button></div>
      <pre v-if="logs.length" class="te-logs">{{ logs.join('\n') }}</pre>
      <button v-for="e in errors" :key="`${e.path}:${e.line}`" class="te-error" @click="jumpToLine(e.path, e.line)">{{ e.path }}:{{ e.line }} · {{ e.message }}</button>
      <div class="te-comfy"><b>ComfyUI 资源</b><input v-model="comfyUrl" @change="checkComfy" /><div class="te-templates"><button v-for="t in comfyTemplates" :key="t.id" @click="loadComfyTemplate(t.id)">{{ t.name }}</button></div><textarea v-model="workflow" placeholder="粘贴 workflow JSON" /><button @click="queueComfy">提交生成</button><button v-if="promptId" @click="pollComfy">查询结果</button><span>{{ comfyState }} {{ comfyResult }}</span></div>
      <div v-if="outputs.length" class="te-outputs"><div v-for="o in outputs" :key="o.filename" class="te-output"><img v-if="o.mime?.startsWith('image/')" :src="o.preview_url" :alt="o.filename" /><audio v-else-if="o.mime?.startsWith('audio/')" :src="o.preview_url" controls /><video v-else-if="o.mime?.startsWith('video/')" :src="o.preview_url" controls /><span>{{ o.filename }}</span><button @click="importOutput(o)">导入</button></div></div>
    </div>
  </div>
</template>
<style scoped>
.te-panel{position:relative}.te-trigger{border:1px solid var(--border-strong);background:transparent;color:var(--text-muted);border-radius:5px;padding:5px 9px;cursor:pointer}.te-pop{position:absolute;right:0;top:34px;width:310px;padding:12px;background:var(--bg-raised);border:1px solid var(--border-strong);border-radius:7px;box-shadow:0 10px 30px #0008;z-index:20}.te-head{display:flex;justify-content:space-between;margin-bottom:9px}.te-head button{background:none;border:0;color:var(--text-muted);font-size:18px}.te-pop input,.te-pop textarea{width:100%;margin:4px 0;padding:7px;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:4px;font:inherit}.te-pop textarea{height:55px;resize:vertical}.te-action,.te-engine button{background:#17304b;border:1px solid #315c86;color:#b9d8f5;border-radius:4px;padding:5px 8px;cursor:pointer}.te-result{color:var(--green);font-size:11px}.te-list{max-height:100px;overflow:auto;padding-left:18px;font:11px var(--font-mono);color:var(--text-muted)}.te-engine{margin-top:10px;padding-top:9px;border-top:1px solid var(--border);display:flex;align-items:center;gap:7px;color:var(--text-muted);font-size:11px}.te-engine button{margin-left:auto}.te-engine span{width:7px;height:7px;border-radius:50%;background:#687587}.te-engine span.live{background:var(--green);box-shadow:0 0 7px var(--green)}
</style>

