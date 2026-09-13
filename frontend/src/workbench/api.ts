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

export interface EngineStatus { ok: boolean; running: boolean; pid?: number | null; error?: string }

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

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(url, init)
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

export const taskApi = {
  create(payload: Record<string, unknown>) { return postJson<{ ok: boolean; task?: Record<string, unknown>; error?: string }>('/api/tasks', payload) },
  validate(payload: Record<string, unknown>) { return postJson<{ ok: boolean; scope: TaskScope }>('/api/tasks/validate', payload) },
  impact(payload: Record<string, unknown>) { return postJson<{ ok: boolean; files: string[]; direct_files: string[]; related_files: string[] }>('/api/tasks/impact', payload) },
  snapshot(payload: Record<string, unknown>) { return postJson<{ ok: boolean; snapshot: Record<string, unknown> }>('/api/tasks/snapshot', payload) },
  verify(payload: Record<string, unknown>) { return postJson<{ ok: boolean; checks: { command: string; ok: boolean; output?: string; error?: string }[] }>('/api/tasks/verify', payload) },
  branch(payload: Record<string, unknown>) { return postJson<{ ok: boolean; branch?: string; error?: string }>('/api/tasks/branch', payload) },
}

export const engineApi = {
  catalog() { return request<{ ok:boolean; engines:{id:string;name:string;executable:string;download:string}[] }>('/api/engine/catalog') },
  config(engine?: string, executable?: string) { return engine ? postJson<{ok:boolean;engine:string;executable:string}>('/api/engine/config',{engine,executable}) : request<{ok:boolean;engine:string;executable:string}>('/api/engine/config') },
  status() { return request<EngineStatus>('/api/engine/status') },
  start(executable = 'godot', embed = true) { return postJson<EngineStatus>('/api/engine/start', { executable, embed }) },
  stop() { return postJson<{ ok: boolean; stopped: boolean }>('/api/engine/stop', {}) },
  logs(limit = 200) { return request<{ ok: boolean; lines: string[]; errors: { path: string; line: number; message: string }[] }>(`/api/engine/logs?limit=${limit}`) },
  verify(executable = 'godot') { return postJson<{ ok: boolean; output?: string; error?: string }>('/api/engine/verify', { executable }) },
}

export const runtimeApi = {
  events() { return request<{ ok: boolean; events: Record<string, unknown>[] }>('/api/runtime/events') },
  append(events: Record<string, unknown>[]) { return postJson<{ ok: boolean; events: Record<string, unknown>[] }>('/api/runtime/events', { events }) },
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

export const comfyApi = {
  status(url = 'http://127.0.0.1:8188') { return request<{ ok: boolean; available: boolean; url: string; error?: string }>(`/api/comfy/status?url=${encodeURIComponent(url)}`) },
  queue(workflow: Record<string, unknown>, url = 'http://127.0.0.1:8188') { return postJson<{ ok: boolean; response?: Record<string, unknown>; error?: string }>('/api/comfy/queue', { url, workflow }) },
  history(promptId: string, url = 'http://127.0.0.1:8188') { return request<{ ok: boolean; done?: boolean; outputs?: { filename?: string; subfolder?: string; type?: string }[]; error?: string }>(`/api/comfy/history/${encodeURIComponent(promptId)}?url=${encodeURIComponent(url)}`) },
  import(promptId: string, image: Record<string, unknown>, url = 'http://127.0.0.1:8188', destDir = 'assets/generated') { return postJson<{ ok: boolean; path?: string; error?: string }>('/api/comfy/import', { prompt_id: promptId, image, url, dest_dir: destDir }) },
  importAll(promptId: string, images: Record<string, unknown>[], url = 'http://127.0.0.1:8188', destDir = 'assets/generated') { return postJson<{ ok: boolean; imported: number; results: { ok: boolean; path?: string; error?: string }[] }>('/api/comfy/import-all', { prompt_id: promptId, images, url, dest_dir: destDir }) },
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
      res = await fetch('/api/verify_contracts')
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
  type: 'token' | 'thought' | 'action' | 'observation' | 'reflection' | 'final' | 'done' | string
  text?: string
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
    res = await fetch(url, { ...init, signal: h.signal })
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

export const aiApi = {
  /** 解释 / Review / 自由提问：走 ReAct agent（可 search_code/read_file/grep，引用文件行号）。 */
  askGrounded(question: string, h: SseStreamHandlers): Promise<void> {
    const fd = new FormData()
    fd.append('question', question)
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
  addonStatus(): Promise<GodotAddonStatus> {
    return request('/api/engine/addon/status')
  },
  addonInstall(force = false): Promise<{ ok: boolean; version?: string; error?: string; next?: string }> {
    return postJson('/api/engine/addon/install', { confirm: true, force })
  },
}
