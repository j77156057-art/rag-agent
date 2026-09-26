// 语义标签与大白话定位（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { postJson, request } from './_base'

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
