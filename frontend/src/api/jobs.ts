/** 后台任务接口：入库/重建是异步的，前端靠轮询这里拿进度。 */

import { http } from './http'

export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

export interface Job {
  id: number
  type: string
  target_id: number | null
  status: JobStatus
  progress: number
  attempts: number
  error_msg: string
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface JobEnqueued {
  job: Job
  deduplicated: boolean
}

export const JOB_LABEL: Record<string, string> = {
  parse: '解析',
  chunk: '分块',
  index: '入库',
  reindex: '重建索引',
  tag: '打标签',
}

export const jobsApi = {
  list: (params: { status?: string; target_id?: number; limit?: number } = {}) => {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.target_id !== undefined) query.set('target_id', String(params.target_id))
    if (params.limit !== undefined) query.set('limit', String(params.limit))
    const suffix = query.toString()
    return http.get<Job[]>(`/jobs${suffix ? `?${suffix}` : ''}`)
  },

  get: (id: number) => http.get<Job>(`/jobs/${id}`),

  retry: (id: number) => http.post<JobEnqueued>(`/jobs/${id}/retry`),
}

/** 任务是否还在跑（前端据此决定要不要继续轮询）。 */
export function isActive(job: Job): boolean {
  return job.status === 'pending' || job.status === 'running'
}
