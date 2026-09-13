<script setup lang="ts">
// 场景节点卡：一个 Godot 场景节点的可视化单元。
// 上/下 handle 走父子层级边，右侧 handle 走「脚本引用 / 实例化」边。
import { Handle, Position } from '@vue-flow/core'
import type { SceneNode } from '../api'

defineProps<{
  data: {
    node: SceneNode
    color: string
    flash?: 'ok' | 'err' | ''
  }
  selected?: boolean
}>()

function posText(node: SceneNode) {
  if (!node.position) return ''
  const dims = node.space === '3d' ? 2 : 2
  const short = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1))
  return `${node.position_from === 'transform' ? '⇢' : ''}(${node.position.slice(0, dims).map(short).join(', ')})`
}
</script>

<template>
  <Handle type="target" :position="Position.Top" id="parent" class="sc-handle" />
  <div
    class="sc-node"
    :class="{ sel: selected, dim: data.node.overridden, flash: !!data.flash, bad: data.flash === 'err' }"
    :style="{ '--nc': data.color }"
  >
    <div class="sc-node-head">
      <span class="sc-node-dot" />
      <span class="sc-node-name" :title="data.node.id">{{ data.node.name }}</span>
      <span v-if="posText(data.node)" class="sc-node-pos">{{ posText(data.node) }}</span>
    </div>
    <div class="sc-node-meta">
      <span class="sc-node-type">{{ data.node.type }}</span>
      <span v-if="data.node.instance" class="sc-chip inst" :title="data.node.instance">⇱ 实例</span>
      <span v-if="data.node.script" class="sc-chip script" :title="data.node.script">⚙ 脚本</span>
      <span v-if="data.node.groups.length" class="sc-chip group" :title="data.node.groups.join(', ')">
        ⌗ {{ data.node.groups.length }}
      </span>
      <span v-if="data.node.overridden" class="sc-chip over" title="位于实例子树内，改动属引擎侧覆写">覆写</span>
    </div>
  </div>
  <Handle type="source" :position="Position.Bottom" id="children" class="sc-handle" />
  <Handle type="source" :position="Position.Right" id="ref" class="sc-handle ref" />
</template>
