<script setup lang="ts">
// 主区工作区标签条（Godot 式上下文切换）：
// 概览驾驶舱 / 代码 / 素材 为当前可用工作区；画布、运行为真实功能入口，
// 点击打开「运行游戏」弹窗并切到对应 tab（场景画布 / 试玩）。
import { useWorkbench } from '../composables/workbench'
import type { WorkspaceView } from '../composables/workbench'

const { workspace, setWorkspace, tabs, runtimeOpen, runtimeTab, openRuntime } = useWorkbench()

const real: { key: WorkspaceView; name: string; icon: string }[] = [
  { key: 'overview', name: '概览', icon: 'home' },
  { key: 'code', name: '代码', icon: 'code' },
  { key: 'assets', name: '素材', icon: 'assets' },
]

// 真实功能入口：点开「运行游戏」弹窗并切到对应 tab
const soon = [
  { key: 'canvas', name: '画布', tip: '场景画布：把 Godot .tscn 画成可操作节点图' },
  { key: 'runtime', name: '运行', tip: '运行游戏：导出 / 启动 Godot，边玩边让 AI 改' },
] as const

const HOTKEY: Record<string, number> = { overview: 1, code: 2, assets: 3 }

function pick(key: WorkspaceView) {
  if (key === 'code' && !tabs.value.length) return
  setWorkspace(key)
}

// 顶层「画布 / 运行」→ 弹窗对应 tab
function pickSoon(key: string) {
  if (key === 'canvas') openRuntime('scene')
  else if (key === 'runtime') openRuntime('play')
}
// 顶层入口高亮：与弹窗当前 tab 对齐
function soonActive(key: string) {
  if (!runtimeOpen.value) return false
  if (key === 'canvas') return runtimeTab.value === 'scene'
  if (key === 'runtime') return runtimeTab.value === 'play'
  return false
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
      :title="item.key === 'code' && !tabs.length ? '先从左侧文件树打开一个文件（Alt+2 切回代码）' : `${item.name}（Alt+${HOTKEY[item.key]}）`"
      @click="pick(item.key)"
    >
      <svg v-if="item.icon === 'home'" width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
        <path d="M2 6 L6.5 2.2 L11 6 V10.6 Q11 11 10.6 11 H2.4 Q2 11 2 10.6 Z" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
        <path d="M5.2 11 V7.4 H7.8 V11" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linejoin="round"/>
      </svg>
      <svg v-else-if="item.icon === 'assets'" width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
        <rect x="1.6" y="2.4" width="9.8" height="8.2" rx="1" fill="none" stroke="currentColor" stroke-width="1.05"/>
        <circle cx="4.3" cy="4.9" r="0.95" fill="none" stroke="currentColor" stroke-width="1.05"/>
        <path d="M2.6 9.6 L5.2 7 L7.2 8.8 L8.8 7.4 L10.4 9" fill="none" stroke="currentColor" stroke-width="1.05" stroke-linejoin="round"/>
      </svg>
      <svg v-else width="13" height="13" viewBox="0 0 13 13" aria-hidden="true">
        <path d="M4.6 4.4 L2.2 6.5 L4.6 8.6 M8.4 4.4 L10.8 6.5 L8.4 8.6" fill="none" stroke="currentColor" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/>
      </svg>
      <span>{{ item.name }}</span>
    </button>

    <!-- 画布 / 运行：真实功能入口，点击打开「运行游戏」弹窗并切到对应 tab -->
    <span class="ws-soon-group">
      <span class="ws-soon-sep" aria-hidden="true" />
      <button
        v-for="item in soon"
        :key="item.key"
        type="button"
        class="ws-soon"
        :class="{ 'ws-soon-on': soonActive(item.key) }"
        :title="item.tip"
        @click="pickSoon(item.key)"
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
  border: 1px solid transparent; background: transparent; border-radius: 7px;
  color: var(--text-faint);
  font-family: var(--font-ui); font-size: 11px; font-weight: 500;
  cursor: pointer;
  transition: color .12s, background .12s, border-color .12s;
}
.ws-soon:hover { color: var(--text); background: rgba(47,111,237,.07); }
.ws-soon-on {
  color: var(--accent);
  background: var(--bg-raised);
  border-color: var(--border);
}
.ws-soon-mark { display: none; }
</style>
