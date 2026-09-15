<script setup lang="ts">
// 主区工作区标签条（Godot 式上下文切换）：
// 概览驾驶舱 / 代码 为当前可用工作区；素材、画布、运行为后续阶段的路线图，
// 视觉上弱化成右侧浅色 ghost 组（不伪装成可点的页签），阶段 2/3/4 直接点亮。
import { useWorkbench } from '../composables/workbench'
import type { WorkspaceView } from '../composables/workbench'

const { workspace, setWorkspace, tabs } = useWorkbench()

const real: { key: WorkspaceView; name: string; icon: string }[] = [
  { key: 'overview', name: '概览', icon: 'home' },
  { key: 'code', name: '代码', icon: 'code' },
]

// 路线图：◌ + 悬浮说明来自哪个阶段
const soon = [
  { key: 'assets', name: '素材', tip: '阶段 2：CC0/CC-BY 网络素材与本地 ComfyUI 生图' },
  { key: 'canvas', name: '画布', tip: '阶段 3：AI 工具流画布，把多步操作连成流水线' },
  { key: 'runtime', name: '运行', tip: '阶段 4：内置 Godot，边玩边让 AI 改' },
] as const

function pick(key: WorkspaceView) {
  if (key === 'code' && !tabs.value.length) return
  setWorkspace(key)
}
</script>

<template>
  <nav class="ws-bar" aria-label="工作区切换">
    <button
      v-for="item in real"
      :key="item.key"
      type="button"
      class="ws-tab"
      :class="{
        'ws-active': workspace === item.key,
        'ws-disabled': item.key === 'code' && !tabs.length,
      }"
      :disabled="item.key === 'code' && !tabs.length"
      :title="item.key === 'code' && !tabs.length ? '先从左侧文件树打开一个文件（Alt+2 切回代码）' : `${item.name}（Alt+${item.key === 'overview' ? 1 : 2}）`"
      @click="pick(item.key)"
    >
      <svg v-if="item.icon === 'home'" width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
        <path d="M2 6 L6.5 2.2 L11 6 V10.6 Q11 11 10.6 11 H2.4 Q2 11 2 10.6 Z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
        <path d="M5.2 11 V7.4 H7.8 V11" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
      </svg>
      <svg v-else width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
        <path d="M4.6 4.4 L2.2 6.5 L4.6 8.6 M8.4 4.4 L10.8 6.5 L8.4 8.6" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span>{{ item.name }}</span>
    </button>

    <!-- 路线图：浅色 ghost，不占页签语义 -->
    <span class="ws-soon-group" aria-label="即将推出">
      <span class="ws-soon-sep" aria-hidden="true" />
      <button
        v-for="item in soon"
        :key="item.key"
        type="button"
        class="ws-soon"
        :title="item.tip"
      >
        <span class="ws-soon-mark" aria-hidden="true">◌</span>{{ item.name }}
      </button>
    </span>
  </nav>
</template>

<style scoped>
.ws-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  flex-wrap: nowrap;
  gap: 2px;
  padding: 0 12px;
  height: 38px;
  background: #f3f6fa;
  border-bottom: 1px solid var(--border);
  user-select: none;
  overflow-x: auto;
  overflow-y: hidden;
  scrollbar-width: none;
}
.ws-bar::-webkit-scrollbar { display: none; height: 0; }
.ws-tab, .ws-soon-group, .ws-soon { flex: 0 0 auto; white-space: nowrap; }
.ws-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 14px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-faint);
  font-family: var(--font-ui);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: color .12s, background .12s;
}
.ws-tab:hover:not(:disabled) { color: var(--text); background: rgba(47,111,237,.07); }
.ws-active {
  color: var(--accent);
  background: var(--bg-raised);
  border-color: var(--border);
  box-shadow: 0 1px 2px rgba(35,52,84,.06);
}
.ws-disabled { opacity: .5; cursor: default; }
.ws-disabled:hover { background: transparent; }

.ws-soon-group { display: inline-flex; align-items: center; gap: 2px; margin-left: 10px; }
.ws-soon-sep { width: 1px; height: 14px; background: var(--border); margin: 0 8px; }
.ws-soon {
  display: inline-flex; align-items: center; gap: 4px;
  height: 26px; padding: 0 9px;
  border: none; background: transparent; border-radius: 7px;
  color: var(--text-faint);
  font-family: var(--font-ui); font-size: 11px; font-weight: 500;
  cursor: help;
  transition: color .12s, background .12s;
}
.ws-soon:hover { color: var(--text-muted); background: var(--bg-hover); }
.ws-soon-mark { font-size: 10px; opacity: .8; }
</style>
