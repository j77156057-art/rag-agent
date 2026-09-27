// 工作台「最近打开」域：localStorage 持久化，按项目根隔离。
import { computed, type ShallowRef } from 'vue'
import type { TreeResp } from '../api'

export interface RecentFile { root: string; path: string; name: string }
const RECENT_KEY = 'docmind:recent-files'
const RECENT_MAX = 12

interface RecentDeps {
  tree: ShallowRef<TreeResp | null>
  openPath: (path: string, preferWritable?: boolean) => Promise<void>
}
let deps!: RecentDeps
export function initRecentDeps(d: RecentDeps) { deps = d }

function readRecent(): RecentFile[] {
  try {
    const v = JSON.parse(window.localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(v) ? v as RecentFile[] : []
  } catch {
    return []
  }
}

/** 只列当前项目根下的最近文件，切换项目不串味。 */
const recentFiles = computed<RecentFile[]>(() => {
  const root = deps.tree.value?.code_root
  if (!root) return []
  return readRecent().filter((r) => r.root === root)
})

function pushRecent(path: string) {
  const root = deps.tree.value?.code_root
  if (!root) return
  const name = path.split('/').pop() || path
  const list = readRecent().filter((r) => !(r.root === root && r.path === path))
  list.unshift({ root, path, name })
  try {
    window.localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)))
  } catch {
    /* 隐私模式等场景写入失败可忽略 */
  }
}

async function openRecent(path: string) {
  await deps.openPath(path)
}

export function useRecentFiles() {
  return { recentFiles, pushRecent, openRecent }
}
