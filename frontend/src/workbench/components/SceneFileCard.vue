<script setup lang="ts">
// 场景外部引用卡：被节点引用的脚本（.gd/.cs）或被实例化的子场景（.tscn）。
// 点一下就在编辑器里打开该文件——这是把「场景图」和「代码」缝起来的那根线。
import { Handle, Position } from '@vue-flow/core'
import type { SceneFile } from '../api'

defineProps<{
  data: {
    file: SceneFile
    color: string
    id?: string
    match?: boolean
    dim?: boolean
    rel?: boolean
    searchActive?: boolean
    highlightActive?: boolean
    onHover?: (id: string | null) => void
  }
  selected?: boolean
}>()

const emit = defineEmits<{ (e: 'open', file: SceneFile): void }>()
</script>

<template>
  <Handle type="target" :position="Position.Left" id="in" class="sc-handle" />
  <div
    class="sc-file"
    :class="{ sel: selected, ghost: !data.file.resolved, hit: !!data.match && !!data.searchActive, faded: !!data.dim, rel: !!data.rel }"
    :style="{ '--fc': data.color }"
    :title="data.file.raw || data.file.rel"
    @mouseenter="data.onHover && data.onHover(data.id ?? null)"
    @mouseleave="data.onHover && data.onHover(null)"
    @dblclick.stop="data.file.resolved && emit('open', data.file)"
  >
    <span class="sc-file-chip">{{ data.file.chip }}</span>
    <div class="sc-file-meta">
      <div class="sc-file-name">{{ data.file.rel || data.file.raw }}</div>
      <div class="sc-file-sub">
        {{ data.file.kind === 'script' ? '脚本' : data.file.kind === 'scene' ? '被实例化场景' : '资源' }}
        · {{ data.file.used }} 处引用
        <span v-if="!data.file.resolved" class="sc-file-warn">· 不在代码库内</span>
      </div>
    </div>
  </div>
</template>
