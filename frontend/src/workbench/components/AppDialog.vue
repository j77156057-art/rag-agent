<script setup lang="ts">
// 统一对话框：confirm / prompt / alert / mtime 冲突。Esc=取消，Enter=确认。
import { nextTick, ref, watch } from 'vue'
import { useWorkbench } from '../composables/workbench'
import { formatMtime } from '../theme'

const { dialog, resolveDialog } = useWorkbench()
const inputVal = ref('')
const inputEl = ref<HTMLInputElement | null>(null)

watch(dialog, async (d) => {
  if (d?.kind === 'prompt') {
    inputVal.value = d.defaultValue
    await nextTick()
    inputEl.value?.focus()
    // 默认选中文件名主体（不含扩展名），方便直接改名
    const dot = d.defaultValue.lastIndexOf('.')
    if (dot > 0) inputEl.value?.setSelectionRange(0, dot)
    else inputEl.value?.select()
  }
})

function onKey(e: KeyboardEvent) {
  if (!dialog.value) return
  if (e.key === 'Escape') {
    resolveDialog(dialog.value.kind === 'alert' ? true : false)
  } else if (e.key === 'Enter' && dialog.value.kind === 'prompt') {
    if (inputVal.value.trim()) resolveDialog(inputVal.value)
  } else if (e.key === 'Enter' && dialog.value.kind === 'alert') {
    resolveDialog(true)
  }
}
</script>

<template>
  <div
    v-if="dialog"
    class="dg-mask"
    @mousedown.self="resolveDialog(dialog.kind === 'alert')"
    @keydown="onKey"
    tabindex="-1"
  >
    <div class="dg-box" role="dialog" aria-modal="true">
      <!-- confirm -->
      <template v-if="dialog.kind === 'confirm'">
        <h3 class="dg-title">{{ dialog.title }}</h3>
        <p class="dg-msg">{{ dialog.message }}</p>
        <pre v-if="dialog.detail" class="dg-detail">{{ dialog.detail }}</pre>
        <div class="dg-actions">
          <button class="dg-btn" @click="resolveDialog(false)">取消</button>
          <button
            class="dg-btn dg-primary"
            :class="{ 'dg-danger': dialog.danger }"
            @click="resolveDialog(true)"
          >{{ dialog.confirmText || '确定' }}</button>
        </div>
      </template>

      <!-- prompt -->
      <template v-else-if="dialog.kind === 'prompt'">
        <h3 class="dg-title">{{ dialog.title }}</h3>
        <p v-if="dialog.message" class="dg-msg dg-context">{{ dialog.message }}</p>
        <input
          ref="inputEl"
          v-model="inputVal"
          class="dg-input"
          :placeholder="dialog.placeholder || ''"
          @keydown="onKey"
        />
        <div class="dg-actions">
          <button class="dg-btn" @click="resolveDialog(null)">取消</button>
          <button
            class="dg-btn dg-primary"
            :disabled="!inputVal.trim()"
            @click="resolveDialog(inputVal)"
          >确定</button>
        </div>
      </template>

      <!-- alert -->
      <template v-else-if="dialog.kind === 'alert'">
        <h3 class="dg-title" :class="{ 'dg-title-danger': dialog.title.includes('失败') || dialog.title.includes('未通过') }">{{ dialog.title }}</h3>
        <p class="dg-msg">{{ dialog.message }}</p>
        <pre v-if="dialog.detail" class="dg-detail">{{ dialog.detail }}</pre>
        <div class="dg-actions">
          <button class="dg-btn dg-primary" @click="resolveDialog(true)">知道了</button>
        </div>
      </template>

      <!-- mtime 冲突 -->
      <template v-else>
        <h3 class="dg-title dg-title-danger">文件已在编辑器外被修改</h3>
        <p class="dg-msg">
          「{{ dialog.path }}」在你编辑期间被其他程序写入（服务端修改时间
          {{ formatMtime(dialog.serverMtime) }}）。
        </p>
        <p class="dg-msg dg-sub">继续保存会用当前编辑器内容覆盖外部改动，且无法自动合并。</p>
        <div class="dg-actions">
          <button class="dg-btn" @click="resolveDialog(false)">取消（我先对比）</button>
          <button class="dg-btn dg-danger" @click="resolveDialog(true)">强制覆盖保存</button>
        </div>
      </template>
    </div>
  </div>
</template>
