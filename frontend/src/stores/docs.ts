/** 文档库状态：列表（分页+搜索+筛选）、统计、筛选器候选、过滤预览。 */

import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  docsApi,
  type DocumentFacets,
  type DocumentItem,
  type DocumentQuery,
  type FilterPreview,
  type KbStats,
  type UploadResult,
} from '@/api/documents'
import { errorMessage } from '@/api/http'

const KB_ID = 1

/** 页面顶部那条提示。放在 store 里，切页回来还看得到（上传结果、打标签结果）。 */
export interface Notice {
  tone: 'ok' | 'warn' | 'bad'
  text: string
}

/** 一次「过滤预览」的状态。按文档 id 存：切页回来还能看到刚算出来的结果。 */
export interface FilterState {
  running: boolean
  error: string
  data: FilterPreview | null
  /** 上次预览用的两个开关，回来时还原，免得结果和勾选状态对不上。 */
  raw: boolean
  ai: boolean
}

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
  /** 顶部提示：上传结果、打标签结果都写这里，切页不会丢。 */
  const notice = ref<Notice | null>(null)
  /** 过滤预览：按文档 id 存，重新进入时直接显示上次的结果。 */
  const filters = ref<Record<number, FilterState>>({})

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

  /** 上传结果 → 一句人话。 */
  function uploadNoticeOf(result: UploadResult): Notice {
    const parts: string[] = []
    if (result.documents.length) parts.push(`已上传 ${result.documents.length} 个`)
    if (result.duplicates.length) parts.push(`跳过重复 ${result.duplicates.length} 个`)
    if (result.warnings.length) parts.push(`${result.warnings.length} 条提示`)
    const head = parts.join('，') || '没有文件被处理'
    if (!result.warnings.length) return { tone: 'ok', text: head }
    return { tone: 'warn', text: `${head}：${result.warnings.join('；')}` }
  }

  /** 上传文件：解析入队后刷新列表，结果提示也写进 store。 */
  async function upload(files: File[]): Promise<UploadResult> {
    uploading.value = true
    error.value = ''
    notice.value = null
    try {
      const result = await docsApi.upload(KB_ID, files)
      // 新文档按"最近上传"排在最前，回到第一页才看得见
      await reload()
      notice.value = uploadNoticeOf(result)
      return result
    } catch (e) {
      notice.value = { tone: 'bad', text: errorMessage(e) }
      throw e
    } finally {
      uploading.value = false
    }
  }

  /** 取某篇文档的过滤预览状态槽（没有就建一个）。 */
  function filterState(documentId: number): FilterState {
    if (!filters.value[documentId]) {
      filters.value[documentId] = { running: false, error: '', data: null, raw: false, ai: false }
    }
    return filters.value[documentId]
  }

  /**
   * 预览过滤效果。
   *
   * 智能过滤要真的调一次模型，几秒到几十秒；状态放 store 里，
   * 切到别的页面再回来，结果还在，不用重跑一遍花钱。
   */
  async function runFilterPreview(
    documentId: number,
    options: { filter_raw: boolean; filter_ai: boolean },
  ): Promise<void> {
    const slot = filterState(documentId)
    if (!options.filter_raw && !options.filter_ai) {
      slot.error = '请先勾选至少一种过滤方式'
      slot.data = null
      return
    }
    slot.raw = options.filter_raw
    slot.ai = options.filter_ai
    slot.running = true
    slot.error = ''
    try {
      slot.data = await docsApi.filterPreview(documentId, options)
    } catch (e) {
      slot.error = errorMessage(e)
      slot.data = null
    } finally {
      slot.running = false
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
    notice,
    filters,
    query,
    totalPages,
    hasFilters,
    load,
    reload,
    goto,
    setPageSize,
    clearFilters,
    upload,
    filterState,
    runFilterPreview,
    remove,
    KB_ID,
  }
})
