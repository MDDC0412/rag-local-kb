/** 标签与分类接口。 */

import { http } from './http'
import type { JobEnqueued } from './jobs'

export interface TagItem {
  id: number
  name: string
  color: string
  usage: number
}

export interface CategoryItem {
  id: number
  name: string
  description: string
  usage: number
}

export interface DocumentTags {
  document_id: number
  tags: string[]
}

export const tagsApi = {
  list: () => http.get<TagItem[]>('/tags'),

  categories: () => http.get<CategoryItem[]>('/categories'),

  forDocument: (id: number) => http.get<DocumentTags>(`/documents/${id}/tags`),

  setForDocument: (id: number, tags: string[]) =>
    http.put<DocumentTags>(`/documents/${id}/tags`, { tags }),

  /** 手动触发打标签（自动流程是入库完成后排队）。 */
  tag: (id: number) => http.post<JobEnqueued>(`/documents/${id}/tag`),
}