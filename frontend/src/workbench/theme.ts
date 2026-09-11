// 视觉主题助手：分区配色（后端 regions.json 无颜色字段，前端按 key 稳定映射）、
// 文件类型 chip、时间/大小格式化。

/** 八个默认分区的固定色板；自定义分区走 hash 兜底，同一 key 永远同色 */
const REGION_COLORS: Record<string, string> = {
  values: '#58a6ff',     // 数值区 · 冷蓝
  behaviors: '#bc8cff',  // 角色行为区 · 紫
  assets: '#e3a83a',     // 素材区 · 琥珀金
  bugs: '#ff6b6b',       // bug 区 · 警戒红
  levels: '#45c98c',     // 关卡·场景区 · 翠绿
  ui: '#f072b6',         // UI·HUD 区 · 品红
  audio: '#2ec4b6',      // 音频区 · 青碧
  net: '#f0883e',        // 网络·存档区 · 橙
}

export function regionColor(key: string | null | undefined): string {
  if (!key) return '#7d8590'
  if (REGION_COLORS[key]) return REGION_COLORS[key]
  let h = 0
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0
  return `hsl(${h % 360} 62% 64%)`
}

export type ChipKind = 'code' | 'data' | 'web' | 'doc' | 'media' | 'lib' | 'plain'

const CHIP_RULES: Array<{ exts: string[]; kind: ChipKind }> = [
  { exts: ['py', 'js', 'jsx', 'ts', 'tsx', 'gd', 'gdshader', 'lua', 'cs', 'go', 'rs', 'java', 'c', 'h', 'cpp', 'hpp', 'rb', 'php', 'kt', 'swift', 'scala', 'sh'], kind: 'code' },
  { exts: ['json', 'yaml', 'yml', 'toml', 'csv', 'ini', 'cfg', 'tscn', 'tres', 'res'], kind: 'data' },
  { exts: ['css', 'html'], kind: 'web' },
  { exts: ['md', 'txt'], kind: 'doc' },
  { exts: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'svg', 'wav', 'mp3', 'ogg', 'mp4', 'webm', 'glb', 'gltf'], kind: 'media' },
]

export function fileChip(name: string): { label: string; kind: ChipKind } {
  const dot = name.lastIndexOf('.')
  const ext = dot > 0 ? name.slice(dot + 1).toLowerCase() : ''
  if (!ext) return { label: '•', kind: 'plain' }
  const rule = CHIP_RULES.find((r) => r.exts.includes(ext))
  return { label: ext.slice(0, 4).toUpperCase(), kind: rule ? rule.kind : 'lib' }
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export function formatMtime(mtime: number): string {
  const d = new Date(mtime * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** git 状态语义：untracked=绿点；dirty=琥珀点；clean/无仓库=不显示 */
export function gitState(tracked: boolean | null | undefined, dirty: boolean | null | undefined) {
  if (tracked === false) return { dot: 'untracked', title: '未纳入 git，删除不可恢复' }
  if (tracked && dirty) return { dot: 'dirty', title: '有未提交的改动' }
  if (tracked) return { dot: 'clean', title: '已纳入 git，无改动' }
  return { dot: 'none', title: '该项目未启用 git 版本管理' }
}

/** P1 符号种类的中文标签 / 角标 / 颜色（大纲面板与符号地图共用） */
const SYMBOL_KINDS: Record<string, { label: string; mark: string; color: string }> = {
  function: { label: '函数', mark: 'ƒ', color: '#bc8cff' },
  class: { label: '类', mark: 'C', color: '#58a6ff' },
  signal: { label: '信号', mark: '~', color: '#f072b6' },
  enum: { label: '枚举', mark: 'E', color: '#e3a83a' },
  const: { label: '常量', mark: 'K', color: '#45c98c' },
  var: { label: '变量', mark: 'x', color: '#2ec4b6' },
  node: { label: '节点', mark: '▣', color: '#f0883e' },
  resource: { label: '资源', mark: 'R', color: '#8b97a7' },
  section: { label: '配置段', mark: '§', color: '#8b97a7' },
  group: { label: '导出分组', mark: '▼', color: '#7d8590' },
}

export function symbolKind(kind: string): { label: string; mark: string; color: string } {
  return SYMBOL_KINDS[kind] ?? { label: kind, mark: '·', color: '#7d8590' }
}
