/** 提示词模板与全局设置接口。 */

import { http } from './http'

export type PromptKind = 'system' | 'optimize' | 'query_rewrite' | 'parse' | 'chunk' | 'tag'

export interface PromptTemplate {
  id: number
  kind: string
  name: string
  content: string
  is_default: boolean
  is_builtin: boolean
  created_at: string
  updated_at: string
}

export interface PromptKindGroup {
  kind: string
  label: string
  items: PromptTemplate[]
}

export interface SettingsPayload {
  items: Record<string, string>
  defaults: Record<string, string>
}

/** 设置项的中文名与类型，前端据此渲染表单而不是硬编码一堆 input。 */
export interface SettingMeta {
  key: string
  label: string
  hint: string
  type: 'number' | 'boolean' | 'select'
  min?: number
  max?: number
  options?: Array<{ value: string; label: string }>
}

export const SETTING_META: SettingMeta[] = [
  {
    key: 'retrieval.vector_top_n',
    label: '向量召回条数',
    hint: '先按语义相似度取多少条候选',
    type: 'number',
    min: 1,
    max: 100,
  },
  {
    key: 'retrieval.bm25_top_n',
    label: 'BM25 召回条数',
    hint: '按关键词匹配取多少条候选',
    type: 'number',
    min: 1,
    max: 100,
  },
  {
    key: 'retrieval.rerank_top_n',
    label: '精排后保留条数',
    hint: '最终交给模型参考的片段数',
    type: 'number',
    min: 1,
    max: 20,
  },
  {
    key: 'retrieval.rrf_k',
    label: 'RRF 常数 k',
    hint: '融合公式 1/(k+rank) 里的 k，越大越平缓',
    type: 'number',
    min: 1,
    max: 1000,
  },
  {
    key: 'retrieval.score_threshold',
    label: '质量判定阈值',
    hint: '最高向量相似度低于它时扩大召回重试',
    type: 'number',
    min: 0,
    max: 1,
  },
  {
    key: 'chat.context_token_budget',
    label: '上下文 token 预算',
    hint: '拼给模型的参考资料上限',
    type: 'number',
    min: 500,
    max: 32000,
  },
  {
    key: 'retrieval.use_rerank',
    label: '启用精排',
    hint: '关闭后按 RRF 融合分排序，更快但略差',
    type: 'boolean',
  },
  {
    key: 'retrieval.expand_retry',
    label: '低分时扩大召回',
    hint: '检索质量不够时自动重试一次',
    type: 'boolean',
  },
  {
    key: 'retrieval.rewrite',
    label: '查询改写',
    hint: '用模型把问题改写成更适合检索的形式',
    type: 'select',
    options: [
      { value: 'off', label: '关闭（最快）' },
      { value: 'rewrite', label: '改写追问' },
      { value: 'hyde', label: 'HyDE 假设答案' },
    ],
  },
]

export const promptsApi = {
  list: (kind?: string) =>
    http.get<PromptKindGroup[]>(`/prompt-templates${kind ? `?kind=${kind}` : ''}`),

  create: (payload: { kind: PromptKind; name: string; content: string; is_default?: boolean }) =>
    http.post<PromptTemplate>('/prompt-templates', payload),

  duplicate: (id: number) => http.post<PromptTemplate>(`/prompt-templates/${id}/duplicate`),

  update: (id: number, payload: { name?: string; content?: string; is_default?: boolean }) =>
    http.put<PromptTemplate>(`/prompt-templates/${id}`, payload),

  reset: (id: number) => http.post<PromptTemplate>(`/prompt-templates/${id}/reset`),

  remove: (id: number) => http.del<void>(`/prompt-templates/${id}`),
}

export const settingsApi = {
  get: () => http.get<SettingsPayload>('/settings'),

  update: (items: Record<string, string>) => http.put<SettingsPayload>('/settings', { items }),
}