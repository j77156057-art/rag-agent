// 项目登记 / 切换 / 重命名（由原 workbench/api.ts 按域切分；调用方继续从 barrel ../api 引入）。
import { projectRequest } from './_base'

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
