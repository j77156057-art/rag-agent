<script setup lang="ts">
// 全局右键菜单：定位做视口边界 clamp；任意点击 / Esc / 滚动关闭。
import { nextTick, ref, watch, onBeforeUnmount } from 'vue'
import { useWorkbench } from '../composables/workbench'

const { ctxMenu, closeContextMenu } = useWorkbench()
const menuEl = ref<HTMLElement | null>(null)
const pos = ref({ x: 0, y: 0 })

watch(ctxMenu, async (m) => {
  if (!m) return
  pos.value = { x: m.x, y: m.y }
  await nextTick()
  const el = menuEl.value
  if (!el) return
  const r = el.getBoundingClientRect()
  const x = Math.min(m.x, window.innerWidth - r.width - 8)
  const y = Math.min(m.y, window.innerHeight - r.height - 8)
  pos.value = { x: Math.max(8, x), y: Math.max(8, y) }
})

function onWinDown() {
  if (ctxMenu.value) closeContextMenu()
}
function onKey(e: KeyboardEvent) {
  if (e.key === 'Escape') closeContextMenu()
}
function onScroll() {
  if (ctxMenu.value) closeContextMenu()
}
window.addEventListener('mousedown', onWinDown)
window.addEventListener('keydown', onKey)
window.addEventListener('wheel', onScroll, true)
onBeforeUnmount(() => {
  window.removeEventListener('mousedown', onWinDown)
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('wheel', onScroll, true)
})

function pick(item: () => void) {
  closeContextMenu()
  item()
}
</script>

<template>
  <div
    v-if="ctxMenu"
    ref="menuEl"
    class="cm-menu"
    :style="{ left: pos.x + 'px', top: pos.y + 'px' }"
    @mousedown.stop
  >
    <button
      v-for="(item, i) in ctxMenu.items"
      :key="i"
      class="cm-item"
      :class="{ 'cm-danger': item.danger, 'cm-sep': item.separatorBefore }"
      :disabled="item.disabled"
      @click="pick(item.run)"
    >
      {{ item.label }}
    </button>
  </div>
</template>
