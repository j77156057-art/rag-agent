<script setup lang="ts">
// 文件卡片节点：区域内的"公民"，左右各一个连接点（左入右出）
import { Handle, Position } from '@vue-flow/core'

export interface FileCardData {
  name: string
  chip: string
  color: string
  symbols: string
  dirty: boolean
}

defineProps<{ data: FileCardData; selected?: boolean }>()
</script>

<template>
  <Handle type="target" :position="Position.Left" id="in" class="card-handle" :style="{ '--hc': data.color }" />
  <div class="file-card" :class="{ selected }" :style="{ '--fc': data.color }">
    <span class="file-chip">{{ data.chip }}</span>
    <div class="file-meta">
      <div class="file-name-row">
        <span class="file-name">{{ data.name }}</span>
        <span v-if="data.dirty" class="dirty-dot" title="有未提交改动" />
      </div>
      <div class="file-symbols">{{ data.symbols }}</div>
    </div>
  </div>
  <Handle type="source" :position="Position.Right" id="out" class="card-handle" :style="{ '--hc': data.color }" />
</template>
