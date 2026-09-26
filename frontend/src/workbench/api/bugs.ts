// 缺陷采集 / 变更集（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { rawJson } from './_base'

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
