<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  SORT_OPTIONS,
  STATUS_LABEL,
  STATUS_TONE,
  formatSize,
  type DocumentItem,
} from '@/api/documents'
import { tagsApi } from '@/api/tags'
import { useDocsStore } from '@/stores/docs'
import { useJobsStore } from '@/stores/jobs'

const store = useDocsStore()
/** 打标签是后台任务：跟踪放在 store 里，切页回来「生成中」还在。 */
const jobs = useJobsStore()
const router = useRouter()

const dragging = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
/** 正在打标签的文档 id（按钮转圈用）：以 store 里运行中的 tag 任务为准。 */
const tagging = computed<number | null>(() => {
  const job = jobs.running.find(
    (item) => item.type === 'tag' && store.items.some((doc) => doc.id === item.target_id),
  )
  return job?.target_id ?? null
})

const stats = computed(() => [
  { label: '文档', value: store.stats.documents },
  { label: '分块', value: store.stats.chunks },
  { label: '已入库分块', value: store.stats.indexed_chunks },
  { label: '向量', value: store.stats.vectors },
])

// ------------------------------------------------------------------------- //
// 搜索：输入时防抖 300ms，避免每敲一个字就打一次接口
// ------------------------------------------------------------------------- //
let searchTimer: number | null = null

watch(
  () => store.query.q,
  () => {
    if (searchTimer !== null) window.clearTimeout(searchTimer)
    searchTimer = window.setTimeout(() => store.reload(), 300)
  },
)

// 下拉类条件改变立刻查，不需要防抖
watch(
  () => [store.query.status, store.query.categoryId, store.query.tagId, store.query.sort],
  () => store.reload(),
)

onUnmounted(() => {
  if (searchTimer !== null) window.clearTimeout(searchTimer)
})

/** 页码列表：页数多时只显示当前页附近，避免一排几十个按钮。 */
const pageNumbers = computed(() => {
  const total = store.totalPages
  const current = store.query.page
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)

  const pages = new Set([1, total, current])
  for (let offset = 1; offset <= 1; offset += 1) {
    if (current - offset > 0) pages.add(current - offset)
    if (current + offset <= total) pages.add(current + offset)
  }
  return [...pages].sort((a, b) => a - b)
})

/** 页码之间要画省略号的位置 */
function showGap(index: number, list: number[]): boolean {
  return index > 0 && list[index] - list[index - 1] > 1
}

const rangeText = computed(() => {
  if (!store.total) return '没有匹配的文档'
  const start = (store.query.page - 1) * store.query.pageSize + 1
  const end = Math.min(store.query.page * store.query.pageSize, store.total)
  return `第 ${start}–${end} 条，共 ${store.total} 条`
})

/** 状态值 → 中文标签。后端返回的 status 是普通字符串，这里做一次收窄。 */
function statusLabel(value: string): string {
  return (STATUS_LABEL as Record<string, string>)[value] ?? value
}

function pickFiles() {
  fileInput.value?.click()
}

async function handleFiles(files: FileList | null) {
  if (!files || !files.length) return
  await store.upload(Array.from(files))
}

function onDrop(event: DragEvent) {
  dragging.value = false
  handleFiles(event.dataTransfer?.files ?? null)
}

async function removeDoc(doc: DocumentItem) {
  if (!confirm(`删除「${doc.filename}」？向量、分块和原始文件都会一起清掉。`)) return
  try {
    await store.remove(doc.id)
  } catch (e) {
    alert((e as Error).message)
  }
}

function openDetail(doc: DocumentItem) {
  router.push(`/documents/${doc.id}`)
}

/** 手动触发打标签：入队后交给全局任务 store 跟踪，切页也不会断。 */
async function tagDoc(doc: DocumentItem, event: Event) {
  event.stopPropagation()
  store.notice = null
  try {
    const result = await tagsApi.tag(doc.id)
    await jobs.track(result.job)
  } catch (e) {
    store.notice = { tone: 'bad', text: (e as Error).message }
  }
}

/** 任务结束时给一句结果提示，并刷新列表（标签/状态都变了）。 */
watch(
  () => jobs.lastFinished,
  (job) => {
    if (!job) return
    const visible = store.items.some((doc) => doc.id === job.target_id)
    if (job.type !== 'tag' || !visible) return
    store.notice =
      job.status === 'failed'
        ? { tone: 'bad', text: job.error_msg || '打标签失败，可以再试一次' }
        : { tone: 'ok', text: '已生成摘要、分类和标签' }
    void store.load()
  },
)

function formatDate(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`
}

onMounted(async () => {
  await store.load()
  // 还在跑的打标签任务由全局 store 接着轮询，列表上的「生成中」不用重进页面等
  void jobs.syncAll()
})
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="desc">
        支持 PDF、Word、Markdown、纯文本。上传后会先解析成文字，再进入分块预览，确认后才写入向量库。
      </p>
    </header>

    <!-- 上传区 -->
    <section
      class="dropzone"
      :class="{ active: dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @click="pickFiles"
    >
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".pdf,.docx,.md,.markdown,.txt"
        hidden
        @change="(e) => handleFiles((e.target as HTMLInputElement).files)"
      />
      <div class="dz-text">
        <span class="dz-main">{{ store.uploading ? '上传并解析中…' : '把文件拖到这里，或点击选择' }}</span>
        <span class="dz-sub">pdf / docx / md / txt，单个不超过 50MB，重复文件会自动跳过</span>
      </div>
    </section>

    <p v-if="store.notice" class="notice" :class="store.notice.tone">{{ store.notice.text }}</p>
    <p v-if="store.error" class="notice bad">{{ store.error }}</p>

    <!-- 统计 -->
    <section class="stats">
      <div v-for="item in stats" :key="item.label" class="stat">
        <div class="stat-value num">{{ item.value }}</div>
        <div class="stat-label">{{ item.label }}</div>
      </div>
    </section>

    <!-- 文件列表 -->
    <section class="list-wrap">
      <div class="list-head">
        <span class="section-title">文档</span>
        <span class="range-text">{{ rangeText }}</span>
        <button
          class="btn btn-sm btn-quiet"
          :disabled="store.loading"
          @click="store.load()"
        >
          {{ store.loading ? '刷新中' : '刷新' }}
        </button>
      </div>

      <!-- 搜索与筛选 -->
      <div class="filters">
        <div class="search-box">
          <span class="search-icon">⌕</span>
          <input
            v-model="store.query.q"
            type="search"
            class="search-input"
            placeholder="搜索文件名或摘要…"
            @keydown.esc="store.query.q = ''"
          />
          <button
            v-if="store.query.q"
            class="search-clear"
            title="清空"
            @click="store.query.q = ''"
          >
            ×
          </button>
        </div>

        <select v-model="store.query.status" class="filter-select">
          <option value="">全部状态</option>
          <option v-for="item in store.facets.statuses" :key="item.value" :value="item.value">
            {{ statusLabel(item.value) }}（{{ item.count }}）
          </option>
        </select>

        <select
          v-if="store.facets.categories.length"
          v-model="store.query.categoryId"
          class="filter-select"
        >
          <option :value="null">全部分类</option>
          <option v-for="item in store.facets.categories" :key="item.id" :value="item.id">
            {{ item.name }}（{{ item.count }}）
          </option>
        </select>

        <select v-model="store.query.sort" class="filter-select">
          <option v-for="item in SORT_OPTIONS" :key="item.value" :value="item.value">
            {{ item.label }}
          </option>
        </select>

        <button v-if="store.hasFilters" class="btn btn-sm btn-quiet" @click="store.clearFilters()">
          清除筛选
        </button>
      </div>

      <!-- 标签筛选：标签通常很多，用横向可滚动的 chip 行 -->
      <div v-if="store.facets.tags.length" class="tag-filter">
        <button
          class="tag-chip"
          :class="{ active: store.query.tagId === null }"
          @click="store.query.tagId = null"
        >
          全部
        </button>
        <button
          v-for="item in store.facets.tags"
          :key="item.id"
          class="tag-chip"
          :class="{ active: store.query.tagId === item.id }"
          @click="store.query.tagId = store.query.tagId === item.id ? null : item.id"
        >
          {{ item.name }}<span class="tag-count">{{ item.count }}</span>
        </button>
      </div>

      <p v-if="store.loading && !store.items.length" class="empty">正在查询…</p>

      <p v-else-if="!store.items.length && store.hasFilters" class="empty">
        没有匹配「{{ store.query.q || '当前筛选条件' }}」的文档。
        <button class="btn btn-sm btn-quiet empty-action" @click="store.clearFilters()">
          清除筛选
        </button>
      </p>

      <p v-else-if="!store.items.length" class="empty">
        还没有文档。先上传一份，看看解析和分块的效果。
      </p>

      <div v-for="doc in store.items" :key="doc.id" class="row" @click="openDetail(doc)">
        <div class="row-main">
          <div class="row-title">
            <span class="file-name">{{ doc.filename }}</span>
            <span class="tag" :class="STATUS_TONE[doc.status]">
              {{ STATUS_LABEL[doc.status] }}
            </span>
            <span v-if="doc.category" class="tag tag-cat">{{ doc.category }}</span>
          </div>
          <div class="row-meta">
            <span>{{ doc.page_count }} 段</span>
            <span>{{ doc.char_count }} 字</span>
            <span>{{ formatSize(doc.size) }}</span>
            <span>{{ formatDate(doc.created_at) }}</span>
            <span v-if="doc.parse_mode === 'llm'" class="tag tag-accent">智能解析</span>
          </div>
          <p v-if="doc.summary" class="row-summary">{{ doc.summary }}</p>
          <div v-if="doc.tags.length" class="row-tags">
            <span v-for="name in doc.tags" :key="name" class="chip">{{ name }}</span>
          </div>
          <p v-if="doc.error_msg" class="row-error">{{ doc.error_msg }}</p>
        </div>

        <div class="row-actions" @click.stop>
          <button
            class="btn btn-sm btn-quiet"
            :disabled="tagging === doc.id"
            :title="doc.tags.length ? '重新生成摘要、分类和标签' : '生成摘要、分类和标签'"
            @click="tagDoc(doc, $event)"
          >
            {{ tagging === doc.id ? '生成中…' : doc.tags.length ? '重打标签' : '打标签' }}
          </button>
          <button class="btn btn-sm" @click="openDetail(doc)">查看分块</button>
          <button class="btn btn-sm btn-quiet btn-danger" @click="removeDoc(doc)">删除</button>
        </div>
      </div>

      <!-- 分页 -->
      <nav v-if="store.total > 0" class="pager">
        <div class="pager-pages">
          <button
            class="page-btn"
            :disabled="store.query.page <= 1"
            title="上一页"
            @click="store.goto(store.query.page - 1)"
          >
            ‹
          </button>
          <template v-for="(num, index) in pageNumbers" :key="num">
            <span v-if="showGap(index, pageNumbers)" class="page-gap">…</span>
            <button
              class="page-btn"
              :class="{ active: num === store.query.page }"
              @click="store.goto(num)"
            >
              {{ num }}
            </button>
          </template>
          <button
            class="page-btn"
            :disabled="store.query.page >= store.totalPages"
            title="下一页"
            @click="store.goto(store.query.page + 1)"
          >
            ›
          </button>
        </div>

        <label class="pager-size">
          每页
          <select
            :value="store.query.pageSize"
            @change="store.setPageSize(Number(($event.target as HTMLSelectElement).value))"
          >
            <option :value="10">10</option>
            <option :value="20">20</option>
            <option :value="50">50</option>
            <option :value="100">100</option>
          </select>
          条
        </label>
      </nav>
    </section>
  </div>
</template>

<style scoped>
.page {
  max-width: 1000px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.desc {
  margin: 0;
  max-width: 62ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.7;
}

/* 上传区 */
.dropzone {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 108px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-lg);
  background: var(--bg-1);
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.dropzone:hover,
.dropzone.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.dz-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  text-align: center;
}
.dz-main {
  font-size: 13.5px;
  color: var(--text-0);
}
.dz-sub {
  font-size: 12px;
  color: var(--text-2);
}

.notice {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--r-md);
  font-size: 12.5px;
  line-height: 1.6;
}
.notice.ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.notice.warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.notice.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

/* 统计 */
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
  padding: var(--sp-4) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.stat-value {
  font-size: 21px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-label {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 2px;
}

/* 列表 */
.list-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.list-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}
.range-text {
  font-size: 11.5px;
  color: var(--text-2);
  margin-left: auto;
  font-family: var(--font-mono);
}

/* 搜索与筛选 */
.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) 0;
}

.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 240px;
  min-width: 200px;
  height: 32px;
  padding: 0 9px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  transition: border-color var(--dur) var(--ease);
}
.search-box:focus-within {
  border-color: var(--accent-border);
}
.search-icon {
  font-size: 14px;
  color: var(--text-2);
  line-height: 1;
}
.search-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: none;
  background: transparent;
  color: var(--text-0);
  font-family: inherit;
  font-size: 12.5px;
  outline: none;
}
.search-input::placeholder {
  color: var(--text-2);
}
/* Safari/webkit 的 search 取消按钮会与本组件的 × 重复 */
.search-input::-webkit-search-cancel-button {
  display: none;
}
.search-clear {
  flex: none;
  padding: 0 2px;
  border: none;
  background: transparent;
  color: var(--text-2);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
}
.search-clear:hover {
  color: var(--text-0);
}

.filter-select {
  flex: none;
  /* 全局 select 是 width:100%（表单里要撑满），筛选条上必须显式收回 */
  width: auto;
  max-width: 180px;
  height: 32px;
  padding: 0 7px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  color: var(--text-1);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
}
.filter-select:hover {
  border-color: var(--line-strong);
}
.filter-select:focus {
  outline: none;
  border-color: var(--accent-border);
}

/* 标签 chip 行 */
.tag-filter {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  padding-bottom: var(--sp-2);
}
.tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--text-1);
  font-family: inherit;
  font-size: 11.5px;
  line-height: 1.6;
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    color var(--dur) var(--ease);
}
.tag-chip:hover {
  background: var(--bg-2);
  color: var(--text-0);
}
.tag-chip.active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
}
.tag-count {
  font-family: var(--font-mono);
  font-size: 10px;
  opacity: 0.7;
}

.empty-action {
  margin-left: 6px;
}

/* 分页 */
.pager {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
  padding-top: var(--sp-3);
  margin-top: var(--sp-1);
  border-top: 1px solid var(--line);
}

.pager-pages {
  display: flex;
  align-items: center;
  gap: 3px;
}

.page-btn {
  min-width: 28px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: var(--bg-1);
  color: var(--text-1);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    color var(--dur) var(--ease);
}
.page-btn:hover:not(:disabled) {
  background: var(--bg-2);
  border-color: var(--line-strong);
  color: var(--text-0);
}
.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.page-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
  cursor: default;
}

.page-gap {
  padding: 0 2px;
  color: var(--text-2);
  font-size: 12px;
}

.pager-size {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--text-2);
}
.pager-size select {
  width: auto;
  height: 26px;
  padding: 0 4px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  color: var(--text-1);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}

.empty {
  margin: 0;
  padding: var(--sp-5);
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-md);
}

.row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: 13px var(--sp-4);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.row:hover {
  border-color: var(--line-strong);
  background: var(--bg-2);
}
.row-main {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.file-name {
  font-size: 13.5px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
}
.row-error {
  margin: 0;
  font-size: 12px;
  color: var(--danger);
}
.row-summary {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-1);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.row-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
}
.chip {
  padding: 1px 7px;
  border-radius: var(--r-sm);
  background: var(--bg-3);
  color: var(--text-1);
  font-size: 11.5px;
  line-height: 1.7;
}
.tag-cat {
  background: var(--accent-soft);
  color: var(--accent);
}
.row-actions {
  display: flex;
  gap: 4px;
  flex: none;
  padding-top: 1px;
}

@media (max-width: 760px) {
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>