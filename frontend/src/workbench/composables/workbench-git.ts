// 工作台 git 域：放弃文件未提交改动（revert）、历史版本浏览/预览/恢复。
// git 写盘后同步编辑器标签的能力由主模块注入。
import { ref, type Ref } from 'vue'
import { fsApi, FsApiError } from '../api'
import type { GitCommit } from '../api'
import { askConfirm, askAlert } from './dialogs'
import type { EditorTab } from './workbench-types'

export interface HistoryState {
  path: string
  name: string
  loading: boolean
  error: string | null
  commits: GitCommit[]
  selected: GitCommit | null
  previewLoading: boolean
  previewError: string | null
  preview: string
  restoring: boolean
}

interface GitDeps {
  tabs: Ref<EditorTab[]>
  docReplacers: Map<number, (content: string) => void>
  loadTree: (selectPath?: string) => Promise<void>
}
let deps!: GitDeps
export function initGitDeps(d: GitDeps) { deps = d }

/** git 改写磁盘后，把最新内容同步回可能已打开的标签（活动/非活动标签都要换）。 */
async function resyncTabAfterGit(path: string, mtime: number): Promise<void> {
  const tab = deps.tabs.value.find((t) => t.path === path)
  if (!tab) return
  const f = await fsApi.read(path)
  tab.mtime = mtime || f.mtime
  tab.savedContent = f.content
  tab.tracked = f.tracked
  tab.gitDirty = f.dirty
  tab.dirty = false
  tab.savedAt = Date.now()
  tab.reindexWarn = null
  deps.docReplacers.get(tab.id)?.(f.content)
}

/** 放弃单个文件的全部未提交改动（含暂存与编辑器未保存内容），恢复到 HEAD。 */
async function revertPath(path: string, name?: string): Promise<void> {
  const label = name ?? path.split('/').pop() ?? path
  const tab = deps.tabs.value.find((t) => t.path === path)
  const ok = await askConfirm({
    title: `放弃「${label}」的未提交修改？`,
    message: '文件将恢复到上次提交（HEAD）时的内容。',
    detail: [
      tab?.dirty ? '· 编辑器里尚未保存的改动也会一并丢弃。' : '',
      '· 已暂存（git add）的改动同样撤销。',
      '· 只影响这一个文件，提交历史不受影响。',
    ].filter(Boolean).join('\n'),
    confirmText: '回滚',
    danger: true,
  })
  if (!ok) return
  try {
    const r = await fsApi.revert(path)
    if (!r.reverted) {
      await askAlert({ title: '无需回滚', message: '该文件没有未提交改动，内容与上次提交一致。' })
      return
    }
    await resyncTabAfterGit(path, r.mtime)
    await deps.loadTree(path)
  } catch (e) {
    const err = e as FsApiError
    await askAlert({
      title: '回滚失败',
      message: err.message,
      detail: err.status === 403
        ? '契约文件/受保护文件禁止在工作台回滚。'
        : err.status ? `HTTP ${err.status}` : undefined,
    })
  }
}

const history = ref<HistoryState | null>(null)

async function openHistory(path: string, name?: string): Promise<void> {
  history.value = {
    path, name: name ?? path.split('/').pop() ?? path,
    loading: true, error: null, commits: [],
    selected: null, previewLoading: false, previewError: null, preview: '', restoring: false,
  }
  try {
    const r = await fsApi.gitlog(path, 50)
    if (!history.value || history.value.path !== path) return
    history.value.commits = r.commits
    history.value.loading = false
  } catch (e) {
    const err = e as FsApiError
    if (history.value) {
      history.value.loading = false
      history.value.error = err.message
    }
  }
}

function closeHistory(): void {
  history.value = null
}

async function selectHistoryVersion(c: GitCommit): Promise<void> {
  const h = history.value
  if (!h || h.restoring) return
  h.selected = c
  h.previewLoading = true
  h.previewError = null
  try {
    const r = await fsApi.gitShow(h.path, c.full_hash)
    // 异步往返期间用户可能点了别的提交/关了弹窗
    if (history.value !== h || h.selected?.full_hash !== c.full_hash) return
    h.preview = r.content
  } catch (e) {
    const err = e as FsApiError
    if (history.value === h && h.selected?.full_hash === c.full_hash) {
      h.previewError = err.message
    }
  } finally {
    if (history.value === h) h.previewLoading = false
  }
}

async function restoreSelectedVersion(): Promise<void> {
  const h = history.value
  if (!h || !h.selected || h.restoring) return
  const c = h.selected
  const ok = await askConfirm({
    title: '恢复为该历史版本？',
    message: `「${h.name}」将恢复为提交 ${c.hash} 时的内容。`,
    detail: '只改写工作区文件，不会改动提交历史；恢复后仍是未提交状态，不满意可再点「回滚」撤销。',
    confirmText: '恢复此版本',
    danger: true,
  })
  if (!ok) return
  h.restoring = true
  try {
    const r = await fsApi.restoreAt(h.path, c.full_hash)
    await resyncTabAfterGit(h.path, r.mtime)
    await deps.loadTree(h.path)
    history.value = null
  } catch (e) {
    const err = e as FsApiError
    await askAlert({
      title: err.status === 422 ? '语法校验未通过，恢复已取消' : '恢复历史版本失败',
      message: err.message,
      detail: err.status ? `HTTP ${err.status}` : undefined,
    })
    h.restoring = false
  }
}

export function useGitHistory() {
  return {
    history, revertPath, openHistory, closeHistory,
    selectHistoryVersion, restoreSelectedVersion,
  }
}
