<script setup lang="ts">
// 编辑器标签栏：脏点、活动切换、中键/按钮关闭（脏标签由 composable 弹确认）。
import { useWorkbench } from '../composables/workbench'
import { regionColor, fileChip } from '../theme'

const { tabs, activeId, activateTab, closeTab } = useWorkbench()

function chipOf(name: string) {
  return fileChip(name)
}
</script>

<template>
  <div v-if="tabs.length" class="et-bar">
    <button
      v-for="tab in tabs"
      :key="tab.id"
      class="et-tab"
      :class="{ 'et-active': tab.id === activeId, 'et-dirty': tab.dirty, 'et-loading': tab.loading }"
      @click="activateTab(tab.id)"
      @mousedown.middle.prevent="closeTab(tab.id)"
      :title="tab.path"
    >
      <span
        v-if="tab.region"
        class="et-tab-rail"
        :style="{ background: regionColor(tab.region) }"
      />
      <span class="et-chip" :class="`ft-chip-${chipOf(tab.name).kind}`">{{ chipOf(tab.name).label }}</span>
      <span class="et-name">{{ tab.name }}</span>
      <span v-if="tab.dirty" class="et-dot" title="未保存" />
      <span
        class="et-x"
        role="button"
        aria-label="关闭标签"
        @click.stop="closeTab(tab.id)"
      >
        <svg width="9" height="9" viewBox="0 0 9 9"><path d="M1 1 L8 8 M8 1 L1 8" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
      </span>
    </button>
  </div>
</template>
