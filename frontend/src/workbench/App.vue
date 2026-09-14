<script setup lang="ts">
// DocMind 开发工作台 · P0 任务 4：多标签编辑 + 保存 + 新建/改名/删除。
import { onMounted, onBeforeUnmount, computed } from 'vue'
import FileTree from './components/FileTree.vue'
import CodeView from './components/CodeView.vue'
import EditorTabs from './components/EditorTabs.vue'
import ContextMenu from './components/ContextMenu.vue'
import AppDialog from './components/AppDialog.vue'
import SymbolOutline from './components/SymbolOutline.vue'
import SymbolMap from './components/SymbolMap.vue'
import RelationGraph from './components/RelationGraph.vue'
import UnityGraph from './components/UnityGraph.vue'
import SelectionToolbar from './components/SelectionToolbar.vue'
import SelectionAiPanel from './components/SelectionAiPanel.vue'
import GitHistoryDialog from './components/GitHistoryDialog.vue'
import RewriteDiffDialog from './components/RewriteDiffDialog.vue'
import RegionMapDialog from './components/RegionMapDialog.vue'
import TaskEnginePanel from './components/TaskEnginePanel.vue'
import SceneRuntimePanel from './components/SceneRuntimePanel.vue'
import ChatDock from './components/ChatDock.vue'
import AgentPolicyPanel from './components/AgentPolicyPanel.vue'
import GpuPanel from './components/GpuPanel.vue'
import { useWorkbench } from './composables/workbench'
import { regionColor } from './theme'

const {
  tree, treeLoading, treeError, loadTree,
  tabs, activeTab, selectedPath, openNode, saveActive,
  openSymbolMap, openRelationGraph,
  openUnityGraph,
  revertPath, openHistory,
  openRegionMap,
  aiPanelOpen,
} = useWorkbench()

const dirtyCount = computed(() => tabs.value.filter((t) => t.dirty).length)
/** 当前标签可回滚：已纳入 git 且磁盘或编辑器存在改动 */
const canRevertActive = computed(() => {
  const t = activeTab.value
  return !!t && t.writable && t.tracked === true && (t.gitDirty === true || t.dirty)
})
const canHistoryActive = computed(() => activeTab.value?.tracked === true)

function beforeUnload(e: BeforeUnloadEvent) {
  if (dirtyCount.value > 0) {
    e.preventDefault()
    e.returnValue = ''
  }
}

onMounted(() => {
  void loadTree()
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <div class="wb-shell">
    <header class="wb-topbar">
      <div class="wb-brand">
        <span class="wb-mark" aria-hidden="true">
          <svg width="15" height="15" viewBox="0 0 15 15">
            <rect x="1.5" y="2.5" width="9" height="10" rx="1.5" fill="none" stroke="#58a6ff" stroke-width="1.3" />
            <path d="M11 5.5 H12.5 L14 7 V12.5 H11 Z" fill="#58a6ff22" stroke="#58a6ff" stroke-width="1.3" stroke-linejoin="round" />
          </svg>
        </span>
        <span class="wb-brand-name">DocMind <em>开发工作台</em></span>
      </div>
      <div v-if="tree" class="wb-topbar-meta">
        <span class="wb-rootpath" :title="tree.code_root">
          <svg width="12" height="12" viewBox="0 0 12 12" class="wb-root-icon"><path d="M1 3 Q1 2.2 1.8 2.2 H4.6 L5.6 3.2 H10.2 Q11 3.2 11 4 V9 Q11 9.8 10.2 9.8 H1.8 Q1 9.8 1 9 Z" fill="none" stroke="currentColor" stroke-width="1.1"/></svg>
          {{ tree.code_root }}
        </span>
        <button
          class="wb-region-pill"
          :class="{ off: !tree.regions_enabled }"
          :title="tree.regions_enabled ? '查看分区依赖图与各区状态' : '查看分区状态'"
          @click="openRegionMap"
        >
          <span class="wb-region-dot" />
          {{ tree.regions_enabled ? `分区治理 · ${tree.regions.length} 区` : '分区未启用' }}
        </button>
      </div>
      <div class="wb-topbar-right">
        <a class="wb-question-link" href="/" title="回到 RAG 问答页（检索问答 / 索引代码目录）">问答</a>
        <TaskEnginePanel />
        <GpuPanel />
        <AgentPolicyPanel />
        <SceneRuntimePanel />
        <button
          v-if="tree"
          class="wb-map-btn"
          title="查看全项目符号语义地图"
          @click="openSymbolMap"
        >
          <svg width="13" height="13" viewBox="0 0 13 13">
            <circle cx="3.4" cy="3.4" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="9.6" cy="3" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="8.2" cy="10" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <path d="M4.6 4.2 L8.4 3.6 M4.3 4.6 L7.3 9 M9 4.4 L8.5 8.6" stroke="currentColor" stroke-width="0.8" />
          </svg>
          符号地图
        </button>
        <button
          v-if="tree"
          class="wb-map-btn"
          title="查看类继承与场景挂载关系图"
          @click="openRelationGraph"
        >
          <svg width="13" height="13" viewBox="0 0 13 13">
            <circle cx="3.2" cy="3.6" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="10" cy="3.6" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="6.6" cy="10" r="1.4" fill="none" stroke="currentColor" stroke-width="1" />
            <path d="M4.4 4.2 L8.8 4.2 M4 4.8 L5.8 8.8 M9.2 4.8 L7.4 8.8" stroke="currentColor" stroke-width="0.85" />
          </svg>
          关系图
        </button>
        <button
          v-if="tree"
          class="wb-map-btn"
          title="Unity 工程：.meta GUID 引用图（场景/预制体/脚本/贴图依赖，断裂引用检测）"
          @click="openUnityGraph"
        >
          <svg width="13" height="13" viewBox="0 0 13 13">
            <rect x="1.2" y="1.8" width="4.4" height="3.6" rx="0.8" fill="none" stroke="currentColor" stroke-width="0.9" />
            <rect x="7.8" y="1.8" width="4.2" height="3.6" rx="0.8" fill="none" stroke="currentColor" stroke-width="0.9" />
            <rect x="4.6" y="8.2" width="4.2" height="3.6" rx="0.8" fill="none" stroke="currentColor" stroke-width="0.9" />
            <path d="M5.4 3.2 L8 2.8 M3.6 5.3 L5.8 8.1 M9.6 5.4 L7.8 8.2" fill="none" stroke="currentColor" stroke-width="0.8" />
          </svg>
          Unity 图
        </button>
        <button
          v-if="activeTab && canHistoryActive"
          class="wb-save-btn wb-history-btn"
          title="查看该文件的提交历史，可恢复为任意历史版本"
          @click="openHistory(activeTab.path, activeTab.name)"
        >
          <svg width="12" height="12" viewBox="0 0 12 12">
            <circle cx="4" cy="3" r="1.1" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="4" cy="9" r="1.1" fill="none" stroke="currentColor" stroke-width="1" />
            <circle cx="9" cy="6" r="1.1" fill="none" stroke="currentColor" stroke-width="1" />
            <path d="M4 4.1 V7.9 M5 3.6 C7 3.6 7.6 5 8.2 5.5 M5 8.4 C7 8.4 7.6 7 8.2 6.5" fill="none" stroke="currentColor" stroke-width="0.85" />
          </svg>
          历史
        </button>
        <button
          v-if="activeTab && canRevertActive"
          class="wb-save-btn wb-revert-btn"
          title="放弃该文件全部未提交修改（含未保存与已暂存），恢复到上次提交"
          @click="revertPath(activeTab.path, activeTab.name)"
        >
          <svg width="12" height="12" viewBox="0 0 12 12">
            <path d="M2.2 5.4 A3.8 3.8 0 1 1 2.2 8.4 M2.2 2.8 V5.4 H4.8" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          回滚
        </button>
        <button
          v-if="activeTab"
          class="wb-save-btn"
          :disabled="activeTab.saving || !activeTab.writable"
          :title="activeTab.writable ? '保存（Ctrl+S）' : '契约文件只读，不能在工作台修改'"
          @click="saveActive"
        >
          <svg width="12" height="12" viewBox="0 0 12 12"><path d="M2 1.5 H8.7 L10.5 3.3 V10.5 H2 Z M3 1.5 V4 H8 V1.5 M3 10.5 V7.5 H9 V10.5" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round"/></svg>
          保存
          <kbd>Ctrl+S</kbd>
          <i v-if="dirtyCount" class="wb-save-dirty">{{ dirtyCount }}</i>
        </button>
      </div>
    </header>

    <div class="wb-body">
      <aside v-if="treeError" class="wb-tree-error">
        <p class="wb-tree-error-title">文件树不可用</p>
        <p class="wb-tree-error-msg">{{ treeError.message }}</p>
        <button class="wb-retry" @click="loadTree">重试</button>
      </aside>

      <template v-else-if="tree">
        <FileTree
          :tree="tree"
          :selected-path="selectedPath"
          :loading="treeLoading"
          @select="openNode"
        />

        <main class="wb-main">
          <EditorTabs />
          <div class="wb-editor-row">
            <CodeView :tab="activeTab" />
            <SelectionAiPanel v-if="aiPanelOpen" />
            <SymbolOutline />
          </div>
          <ChatDock />
          <footer class="wb-statusbar">
            <span v-if="selectedPath" class="wb-status-path">{{ selectedPath }}</span>
            <span v-else class="wb-status-faint">未选择文件</span>
            <span v-if="dirtyCount" class="wb-status-dirty">● {{ dirtyCount }} 个文件未保存</span>
            <span class="wb-status-spacer" />
            <span v-if="tree.regions_enabled" class="wb-status-legend">
              <i v-for="r in tree.regions" :key="r.key">
                <b :style="{ background: regionColor(r.key) }" />{{ r.name }}
              </i>
            </span>
          </footer>
        </main>
      </template>

      <div v-else class="wb-booting">
        <div class="cv-spinner" />
        <p>正在加载工作台…</p>
      </div>
    </div>

    <ContextMenu />
    <AppDialog />
    <GitHistoryDialog />
    <RewriteDiffDialog />
    <RegionMapDialog />
    <SymbolMap />
    <RelationGraph />
    <UnityGraph />
    <SelectionToolbar />
  </div>
</template>
