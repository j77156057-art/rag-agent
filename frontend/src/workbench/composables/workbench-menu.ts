// 工作台文件树右键菜单域。菜单项回调依赖的文件操作由主模块注入，
// 避免与标签/文件操作域形成环依赖。
import { ref } from 'vue'
import type { TreeNode } from '../api'

export interface MenuItem {
  label: string
  danger?: boolean
  disabled?: boolean
  separatorBefore?: boolean
  run: () => void
}
export interface MenuPos { x: number; y: number; items: MenuItem[] }

interface MenuDeps {
  createAt: (node: TreeNode | null, kind: 'file' | 'folder') => void
  renameNode: (node: TreeNode) => void
  deleteNode: (node: TreeNode) => void
  openNode: (node: TreeNode) => void
  revertPath: (path: string, name: string) => Promise<void>
  openHistory: (path: string, name: string) => Promise<void>
}
let deps!: MenuDeps
export function initMenuDeps(d: MenuDeps) { deps = d }

const ctxMenu = ref<MenuPos | null>(null)

function openNodeMenu(ev: MouseEvent, node: TreeNode) {
  const isDir = node.type === 'dir'
  const items: MenuItem[] = isDir
    ? [
        { label: '新建文件', run: () => deps.createAt(node, 'file') },
        { label: '新建文件夹', run: () => deps.createAt(node, 'folder') },
        { label: '重命名', separatorBefore: true, run: () => deps.renameNode(node) },
        { label: '删除', danger: true, run: () => deps.deleteNode(node) },
      ]
    : [
        { label: node.writable ? '打开' : '只读打开', run: () => deps.openNode(node) },
        {
          label: '放弃未提交修改',
          separatorBefore: true,
          disabled: !(node.tracked === true && node.dirty === true),
          run: () => deps.revertPath(node.path, node.name),
        },
        {
          label: '历史版本…',
          disabled: node.tracked !== true,
          run: () => deps.openHistory(node.path, node.name),
        },
        { label: '重命名', separatorBefore: true, run: () => deps.renameNode(node) },
        { label: '删除', danger: true, run: () => deps.deleteNode(node) },
      ]
  ctxMenu.value = { x: ev.clientX, y: ev.clientY, items }
}

function openRootMenu(ev: MouseEvent) {
  ctxMenu.value = {
    x: ev.clientX,
    y: ev.clientY,
    items: [
      { label: '新建文件', run: () => deps.createAt(null, 'file') },
      { label: '新建文件夹', run: () => deps.createAt(null, 'folder') },
    ],
  }
}

function closeContextMenu() {
  ctxMenu.value = null
}

export function useContextMenu() {
  return { ctxMenu, openNodeMenu, openRootMenu, closeContextMenu }
}
