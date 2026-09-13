<script setup lang="ts">
// 区域容器节点（Vue Flow group）：标题栏 + NodeResizer 边缘缩放
import { NodeResizer } from '@vue-flow/node-resizer'

export interface RegionData {
  name: string
  key: string
  color: string
  count: number
  desc: string
}

defineProps<{ data: RegionData }>()
</script>

<template>
  <NodeResizer
    min-width="240"
    min-height="170"
    :color="data.color"
    line-class-name="region-resize-line"
    handle-class-name="region-resize-handle"
  />
  <div class="region-node" :style="{ '--rc': data.color }">
    <div class="region-head">
      <span class="region-dot" />
      <span class="region-name">{{ data.name }}</span>
      <span class="region-key">{{ data.key }}/</span>
      <span class="region-spacer" />
      <span class="region-count">{{ data.count }} 文件</span>
    </div>
    <div class="region-desc">{{ data.desc }}</div>
  </div>
</template>
