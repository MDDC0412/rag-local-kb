/** 文档与分块相关接口。 */

import { http } from './http'
import type { JobEnqueued } from './jobs'

export interface DocumentItem {
  id: number
  kb_id: number
  filename: string
  status: DocumentStatus
  parse_mode: string
  page_count: number
  char_count: number
  size: number
  error_msg: string
  summary: string
  category: string | null
  tags: string[]
  created_at: string
  indexed_at: string | null
}

export type DocumentStatus =
  | 'uploaded'
  | 'parsing'
  | 'parsed'
  | 'chunked'
  | 'indexing'
  | 'indexed'
  | 'failed'

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  uploaded: '待解析',
  parsing: '解析中',
  parsed: '已解析',
  chunked: '待入库',
  indexing: '入库中',
  indexed: '已入库',
  failed: '失败',
}

export const STATUS_TONE: Record<DocumentStatus, string> = {
  uploaded: '',
  parsing: 'tag-warn',
  parsed: '',
  chunked: 'tag-warn',
  indexing: 'tag-warn',
  indexed: 'tag-ok',
  failed: 'tag-danger',
}

export interface ChunkItem {
  id: number | null
  seq: number
  content: string
  token_count: number
  char_count: number
  page_no: number
  is_confirmed: number
}

export interface PageItem {
  page_no: number
  text: string
  char_count: number
}

export interface UploadResult {
  documents: DocumentItem[]
  duplicates: string[]
  warnings: string[]
}

export interface PreviewResult {
  document_id: number
  strategy: string
  chunk_size: number
  overlap: number
  chunks: ChunkItem[]
  count: number
  total_tokens: number
  avg_tokens: number
  warnings: string[]
}

export interface DocumentDetail {
  document: DocumentItem
  chunks: ChunkItem[]
  stats: Record<string, number>
}

export interface KbStats {
  documents: number
  chunks: number
  indexed_chunks: number
  vectors: number
}

export type ChunkStrategy =
  | 'fixed'
  | 'recursive'
  | 'sentence'
  | 'paragraph'
  | 'markdown'
  | 'semantic'
  | 'parent_child'

export interface StrategyMeta {
  value: ChunkStrategy
  label: string
  hint: string
  needs_api: boolean
}

export interface ChunkOptions {
  strategy: ChunkStrategy
  chunk_size: number
  overlap: number
  semantic_threshold?: number
  save?: boolean
  /** 原文过滤：规则清洗页眉页脚/页码/碎片行，零成本 */
  filter_raw?: boolean
  /** 智能过滤：让模型判断哪些行没有检索价值，只调 1 次，默认关 */
  filter_ai?: boolean
}

export interface FilterRemoval {
  page_no: number
  line_no: number
  text: string
  reason: string
}

export interface FilterPreview {
  document_id: number
  mode: string
  raw_chars: number
  kept_chars: number
  removed_count: number
  removals: FilterRemoval[]
  warnings: string[]
  pages: Array<{ page_no: number; text: string }>
}

export interface StrategySample {
  strategy: string
  title: string
  why: string
  traits: string[]
  char_count: number
}

export interface StrategySampleDetail extends StrategySample {
  document: string
  chunk_size: number
  overlap: number
  chunks: Array<{
    seq: number
    content: string
    token_count: number
    page_no: number
    section_path: string
  }>
  count: number
  avg_tokens: number
  warnings?: string[]
  note?: string
}

export interface DocumentList {
  items: DocumentItem[]
  total: number
  page: number
  page_size: number
}

export interface FacetCount {
  value?: string
  id?: number
  name?: string
  count: number
}

export interface DocumentFacets {
  statuses: Array<{ value: string; count: number }>
  categories: Array<{ id: number; name: string; count: number }>
  tags: Array<{ id: number; name: string; count: number }>
}

/** 列表查询条件。所有字段都可选，不传就是默认视图。 */
export interface DocumentQuery {
  q?: string
  status?: string
  categoryId?: number | null
  tagId?: number | null
  sort?: 'recent' | 'oldest' | 'name' | 'size' | 'chars'
  page?: number
  pageSize?: number
}

export const SORT_OPTIONS = [
  { value: 'recent', label: '最近上传' },
  { value: 'oldest', label: '最早上传' },
  { value: 'name', label: '文件名' },
  { value: 'size', label: '文件大小' },
  { value: 'chars', label: '字数' },
] as const

function buildQuery(base: string, params: Record<string, unknown>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const suffix = search.toString()
  return suffix ? `${base}?${suffix}` : base
}

export const docsApi = {
  strategies: () => http.get<{ items: StrategyMeta[] }>('/chunk-strategies'),

  sampleIndex: () => http.get<{ items: StrategySample[] }>('/chunk-samples'),

  sample: (strategy: string, chunkSize = 320, overlap = 32) =>
    http.get<StrategySampleDetail>(
      `/chunk-samples/${strategy}?chunk_size=${chunkSize}&overlap=${overlap}`,
    ),

  stats: (kbId = 1) => http.get<KbStats>(`/kbs/${kbId}/stats`),

  /** 分页 + 搜索 + 筛选的文档列表（契约：{items,total,page,page_size}）。 */
  list: (kbId = 1, query: DocumentQuery = {}) =>
    http.get<DocumentList>(
      buildQuery(`/kbs/${kbId}/documents`, {
        q: query.q?.trim(),
        status: query.status,
        category_id: query.categoryId,
        tag_id: query.tagId,
        sort: query.sort,
        page: query.page,
        page_size: query.pageSize,
      }),
    ),

  /** 筛选器候选：有哪些状态/分类/标签，各多少篇。 */
  facets: (kbId = 1) => http.get<DocumentFacets>(`/kbs/${kbId}/document-facets`),

  detail: (id: number) => http.get<DocumentDetail>(`/documents/${id}`),

  pages: (id: number) =>
    http.get<{ document_id: number; filename: string; pages: PageItem[] }>(
      `/documents/${id}/pages`,
    ),

  /** 上传用 FormData，不能走 JSON 封装，单独实现。 */
  async upload(kbId: number, files: File[]): Promise<UploadResult> {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    const resp = await fetch(`/api/kbs/${kbId}/documents`, { method: 'POST', body: form })
    const text = await resp.text()
    const payload = text ? JSON.parse(text) : {}
    if (!resp.ok) {
      throw new Error(payload?.error?.message ?? `上传失败（HTTP ${resp.status}）`)
    }
    return payload as UploadResult
  },

  preview: (id: number, opts: ChunkOptions) =>
    http.post<PreviewResult>(`/documents/${id}/chunks/preview`, {
      strategy: opts.strategy,
      chunk_size: opts.chunk_size,
      overlap: opts.overlap,
      semantic_threshold: opts.semantic_threshold ?? 0.55,
      save: opts.save ?? true,
      filter_raw: opts.filter_raw ?? false,
      filter_ai: opts.filter_ai ?? false,
    }),

  /** 过滤预览：分块之前先看清洗效果（不落库） */
  filterPreview: (id: number, opts: { filter_raw: boolean; filter_ai: boolean }) =>
    http.post<FilterPreview>(`/documents/${id}/filter/preview`, {
      filter_raw: opts.filter_raw,
      filter_ai: opts.filter_ai,
    }),

  updateChunk: (chunkId: number, content: string) =>
    http.put<ChunkItem>(`/chunks/${chunkId}`, { content }),

  deleteChunk: (chunkId: number) => http.del<void>(`/chunks/${chunkId}`),

  mergeChunks: (chunkIds: number[]) => http.post<ChunkItem>('/chunks/merge', { chunk_ids: chunkIds }),

  // 入库/重建是异步的：立刻返回任务，进度靠 GET /jobs/{id} 轮询
  index: (id: number) => http.post<JobEnqueued>(`/documents/${id}/index`),

  reindex: (id: number) => http.post<JobEnqueued>(`/documents/${id}/reindex`),

  remove: (id: number) => http.del<void>(`/documents/${id}`),

  reparse: (id: number, mode: 'local' | 'llm' = 'local') =>
    http.post<{ page_count: number; char_count: number; mode: string; warnings: string[] }>(
      `/documents/${id}/parse`,
      { mode },
    ),
}

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}