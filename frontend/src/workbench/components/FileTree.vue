<script setup lang="ts">
// 文件树侧栏：头部（根目录名 + 新建 + 刷新）、递归树、节点截断提示。
// 展开状态本地维护：首次/刷新后默认展开全部一级目录，并保留用户已展开的路径。
import { ref, watch } from 'vue'
import type { TreeNode, TreeResp } from '../api'
import FileTreeNode from './FileTreeNode.vue'
import { useWorkbench } from '../composables/workbench'

const props = defineProps<{
  tree: TreeResp
  selectedPath: string | null
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'select', node: TreeNode): void
}>()

const { loadTree, createAt, openNodeMenu, openRootMenu } = useWorkbench()

const openPaths = ref<Set<string>>(new Set())

function allDirPaths(nodes: TreeNode[], acc: Set<string>) {
  for (const n of nodes) {
    if (n.type === 'dir') {
      acc.add(n.path)
      if (n.children.length) allDirPaths(n.children, acc)
    }
  }
}

function reseed(nodes: TreeNode[]) {
  // 保留旧展开集合中仍存在的目录，再补开一级目录
  const all = new Set<string>()
  allDirPaths(nodes, all)
  const kept = new Set([...openPaths.value].filter((p) => all.has(p)))
  for (const n of nodes) if (n.type === 'dir') kept.add(n.path)
  openPaths.value = kept
}

watch(() => props.tree, (t) => reseed(t.nodes), { immediate: true })

function toggle(path: string) {
  const next = new Set(openPaths.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  openPaths.value = next
}

function rootLabel(p: string): string {
  const parts = p.replace(/\\/g, '/').replace(/\/+$/, '').split('/')
  return parts[parts.length - 1] || p
}
</script>

<template>
  <aside class="ft-panel">
    <header class="ft-head">
      <div class="ft-head-title">
        <span class="ft-head-label">资源管理器</span>
        <span class="ft-head-root" :title="tree.code_root">{{ rootLabel(tree.code_root) }}</span>
      </div>
      <div class="ft-head-actions">
        <button class="ft-iconbtn" title="在根目录新建文件" @click="createAt(null, 'file')">
          <svg width="13" height="13" viewBox="0 0 13 13">
            <path d="M7 1.5 H11.5 V11.5 H1.5 V6.2" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
            <path d="M6.7 4.6 V9.2 M4.4 6.9 H9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="ft-iconbtn" title="在根目录新建文件夹" @click="createAt(null, 'folder')">
          <svg width="14" height="13" viewBox="0 0 14 13">
            <path d="M1 2.8 Q1 2.1 1.7 2.1 H5.2 L6.3 3.3 H12.3 Q13 3.3 13 4 V10.2 Q13 10.9 12.3 10.9 H1.7 Q1 10.9 1 10.2 Z" fill="none" stroke="currentColor" stroke-width="1"/>
            <path d="M7 5.6 V9.4 M5.1 7.5 H8.9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="ft-iconbtn" :disabled="loading" title="重新加载文件树" @click="loadTree()">
          <svg width="13" height="13" viewBox="0 0 13 13" :class="{ spinning: loading }">
            <path d="M11.2 6.6 A4.7 4.7 0 1 1 6.5 1.8 A4.7 4.7 0 0 1 10.6 3.6 M10.8 1.6 V3.9 H8.5"
                  fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>
      </div>
    </header>

    <div class="ft-body" @contextmenu.prevent="openRootMenu($event)">
      <FileTreeNode
        v-for="node in tree.nodes"
        :key="node.path"
        :node="node"
        :depth="0"
        :selected-path="selectedPath"
        :open-paths="openPaths"
        @toggle="toggle"
        @select="emit('select', $event)"
        @contextmenu="(ev, n) => openNodeMenu(ev, n)"
      />
      <p v-if="tree.nodes.length === 0" class="ft-empty">
        代码库目录为空<br />右键此处新建文件或文件夹
      </p>
      <p v-if="tree.truncated" class="ft-truncated">
        文件过多，仅显示前 2000 个节点
      </p>
    </div>
  </aside>
</template>
