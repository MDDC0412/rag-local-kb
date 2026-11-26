/** 文档库状态：列表（分页+搜索+筛选）、统计、筛选器候选。 */

import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  docsApi,
  type DocumentFacets,
  type DocumentItem,
  type DocumentQuery,
  type KbStats,
  type UploadResult,
} from '@/api/documents'

const KB_ID = 1

/** 一次查询的完整条件。翻页时只改 page，其余保持不变。 */
export interface DocQueryState {
  q: string
  status: string
  categoryId: number | null
  tagId: number | null
  sort: NonNullable<DocumentQuery['sort']>
  page: number
  pageSize: number
}

export const useDocsStore = defineStore('docs', () => {
  const items = ref<DocumentItem[]>([])
  const total = ref(0)
  const stats = ref<KbStats>({ documents: 0, chunks: 0, indexed_chunks: 0, vectors: 0 })
  const facets = ref<DocumentFacets>({ statuses: [], categories: [], tags: [] })
  const loading = ref(false)
  const uploading = ref(false)
  const error = ref('')

  /** 当前查询条件。页面直接 v-model 到它上面，然后调 reload()。 */
  const query = reactive<DocQueryState>({
    q: '',
    status: '',
    categoryId: null,
    tagId: null,
    sort: 'recent',
    page: 1,
    pageSize: 20,
  })

  const totalPages = computed(() =>
    Math.max(1, Math.ceil(total.value / Math.max(1, query.pageSize))),
  )

  /** 有没有启用任何搜索/筛选条件（用于显示"清除筛选"按钮）。 */
  const hasFilters = computed(
    () => Boolean(query.q.trim() || query.status || query.categoryId || query.tagId),
  )

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const [list, s, f] = await Promise.all([
        docsApi.list(KB_ID, {
          q: query.q,
          status: query.status || undefined,
          categoryId: query.categoryId,
          tagId: query.tagId,
          sort: query.sort,
          page: query.page,
          pageSize: query.pageSize,
        }),
        docsApi.stats(KB_ID),
        docsApi.facets(KB_ID).catch(() => ({ statuses: [], categories: [], tags: [] })),
      ])
      items.value = list.items
      total.value = list.total
      stats.value = s
      facets.value = f

      // 删到最后一页只剩 0 条时，自动退回上一页 —— 否则用户会盯着空列表发愣
      if (list.items.length === 0 && query.page > 1 && list.total > 0) {
        query.page = Math.max(1, Math.ceil(list.total / query.pageSize))
        await load()
      }
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  /** 改条件后从第 1 页重新查（搜索/筛选/排序都应该回到第一页）。 */
  async function reload() {
    query.page = 1
    await load()
  }

  async function goto(page: number) {
    const target = Math.min(Math.max(1, page), totalPages.value)
    if (target === query.page) return
    query.page = target
    await load()
  }

  async function setPageSize(size: number) {
    query.pageSize = size
    await reload()
  }

  function clearFilters() {
    query.q = ''
    query.status = ''
    query.categoryId = null
    query.tagId = null
    return reload()
  }

  async function upload(files: File[]): Promise<UploadResult> {
    uploading.value = true
    error.value = ''
    try {
      const result = await docsApi.upload(KB_ID, files)
      // 新文档按"最近上传"排在最前，回到第一页才看得见
      await reload()
      return result
    } finally {
      uploading.value = false
    }
  }

  async function remove(id: number) {
    await docsApi.remove(id)
    await load()
  }

  return {
    items,
    total,
    stats,
    facets,
    loading,
    uploading,
    error,
    query,
    totalPages,
    hasFilters,
    load,
    reload,
    goto,
    setPageSize,
    clearFilters,
    upload,
    remove,
    KB_ID,
  }
})
