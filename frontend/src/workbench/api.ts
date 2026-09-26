// 工作台文件系统接口 client（对应后端 workbench_fs.py，前缀 /api/fs）
// dev 由 vite 代理到 127.0.0.1:8000；打包后与后端同源，全部用相对路径。

export interface RegionMeta {
  key: string
  name: string
  dir: string
  desc: string
}

export interface TreeNode {
  path: string
  name: string
  type: 'dir' | 'file'
  region: string | null
  region_name: string | null
  lang: string | null
  writable: boolean
  children: TreeNode[]
  size?: number
  /** true=已纳入 git 且干净；false=未跟踪；null=不在任何 git 仓库 */
  tracked?: boolean | null
  /** true=有未提交改动（含新增已 add） */
  dirty?: boolean | null
}

export interface TreeResp {
  ok: boolean
  code_root: string
  regions_enabled: boolean
  regions: RegionMeta[]
  nodes: TreeNode[]
  truncated: boolean
}

export interface TaskScope {
  ok: boolean
  errors: { path: string; error: string }[]
  region: string
  allowed_paths: string[]
}

export interface EngineStatus {
  ok: boolean
  running: boolean
  pid?: number | null
  error?: string
  /** 引擎窗口是否已嵌进桌面宿主（浏览器模式下永远为 false） */
  embedded?: boolean
  child_hwnd?: number | null
  host_hwnd?: number | null
  embed_title?: string
  embed_offset_y?: number
  embed_dpi?: number
  host_dpi?: number
  embed_mode?: 'rect' | 'fill'
  embed_size?: { width: number; height: number }
  embed_error?: string
  /** engine/start 成功嵌入时回传的实际落点与 DPI */
  embed?: { width: number; height: number; offset_y: number; dpi: number; host_dpi: number; title: string }
}

/** 桌面宿主信息。浏览器模式下 desktop=false，前端据此降级（不显示"嵌入工作台"）。 */
export interface DesktopHost {
  ok: boolean
  host_hwnd: number | null
  desktop?: boolean
  dpi_awareness?: string
  /** 宿主客户区尺寸（物理像素）——把页面 CSS 坐标换算成引擎窗口坐标就靠它 */
  client?: { width: number; height: number }
  dpi?: number
  embedded?: {
    hwnd: number
    host: number
    mode?: 'rect' | 'fill'
    placed?: { x: number; y: number; width: number; height: number }
    alive: boolean
  }[]
  bridge_error?: string
}

/** 引擎视窗矩形：宿主客户区物理像素坐标 */
export interface EmbedRect { x: number; y: number; width: number; height: number }

export interface FileResp {
  ok: boolean
  path: string
  content: string
  lang: string
  size: number
  mtime: number
  region: string | null
  region_name: string | null
  writable: boolean
  tracked: boolean | null
  dirty: boolean | null
}

export interface SaveResp {
  ok: boolean
  path: string
  mtime: number
  changed: boolean
  region: string | null
  region_name: string | null
  reindexed: { code_chunks: number } | null
  reindex_warnings: string[]
}

export interface DeleteResp {
  ok: boolean
  deleted: string
  recoverable: boolean
}

export interface RenameResp {
  ok: boolean
  from: string
  to: string
  git_renamed: boolean
  reindex_warnings: string[]
}

/** P3：git 提交记录（/gitlog） */
export interface GitCommit {
  hash: string
  full_hash: string
  time: number | null
  time_raw: string
  author: string
  message: string
}
export interface GitLogResp {
  ok: boolean
  path: string
  commits: GitCommit[]
}

/** P3：文件级回滚响应；reverted=false 且 reason='clean' 表示本就无改动 */
export interface RevertResp {
  ok: boolean
  path: string
  reverted: boolean
  reason?: string
  mtime: number
}

/** P3：某文件某次提交时的历史内容 */
export interface GitShowResp {
  ok: boolean
  path: string
  ref: string
  content: string
  size: number
}

/** P3：恢复到历史版本（响应体同 save，另带 ref/restored） */
export interface RestoreResp extends SaveResp {
  ref: string
  restored: boolean
}

/** P1：符号（func/class/const/var/signal/enum/node ...） */
export interface SymbolInfo {
  name: string
  kind: string
  start: number
  end: number
  parent: string
  signature: string
  doc: string
  detail: string
}

export interface SymbolsResp {
  ok: boolean
  path: string
  lang: string
  class_name: string
  extends: string
  doc: string
  symbols: SymbolInfo[]
}

export interface SymbolMapFile {
  rel: string
  lang: string
  region: string
  region_name: string
  class_name: string
  extends: string
  doc: string
  symbols: SymbolInfo[]
}

export interface SymbolMapResp {
  ok: boolean
  code_root: string
  regions_enabled: boolean
  files: SymbolMapFile[]
  stats: {
    files: number
    symbols: number
    by_kind: Record<string, number>
    skipped: number
  }
}

/** P1：关系图节点（class/script/scene/external） */
export interface RelationNode {
  id: string
  label: string
  sub: string
  kind: 'class' | 'script' | 'scene' | 'engine' | 'external' | string
  rel: string
  line: number
  region: string
  region_name: string
  external: boolean
  doc: string
}

export interface RelationEdge {
  source: string
  target: string
  /** inherits=继承；mounts=场景挂载脚本；calls=项目内调用 */
  kind: 'inherits' | 'mounts' | 'calls' | string
  label: string
  line: number
  methods?: string[]
}

export interface RelationGraphResp {
  ok: boolean
  code_root: string
  regions_enabled: boolean
  nodes: RelationNode[]
  edges: RelationEdge[]
  stats: {
    files: number
    nodes: number
    user_nodes: number
    external_nodes: number
    edges: number
    edges_by_kind: Record<string, number>
    skipped: number
  }
}

/** P1-2：Unity GUID 引用图节点（资产 / 缺失外部引用） */
export interface UnityNode {
  id: string
  guid: string
  label: string
  sub: string
  /** scene/prefab/script/material/texture/.../missing */
  kind: string
  rel: string
  line: number
  external: boolean
  doc: string
}

export interface UnityEdge {
  source: string
  target: string
  kind: 'guid-ref' | string
  label: string
  line: number
  /** 同一 (源,目标) 的引用处数（已聚合） */
  count: number
}

export interface UnityGraphResp {
  ok: boolean
  root?: string
  nodes: UnityNode[]
  edges: UnityEdge[]
  stats: {
    unity_project: boolean
    scanned_root: string
    metas: number
    serialized_files: number
    assets_total: number
    nodes: number
    asset_nodes: number
    missing_nodes: number
    orphan_meta: number
    duplicate_guids: number
    edges: number
    resolved_edges: number
    missing_edges: number
    skipped: number
    by_kind: Record<string, number>
  }
  error?: string
}

/** 业务/HTTP 错误；status=0 表示网络层失败（服务未启动） */
export class FsApiError extends Error {
  status: number
  extra: Record<string, unknown>

  constructor(status: number, message: string, extra: Record<string, unknown> = {}) {
    super(message)
    this.name = 'FsApiError'
    this.status = status
    this.extra = extra
  }
}

// ---------------------------------------------------------------- P3/P4 项目上下文
// 全请求出口统一注入 `X-DocMind-Project`，把每个请求绑定到「当前项目」。
// 空串 = 未选择项目 → **不注入**（后端缺省回落当前项目，保持生命线）。持久化到
// localStorage 以便刷新后保持选中；后端切换项目后由 UI 回写对齐。
const PROJECT_ID_KEY = 'docmind_project_id'

/** 当前项目 id（持久化于 localStorage）；空串表示未选择。 */
export function getProjectId(): string {
  try {
    return localStorage.getItem(PROJECT_ID_KEY) || ''
  } catch {
    return '' // 隐私模式 / 无 localStorage：视作未选择
  }
}

/** 设置当前项目 id（空串 = 清除选择）；持久化以便刷新后保持。 */
export function setProjectId(pid: string): void {
  const previous = getProjectId()
  try {
    if (pid) localStorage.setItem(PROJECT_ID_KEY, pid)
    else localStorage.removeItem(PROJECT_ID_KEY)
  } catch {
    /* 写入失败（隐私模式等）不影响主流程 */
  }
  if (previous !== pid) window.dispatchEvent(new CustomEvent('docmind:project-context-changed'))
}

export interface ActiveTask { id: string; region: string; allowedPaths: string[] }
export function getActiveTask(project = getProjectId()): ActiveTask {
  try {
    const task = JSON.parse(localStorage.getItem('docmind.activeTask:' + project) || 'null')
    if (task && typeof task.id === 'string' && typeof task.region === 'string' &&
        Array.isArray(task.allowedPaths) && task.allowedPaths.every((p: unknown) => typeof p === 'string')) return task
  } catch { /* Invalid or legacy unscoped records must not grant scope to another project. */ }
  return { id: '', region: '', allowedPaths: [] }
}
export function setActiveTask(task: ActiveTask, project = getProjectId()) {
  localStorage.setItem('docmind.activeTask:' + project, JSON.stringify(task))
}

/**
 * 在 init.headers 基础上**合并** `X-DocMind-Project`。
 *
 * - 不改动既有头（尤其 `Content-Type`：FormData 场景必须由浏览器自带 multipart boundary）；
 * - `getProjectId()` 为空时**不注入**，原样返回（生命线：后端回落当前项目）；
 * - 用 `new Headers(...)` 统一处理 headers 为 `undefined` / 普通对象 / `Headers` 实例三种形态。
 */
export function withProject(init?: RequestInit, projectId = getProjectId()): RequestInit {
  const base: RequestInit = init ? { ...init } : {}
  const pid = projectId
  if (!pid) return base
  const merged = new Headers(base.headers as HeadersInit | undefined)
  merged.set('X-DocMind-Project', pid)
  base.headers = merged
  return base
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, withProject(init))
  } catch {
    throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
  }
  let body: any = null
  try {
    body = await res.json()
  } catch {
    /* 非 JSON 响应走下面的状态码分支 */
  }
  if (!res.ok || body?.ok === false) {
    const { ok: _ok, error: _e, ...extra } = body || {}
    throw new FsApiError(
      res.status,
      body?.error || `请求失败（HTTP ${res.status}）`,
      extra as Record<string, unknown>,
    )
  }
  return body as T
}

async function postJson<T>(url: string, payload: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/**
 * 业务失败也当数据返回（不抛错）。
 *
 * 通用 request 会把 `ok:false` 抛成异常，但场景编辑必须读到失败体里的
 * stale / rolled_back / warnings 才能正确分支，所以这里单独给一条原始通道；
 * 只有网络不可达或响应不是 JSON 才抛错。
 */
async function rawJson<T>(url: string, payload?: unknown, signal?: AbortSignal, projectId = getProjectId()): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, withProject(payload === undefined
      ? { method: 'GET', signal }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal }, projectId))
  } catch {
    throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
  }
  try {
    return (await res.json()) as T
  } catch {
    throw new FsApiError(res.status, `响应不是合法 JSON（HTTP ${res.status}）`)
  }
}

// ---------------------------------------------------------------- 项目 API（P3 端点）
export interface ProjectInfo {
  project_id: string
  root: string
  name: string
  created_at?: string
  last_opened?: string
}

export interface ProjectListResp {
  ok: boolean
  current: string
  projects: ProjectInfo[]
}

export interface ProjectMutationResp {
  ok: boolean
  project_id?: string
  project?: ProjectInfo
  code_root?: string
  current?: string
  notice?: string
  error?: string
}

/**
 * 项目 CRUD 的原始通道：**不**把 `ok:false` 抛错，便于 UI 读失败体里的 `error`
 * （例如「目录不存在」「项目名称不能为空」）；只有网络不可达或响应非 JSON 才抛错。
 * 全部经 `withProject` 注入项目头。
 */
async function projectRequest<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, withProject(init))
  } catch {
    throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
  }
  try {
    return (await res.json()) as T
  } catch {
    throw new FsApiError(res.status, `响应不是合法 JSON（HTTP ${res.status}）`)
  }
}

export const projectApi = {
  /** 项目列表 + 当前项目。返回字段：ok / current / projects[]。 */
  list(): Promise<ProjectListResp> {
    return projectRequest<ProjectListResp>('/api/projects')
  },
  /** 登记并激活一个代码库（后端 ensure+activate）。root 必须是已存在目录。 */
  create(root: string, name?: string): Promise<ProjectMutationResp> {
    return projectRequest<ProjectMutationResp>('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ root, name: name || '' }),
    })
  },
  /** 把某项目切为「当前项目」。 */
  activate(pid: string): Promise<ProjectMutationResp> {
    return projectRequest<ProjectMutationResp>(
      `/api/projects/${encodeURIComponent(pid)}/activate`, { method: 'POST' })
  },
  /** 重命名项目（仅登记信息，不动磁盘）。 */
  rename(pid: string, name: string): Promise<ProjectMutationResp> {
    return projectRequest<ProjectMutationResp>(`/api/projects/${encodeURIComponent(pid)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    })
  },
  /** 注销项目登记（不删磁盘上的索引/会话）。 */
  remove(pid: string): Promise<ProjectMutationResp> {
    return projectRequest<ProjectMutationResp>(
      `/api/projects/${encodeURIComponent(pid)}`, { method: 'DELETE' })
  },
}

export const fsApi = {
  tree(depth = 4): Promise<TreeResp> {
    return request<TreeResp>(`/api/fs/tree?depth=${depth}`)
  },
  read(path: string): Promise<FileResp> {
    return request<FileResp>(`/api/fs/file?path=${encodeURIComponent(path)}`)
  },
  save(path: string, content: string, ifMtime: number | null, reindex = true, taskId = ''): Promise<SaveResp> {
    return postJson<SaveResp>('/api/fs/save', {
      path,
      content,
      if_mtime: ifMtime,
      reindex,
      task_id: taskId,
    })
  },
  create(path: string, type: 'file' | 'folder', content = '') {
    return postJson<{ ok: boolean; path: string; node: TreeNode }>('/api/fs/create', {
      path, type, content,
    })
  },
  rename(path: string, newPath: string, reindex = true): Promise<RenameResp> {
    return postJson<RenameResp>('/api/fs/rename', {
      path, new_path: newPath, reindex,
    })
  },
  delete(path: string, recursive = false, force = false): Promise<DeleteResp> {
    return postJson<DeleteResp>('/api/fs/delete', { path, recursive, force })
  },
  symbols(path: string): Promise<SymbolsResp> {
    return request<SymbolsResp>(`/api/fs/symbols?path=${encodeURIComponent(path)}`)
  },
  symbolMap(): Promise<SymbolMapResp> {
    return request<SymbolMapResp>('/api/fs/symbol-map')
  },
  relationGraph(): Promise<RelationGraphResp> {
    return request<RelationGraphResp>('/api/fs/relation-graph')
  },
  unityGuidGraph(): Promise<UnityGraphResp> {
    return request<UnityGraphResp>('/api/unity/guid-graph')
  },
  sceneTree(path: string) { return request<{ ok: boolean; nodes: { name: string; type: string; parent: string; line: number; node_path?: string; properties?: {name:string;value:string;line:number}[] }[] }>(`/api/fs/scene-tree?path=${encodeURIComponent(path)}`) },
  setSceneProperty(path: string, node: string, property: string, value: string) { return postJson<{ ok: boolean; error?: string }>('/api/fs/scene-property', { path, node, property, value }) },
  gitlog(path: string, limit = 20): Promise<GitLogResp> {
    return request<GitLogResp>(
      `/api/fs/gitlog?path=${encodeURIComponent(path)}&limit=${limit}`,
    )
  },
  revert(path: string): Promise<RevertResp> {
    return postJson<RevertResp>('/api/fs/revert', { path })
  },
  gitShow(path: string, ref: string): Promise<GitShowResp> {
    return request<GitShowResp>(
      `/api/fs/git-show?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`,
    )
  },
  restoreAt(path: string, ref: string, reindex = true): Promise<RestoreResp> {
    return postJson<RestoreResp>('/api/fs/restore-at', { path, ref, reindex })
  },
}

// ------------------------------------------------ 阶段 1：语义标签 + 大白话定位
/** 单文件业务标签记录（/api/fs/semantic-tags 的 files 值） */
export interface SemanticTagRecord {
  mtime: number
  size: number
  tags: string[]
  summary: string
  symbols: string[]
  /** llm=模型标注；rules=关键词降级；manual=人工修正（不被自动刷新覆盖） */
  origin: 'llm' | 'rules' | 'manual' | string
  stale?: boolean
  updated_at?: string
}

export interface SemanticTagsResp {
  ok: boolean
  files: Record<string, SemanticTagRecord>
  total_files: number
  tagged_files: number
  pending: number
  stale: number
  counts: { llm: number; rules: number; manual: number }
  has_store?: boolean
  error?: string
}

/** /api/fs/locate 单条文件命中 */
export interface LocateFileHit {
  path: string
  name: string
  score: number
  reasons: string[]
  line: number | null
  symbol: string
  region: string
  region_name: string
  tags: string[]
  summary: string
}

export interface LocateRegionHit {
  key: string
  name: string
  dir: string
  desc: string
}

export interface LocateResp {
  ok: boolean
  query: string
  files: LocateFileHit[]
  regions: LocateRegionHit[]
  total: number
  degraded?: string
  error?: string
}

/** /api/fs/region-cards 分区首页卡片 */
export interface RegionCard {
  key: string
  name: string
  dir: string
  desc: string
  access: string
  depends_on: string[]
  exports: string[]
  missing_exports: string[]
  verify: string
  exists: boolean
  git: boolean
  branch: string
  dirty: boolean
  files: number
  dirty_count: number
  own_repo: boolean
  last_commit: {
    hash: string
    full_hash: string
    message: string
    author: string
    time: number | null
    time_raw: string
  } | null
}

export interface RegionCardsResp {
  ok: boolean
  code_root: string
  regions: RegionCard[]
}

export const semanticApi = {
  /** 读标签现状（不调模型） */
  tags(): Promise<SemanticTagsResp> {
    return request<SemanticTagsResp>('/api/fs/semantic-tags')
  },
  /** 现场增量标注（LLM 批量 + 规则降级），limit=本次最多标注文件数 */
  refreshTags(limit = 60): Promise<SemanticTagsResp> {
    return request<SemanticTagsResp>(`/api/fs/semantic-tags?refresh=1&limit=${limit}`)
  },
  /** 人工修正标签 */
  updateTags(path: string, tags: string[], summary = ''): Promise<{ ok: boolean }> {
    return postJson('/api/fs/semantic-tags/update', { path, tags, summary })
  },
  locate(q: string, limit = 20): Promise<LocateResp> {
    return request<LocateResp>(`/api/fs/locate?q=${encodeURIComponent(q)}&limit=${limit}`)
  },
  regionCards(): Promise<RegionCardsResp> {
    return request<RegionCardsResp>('/api/fs/region-cards')
  },
}

export const taskApi = {
  list() { return request<{ ok: boolean; tasks: Record<string, unknown>[] }>('/api/tasks') },
  create(payload: Record<string, unknown>) { return postJson<{ ok: boolean; task?: Record<string, unknown>; error?: string }>('/api/tasks', payload) },
  validate(payload: Record<string, unknown>) { return postJson<{ ok: boolean; scope: TaskScope }>('/api/tasks/validate', payload) },
  impact(payload: Record<string, unknown>) { return postJson<{ ok: boolean; files: string[]; direct_files: string[]; related_files: string[] }>('/api/tasks/impact', payload) },
  snapshot(payload: Record<string, unknown>) { return postJson<{ ok: boolean; snapshot: Record<string, unknown> }>('/api/tasks/snapshot', payload) },
  verify(payload: Record<string, unknown>) { return postJson<{ ok: boolean; checks: { command: string; ok: boolean; output?: string; error?: string }[] }>('/api/tasks/verify', payload) },
  branch(payload: Record<string, unknown>) { return postJson<{ ok: boolean; branch?: string; error?: string }>('/api/tasks/branch', payload) },
}

export const engineApi = {
  unrealBridgeStatus() { return request<{ok:boolean;available:boolean;error?:string}>('/api/engine/unreal-bridge/status') },
  unrealAssets() { return request<{ok:boolean;available:boolean;assets?:string[];error?:string}>('/api/engine/unreal-bridge/assets') },
  unrealActors() { return request<{ok:boolean;available:boolean;actors?:{name:string;class:string}[];error?:string}>('/api/engine/unreal-bridge/actors') },
  unrealBlueprint(path: string) { return request<{ok:boolean;available:boolean;blueprint?:string;nodes?:unknown[];variables?:unknown[];links?:unknown[];error?:string}>(`/api/engine/unreal-bridge/blueprint/${encodeURIComponent(path)}`) },
  unrealActor(name: string) { return request<{ok:boolean;available:boolean;actor?:string;components?:unknown[];properties?:unknown[];error?:string}>(`/api/engine/unreal-bridge/actor/${encodeURIComponent(name)}`) },
  unrealWrite(body: {task_id:string;target_path:string;property:string;value:unknown;confirm:boolean}) { return postJson<{ok:boolean;available?:boolean;error?:string}>('/api/engine/unreal-bridge/write', body) },
  catalog() { return request<{ ok:boolean; engines:{id:string;name:string;executable:string;download:string}[] }>('/api/engine/catalog') },
  config(engine?: string, executable?: string) { return engine ? postJson<{ok:boolean;engine:string;executable:string}>('/api/engine/config',{engine,executable}) : request<{ok:boolean;engine:string;executable:string}>('/api/engine/config') },
  status() { return request<EngineStatus>('/api/engine/status') },
  start(executable = 'godot', embed = true, rect?: EmbedRect | null) {
    return postJson<EngineStatus>('/api/engine/start', rect ? { executable, embed, rect } : { executable, embed })
  },
  host() { return request<DesktopHost>('/api/desktop/host') },
  /** 把已运行的引擎窗口嵌进桌面宿主。rect 省略时按宿主客户区铺满。 */
  embed(rect?: EmbedRect | null) { return postJson<{ ok: boolean; error?: string; width?: number; height?: number; embedded?: boolean }>('/api/engine/embed', rect || {}) },
  /** 引擎视窗随前端布局变化重新定位（弹窗移动、窗口缩放时调用）。 */
  place(rect: EmbedRect) { return postJson<{ ok: boolean; error?: string }>('/api/engine/place', rect) },
  detach() { return postJson<{ ok: boolean; was_embedded?: boolean; error?: string }>('/api/engine/detach', {}) },
  focusEngine(keepAttached = false) { return postJson<{ ok: boolean; focused?: number; error?: string }>('/api/engine/focus', { keep_attached: !!keepAttached }) },
  /** 按宿主当前客户区重排"铺满模式"的嵌入窗口 */
  resizeEngine() { return postJson<{ ok: boolean; error?: string }>('/api/engine/resize', {}) },
  stop() { return postJson<{ ok: boolean; stopped: boolean }>('/api/engine/stop', {}) },
  /** 保存启动参数并快速重启运行中的 Godot，恢复原嵌入位置。 */
  reload() { return postJson<{ ok: boolean; running?: boolean; embedded?: boolean; reloaded?: boolean; reload_mode?: string; error?: string }>('/api/engine/reload', {}) },
  /** 检查引擎启动后项目脚本/场景/资源是否被外部修改，不会自动重载。 */
  changes() { return request<{ ok: boolean; running?: boolean; changed?: string[]; added?: string[]; deleted?: string[]; files?: string[]; count?: number; error?: string }>('/api/engine/changes') },
  logs(limit = 200) { return request<{ ok: boolean; lines: string[]; errors: { path: string; line: number; message: string }[] }>(`/api/engine/logs?limit=${limit}`) },
  verify(executable = 'godot') { return postJson<{ ok: boolean; output?: string; error?: string }>('/api/engine/verify', { executable }) },
}

export const runtimeApi = {
  events() { return request<{ ok: boolean; events: Record<string, unknown>[] }>('/api/runtime/events') },
  append(events: Record<string, unknown>[]) { return postJson<{ ok: boolean; events: Record<string, unknown>[] }>('/api/runtime/events', { events }) },
  /** 会话切分（时间线按会话分组） */
  sessions(gap = 120) { return request<{ ok: boolean; total: number; sessions: RuntimeSession[] }>(`/api/runtime/sessions?gap=${gap}`) },
  clear(scope: 'stored' | 'all' = 'stored') { return postJson<{ ok: boolean; scope: string; removed: number }>('/api/runtime/clear', { scope }) },
}

export interface RuntimeSession {
  index: number
  id: string
  start: string
  end: string
  count: number
  types: Record<string, number>
  sources: Record<string, number>
}

/* ---------------- 场景画布（P0-2） ---------------- */

export interface SceneProperty {
  name: string
  value: string
  line: number
  kind: 'ref' | 'transform' | 'vector' | 'array' | 'string' | 'bool' | 'number' | 'other'
}

export interface SceneNode {
  id: string
  name: string
  type: string
  parent: string | null
  parent_attr: string
  line: number
  space: '2d' | '3d'
  depth: number
  instance_id: string
  script_id: string
  instance: string
  script: string
  groups: string[]
  index: string
  position: number[] | null
  position_from: string
  rotation: number | null
  scale: number[] | null
  children: string[]
  /** 引用的其它外部资源卡 id（'ext:<rid>'），贴图/材质/字体等 */
  resource_ids: string[]
  /** true = 位于实例（instance）子树内，改动属于引擎侧覆写 */
  overridden: boolean
  properties: SceneProperty[]
}

export interface SceneFile {
  id: string
  rel: string
  raw: string
  kind: 'scene' | 'script' | 'resource'
  chip: string
  resolved: boolean
  type: string
  uid: string
  nodes: string[]
  used: number
  orphan?: boolean
}

export interface SceneEdge {
  id: string
  source: string
  target: string
  kind: 'hierarchy' | 'script' | 'instance' | 'reference'
}

export interface SceneGuard {
  writable: boolean
  reason: string
  region: string | null
  region_name: string | null
  tracked: boolean | null
  dirty: boolean | null
}

export interface SceneGraph {
  ok: boolean
  error?: string
  path: string
  root_id: string
  header: Record<string, string>
  space: '2d' | '3d'
  nodes: SceneNode[]
  files: SceneFile[]
  edges: SceneEdge[]
  sub_resources: { id: string; type: string; line: number }[]
  structure_errors: string[]
  warnings: string[]
  guard: SceneGuard
  stats: {
    nodes: number
    edges: number
    files: number
    max_depth: number
    instances: number
    scripts: number
    lines: number
    size: number
    /** 零引用的外部资源数（可清理提示） */
    orphans: number
    revision: string
    mtime: number
    editable: boolean
  }
  read_only: boolean
}

/**
 * 一条可反向执行的编辑描述。
 * 字段名与 POST /api/scene/op 的请求体完全同形，所以 `sceneApi.op(undo)` 就能撤销，
 * 前端不需要任何字段映射（这个"同形"是刻意的：曾经用过 props/properties 两套名字，
 * 结果撤销请求被后端当成空 payload 拒掉，所以定死一套）。
 */
export interface SceneUndo {
  op: string
  node?: string
  name?: string
  parent?: string
  properties?: Record<string, string>
  remove?: string[]
  lines?: string[]
  at?: number
}

export interface SceneOpResult {
  ok: boolean
  error?: string
  status?: number
  /** 文件已被外部修改，需重新加载 */
  stale?: boolean
  /** 编辑导致结构非法且已自动回滚 */
  rolled_back?: boolean
  errors?: string[]
  path?: string
  op?: string
  node?: string
  undo?: SceneUndo
  warnings?: string[]
  revision?: string
  mtime?: number
}

export const sceneApi = {
  graph(path: string) { return rawJson<SceneGraph>(`/api/scene/graph?path=${encodeURIComponent(path)}`) },
  op(payload: Record<string, unknown>) { return rawJson<SceneOpResult>('/api/scene/op', payload) },
  /** 项目主场景（project.godot 的 run/main_scene）——场景画布打开时自动加载用 */
  main() { return rawJson<{ ok: boolean; scene?: string; exists?: boolean; error?: string; note?: string }>('/api/scene/main') },
}

/** P1：Web 导出 / iframe 试玩 */
export interface WebTemplates {
  ok: boolean
  version?: string
  template_dir?: string
  installed: boolean
  web_debug: boolean
  web_release: boolean
  install?: { state: string; downloaded: number; total: number; error: string; started_at: number; mirror?: string }
  error?: string
}
export interface WebExportResult {
  ok: boolean
  token?: string
  url?: string
  elapsed?: number
  files?: Record<string, number>
  html_injected?: boolean
  bridge_changed?: string[]
  preset_added?: boolean
  output?: string
  error?: string
  message?: string
  templates?: WebTemplates
}
export const playApi = {
  templates() { return request<WebTemplates>('/api/engine/web/templates') },
  installTemplates() { return postJson<{ ok: boolean; started: boolean }>('/api/engine/web/templates/install', { confirm: true }) },
  exportWeb() { return postJson<WebExportResult>('/api/engine/web/export', {}) },
}

export interface ComfyWatchJob {
  prompt_id: string
  running: boolean
  done: boolean
  result?: Record<string, unknown> | null
  started_at?: string
  finished_at?: string
  cancel_requested?: boolean
  cancel_requested_at?: string
  cancel_state?: 'requesting' | 'requested' | 'terminated' | 'failed'
  status?: 'queued' | 'running' | 'completed' | 'failed' | 'timeout' | 'recovered'
  recovery_note?: string
  recovered_at?: string
  progress?: { executed_nodes: number; total_nodes: number; percent: number }
}

export interface ComfyModelCheckTemplate {
  id: string
  name: string
  present: boolean
  missing: string[]
  pending: boolean
}
export interface ComfyModelCheckResult {
  ok: boolean
  root?: string
  found?: string[]
  templates?: ComfyModelCheckTemplate[]
  error?: string
}

export const comfyApi = {
  start(root?: string, port = 8188) { return postJson<{ok:boolean;running?:boolean;pid?:number;error?:string}>('/api/comfy/start', { root, port }) },
  stop() { return postJson<{ok:boolean;running?:boolean;stopped?:boolean;pid?:number;error?:string}>('/api/comfy/stop', {}) },
  templates() { return request<{ ok: boolean; templates: { id:string; name:string; model:string; kind:string; group?:'image'|'control'|'character'|'video'; vram_gb?:number; models?:string[]; pending?:boolean; hint?:string; source_url?:string; workflow?:string; schema?:Record<string,string> }[] }>('/api/comfy/templates') },
  /** 只读检测各模板依赖的模型文件是否存在（不下载）。 */
  modelCheck() { return request<ComfyModelCheckResult>('/api/comfy/model-check') },
  template(id: string) { return request<{ ok:boolean; workflow?:Record<string, unknown>; format?:string; error?:string }>(`/api/comfy/templates/${encodeURIComponent(id)}`) },
  apply(workflow: Record<string, unknown>, parameters: Record<string, unknown>) { return postJson<{ok:boolean; workflow?:Record<string, unknown>; error?:string}>('/api/comfy/templates/apply', {workflow, parameters}) },
  jobs(page=1, pageSize=20) { return request<{ok:boolean; total:number; items:Record<string, unknown>[]}>(`/api/comfy/jobs?page=${page}&page_size=${pageSize}`) },
  retry(promptId: string, url = 'http://127.0.0.1:8188') { return postJson<{ok:boolean; response?:Record<string, unknown>; retry_count?:number; error?:string}>(`/api/comfy/retry/${encodeURIComponent(promptId)}?url=${encodeURIComponent(url)}`, {}) },
  status(url = 'http://127.0.0.1:8188') { return request<{ ok: boolean; available: boolean; url: string; error?: string }>(`/api/comfy/status?url=${encodeURIComponent(url)}`) },
  /** 提交成功后租约 reown 为 comfyui:{prompt_id}，整作业周期持有；watch 仅轮询不持租约 */
  queue(workflow: Record<string, unknown>, url = 'http://127.0.0.1:8188') { return postJson<{ ok: boolean; response?: Record<string, unknown>; workflow_sha256?: string; watch?: { ok: boolean; job?: ComfyWatchJob }; lease?: { owner: string; ttl: number; gpu: number | null; evicted: boolean }; error?: string }>('/api/comfy/queue', { url, workflow }) },
  history(promptId: string, url = 'http://127.0.0.1:8188') { return request<{ ok: boolean; done?: boolean; finished?: boolean; failed?: boolean; lease_released?: boolean; progress?: { executed_nodes: number; total_nodes: number; percent: number }; outputs?: { filename?: string; subfolder?: string; type?: string; preview_url?: string; mime?: string; asset_kind?: 'media'|'3d'|'unknown'; preview_supported?: boolean }[]; status?: Record<string, unknown>; error?: string }>(`/api/comfy/history/${encodeURIComponent(promptId)}?url=${encodeURIComponent(url)}`) },
  wait(promptId: string, url = 'http://127.0.0.1:8188', timeout = 120, interval = 1.0) { return request<{ ok: boolean; timeout?: boolean; error?: string }>(`/api/comfy/wait/${encodeURIComponent(promptId)}?url=${encodeURIComponent(url)}&timeout=${timeout}&interval=${interval}`) },
  watch(promptId: string, url = 'http://127.0.0.1:8188', timeout = 900, interval = 1.0) { return postJson<{ ok: boolean; job?: ComfyWatchJob; error?: string }>(`/api/comfy/watch/${encodeURIComponent(promptId)}?url=${encodeURIComponent(url)}&timeout=${timeout}&interval=${interval}`, {}) },
  watchStatus(promptId: string) { return request<{ ok: boolean; job?: ComfyWatchJob | null }>(`/api/comfy/watch/${encodeURIComponent(promptId)}`) },
  cancel(promptId: string, url = 'http://127.0.0.1:8188') { return postJson<{ ok: boolean; prompt_id: string; deleted: boolean; interrupted: boolean; lease_released: boolean; error?: string }>('/api/comfy/cancel', { url, prompt_id: promptId }) },
  duplicates(directory = 'assets/generated') { return request<{ ok: boolean; directory: string; groups: { sha256: string; paths: string[]; duplicate_count: number }[]; duplicate_files: number; files: number }>(`/api/comfy/resources/duplicates?directory=${encodeURIComponent(directory)}`) },
  unused(directory = 'assets/generated') { return request<{ ok: boolean; directory: string; unused: string[]; files: number; unused_count: number }>(`/api/comfy/resources/unused?directory=${encodeURIComponent(directory)}`) },
  import(promptId: string, image: Record<string, unknown>, url = 'http://127.0.0.1:8188', destDir = 'assets/generated') { return postJson<{ ok: boolean; path?: string; error?: string }>('/api/comfy/import', { prompt_id: promptId, image, url, dest_dir: destDir }) },
  importAll(promptId: string, images: Record<string, unknown>[], url = 'http://127.0.0.1:8188', destDir = 'assets/generated') { return postJson<{ ok: boolean; imported: number; results: { ok: boolean; path?: string; error?: string }[] }>('/api/comfy/import-all', { prompt_id: promptId, images, url, dest_dir: destDir }) },
  validateProvenance(meta: Record<string, unknown>) { return postJson<{ok:boolean;errors:string[];review_required:boolean}>('/api/comfy/provenance/validate', meta) },
}

/** 素材中心（asset_sources.py / /api/assets/*，阶段 2） */
export type AssetKind = 'model' | 'texture' | 'hdri' | 'image' | 'audio' | '2d' | 'animation' | 'other'
export interface AssetSourceInfo {
  key: string; name: string; mode: 'remote' | 'pack'
  kinds: string[]; license: string; home: string
}
export interface ExternalSource { key: string; name: string; url: string }
export interface AssetItem {
  id: string; source: string; kind: AssetKind; name: string
  author: string; license: string; page_url: string; thumb_url: string
  tags: string[]; summary: string
}
export interface AssetOption { label: string; ext: string; size: number; url: string; md5: string }
export interface KenneyPack {
  slug: string; name: string; kinds: string[]; summary: string
  source: string; license: string; page_url: string; thumb_url: string
}
export interface PackFile {
  path: string; show_path: string; ext: string; kind: AssetKind
  size: number; dep: boolean
}
export interface PackPeek {
  ok: boolean; token: string; name: string; thumb: string; page_url: string
  files: PackFile[]; prefix: string; cached: boolean; error?: string
}
export interface ImportResult {
  ok: boolean; path?: string; kind?: string; size?: number; sha256?: string
  license?: string; author?: string; error?: string
}
export interface PackImportResp {
  ok: boolean; imported: ImportResult[]; skipped: ImportResult[]
  imported_count: number; skipped_count: number; error?: string
}
export interface LibraryItem {
  path: string; name: string; kind: AssetKind; size: number; mtime: number
  source: string; author: string; license: string; imported_at: string; duplicate: boolean
  /** 仅离线演示数据使用：内联占位缩略图（真实接口不返回） */
  thumb?: string
  /** kind === 'animation' 时的精灵播放元数据 */
  fps?: number; frame_count?: number; cols?: number; rows?: number
  frame_width?: number; frame_height?: number
  frames_dir?: string; first_frame?: string; prompt?: string; manifest?: string
}
export interface LibraryResp {
  ok: boolean; total: number
  dirs: { dir: string; items: LibraryItem[] }[]; error?: string
}

export const assetsApi = {
  sources() {
    return request<{ ok: boolean; sources: AssetSourceInfo[]; external: ExternalSource[] }>('/api/assets/sources')
  },
  polySearch(q: string, kind: string, page: number) {
    return request<{ ok: boolean; page: number; has_more: boolean; items: AssetItem[]; error?: string }>(
      `/api/assets/search?source=polyhaven&kind=${encodeURIComponent(kind)}&page=${page}&q=${encodeURIComponent(q)}`)
  },
  resolve(id: string, kind: string) {
    return request<{ ok: boolean; options: AssetOption[]; error?: string }>(
      `/api/assets/resolve?id=${encodeURIComponent(id)}&kind=${encodeURIComponent(kind)}`)
  },
  importItem(payload: { source: string; item_id: string; option: AssetOption; kind: string;
                        dest_dir: string; author: string; source_url: string }) {
    return postJson<ImportResult>('/api/assets/import', payload)
  },
  packs(q = '', kinds = '') {
    return request<{ ok: boolean; items: KenneyPack[] }>(
      `/api/assets/packs?q=${encodeURIComponent(q)}&kinds=${encodeURIComponent(kinds)}`)
  },
  packPeek(slug: string) {
    return postJson<PackPeek>('/api/assets/packs/peek', { slug })
  },
  packImport(token: string, selected: string[], destRoot: string) {
    return postJson<PackImportResp>('/api/assets/packs/import',
      { token, selected, dest_root: destRoot })
  },
  packPreviewUrl(token: string, file: string) {
    return `/api/assets/packs/preview?token=${encodeURIComponent(token)}&file=${encodeURIComponent(file)}`
  },
  library() {
    return request<LibraryResp>('/api/assets/library')
  },
  rawUrl(path: string) {
    return `/api/assets/raw?path=${encodeURIComponent(path)}`
  },
  proxyUrl(url: string) {
    return `/api/assets/proxy?url=${encodeURIComponent(url)}`
  },
}

/** 本地 AI 生成（asset_gen.py / /api/assets/generate/*，阶段 5） */
export interface GenStatus {
  ok: boolean
  online: boolean
  online_error: string
  comfy_root: string
  models_dir: string
  image: { ready: boolean; missing: string[] }
  video: { ready: boolean; missing: string[] }
  workflows: { i2v: string; t2v: string }
}
export interface GenJob {
  id: string
  type: 'image' | 'animation'
  status: 'queued' | 'running' | 'completed' | 'failed' | 'canceling'
  phase: string
  progress: number
  prompt_id: string
  prompt: string
  result: Record<string, unknown> | null
  error: string
  created_at: string
  cancel_requested?: boolean
  /** 云端任务自带：engine='cloud'，并带服务商与模型 */
  engine?: 'local' | 'cloud'
  provider?: string
  model?: string
}
export interface GenSubmitResp { ok: boolean; job_id?: string; error?: string }

export const genApi = {
  status() {
    return request<GenStatus>('/api/assets/generate/status')
  },
  image(p: { prompt: string; negative_prompt: string; width: number; height: number;
             steps: number; seed: number; batch_size: number }) {
    return postJson<GenSubmitResp>('/api/assets/generate/image', p)
  },
  animation(p: { prompt: string; duration: number; seed: number; fps: number;
                 max_frames: number; turbo: boolean;
                 first_frame_path?: string; first_frame_name?: string }) {
    return postJson<GenSubmitResp>('/api/assets/generate/animation', p)
  },
  async uploadFrame(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/assets/generate/upload-frame', withProject({ method: 'POST', body: fd }))
    const body = await r.json().catch(() => ({})) as { ok?: boolean; name?: string; error?: string }
    if (!r.ok || !body.ok) throw new FsApiError(r.status, body.error || '首帧上传失败')
    return body.name as string
  },
  jobs() {
    return request<{ ok: boolean; jobs: GenJob[] }>('/api/assets/generate/jobs')
  },
  job(id: string) {
    return request<{ ok: boolean; job: GenJob; error?: string }>(
      `/api/assets/generate/jobs/${encodeURIComponent(id)}`)
  },
  cancel(id: string) {
    return postJson<{ ok: boolean; error?: string }>(
      `/api/assets/generate/jobs/${encodeURIComponent(id)}/cancel`, {})
  },
}

/** 云端 AI 生成（cloud_gen.py / /api/assets/cloud/*，用户自带 Key） */
export interface CloudModelInfo { id: string; label: string; i2v?: boolean }
export interface CloudProvider {
  id: string
  name: string
  base_url: string
  adapter: string
  key_url: string
  doc_url: string
  note: string
  image_models: CloudModelInfo[]
  video_models: CloudModelInfo[]
}
export interface CloudKeyState { saved: boolean; mask: string }

export const cloudGenApi = {
  providers() {
    return request<{ ok: boolean; providers: CloudProvider[] }>('/api/assets/cloud/providers')
  },
  keys() {
    return request<{ ok: boolean; keys: Record<string, CloudKeyState> }>('/api/assets/cloud/keys')
  },
  saveKey(provider: string, key: string) {
    return postJson<{ ok: boolean; error?: string }>('/api/assets/cloud/key', { provider, key })
  },
  deleteKey(provider: string) {
    return postJson<{ ok: boolean; error?: string }>('/api/assets/cloud/key/delete', { provider, key: '' })
  },
  image(p: { provider: string; model: string; prompt: string; negative_prompt?: string;
              width: number; height: number; seed?: number; batch?: number;
              api_key?: string; base_url?: string }) {
    return postJson<GenSubmitResp>('/api/assets/cloud/image', p)
  },
  animation(p: { provider: string; model: string; prompt: string; first_frame_path?: string;
                  duration: number; seed?: number; fps: number; max_frames: number;
                  api_key?: string; base_url?: string }) {
    return postJson<GenSubmitResp>('/api/assets/cloud/animation', p)
  },
  async uploadFrame(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    const r = await fetch('/api/assets/cloud/upload-frame', withProject({ method: 'POST', body: fd }))
    const body = await r.json().catch(() => ({})) as { ok?: boolean; path?: string; error?: string }
    if (!r.ok || !body.ok) throw new FsApiError(r.status, body.error || '首帧上传失败')
    return body.path as string
  },
}

/** GPU 协调器（gpu_coordinator.py / /api/gpu/*） */
export interface GpuHolder {
  owner: string
  purpose: string
  held_for_seconds: number | null
  lease_ttl: number
}
export interface GpuQueueItem {
  owner: string
  purpose: string
  /** 指定卡序号；null=自动挑最空的卡 */
  gpu: number | null
  waiting_gpu: boolean
}
export interface GpuInfo {
  index: number
  name: string | null
  used_mb: number | null
  total_mb: number | null
  free_mb: number | null
  utilization: number | null
  temperature_c: number | null
  holder: GpuHolder | null
  queue: GpuQueueItem[]
}
export interface GpuSamplePoint {
  t: number
  gpus: { index: number; used_mb: number; total_mb: number; utilization: number }[]
}
export interface GpuStatus {
  ok?: boolean
  mode: 'serial' | 'parallel' | 'multi'
  coordinating: boolean
  active: string | null
  purpose: string
  held_for_seconds: number | null
  holders: (GpuHolder & { gpu: number })[]
  queue: GpuQueueItem[]
  queue_length: number
  memory: { used_mb: number; total_mb: number; free_mb: number; utilization: number } | null
  gpus: GpuInfo[]
  samples: GpuSamplePoint[]
  ollama_idle: { unload_seconds: number; last_activity_ago: number | null; last_unload_ago: number | null }
  hooks: string[]
  processes?: { pid:number; owner:string; gpu:number|null; purpose:string; status:string; last_heartbeat:number }[]
  compute_apps?: { pid:number; process_name:string; used_mb:number }[]
  recovery_events?: Record<string, unknown>[]
}

export const gpuApi = {
  status() { return request<GpuStatus>('/api/gpu/status') },
  cancel(owner: string) { return postJson<{ ok: boolean; canceled?: number; error?: string }>('/api/gpu/cancel', { owner }) },
  forceRelease(owner?: string) { return postJson<{ ok: boolean; released?: string; error?: string }>('/api/gpu/force-release', { owner: owner ?? '' }) },
  configure(idleUnloadSeconds?: number, pollInterval?: number) {
    return postJson<GpuStatus & { ok: boolean }>('/api/gpu/configure', {
      idle_unload_seconds: idleUnloadSeconds,
      poll_interval: pollInterval,
    })
  },
  environment(deviceIndex = -1) {
    return request<{ ok: boolean; environment: Record<string, string> }>(`/api/gpu/environment?device_index=${deviceIndex}`)
  },
}

/** 本地模型驻留（显存占用）：GET /api/model_status + POST /api/model_power。
 *  本地大模型默认长期驻留显存，切模型 / 跑 ComfyUI 生图前可一键卸载腾显存。 */
export interface ModelResident {
  name: string
  size_vram_gb: number
  size_gb: number
  expires_minutes: number | null
  processor: string
}
export interface ModelStatus {
  reachable: boolean
  loaded: ModelResident[]
  vram_gb: number
  needs_ollama: boolean
  needed_models?: string[]
  error?: string
}
export interface ModelPowerResult {
  ok: boolean
  action?: string
  unloaded?: string[]
  preloaded?: string[]
  skipped?: { name: string; size_gb: number; free_gb: number }[]
  loaded?: ModelResident[]
  vram_gb?: number
  error?: string
}
export const modelResidencyApi = {
  status() { return request<ModelStatus>('/api/model_status') },
  /** off=立即卸载全部驻留模型（释放显存，下次对话自动重载）；on=预加载当前配置所需模型 */
  power(action: 'off' | 'on') {
    return postJson<ModelPowerResult>('/api/model_power', { action })
  },
}

/** P3：分区状态（GET /api/regions → regions.list_regions） */
export interface RegionInfo {
  key: string
  name: string
  dir: string
  desc: string
  access: string
  depends_on: string[]
  exports: string[]
  /** 目录已存在但缺失的导出文件（空=契约完整或目录尚未创建） */
  missing_exports: string[]
  verify: string
  exists: boolean
  /** 该分区目录是否为独立 git 仓库 */
  git: boolean
  branch: string
  dirty: boolean
  files: number
}
export interface RegionsResp {
  code_root: string
  regions: RegionInfo[]
}
export interface ContractsResp {
  ok: boolean
  errors: string[]
  /** key → 依赖的 key 列表（DAG） */
  graph: Record<string, string[]>
}

export const regionsApi = {
  list(): Promise<RegionsResp> {
    return request<RegionsResp>('/api/regions')
  },
  /**
   * 注意：契约校验失败是正常业务结果（HTTP 200 + {ok:false,errors}），
   * 不能走通用 request（它会把 body.ok===false 当错误抛出）。
   */
  async contracts(): Promise<ContractsResp> {
    let res: Response
    try {
      res = await fetch('/api/verify_contracts', withProject())
    } catch {
      throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
    }
    let body: ContractsResp | null = null
    try {
      body = (await res.json()) as ContractsResp
    } catch {
      /* 非 JSON */
    }
    if (!res.ok) {
      throw new FsApiError(res.status, body?.errors?.join('；') || `请求失败（HTTP ${res.status}）`)
    }
    return {
      ok: !!body?.ok,
      errors: body?.errors || [],
      graph: body?.graph || {},
    }
  },
  /** 一键创建缺失分区（目录 + 导出接口桩/README） */
  createRegion(key: string): Promise<CreateRegionResp> {
    return postJson<CreateRegionResp>('/api/regions/create', { key })
  },
  /** 为已存在但缺导出文件的分区补齐导出桩 */
  fillExports(key: string): Promise<FillExportsResp> {
    return postJson<FillExportsResp>('/api/regions/fill_exports', { key })
  },
}

/** POST /api/regions/create 响应：创建结果 + 刷新后的全量分区状态 */
export interface CreateRegionResp {
  ok: boolean
  key: string
  name: string
  dir: string
  created_dir: boolean
  created_files: string[]
  git_warning: string
  regions: RegionInfo[]
}

/** POST /api/regions/fill_exports 响应 */
export interface FillExportsResp {
  ok: boolean
  key: string
  name: string
  dir: string
  created_files: string[]
  regions: RegionInfo[]
}

// ---------------------------------------------------------------- P2：选区 AI（SSE 流式）
export interface SseEvent {
  type:
    | 'token' | 'thought' | 'action' | 'observation' | 'reflection'
    | 'reasoning' | 'notice' | 'plan' | 'route' | 'final' | 'done'
    | 'context' | string
  text?: string
  steps?: string[]
  /** type=context 时的上下文窗口用量 */
  used_tokens?: number
  context_window?: number
  prompt_budget?: number
  percent?: number
  level?: 'ok' | 'high' | 'warn' | string
  /** 会话历史 token（与压缩触发口径同源） */
  history_tokens?: number
  compact_trigger_tokens?: number
  compact_percent?: number
}

/** 上下文窗口占用（GET /api/context 与 SSE context 事件同构） */
export interface ContextUsage {
  used_tokens: number
  context_window: number
  prompt_budget: number
  percent: number
  level: 'ok' | 'high' | 'warn'
  /** 会话历史 token 占用（压缩口径，可选：旧后端可能不下发） */
  history_tokens?: number
  /** 压缩触发线（= prompt_budget × COMPACT_TRIGGER_RATIO） */
  compact_trigger_tokens?: number
  /** 历史 token / 压缩触发线（0-100）：level 即由此分级 */
  compact_percent?: number
}

export interface SseStreamHandlers {
  onEvent: (ev: SseEvent) => void
  signal?: AbortSignal
}

export interface SelectionAiRequest {
  path: string
  lang: string
  start_line: number
  end_line: number
  selection: string
  /** 改写指令（rewrite 快通道）；解释/Review/提问走 agent 不用此结构 */
  instruction?: string
  file_context?: string
  task_id?: string
  task_region?: string
  allowed_paths?: string[]
  verification?: string[]
  engine?: string
}

/**
 * 以 POST 发起 SSE：预检错误（400/409/413 等非 event-stream 响应）读成 JSON 抛 FsApiError；
 * 流开始后的错误由 final 事件承载。AbortError 原样上抛，由调用方区分"用户停止"。
 */
async function postSse(url: string, init: RequestInit, h: SseStreamHandlers): Promise<void> {
  let res: Response
  try {
    res = await fetch(url, withProject({ ...init, signal: h.signal }))
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
  }
  const ctype = res.headers.get('content-type') || ''
  if (!res.ok || !ctype.includes('text/event-stream')) {
    let body: { error?: string } | null = null
    try {
      body = await res.json()
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new FsApiError(res.status, body?.error || `请求失败（HTTP ${res.status}）`, (body as Record<string, unknown>) || {})
  }
  if (!res.body) throw new FsApiError(0, '服务未返回数据流。')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const raw of chunk.split('\n')) {
        const line = raw.trimStart()
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (!payload || payload === '[DONE]') continue
        try {
          h.onEvent(JSON.parse(payload) as SseEvent)
        } catch {
          /* 忽略半条/非 JSON 心跳 */
        }
      }
    }
  }
}

/**
 * 以 GET 订阅 SSE（工作流实时事件用）：预检错误读成 JSON 抛 FsApiError；
 * AbortError 原样上抛，由调用方区分"主动退订/终态自关"与真实故障。
 */
async function openSse(
  url: string,
  signal: AbortSignal,
  onEvent: (ev: SseEvent) => void,
): Promise<void> {
  let res: Response
  try {
    res = await fetch(url, withProject({
      method: 'GET',
      headers: { Accept: 'text/event-stream' },
      signal,
    }))
  } catch (e) {
    if ((e as Error).name === 'AbortError') throw e
    throw new FsApiError(0, '无法连接本地服务（127.0.0.1:8000），请确认 DocMind 已启动。')
  }
  const ctype = res.headers.get('content-type') || ''
  if (!res.ok || !ctype.includes('text/event-stream')) {
    let body: { error?: string } | null = null
    try {
      body = await res.json()
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new FsApiError(res.status, body?.error || `请求失败（HTTP ${res.status}）`, (body as Record<string, unknown>) || {})
  }
  if (!res.body) throw new FsApiError(0, '服务未返回数据流。')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      for (const raw of chunk.split('\n')) {
        const line = raw.trimStart()
        if (!line.startsWith('data:')) continue
        const payload = line.slice(5).trim()
        if (!payload || payload === '[DONE]') continue
        try {
          onEvent(JSON.parse(payload) as SseEvent)
        } catch {
          /* 忽略半条/非 JSON 心跳 */
        }
      }
    }
  }
}

// ---------------------------------------------------------------- 会话 id
// Shared browser lock is acquired before either page mounts (public/session.js).
interface BrowserSession {
  ready: Promise<string>
  get(): string
  set(id: string): Promise<boolean>
  create(): Promise<string>
}
declare global { interface Window { DocMindSession: BrowserSession } }
export function getSessionId(): string { return window.DocMindSession.get() }
export function setSessionId(id: string): Promise<boolean> { return window.DocMindSession.set(id) }
export function startNewSession(): Promise<string> { return window.DocMindSession.create() }
function newSessionId(): string {
  const c = globalThis.crypto as Crypto | undefined
  const raw = c && typeof c.randomUUID === 'function'
    ? c.randomUUID().replace(/-/g, '')
    : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)
  return 'web-' + raw.replace(/[^0-9a-fA-F]/g, '').slice(0, 24).padEnd(24, '0')
}

// ---------------------------------------------------------------- 标签页存活探测
// 目的：判断「本标签页是否为当前唯一打开的 DocMind 标签」。该判断仅供「关掉重开自动续上
// 最近对话」这个仅在单窗口下才安全的兜底使用——多标签时各自独立、互不串（见 ChatDock.restoreHistory）。
// 首选 Web Locks（最可靠，见下）；不可用时退回 BroadcastChannel 心跳 → localStorage 时间戳租约；
// 三者都不可用 → 「不确定」，一律按「非唯一」处理（隔离优先）。
//
// 为什么首选 Web Locks：BroadcastChannel 心跳与 localStorage 租约都依赖**定时器**，而浏览器会把
// 后台标签的定时器节流到 ≥1 分钟——别的标签在后台时，新标签在 900ms 宽限内收不到它心跳、其租约
// 条目也会在 10s 后过期，于是**误判唯一 → 自动接到旧会话 → 两个标签共用一段对话**（正是 P0 要消除
// 的现象）。Web Locks 在文档销毁/标签关闭时才释放，**不受后台节流影响**，且 query() 是权威的、
// 立即可得；本地 http://127.0.0.1 属安全上下文，WebView2/Edge 均支持。
const TAB_CHANNEL_NAME = 'docmind-tabs'
const TAB_LEASE_KEY = 'docmind_tab_lease'
const TAB_LOCK_PREFIX = 'docmind-tab-'   // 每标签一把同名前缀的 Web Lock
const TAB_STALE_MS = 10000      // 超过此时长没收到心跳即视为该标签已离开
const TAB_HEARTBEAT_MS = 3000   // 心跳间隔
const TAB_SETTLE_MS = 900       // 心跳/租约模式的探测宽限期：给其它标签回心跳留出时间

interface TabMessage { t: 'hb' | 'hello'; id: string; ts: number }

/** Web Locks 的最小类型（不依赖 lib.dom 是否带 LockManager，兼容不同 TS 版本）。 */
interface TabLockManager {
  request(name: string, cb: (lock: unknown) => Promise<unknown>): Promise<unknown>
  query(): Promise<{ held: { name: string }[] }>
}

const docmindTabs = (() => {
  const tabId = newSessionId() + '-tab'   // 本标签标识（非会话 id，不参与落盘）
  const peers = new Map<string, number>() // 其它标签 id -> 最近一次心跳时刻
  let channel: BroadcastChannel | null = null
  let timer: number | null = null
  let mode: 'locks' | 'channel' | 'lease' | 'none' = 'none'
  let lockSole = false        // locks 模式下的同步缓存（async 探测结果），初值 false
  let started = false

  function post(kind: TabMessage['t']): void {
    if (!channel) return
    const msg: TabMessage = { t: kind, id: tabId, ts: Date.now() }
    try { channel.postMessage(msg) } catch { /* 通道已关闭：忽略 */ }
  }

  function prunePeers(): void {
    const now = Date.now()
    for (const [id, ts] of peers) {
      if (now - ts >= TAB_STALE_MS) peers.delete(id)
    }
  }

  /** 读 localStorage 租约：剔除过期条目后返回「其它标签」条数；不可读返回 -1（不确定）。 */
  function leaseOthers(): number {
    try {
      const raw = localStorage.getItem(TAB_LEASE_KEY)
      if (!raw) return 0
      const obj = JSON.parse(raw) as Record<string, unknown>
      if (!obj || typeof obj !== 'object') return 0
      const now = Date.now()
      let others = 0
      for (const k of Object.keys(obj)) {
        if (k === tabId) continue
        const v = Number(obj[k])
        if (Number.isFinite(v) && now - v < TAB_STALE_MS) others++
      }
      return others
    } catch {
      return -1
    }
  }

  /** 写租约：剔除过期条目后写入本标签时间戳（存储不可用则静默跳过）。 */
  function writeLease(): void {
    try {
      const raw = localStorage.getItem(TAB_LEASE_KEY)
      let obj: Record<string, number> = {}
      if (raw) {
        try {
          const j = JSON.parse(raw) as Record<string, unknown>
          if (j && typeof j === 'object') {
            const now = Date.now()
            for (const k of Object.keys(j)) {
              const v = Number(j[k])
              if (Number.isFinite(v) && now - v < TAB_STALE_MS) obj[k] = v
            }
          }
        } catch { obj = {} }
      }
      obj[tabId] = Date.now()
      localStorage.setItem(TAB_LEASE_KEY, JSON.stringify(obj))
    } catch { /* 存储不可用：租约机制禁用 */ }
  }

  function start(): void {
    if (started) return
    started = true
    // 首选 Web Locks：长期持有一把以本标签命名的锁（回调永不 resolve → 持有到标签关闭/文档销毁，
    // 届时浏览器自动释放）。不受后台节流影响，query() 能权威回答"是否有别的标签"。
    const locks = typeof navigator !== 'undefined'
      ? (navigator as unknown as { locks?: TabLockManager }).locks
      : undefined
    if (locks && typeof locks.request === 'function') {
      try {
        locks.request(TAB_LOCK_PREFIX + tabId, () => new Promise<never>(() => { /* 永不 resolve：长期持有 */ }))
          .catch(() => { /* 申请被拒：忽略，探测会保守返回"非唯一" */ })
        mode = 'locks'
        return
      } catch { /* 申请同步抛错：掉落到 BroadcastChannel */ }
    }
    // 次选 BroadcastChannel 心跳（真正跨标签、无残留）
    const BC = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    if (typeof BC === 'function') {
      try {
        channel = new BC(TAB_CHANNEL_NAME)
        channel.onmessage = (ev: MessageEvent) => {
          const m = ev.data as Partial<TabMessage> | null
          if (!m || typeof m.id !== 'string' || m.id === tabId) return
          peers.set(m.id, Date.now())
          if (m.t === 'hello') post('hb')   // 有新标签加入：立刻回心跳，让它尽快感知到我
        }
        mode = 'channel'
      } catch {
        channel = null
      }
    }
    if (mode === 'channel') {
      post('hello')   // 宣告自己加入，已存在的标签会立刻回心跳
      timer = window.setInterval(() => { post('hb'); prunePeers() }, TAB_HEARTBEAT_MS)
    } else {
      // 退回 localStorage 时间戳租约：能写才算可用
      try {
        localStorage.setItem(TAB_LEASE_KEY + ':probe', '1')
        localStorage.removeItem(TAB_LEASE_KEY + ':probe')
        mode = 'lease'
        writeLease()
        timer = window.setInterval(writeLease, TAB_HEARTBEAT_MS)
      } catch {
        mode = 'none'
      }
    }
    if (timer !== null && typeof window !== 'undefined') {
      // 标签页卸载时停掉心跳（不影响正确性，只是卫生）
      window.addEventListener('pagehide', () => { if (timer !== null) window.clearInterval(timer) })
    }
  }

  /** 权威查锁：held 里存在「名字以 TAB_LOCK_PREFIX 开头且不是自己那把」的锁 → 非唯一。 */
  async function locksSole(): Promise<boolean> {
    const locks = typeof navigator !== 'undefined'
      ? (navigator as unknown as { locks?: TabLockManager }).locks
      : undefined
    if (!locks || typeof locks.query !== 'function') return false   // 无 query：不确定 → 非唯一
    try {
      const snap = await locks.query()
      const held = (snap && Array.isArray(snap.held)) ? snap.held : []
      const mine = TAB_LOCK_PREFIX + tabId
      for (const l of held) {
        if (l && typeof l.name === 'string' && l.name.startsWith(TAB_LOCK_PREFIX) && l.name !== mine) {
          return false   // 有别的标签仍持有锁 → 非唯一
        }
      }
      return true        // 只有自己那把 → 唯一
    } catch {
      return false       // 查询异常 → 不确定 → 非唯一（隔离优先）
    }
  }

  /** 同步判断「本标签是否唯一」（locks 模式返回最近一次 async 探测的缓存，初值 false）。 */
  function soleSync(): boolean {
    start()
    if (mode === 'locks') return lockSole
    if (mode === 'channel') { prunePeers(); return peers.size === 0 }
    if (mode === 'lease') { return leaseOthers() === 0 }
    return false   // 探测不可用 → 不确定 → 按非唯一处理（隔离优先）
  }

  /** 异步判断「本标签是否唯一」：locks 模式查锁（权威、立即）；其余模式同同步逻辑。 */
  async function probe(): Promise<boolean> {
    start()
    if (mode === 'locks') {
      lockSole = await locksSole()
      return lockSole
    }
    return soleSync()
  }

  return { start, soleSync, probe, isLockMode: () => { start(); return mode === 'locks' } }
})()

/** 启动标签页存活探测（幂等）。应用启动时调用一次即可开始心跳与应答。 */
export function startTabProbe(): void {
  docmindTabs.start()
}

/** 本标签页是否为当前唯一打开的 DocMind 标签（**同步**）。
 *  locks 模式：返回最近一次 probeSoleDocMindTab() 的缓存结果（尚未探测过时为 false）；
 *  channel/lease 模式：即时判定；none/不确定：false（隔离优先）。 */
export function isSoleDocMindTab(): boolean {
  return docmindTabs.soleSync()
}

let tabSettled: Promise<void> | null = null
/** **异步**判断唯一性（ChatDock 用它做自动续接的门禁）。
 *  locks 模式：直接查 Web Locks，**立即返回**（权威信号，无需宽限期）；
 *  channel/lease 模式：先等心跳「稳定」再判定（新标签刚打开时其它标签可能还没回心跳），
 *  仅首次真正等待宽限期，之后立即返回；探测不可用 → false（不自动续接）。 */
export async function probeSoleDocMindTab(settleMs = TAB_SETTLE_MS): Promise<boolean> {
  startTabProbe()
  if (docmindTabs.isLockMode()) return docmindTabs.probe()   // 权威：无需等待
  if (!tabSettled) {
    tabSettled = new Promise<void>((resolve) => { window.setTimeout(resolve, settleMs) })
  }
  await tabSettled
  return docmindTabs.probe()
}

export const aiApi = {
  /** 解释 / Review / 自由提问：走 ReAct agent（可 search_code/read_file/grep，引用文件行号）。
   *  web/thinking：逐请求的联网搜索 / 深度思考开关（后端默认均为关/模型默认）。
   *  session_id：当前标签页会话，用于隔离并发请求的逐请求开关。 */
  askGrounded(
    question: string,
    h: SseStreamHandlers,
    opts: { web?: boolean; thinking?: boolean | null; images?: Blob[] } = {},
  ): Promise<void> {
    const fd = new FormData()
    fd.append('question', question)
    fd.append('session_id', getSessionId())
    fd.append('web_mode', opts.web ? '1' : '0')
    if (opts.thinking === true) fd.append('thinking_mode', '1')
    else if (opts.thinking === false) fd.append('thinking_mode', '0')
    // 多模态：附带图片（后端按 filename 扩展名 + 文件头魔数双重校验）
    for (const img of opts.images || []) {
      fd.append('images', img, (img as File).name || 'image.png')
    }
    return postSse('/api/chat', { method: 'POST', body: fd }, h)
  },
  /** 改写：直连 LLM 快通道，强约束只产出可直接替换的纯代码。 */
  rewriteSelection(req: SelectionAiRequest, h: SseStreamHandlers): Promise<void> {
    return postSse(
      '/api/selection_ai',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...req, mode: 'rewrite' }),
      },
      h,
    )
  },
}

// ---------------------------------------------------------------- 模型设置（/api/config）
export interface ProviderMeta {
  label: string
  base_url: string
  default_model: string
  needs_key: boolean
  cloud: boolean
}

export interface ModelCapability {
  context_window: number
  /** native=模型天生推理 / toggle=可开关 / none=不支持 / unknown=待确认 */
  thinking: 'native' | 'toggle' | 'none' | 'unknown' | string
  /** native=原生图片 / harness=Harness 视觉辅助 / none=不支持 / unknown=待确认 */
  vision: 'native' | 'harness' | 'none' | 'unknown' | string
  /** native=原生视频 / frames=Harness 抽帧 / none=不支持 / unknown=待确认 */
  video: 'native' | 'frames' | 'none' | 'unknown' | string
  cloud: boolean
}

export const visionApi = {
  analyzeVideo(file: File): Promise<{ ok: boolean; context?: string[]; info?: { frame_count?: number }; error?: string }> {
    const fd = new FormData()
    fd.append('file', file, file.name || 'clip.mp4')
    return fetch('/api/vision/video', withProject({ method: 'POST', body: fd }))
      .then(async (res) => (await res.json()) as { ok: boolean; context?: string[]; info?: { frame_count?: number }; error?: string })
  },
}

export interface OllamaStatus {
  reachable: boolean
  needed_models?: string[]
  present_models?: string[]
  missing_models?: string[]
  guidance?: string
}

export interface ModelConfigInfo {
  ok?: boolean
  llm_provider: string
  llm_model: string
  embedding_provider: string
  has_key: boolean
  providers: string[]
  provider_meta: Record<string, ProviderMeta>
  custom_base_url: string
  capability: ModelCapability
  ollama_status?: OllamaStatus
  /** 用户手填的窗口覆盖；0/缺省 = 自动（实时探测或内置画像） */
  context_window_override?: number
  capability_override?: { thinking?: string; vision?: string; video?: string }
  /** 当前生效窗口来源：custom 手填 / probe Ollama 实时探测 / profile 内置画像 */
  context_source?: 'custom' | 'probe' | 'profile'
  /** AI 越界访问模式：safe=仅限项目内；high=允许受控越界读写（须配置白名单） */
  external_access_mode?: 'safe' | 'high'
  // 以下字段问答页（/）使用，后端 /api/config 始终返回；工作台类型里保留为可选
  /** 已入库资料文档文件名列表（/api/ingest 上传的 PDF/MD/TXT） */
  ingested_files?: string[]
  /** 当前代码库切片条数 */
  code_sources?: number
  /** 当前代码库根目录 */
  code_root?: string
  /** AI 改文件前是否需要人工确认 */
  edit_confirm?: boolean
}

export interface SaveModelReq {
  provider: string
  model?: string
  api_key?: string
  base_url?: string
  /** >0 设置窗口覆盖；0 清除覆盖回到自动；undefined=不改动 */
  context_window?: number | null
  thinking_capability?: string
  vision_capability?: string
  video_capability?: string
  /** AI 越界访问模式：'safe' | 'high'；undefined=不改动 */
  external_access_mode?: 'safe' | 'high'
}

/** 联网识别出的候选窗口（附带出处片段，由用户判断后采用） */
export interface ContextCandidate {
  tokens: number
  evidence: string
}

export interface ContextLookupResult {
  ok: boolean
  error?: string
  query?: string
  best?: number
  candidates?: ContextCandidate[]
}

export interface OllamaProbeResult {
  ok: boolean
  error?: string
  model?: string
  context_window?: number
}

export const modelApi = {
  get(): Promise<ModelConfigInfo> {
    return request('/api/config')
  },
  save(req: SaveModelReq): Promise<ModelConfigInfo & { warnings?: string[]; model_error?: string; error?: string }> {
    // 后端配置保存失败是 HTTP 200 + {ok:false, model_error?}：必须走 rawJson 原样返回失败体，
    // 否则通用 request 会把真实原因吞成「请求失败（HTTP 200）」，model_error 分支永不触发。
    return rawJson<ModelConfigInfo & { warnings?: string[]; model_error?: string; error?: string }>('/api/config', req)
  },
  /** 实时探测本机已安装 Ollama 模型的真实上下文窗口 */
  probeOllama(model: string): Promise<OllamaProbeResult> {
    return rawJson<OllamaProbeResult>(`/api/ollama/probe?model=${encodeURIComponent(model)}`)
  },
  /** 联网搜索模型公开的上下文窗口（返回候选列表，不自动写配置） */
  lookupModelContext(provider: string, model: string): Promise<ContextLookupResult> {
    return rawJson<ContextLookupResult>('/api/model_context_lookup', { provider, model })
  },
}

// ---------------------------------------------------------------- 问答页：资料摄取 / 提示词增强
export interface IngestResult {
  ok: boolean
  chunks?: number
  error?: string
}

export interface EnhancePromptResult {
  ok: boolean
  enhanced?: string
  /** llm=模型重写；local=本地规则兜底 */
  mode?: 'llm' | 'local' | string
  note?: string
  error?: string
}

export const kbApi = {
  /** 上传 PDF/MD/TXT 资料文档入向量库（multipart，字段名 file）。 */
  ingest(file: File): Promise<IngestResult> {
    const fd = new FormData()
    fd.append('file', file, file.name)
    return fetch('/api/ingest', withProject({ method: 'POST', body: fd }))
      .then(async (res) => {
        const body = (await res.json().catch(() => null)) as IngestResult | null
        if (!res.ok && body === null) throw new FsApiError(res.status, `请求失败（HTTP ${res.status}）`)
        return body as IngestResult
      })
  },
}

export const promptApi = {
  /** 把草稿重写为更清晰的提问；无可用模型时后端本地规则兜底（mode=local）。 */
  enhance(prompt: string): Promise<EnhancePromptResult> {
    return rawJson<EnhancePromptResult>('/api/enhance_prompt', { prompt })
  },
}

/** 上下文窗口用量查询（页面刷新后恢复指示用）。session_id 为 query 参数，
 *  与 /api/chat 的 form 字段对应，保证读到的是当前标签页会话的用量。 */
export const contextApi = {
  async get(): Promise<ContextUsage | null> {
    const r = await request<{ ok: boolean; active?: boolean } & Partial<ContextUsage>>(
      `/api/context?session_id=${encodeURIComponent(getSessionId())}`,
    )
    if (!r.active) return null
    return {
      used_tokens: r.used_tokens ?? 0,
      context_window: r.context_window ?? 0,
      prompt_budget: r.prompt_budget ?? 0,
      percent: r.percent ?? 0,
      level: (r.level as ContextUsage['level']) ?? 'ok',
      history_tokens: r.history_tokens,
      compact_trigger_tokens: r.compact_trigger_tokens,
      compact_percent: r.compact_percent,
    }
  },
}

// ---------------------------------------------------------------- P0：MCP 引擎桥 + Godot 插件
export interface McpServer {
  key: string
  label?: string
  engine?: string
  transport: 'stdio' | 'http'
  command?: string
  args?: string[]
  url?: string
  enabled: boolean
  help?: string
  config_error?: string
}

export interface McpToolInfo {
  name: string
  description: string
  input_schema: Record<string, unknown>
}

export interface McpCapabilityCandidate {
  id: string
  server: string
  domain: string
  capabilities: string[]
  keywords: string[]
  best_for: string
  tool_mappings: Array<{ tool: string; capabilities: string[] }>
  ui_requirements: Array<{ tool: string; kind: string; fields: string[]; renderer: string }>
  sources: string[]
  confidence: number
  tool_count: number
  status: 'pending' | 'active' | 'rejected'
}

export interface McpDirectoryResult {
  id: string
  label: string
  summary: string
  capabilities: string[]
  connection_options: Array<{
    id?: string
    label?: string
    transport: 'stdio' | 'http'
    when: string
    value: string
    config?: Partial<McpAutoConnectConfig>
    trust_tier?: McpAutoConnectCandidate['trust_tier']
    secrets?: McpAutoConnectCandidate['secrets']
    register_provider?: string
  }>
  setup_steps: string[]
  template: { key: string; label: string; transport: 'stdio' | 'http'; command: string; args: string[]; url: string }
  sources: string[]
  source_status: 'web_sources' | 'offline_guide' | 'registry'
}

// P1：MCP 自动连接向导（设置 → MCP 链路重做）
export interface McpProvenance {
  url: string
  domain: string
  /** registry server 名 / 精选索引 name，前端卡片标题优先用 */
  server_name?: string
  /** registry 命名空间，用于信任分档（官方命名空间判定） */
  namespace?: string
  /** 标记来自官方 Registry */
  registry?: boolean
  /** 标记来自离线精选索引 */
  curated?: boolean
  note?: string
}
export interface McpAutoConnectConfig {
  transport: 'stdio' | 'http'
  command: string
  args: string[]
  url: string
  env: Record<string, string>
  headers: Record<string, string>
  provenance: McpProvenance
  command_unresolved: boolean
}
export interface McpAutoConnectCandidate {
  config: McpAutoConnectConfig
  trust: 'trusted' | 'source_untrusted'
  /** 候选引用的凭证；required=false 仅作可选预填，不阻塞提交。 */
  secrets?: { name: string; required: boolean }[]
  /** 显示用信任分档：official（官方发布）/ community（受信域第三方）/ unknown（来源不可信）。R8 自动填参闸门仍看 trust。 */
  trust_tier?: 'official' | 'community' | 'unknown'
  validation_errors: string[]
}
export interface McpAutoConnectSearchRes {
  ok: boolean
  candidates: McpAutoConnectCandidate[]
  search_error?: string
  error?: string
}
export interface McpProbeRes {
  ok: boolean
  probe_ok: boolean
  tools?: string[]
  error?: string
  ready?: boolean
  readiness_message?: string
}
/** L0/L1/L2 档位：L0=全自动填表取凭证；L1=遇验证码/2FA 暂停、用户点继续；L2=仅人工回填。 */
export type McpRegisterTier = 'L0' | 'L1' | 'L2'
/** 注册会话状态：running=自动进行中；waiting_user=等用户人工完成一步；done=成功；failed=失败。 */
export type McpRegisterState = 'running' | 'waiting_user' | 'done' | 'failed'

export interface McpRegisterStartRes {
  ok: boolean
  task_id?: string
  tier: McpRegisterTier
  url?: string
  note?: string
  error?: string
}
/** GET register/status 展平后的会话字典（后端 autoconnect_sessions.json 的一条）。 */
export interface McpRegisterStatusRes {
  ok: boolean
  task_id?: string
  tier?: McpRegisterTier
  status?: McpRegisterState
  url?: string
  context_dir?: string
  resume_token?: string
  created_at?: number
  /** 本次代管的 provider（与 secrets_store / commit 的 stored 同名）。 */
  provider?: string
  /** 给用户看的一步提示（如「请在浏览器里完成验证码后点继续」）。L1 展示。 */
  user_prompt?: string
  /** 当前自动步子（challenge/submit/untrusted/fill/capture/opening/filling/submitting/capturing）。 */
  step?: string
  error?: string
}
export interface McpRegisterResumeRes {
  ok: boolean
  task_id?: string
  tier?: McpRegisterTier
  status?: McpRegisterState
  url?: string
  user_prompt?: string
  step?: string
  /** 业务态说明（如「没有可续跑的会话」），与传输失败区分。 */
  error?: string
}
export interface McpRegisterCommitRes {
  ok: boolean
  stored?: string[]
  error?: string
}

export interface GodotAddonStatus {
  ok: boolean
  is_godot_project: boolean
  installed: boolean
  version: string
  enabled: boolean
  uvx: string
  uvx_available: boolean
  godot: string
  godot_available: boolean
  min_version: string
}

export const mcpApi = {
  servers(): Promise<{ ok: boolean; servers: McpServer[] }> {
    return request('/api/mcp/servers')
  },
  probe(key: string): Promise<{ ok: boolean; tool_count?: number; tools?: string[]; error?: string }> {
    return postJson('/api/mcp/probe', { key })
  },
  capabilities(): Promise<{ ok: boolean; active: Record<string, McpCapabilityCandidate>; pending: Record<string, McpCapabilityCandidate>; error?: string }> {
    return request('/api/mcp/capabilities')
  },
  searchCatalog(query: string, webEnabled = true): Promise<{ ok: boolean; results: McpDirectoryResult[]; search_error?: string; error?: string }> {
    return postJson('/api/mcp/catalog/search', { query, web_enabled: webEnabled })
  },
  discover(key: string, webEnabled = false): Promise<{ ok: boolean; candidate?: McpCapabilityCandidate; error?: string }> {
    return postJson('/api/mcp/discover', { key, web_enabled: webEnabled })
  },
  decideCapability(key: string, approved: boolean): Promise<{ ok: boolean; approved?: boolean; capability?: McpCapabilityCandidate; error?: string }> {
    return postJson('/api/mcp/capabilities/decision', { key, approved })
  },
  tools(key: string): Promise<{ ok: boolean; tools: McpToolInfo[]; count?: number; error?: string }> {
    return request(`/api/mcp/tools?key=${encodeURIComponent(key)}`)
  },
  call(key: string, name: string, args: Record<string, unknown>):
      Promise<{ ok: boolean; text?: string; is_error?: boolean; error?: string }> {
    return postJson('/api/mcp/call', { key, name, arguments: args })
  },
  save(key: string, config: Record<string, unknown>):
      Promise<{ ok: boolean; servers?: McpServer[]; error?: string }> {
    return postJson('/api/mcp/servers', { key, config })
  },
  remove(key: string): Promise<{ ok: boolean; servers?: McpServer[]; error?: string }> {
    return postJson('/api/mcp/servers/remove', { key })
  },
  close(key: string): Promise<{ ok: boolean; closed?: boolean; error?: string }> {
    return postJson('/api/mcp/close', { key })
  },
  status(): Promise<{ ok: boolean; active?: string[]; error?: string }> {
    return request('/api/mcp/status')
  },
  // ---- P1：MCP 自动连接 ----
  // search / probe / vision 的「没找到候选」属于业务数据（ok:false + 可选 error），
  // 不是传输失败。若走会抛错的 request 通道，HTTP 200 会被误报成「请求失败（HTTP 200）」，
  // 所以这里统一走 rawJson（不抛业务错，只有网络不可达/非 JSON 才抛）。
  async autoConnectSearch(query: string, webEnabled = true, timeoutMs = 60000): Promise<McpAutoConnectSearchRes> {
    // 联网搜索+多页抓取可能较慢；加硬超时，避免后端卡住时向导无限转圈。
    const ctrl = new AbortController()
    const timer = window.setTimeout(() => ctrl.abort(), timeoutMs)
    try {
      return await rawJson('/api/mcp/autoconnect/search', { query, web_enabled: webEnabled }, ctrl.signal)
    } catch (e) {
      if (ctrl.signal.aborted) {
        return { ok: false, candidates: [], search_error: `联网搜索超时（>${Math.round(timeoutMs / 1000)}s），请关闭“允许联网”后重试或手动添加。` }
      }
      throw e
    } finally {
      window.clearTimeout(timer)
    }
  },
  probeCandidate(config: McpAutoConnectConfig): Promise<McpProbeRes> {
    return rawJson('/api/mcp/autoconnect/probe', { config })
  },
  confirmConnect(key: string, config: McpAutoConnectConfig):
      Promise<{ ok: boolean; servers?: McpServer[]; error?: string }> {
    return postJson('/api/mcp/autoconnect/confirm', { key, config, user_ack: true })
  },
  registerStart(key: string, config: McpAutoConnectConfig, provider: string): Promise<McpRegisterStartRes> {
    return postJson('/api/mcp/autoconnect/register/start', { key, config, provider })
  },
  registerStatus(taskId: string): Promise<McpRegisterStatusRes> {
    return request(`/api/mcp/autoconnect/register/status?task_id=${encodeURIComponent(taskId)}`)
  },
  /**
   * L1「我已完成，继续」：让后端在同一持久会话上续跑（撞到下一个挑战会再次 waiting_user）。
   *
   * 走 rawJson：响应里的 `status`（waiting_user/done/failed）与 `user_prompt` 是**业务态**，
   * 即便后端以 `ok:false` 返回（例如「没有可续跑的 live 会话」）也要读出来渲染，
   * 不能被通用 request 当成传输失败抛掉（与上面 autoConnectSearch/probe 同一通道约定）。
   */
  registerResume(taskId: string): Promise<McpRegisterResumeRes> {
    return rawJson('/api/mcp/autoconnect/register/resume', { task_id: taskId })
  },
  registerCommit(taskId: string, credentials: Record<string, string>): Promise<McpRegisterCommitRes> {
    return postJson('/api/mcp/autoconnect/register/commit', { task_id: taskId, credentials })
  },
  visionExtract(imageBase64: string): Promise<McpAutoConnectSearchRes> {
    return rawJson('/api/mcp/autoconnect/vision/extract', { image_base64: imageBase64 })
  },
  addonStatus(): Promise<GodotAddonStatus> {
    return request('/api/engine/addon/status')
  },
  addonInstall(force = false): Promise<{ ok: boolean; version?: string; error?: string; next?: string }> {
    return postJson('/api/engine/addon/install', { confirm: true, force })
  },
}

// ---------------------------------------------------------------- harness 运维层
// 对应后端 agent_trace / sessions / pricing / hooks / skills（纯只读查看 + 预算/热重载操作）。

export interface BudgetStatus {
  global_limit: number
  global_spent: number
  day: string
  day_spent: number
  per_minute_calls_limit: number
  per_minute_cost_limit: number
  minute_calls: number
  minute_cost: number
  sessions: Record<string, { spent: number; limit: number }>
  pricing_file: string
}
export interface BudgetCheck {
  ok: boolean
  reason: string
  global_limit: number
  global_spent: number
  session_limit: number
  session_spent: number
  day_spent: number
  minute_calls: number
  minute_cost: number
  per_minute_calls_limit: number
  per_minute_cost_limit: number
}
export interface TraceStep {
  i: number
  action: string
  arg_chars?: number
  latency_ms?: number
  obs_chars?: number
  ok?: boolean
}
/**
 * trace 记录级 `error` 字段：
 * - 普通 Agent 回合：错误原文（字符串，来源 agent_trace）；
 * - flow 回合（AI 工具流编排器）：按隐私边界（方案 §9.5）已脱敏为元数据对象
 *   `{ error_kind, chars }`，**绝不**含原文 / 文件路径 / 代码。
 */
export type TraceError = string | { error_kind?: string; chars?: number }

/**
 * 统一格式化 trace 的 `error` 字段，供各面板渲染，避免对象被直接渲染成 `[object Object]`。
 * - 字符串 → 原样返回（普通回合的原文错误）；
 * - 对象 → 显示可读中文（flow 回合的脱敏元数据）；
 * - 空 → 返回空串。
 */
export function fmtTraceError(err: TraceError | null | undefined): string {
  if (!err) return ''
  if (typeof err === 'string') return err
  return `已脱敏（错误类型：${err.error_kind || 'unknown'}）`
}

export interface TraceItem {
  turn_id: string
  ts: string
  session_id: string
  provider: string
  model: string
  route: string | null
  question_chars: number
  messages_count: number
  prompt_tokens: number
  completion_tokens: number
  cache_read_tokens?: number
  cache_creation_tokens?: number
  total_tokens: number
  cost_cny: number
  llm_calls: number
  llm_ms: number
  elapsed_ms: number
  outcome: string
  finish_reason?: string | null
  aborted: boolean
  error: TraceError
  final_chars?: number
  steps: TraceStep[]
}
export interface TraceSummary {
  turns: number
  prompt_tokens: number
  completion_tokens: number
  cache_read_tokens?: number
  cache_creation_tokens?: number
  total_tokens: number
  total_cost_cny: number
  avg_elapsed_ms: number
  aborted: number
  errors: number
  by_provider: Record<string, {
    turns: number
    prompt_tokens: number
    completion_tokens: number
    cache_read_tokens?: number
    cache_creation_tokens?: number
    tokens: number
    cost_cny: number
  }>
}
export interface SessionInfo {
  session_id: string
  turns: number
  has_summary: boolean
  updated_at: string
  title?: string
  preview?: string
}
export interface SkillInfo {
  name: string
  description: string
  when_to_use: string
  path: string
  source?: string
  version?: string
  checksum?: string
  history_versions?: Array<{ version?: string; checksum?: string }>
  stats?: { uses?: number; successes?: number; failures?: number; last_score?: number }
}

export const harnessApi = {
  budget(): Promise<{ ok: boolean; status: BudgetStatus; check: BudgetCheck }> {
    return request('/api/budget')
  },
  setBudgetLimit(limitCny: number): Promise<{ ok: boolean; check?: BudgetCheck; error?: string }> {
    return postJson('/api/budget', { limit_cny: limitCny })
  },
  setUsageLimits(limitCny: number, perMinuteCalls: number, perMinuteCost: number): Promise<{
    ok: boolean
    check?: BudgetCheck
    rate?: { per_minute_calls_limit: number; per_minute_cost_limit: number }
    error?: string
  }> {
    return postJson('/api/budget', {
      limit_cny: limitCny,
      per_minute_calls: perMinuteCalls,
      per_minute_cost: perMinuteCost,
    })
  },
  resetBudget(): Promise<{ ok: boolean; check?: BudgetCheck }> {
    return postJson('/api/budget', { reset: true })
  },
  sessions(): Promise<{ ok: boolean; items: SessionInfo[] }> {
    return request('/api/sessions')
  },
  /** 取回某会话的完整问答历史（供刷新/重开后回灌对话）。会话不存在时返回空 turns。 */
  sessionDetail(id: string): Promise<{
    ok: boolean
    session_id: string
    turns: { user: string; assistant: string; ts?: string }[]
    summary?: string
    updated_at?: string
  }> {
    return request(`/api/sessions/${encodeURIComponent(id)}`)
  },
  deleteSession(id: string): Promise<{ ok: boolean }> {
    return fetch(`/api/sessions/${encodeURIComponent(id)}`, withProject({ method: 'DELETE' })).then((r) => r.json())
  },
  trace(limit = 30): Promise<{ ok: boolean; items: TraceItem[]; summary: TraceSummary }> {
    return request(`/api/trace?limit=${limit}`)
  },
  clearTrace(): Promise<{ ok: boolean }> {
    return postJson('/api/trace/clear', {})
  },
  skills(): Promise<{ ok: boolean; skills_dir: string; exists: boolean; count: number; errors: string[]; items: SkillInfo[] }> {
    return request('/api/skills')
  },
  reloadSkills(): Promise<{ ok: boolean; count?: number; errors?: string[] }> {
    return postJson('/api/skills/reload', {})
  },
  rollbackSkill(name: string): Promise<{ ok: boolean; rolled_back?: boolean; restored_previous?: boolean; approval_required?: boolean; error?: string }> {
    return postJson('/api/skills/rollback', { name })
  },
  hooks(): Promise<{ ok: boolean; hooks_dir: string; exists: boolean; counts: Record<string, number>; workflow_counts?: Record<string, number>; workflow_kinds?: string[]; breakpoints?: Record<string, { enabled?: boolean; block?: boolean; match?: string; reason?: string }>; sources: Record<string, unknown>; errors: string[] }> {
    return request('/api/hooks')
  },
  reloadHooks(): Promise<{ ok: boolean; errors?: string[] }> {
    return postJson('/api/hooks/reload', {})
  },
  setHookBreakpoint(payload: { kind: string; enabled?: boolean; block?: boolean; match?: string; reason?: string }): Promise<{ ok: boolean; breakpoint?: Record<string, unknown>; error?: string }> {
    return request('/api/hooks/breakpoints', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
  },
  removeHookBreakpoint(kind: string): Promise<{ ok: boolean; breakpoint?: Record<string, unknown>; error?: string }> {
    return request(`/api/hooks/breakpoints/${encodeURIComponent(kind)}`, { method: 'DELETE' })
  },
}

// ---------------------------------------------------------------- Agent 策略 / 审批（P4 收口）
// 对应后端 /api/agent/*。这些是**项目作用域**请求（审批/授权针对当前项目的外部文件），
// 必须带上 X-DocMind-Project（经 withProject）。统一走 rawJson 通道：不把 ok:false 抛错，
// 与原组件「读失败体字段（reason/recorded/approval）」的语义一致。
export interface AgentRoutingResp { ok?: boolean; auto_cloud_enabled?: boolean }
export interface AgentConnectorInfo { key: string; label: string; enabled: boolean }
export interface AgentConnectorsResp { ok?: boolean; connectors?: AgentConnectorInfo[] }
export interface AgentApproval { id: string; status: string; summary?: string; diff?: string; paths?: string[] }
export interface AgentApprovalsResp { ok?: boolean; approvals?: AgentApproval[] }
export interface CockpitApprovalRequest {
  id: string; action: string; target: string; risk: 'L2' | 'L3'; created_at: string
}
export interface AgentPermissionResp { ok?: boolean; recorded?: boolean; reason?: string }
export interface AgentApprovalCreateResp { ok?: boolean; approval?: AgentApproval; error?: string }

export interface WorkflowOption {
  id: string; title: string; summary: string; recommended?: boolean
  source?: string; requires_web?: boolean
}
export interface WorkflowEvent {
  ts?: string; kind?: string
  /** 持久事件的单调序号；SSE 重连按 after=seq 去重重放 */
  seq?: number
  // subagent_step / subagent_start / subagent_complete 实时轨迹字段
  task_id?: string; role?: string; step_type?: string; text?: string
  task?: string; status?: string; phase?: string; elapsed_ms?: number
  conclusion?: string; error?: string; trace?: WorkflowChildTrace
  /** 部分事件（如 langsmith 导出状态）直接携带 LangSmith 状态体 */
  langsmith?: LangSmithStatus
  [key: string]: unknown
}
export interface WorkflowTraceStep {
  action?: string; obs?: string; ok?: boolean; tool?: string; error?: string
  [key: string]: unknown
}
/** 子代理单条实时轨迹（step_sink → subagent_step SSE 事件），文本后端已按类型裁剪。 */
export interface WorkflowStepItem {
  type: 'thought' | 'action' | 'observation' | string
  text: string
}
/** 子代理完成时回传的有界轨迹（subagent_complete.trace / state.results[].trace）。 */
export interface WorkflowChildTrace {
  steps?: WorkflowTraceStep[]
  n_steps?: number
  thoughts?: string[]
  reflections?: string[]
  tokens?: { in?: number; out?: number; total?: number; [k: string]: unknown }
  elapsed_ms?: number
  cost?: number
  [key: string]: unknown
}
export interface WorkflowBackendStatus {
  ok?: boolean
  backend?: string
  langgraph_installed?: boolean
  checkpoint_backend?: string
  checkpoint_error?: string
  persistent_checkpoint?: boolean
  distributed_leases?: boolean
  checkpoint_health?: {
    backend?: string
    configured?: boolean
    required?: boolean
    healthy?: boolean
    state?: string
    schema_version?: number | null
    error?: string
  }
}
export interface LangSmithStatus {
  ok?: boolean
  installed?: boolean
  key_configured?: boolean
  enabled?: boolean
  realtime?: boolean
  project?: string
  endpoint?: string
  pending_exports?: number
  recent_export_errors?: Array<{ name?: string; error?: string }>
}
export interface WorkflowEvaluation {
  workflow_id?: string
  passed?: boolean
  score?: number
  checks?: Array<{ name?: string; ok?: boolean; detail?: string }>
  task_count?: number
  steps?: number
  replans?: number
  metrics?: Record<string, number | boolean>
}
export interface AcceptanceItem {
  id: string; statement: string; method: string; evidence: string[]
  required: boolean; user_approved: boolean
}
export interface AcceptanceContract {
  revision: number; approved_revision: number; items: AcceptanceItem[]
  final_decision: 'pending' | 'accepted' | 'rejected'; final_note: string
}
export interface WorkflowPreviewArtifact {
  id: string; kind: string; label: string; renderer?: string; adapter?: string; path?: string; uri?: string; mime?: string
  status?: string; summary?: string; before?: string; after?: string
  evidence?: string[]; metadata?: Record<string, string>
  change_summary?: { before_lines?: number; after_lines?: number; added_lines?: number; removed_lines?: number }
}
export interface WorkflowPreview {
  schema?: string; workflow_id?: string; generated_at?: string; status?: string
  summary?: string; artifacts?: WorkflowPreviewArtifact[]
  checks?: Array<{ name?: string; ok?: boolean; detail?: string }>
  counts?: { artifacts?: number; by_kind?: Record<string, number> }
  changes?: { total?: number; files?: Array<{ path?: string; kind?: string; artifact_id?: string; summary?: Record<string, number> }>; counts?: Record<string, number> }
}
export interface VisualFeedbackRecord {
  id: string; artifact_id?: string; label: string; note: string
  region?: { x: number; y: number; width: number; height: number } | null
  screenshot: boolean; sent_at: string; status?: string; detail?: string
  snapshots?: Partial<Record<'before' | 'after', { width: number; height: number; bytes: number; captured_at: string }>>
}
export interface WorkflowState {
  workflow_id: string; status: string; phase: string; request?: string
  kind?: 'generic' | 'game' | 'eda' | string
  options?: WorkflowOption[]; selected_option?: WorkflowOption | null
  tasks?: Array<Record<string, unknown>>; pending_tasks?: Array<Record<string, unknown>>
  subagents?: Array<{
    id?: string; role?: string; status?: string; persona?: string
    tools?: string[]; mcp?: string; reflection?: boolean
    task_thread?: string
    /** 子代理当前任务描述与终态结论（对话流团队面板展示用） */
    task?: string; conclusion?: string; trace?: WorkflowChildTrace
    reflection_result?: { ok?: boolean; source?: string; issues?: string[]; next_step?: string }
    steps?: number; elapsed_ms?: number; error?: string
    retry_count?: number
  }>
  results?: Record<string, Record<string, unknown>>; events?: WorkflowEvent[]
  dispatches?: Array<{ planner?: string; added?: string[]; kind?: string }>
  review?: Record<string, unknown>; interrupt_reason?: string
  acceptance_contract?: AcceptanceContract
  visual_feedback?: VisualFeedbackRecord[]
  preview?: WorkflowPreview
  steps?: number; replans?: number; subagent_retries?: Record<string, number>; context_layers?: Record<string, unknown>
  langsmith_trace?: Record<string, unknown>
  observability?: {
    duration_ms?: number; elapsed_ms?: number; events?: number
    prompt_tokens?: number; completion_tokens?: number; total_tokens?: number
    tool_events?: number; mcp_events?: number; failures?: number
    subagents_started?: number; subagents_completed?: number
  }
}
export interface WorkflowResp { ok?: boolean; workflow?: WorkflowState; error?: string }
/** GET /api/agent/workflows 列表项：仅摘要，不含 tasks/options/events 大对象。 */
export interface WorkflowSummary {
  workflow_id: string
  project_id?: string; project_root?: string
  status: string; phase: string; kind?: string; request?: string
  task_count?: number; task_done?: number
  error?: string; interrupt_reason?: string
  created_at?: string; updated_at?: string
}
export interface WorkflowListResp { ok?: boolean; items?: WorkflowSummary[]; error?: string }
export interface WorkflowDeleteResp { ok?: boolean; workflow_id?: string; error?: string }
export interface RetrievalEvalCase {
  id?: string; query: string; relevant_ids?: string[]
  relevant_sources?: string[]; relevant_terms?: string[]
}
export interface RetrievalEvalReport {
  metrics?: Record<string, number>
  ks?: number[]
  items?: Array<Record<string, unknown>>
}
export interface RetrievalEvalResp {
  ok?: boolean; collection?: string; modes?: Record<string, RetrievalEvalReport>
  metric?: string
  comparison?: { metric?: string; best?: string; ranking?: Array<{ mode?: string; value?: number }> }
  error?: string
}
export interface RetrievalRuntimeStatus {
  ok?: boolean
  retrieval?: {
    backend?: string; collection?: string; top_k?: number
    bm25?: boolean; reranker?: string; reranker_model?: string
    lexical_persistence?: boolean; lexical_index?: Record<string, unknown>
    recent_events?: RetrievalTraceEvent[]
  }
}
export interface RetrievalTraceDocument {
  id?: string; source?: string; snippet?: string
  start_line?: number | string; end_line?: number | string
  dense_rank?: number | null; bm25_score?: number; hybrid_score?: number
  rerank_score?: number; distance?: number
}
export interface RetrievalTraceEvent {
  ts?: number; query?: string; collection?: string; mode?: string
  duration_ms?: number; candidates?: number; returned?: number
  reranker?: string; lexical_index?: string
  documents?: RetrievalTraceDocument[]
  [key: string]: unknown
}

export const agentApi = {
  /** Agent 路由策略（本地/云端、自动转云开关）。 */
  routing(): Promise<AgentRoutingResp> {
    return rawJson<AgentRoutingResp>('/api/agent/routing')
  },
  /** 已接入的外部工具（连接器）列表。 */
  connectors(): Promise<AgentConnectorsResp> {
    return rawJson<AgentConnectorsResp>('/api/agent/connectors')
  },
  /** 外部文件改动审批列表。 */
  approvals(): Promise<AgentApprovalsResp> {
    return rawJson<AgentApprovalsResp>('/api/agent/approvals')
  },
  approvalRequests(): Promise<{ ok?: boolean; items?: CockpitApprovalRequest[]; error?: string }> {
    return rawJson('/api/agent/approval-requests')
  },
  decideApprovalRequest(id: string, approved: boolean): Promise<{ ok?: boolean; error?: string }> {
    return rawJson('/api/agent/approval-requests/decide', { id, approved })
  },
  /** 为「项目外文件」发起改动审批请求。 */
  requestApproval(paths: string[], summary: string): Promise<AgentApprovalCreateResp> {
    return rawJson<AgentApprovalCreateResp>('/api/agent/approvals', { paths, summary })
  },
  /** 记录/查询「允许修改项目外文件」的授权。 */
  permission(payload: { path: string; allow_external: boolean; approved: boolean }): Promise<AgentPermissionResp> {
    return rawJson<AgentPermissionResp>('/api/agent/permission', payload)
  },
  /** 审批决定：approved / rejected。 */
  decide(id: string, status: string): Promise<{ ok?: boolean }> {
    return rawJson<{ ok?: boolean }>('/api/agent/approvals/decide', { id, status })
  },
  workflowBackend(): Promise<WorkflowBackendStatus> {
    return rawJson('/api/agent/workflow/backend')
  },
  retrievalEvaluate(payload: {
    cases: RetrievalEvalCase[]; collection?: string; top_k?: number
    modes?: string[]; metric?: string; ks?: number[]
  }): Promise<RetrievalEvalResp> {
    return rawJson('/api/agent/retrieval/evaluate', payload)
  },
  retrievalStatus(collection?: string, top_k = 5): Promise<RetrievalRuntimeStatus> {
    const query = new URLSearchParams({ top_k: String(top_k) })
    if (collection) query.set('collection', collection)
    return rawJson(`/api/agent/retrieval/status?${query.toString()}`)
  },
  langsmith(): Promise<LangSmithStatus> {
    return rawJson('/api/agent/langsmith')
  },
  workflowEvaluation(id: string): Promise<{ ok?: boolean; evaluation?: WorkflowEvaluation; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/evaluation`)
  },
  workflowStart(
    prompt: string,
    options: { use_llm?: boolean; web_enabled?: boolean; kind?: 'generic' | 'game' | 'eda' } = {},
  ): Promise<WorkflowResp> {
    return rawJson('/api/agent/workflow/start', { prompt, ...options })
  },
  workflow(id: string): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}`)
  },
  /** 当前项目进程内未终结的工作流；无则 workflow=null（孤儿卡片发现用）。 */
  workflowActive(): Promise<{ ok?: boolean; workflow?: WorkflowState | null; error?: string }> {
    return rawJson('/api/agent/workflow/active')
  },
  /** 当前项目最近工作流摘要（工作台「工作流历史」）。 */
  workflowList(limit = 50): Promise<WorkflowListResp> {
    return rawJson(`/api/agent/workflows?limit=${encodeURIComponent(String(limit))}`)
  },
  /** 删除终态工作流并清理磁盘文件；运行中需先 workflowInterrupt。 */
  workflowDelete(id: string): Promise<WorkflowDeleteResp> {
    return request(`/api/agent/workflow/${encodeURIComponent(id)}`, { method: 'DELETE' })
  },
  workflowChoice(id: string, choice: string, custom_request = ''): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/choice`, { choice, custom_request })
  },
  workflowResearch(id: string, findings: string, source = 'web'): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/research`, { findings, source })
  },
  workflowResearchRun(id: string, query = ''): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/research/run`, { query })
  },
  workflowPlan(id: string, tasks?: Array<Record<string, unknown>>): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/plan`, { tasks })
  },
  workflowApprove(id: string, approved: boolean, auto_execute = false): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/approve`, { approved, auto_execute })
  },
  workflowPreview(id: string): Promise<{ ok?: boolean; preview?: WorkflowPreview; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/preview`)
  },
  workflowAcceptance(id: string, items: AcceptanceItem[]): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/acceptance`, { items })
  },
  workflowAcceptanceDecide(id: string, approved: boolean, note = ''): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/acceptance/decide`, { approved, note })
  },
  workflowVisualFeedback(id: string, feedback: Record<string, unknown>, projectId = getProjectId()): Promise<{ ok?: boolean; feedback?: VisualFeedbackRecord; items?: VisualFeedbackRecord[]; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/visual-feedback`, feedback, undefined, projectId)
  },
  workflowFeedbackStatus(id: string, feedbackId: string, status: string, detail = '', projectId = getProjectId()): Promise<{ ok?: boolean; feedback?: VisualFeedbackRecord; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/visual-feedback/${encodeURIComponent(feedbackId)}/status`, { status, detail }, undefined, projectId)
  },
  workflowFeedbackSnapshot(id: string, feedbackId: string, phase: 'before' | 'after', image_base64: string, projectId = getProjectId()): Promise<{ ok?: boolean; feedback?: VisualFeedbackRecord; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/visual-feedback/${encodeURIComponent(feedbackId)}/snapshot/${phase}`, { image_base64 }, undefined, projectId)
  },
  workflowFeedbackSnapshotUrl(id: string, feedbackId: string, phase: 'before' | 'after', projectId = getProjectId()): string {
    return `/api/agent/workflow/${encodeURIComponent(id)}/visual-feedback/${encodeURIComponent(feedbackId)}/snapshot/${phase}?project_id=${encodeURIComponent(projectId)}`
  },
  workflowCheckpoint(id: string, approved?: boolean): Promise<{ ok?: boolean; checkpoint?: Record<string, unknown>; error?: string }> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/checkpoint`, { approved })
  },
  workflowInterrupt(id: string, reason = '用户请求中断'): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/interrupt`, { reason })
  },
  workflowResume(id: string): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/resume`, {})
  },
  workflowRevise(id: string, tasks: Array<Record<string, unknown>>): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/revise`, { tasks })
  },
  workflowReviseApprove(id: string, approved: boolean): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/revise/approve`, { approved })
  },
  workflowExecute(id: string, session_id = 'workflow'): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/execute`, { session_id })
  },
  workflowSubagentRetry(id: string, taskId: string, session_id = 'workflow'): Promise<WorkflowResp> {
    return rawJson(`/api/agent/workflow/${encodeURIComponent(id)}/subagents/${encodeURIComponent(taskId)}/retry`, { session_id })
  },
}

export interface WorkflowEventHandlers {
  onEvent: (ev: WorkflowEvent) => void
  /** 后端推完终态事件并发 __stream_done__ 自关时回调（卡片此时拉一次完整状态 hydrate）。 */
  onDone?: () => void
  /** 网络/服务错误；AbortError（主动退订）不上报。 */
  onError?: (e: unknown) => void
}

/**
 * 订阅工作流实时事件 SSE（GET /api/agent/workflow/{id}/events?after=seq）。
 * 连接先重放 seq>after 的持久事件，再推送实时事件；终态收到 __stream_done__ 后
 * 自动断流并回调 onDone。返回退订函数（AbortController 断开），可安全重复调用。
 */
export function workflowEvents(
  workflowId: string,
  after = 0,
  handlers: WorkflowEventHandlers,
): () => void {
  const ctrl = new AbortController()
  let alive = true
  const url = `/api/agent/workflow/${encodeURIComponent(workflowId)}/events?after=${Math.max(0, Math.floor(after) || 0)}`
  void openSse(url, ctrl.signal, (raw) => {
    const ev = raw as unknown as WorkflowEvent
    if (ev.kind === '__stream_done__') {
      alive = false
      ctrl.abort()
      handlers.onDone?.()
      return
    }
    handlers.onEvent(ev)
  }).catch((e: unknown) => {
    if ((e as Error)?.name === 'AbortError') return
    handlers.onError?.(e)
  })
  return () => {
    if (!alive) return
    alive = false
    ctrl.abort()
  }
}

// ---------------------------------------------------------------- 网络搜索 / URL 获取 设置
export type WebSearchProvider =
  | 'builtin_auto' | 'ddg' | 'bing' | 'baidu' | 'exa' | 'tavily'
  | 'searxng' | 'zhipu' | 'bocha' | 'querit' | 'firecrawl' | 'parallel' | 'mcp_exa'
export type WebFetchProvider = 'builtin' | 'jina' | 'firecrawl' | 'custom'

export interface ProviderOption {
  value: string
  label: string
  /** 需要 API Key */
  needs_key?: boolean
  /** 需要自建实例地址（API URL） */
  needs_url?: boolean
  /** 一句话说明 */
  desc?: string
}

/** 设置页统一读取的配置（GET /api/config 的搜索相关字段）。 */
export interface SettingsConfigInfo {
  web_search_provider: WebSearchProvider
  web_search_api_url: string
  web_search_has_key: boolean
  /** 优先使用模型内置 Web 工具（如模型本身支持联网） */
  web_search_prefer_builtin: boolean
  web_fetch_provider: WebFetchProvider
  web_fetch_api_url: string
  web_fetch_has_key: boolean
}

export interface SaveSettingsReq {
  web_search_provider?: WebSearchProvider
  web_search_api_key?: string
  web_search_api_url?: string
  web_search_prefer_builtin?: boolean
  web_fetch_provider?: WebFetchProvider
  web_fetch_api_key?: string
  web_fetch_api_url?: string
}

export const WEB_SEARCH_PROVIDERS: ProviderOption[] = [
  { value: 'builtin_auto', label: '自动（内置 DDG / 百度 / Bing）', desc: '无需 Key，自动故障转移' },
  { value: 'ddg', label: 'DuckDuckGo', desc: '无需 Key' },
  { value: 'baidu', label: '百度', desc: '国内更稳，无需 Key' },
  { value: 'bing', label: 'Bing', desc: '无需 Key' },
  { value: 'exa', label: 'Exa', needs_key: true, desc: 'api.exa.ai，语义检索' },
  { value: 'tavily', label: 'Tavily', needs_key: true, desc: 'api.tavily.com' },
  { value: 'searxng', label: 'SearXNG', needs_url: true, desc: '自建实例' },
  { value: 'bocha', label: 'Bocha 博查', needs_key: true, desc: 'api.bochaai.com' },
  { value: 'firecrawl', label: 'Firecrawl', needs_key: true, desc: '搜索 + 抓取' },
  { value: 'zhipu', label: '智谱搜索', needs_url: true, desc: '通用 keyed POST' },
  { value: 'querit', label: 'Querit', needs_url: true, desc: '通用 keyed POST' },
  { value: 'parallel', label: 'Parallel', needs_url: true, desc: '通用 keyed POST' },
  { value: 'mcp_exa', label: 'ExaMCP', needs_url: true, desc: '经 MCP 接入的 Exa' },
]

export const WEB_FETCH_PROVIDERS: ProviderOption[] = [
  { value: 'builtin', label: '内置（直接抓 HTML）', desc: '无需 Key' },
  { value: 'jina', label: 'Jina', needs_key: true, desc: 'r.jina.ai' },
  { value: 'firecrawl', label: 'Firecrawl', needs_key: true, desc: 'v1/scrape' },
  { value: 'custom', label: '自定义服务', needs_url: true, desc: 'POST {url} 取 markdown' },
]

// ---------------------------------------------------------------- 阶段 3b：AI 工具流编排器
// 对应后端 flows.py：/api/flows（列表/保存/删除）+ /api/flows/run-step（单步受控执行）。
// 节点动作都在「受控动作白名单」内（后端强制），前端只负责编排与展示。
export interface FlowField {
  name: string
  label: string
  kind: 'region' | 'relpath' | 'changeset' | 'message' | 'text'
  required: boolean
  placeholder: string
}
export interface FlowAction {
  action: string
  label: string
  glyph: string
  cat: string
  mutating: boolean
  summary: string
  fields: FlowField[]
}
export interface FlowNode {
  id: string
  action: string
  label: string
  params: Record<string, string>
  x?: number
  y?: number
}
export interface FlowEdge { source: string; target: string }
export interface FlowDef {
  id: string
  name: string
  desc: string
  nodes: FlowNode[]
  edges: FlowEdge[]
  created_at?: string
  updated_at?: string
}
/** 单步执行结果（与后端 execute_step 返回同构）。 */
export interface FlowStepResult {
  ok: boolean
  action: string
  status: 'ok' | 'fail'
  output: string
  detail?: Record<string, unknown> | null
  error?: string
  latency_ms?: number
  trace_written?: boolean
  /** 前端补充：参数字数 / 返回字数（trace step 同构用） */
  arg_chars?: number
  obs_chars?: number
}

export const flowsApi = {
  list(): Promise<{ ok: boolean; flows: FlowDef[] }> {
    return request<{ ok: boolean; flows: FlowDef[] }>('/api/flows')
  },
  /** 保存流程：走 rawJson 原样返回失败体（读 error/errors），不把 ok:false 抛错。 */
  save(flow: FlowDef): Promise<{ ok: boolean; flow?: FlowDef; error?: string; errors?: string[] }> {
    return rawJson('/api/flows', flow)
  },
  remove(id: string): Promise<{ ok: boolean; deleted?: string; error?: string }> {
    return rawJson('/api/flows/delete', { id })
  },
  /** 服务端执行单个受控步骤；失败也当数据返回（读 output/error 分支）。 */
  runStep(payload: {
    action: string
    params?: Record<string, string>
    run_id?: string
    flow_id?: string
    flow_name?: string
    node_id?: string
    finish?: boolean
  }): Promise<FlowStepResult> {
    return rawJson<FlowStepResult>('/api/flows/run-step', payload)
  },
}

export const settingsApi = {
  /** 读取模型 + 搜索相关配置（复用 /api/config）。 */
  get(): Promise<SettingsConfigInfo & ModelConfigInfo> {
    return request('/api/config')
  },
  /** 保存网络搜索 / URL 获取设置（复用 /api/config 的保存通道）。 */
  save(req: SaveSettingsReq): Promise<SettingsConfigInfo & ModelConfigInfo & { ok?: boolean; error?: string; warnings?: string[] }> {
    return rawJson<SettingsConfigInfo & ModelConfigInfo & { ok?: boolean; error?: string; warnings?: string[] }>('/api/config', req)
  },
}

// ================================================================ 阶段 4：边玩边改闭环
// Bug 归档（受控 bugs 分区）+ 变更集回滚/提交。全部经 request/rawJson（自动带项目头），
// 失败体（ok:false / 400 / 403）走 rawJson 原样返回，不抛错，便于面板分支展示。

/** POST /api/dev_capture_bug 的入参（与后端 BugReq 一一对应，7 个文本字段）。 */
export interface BugCaptureReq {
  error?: string
  exception?: string
  traceback?: string
  source_region?: string
  reproduction?: string
  severity?: string
  title?: string
}

/** bugs 分区里的结构化异常记录（tools.dev_capture_bug 落盘的字段）。 */
export interface BugItem {
  id: string
  title: string
  severity: string
  source_region?: string | null
  error?: string
  traceback?: string
  reproduction?: string
  created_at?: string
  updated_at?: string
  /** open / investigating / fixed / ignored */
  status: string
}

/** dev_changesets.jsonl 里的一条变更集（regions.commit_all 写入）。 */
export interface ChangesetItem {
  id: string
  message?: string
  commits?: Record<string, string>
  snapshot?: string | null
  rollback_status?: string
  rollback_at?: string
}

export interface BugsListResp {
  ok: boolean
  bugs: BugItem[]
  total?: number
  error?: string
}

export const bugsApi = {
  /** 归档一条 Bug（写入受控 bugs/ 分区；source_region 传错后端会拒）。 */
  capture(req: BugCaptureReq): Promise<{ ok: boolean; bug_id?: string; path?: string; error?: string }> {
    return rawJson('/api/dev_capture_bug', req)
  },
  /** 列出 Bug；可按 status（open/fixed…）与 source_region 过滤。 */
  list(filter: { status?: string; source_region?: string } = {}): Promise<BugsListResp> {
    const q = new URLSearchParams()
    if (filter.status) q.set('status', filter.status)
    if (filter.source_region) q.set('source_region', filter.source_region)
    const qs = q.toString()
    return rawJson<BugsListResp>('/api/bugs' + (qs ? `?${qs}` : ''))
  },
  /** 更新 Bug 状态（open / investigating / fixed / ignored）。 */
  setStatus(bug_id: string, status: string): Promise<{ ok: boolean; bug?: BugItem; error?: string }> {
    return rawJson('/api/bugs/status', { bug_id, status })
  },
}

export const changesetApi = {
  /** 列出变更集（取最近一条用于「回滚本次改动」）。 */
  list(): Promise<{ changesets: ChangesetItem[] }> {
    return rawJson('/api/changesets')
  },
  /** 回滚某变更集（生成新提交撤销，不破坏历史）。 */
  rollback(changeset: string): Promise<{ ok: boolean; detail?: unknown; error?: string }> {
    return rawJson('/api/rollback_changeset', { changeset })
  },
  /** 跨分区提交全部改动；成功返回变更集 id（供回滚引用）。 */
  commitAll(message: string): Promise<{ ok: boolean; id?: string | null; commits?: Record<string, string>; message?: string; errors?: unknown; error?: string }> {
    return rawJson('/api/commit_all', { message })
  },
}
