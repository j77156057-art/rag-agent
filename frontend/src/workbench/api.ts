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
  try {
    if (pid) localStorage.setItem(PROJECT_ID_KEY, pid)
    else localStorage.removeItem(PROJECT_ID_KEY)
  } catch {
    /* 写入失败（隐私模式等）不影响主流程 */
  }
}

/**
 * 在 init.headers 基础上**合并** `X-DocMind-Project`。
 *
 * - 不改动既有头（尤其 `Content-Type`：FormData 场景必须由浏览器自带 multipart boundary）；
 * - `getProjectId()` 为空时**不注入**，原样返回（生命线：后端回落当前项目）；
 * - 用 `new Headers(...)` 统一处理 headers 为 `undefined` / 普通对象 / `Headers` 实例三种形态。
 */
export function withProject(init?: RequestInit): RequestInit {
  const base: RequestInit = init ? { ...init } : {}
  const pid = getProjectId()
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
async function rawJson<T>(url: string, payload?: unknown): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, withProject(payload === undefined
      ? { method: 'GET' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }))
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
  embed(rect?: EmbedRect | null) { return postJson<{ ok: boolean; error?: string; width?: number; height?: number }>('/api/engine/embed', rect || {}) },
  /** 引擎视窗随前端布局变化重新定位（弹窗移动、窗口缩放时调用）。 */
  place(rect: EmbedRect) { return postJson<{ ok: boolean; error?: string }>('/api/engine/place', rect) },
  detach() { return postJson<{ ok: boolean; was_embedded?: boolean; error?: string }>('/api/engine/detach', {}) },
  focusEngine(keepAttached = false) { return postJson<{ ok: boolean; focused?: number; error?: string }>('/api/engine/focus', { keep_attached: !!keepAttached }) },
  /** 按宿主当前客户区重排"铺满模式"的嵌入窗口 */
  resizeEngine() { return postJson<{ ok: boolean; error?: string }>('/api/engine/resize', {}) },
  stop() { return postJson<{ ok: boolean; stopped: boolean }>('/api/engine/stop', {}) },
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

export const comfyApi = {
  start(root?: string, port = 8188) { return postJson<{ok:boolean;running?:boolean;pid?:number;error?:string}>('/api/comfy/start', { root, port }) },
  stop() { return postJson<{ok:boolean;running?:boolean;stopped?:boolean;pid?:number;error?:string}>('/api/comfy/stop', {}) },
  templates() { return request<{ ok: boolean; templates: { id:string; name:string; model:string; kind:string; workflow?:string; schema?:Record<string,string> }[] }>('/api/comfy/templates') },
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

// ---------------------------------------------------------------- 会话 id
// 每个浏览器标签页使用独立的 session_id：后端每个 session_id 对应一个长驻共享 Agent，
// 逐请求开关（web_enabled / thinking_enabled / tool_mode / plan_mode / llm）由 Agent.run()
// 在开头快照、finally 还原，只保证「串行」正确；而 /api/chat 的 SSE 由工作线程消费，
// 同一会话的两个请求会真正并行并互相覆盖这些开关。给每个标签页分配独立 id，
// 从根上隔离并发请求的逐请求开关（而不是在后端加锁把流式响应串行化）。
//
// 存储位置：sessionStorage（每标签独立）。标签页刷新后仍保留本会话，关掉标签页即清除；
// localStorage 不再承载会话 id——旧实现「localStorage 优先」会让新标签静默接到旧会话。
// 「关掉重开找回历史」改由「会话列表显式切换」或单窗口下的唯一性门禁兜底（见 docmindTabs）。
const SESSION_ID_KEY = 'docmind_session_id'
let cachedSessionId: string | null = null

/** 生成 slug 安全的会话 id（仅含 [A-Za-z0-9_-]），形如 web-<12位十六进制>。
 *  后端 sessions.py::_slug() 会把 id 用于拼文件名，故必须规避路径分隔符等字符。 */
function newSessionId(): string {
  const c = globalThis.crypto as Crypto | undefined
  const raw = c && typeof c.randomUUID === 'function'
    ? c.randomUUID().replace(/-/g, '')
    : Math.random().toString(16).slice(2) + Math.random().toString(16).slice(2)
  const hex = raw.replace(/[^0-9a-fA-F]/g, '').slice(0, 12).padEnd(12, '0')
  return `web-${hex}`
}

/** 当前会话 id（sessionStorage 持久化：标签页刷新后保留、关掉标签页即清除）。
 *  每个标签页各自独立——不再读 localStorage，避免同一浏览器多标签共享同一段对话
 *  （旧实现「localStorage 优先」会让新标签静默接到旧会话）。取不到就新生成一个。
 *  存储不可用（隐私模式 / 非浏览器）时 try/catch 退回模块级 id，保证同页一致。 */
export function getSessionId(): string {
  if (cachedSessionId) return cachedSessionId
  try {
    const existing = sessionStorage.getItem(SESSION_ID_KEY)
    if (existing) {
      cachedSessionId = existing
      return existing
    }
    const fresh = newSessionId()
    try { sessionStorage.setItem(SESSION_ID_KEY, fresh) } catch { /* 存储不可写：仅内存 */ }
    cachedSessionId = fresh
    return fresh
  } catch {
    // 非浏览器 / 隐私模式：退回模块级 id（同一页面内保持一致）
    cachedSessionId = newSessionId()
    return cachedSessionId
  }
}

/** 显式切换当前会话 id（如「继续某段历史对话」/「新会话」）：只写 sessionStorage 并刷新缓存。
 *  返回是否切换成功（空 id 视为无效）。存储不可用时仍更新内存缓存。 */
export function setSessionId(id: string): boolean {
  const v = (id || '').trim()
  if (!v) return false
  try { sessionStorage.setItem(SESSION_ID_KEY, v) } catch { /* 存储不可用：仍更新内存 */ }
  cachedSessionId = v
  return true
}

/** 开始一段全新会话：生成新 id、写入存储并刷新缓存，返回该 id。
 *  ChatDock 会因新 id 无历史而显示空态（区别于 clearConversation 的「清空并删档」）。 */
export function startNewSession(): string {
  const fresh = newSessionId()
  setSessionId(fresh)
  return fresh
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
    opts: { web?: boolean; thinking?: boolean | null } = {},
  ): Promise<void> {
    const fd = new FormData()
    fd.append('question', question)
    fd.append('session_id', getSessionId())
    fd.append('web_mode', opts.web ? '1' : '0')
    if (opts.thinking === true) fd.append('thinking_mode', '1')
    else if (opts.thinking === false) fd.append('thinking_mode', '0')
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
  /** native=模型天生推理（开关恒开） / toggle=可开关（qwen3 家族） / none=不支持 */
  thinking: 'native' | 'toggle' | 'none' | string
  cloud: boolean
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
  /** 当前生效窗口来源：custom 手填 / probe Ollama 实时探测 / profile 内置画像 */
  context_source?: 'custom' | 'probe' | 'profile'
  /** AI 越界访问模式：safe=仅限项目内；high=允许受控越界读写（须配置白名单） */
  external_access_mode?: 'safe' | 'high'
}

export interface SaveModelReq {
  provider: string
  model?: string
  api_key?: string
  base_url?: string
  /** >0 设置窗口覆盖；0 清除覆盖回到自动；undefined=不改动 */
  context_window?: number | null
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
  save(req: SaveModelReq): Promise<ModelConfigInfo & { warnings?: string[]; model_error?: string }> {
    // 后端配置保存失败是 HTTP 200 + {ok:false, model_error?}：必须走 rawJson 原样返回失败体，
    // 否则通用 request 会把真实原因吞成「请求失败（HTTP 200）」，model_error 分支永不触发。
    return rawJson<ModelConfigInfo & { warnings?: string[]; model_error?: string }>('/api/config', req)
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
  total_tokens: number
  cost_cny: number
  llm_calls: number
  llm_ms: number
  elapsed_ms: number
  outcome: string
  finish_reason?: string | null
  aborted: boolean
  error: string
  steps: TraceStep[]
}
export interface TraceSummary {
  turns: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  avg_elapsed_ms: number
  aborted: number
  errors: number
  by_provider: Record<string, { turns: number; tokens: number } | number>
}
export interface SessionInfo {
  session_id: string
  turns: number
  has_summary: boolean
  updated_at: string
}
export interface SkillInfo {
  name: string
  description: string
  when_to_use: string
  path: string
}

export const harnessApi = {
  budget(): Promise<{ ok: boolean; status: BudgetStatus; check: BudgetCheck }> {
    return request('/api/budget')
  },
  setBudgetLimit(limitCny: number): Promise<{ ok: boolean; check?: BudgetCheck; error?: string }> {
    return postJson('/api/budget', { limit_cny: limitCny })
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
  hooks(): Promise<{ ok: boolean; hooks_dir: string; exists: boolean; counts: Record<string, number>; sources: Record<string, unknown>; errors: string[] }> {
    return request('/api/hooks')
  },
  reloadHooks(): Promise<{ ok: boolean; errors?: string[] }> {
    return postJson('/api/hooks/reload', {})
  },
}

// ---------------------------------------------------------------- Agent 策略 / 审批（P4 收口）
// 对应后端 /api/agent/*。这些是**项目作用域**请求（审批/授权针对当前项目的外部文件），
// 必须带上 X-DocMind-Project（经 withProject）。统一走 rawJson 通道：不把 ok:false 抛错，
// 与原组件「读失败体字段（reason/recorded/approval）」的语义一致。
export interface AgentRoutingResp { ok?: boolean; auto_cloud_enabled?: boolean }
export interface AgentConnectorInfo { key: string; label: string; enabled: boolean }
export interface AgentConnectorsResp { ok?: boolean; connectors?: AgentConnectorInfo[] }
export interface AgentApproval { id: string; status: string; summary?: string; diff?: string }
export interface AgentApprovalsResp { ok?: boolean; approvals?: AgentApproval[] }
export interface AgentPermissionResp { ok?: boolean; recorded?: boolean; reason?: string }
export interface AgentApprovalCreateResp { ok?: boolean; approval?: AgentApproval; error?: string }

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
