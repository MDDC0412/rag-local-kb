<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  STATUS_LABEL,
  STATUS_TONE,
  docsApi,
  formatSize,
  type ChunkItem,
  type ChunkStrategy,
  type DocumentDetail,
  type PageItem,
  type PreviewResult,
  type StrategyMeta,
  type StrategySample,
} from '@/api/documents'
import { JOB_LABEL, isActive, jobsApi, type Job } from '@/api/jobs'
import { tagsApi } from '@/api/tags'
import StrategySampleModal from '@/components/StrategySampleModal.vue'
import { useDocsStore } from '@/stores/docs'
import { useJobsStore } from '@/stores/jobs'

const route = useRoute()
const router = useRouter()
const docId = Number(route.params.id)

const docs = useDocsStore()
/** 后台任务（入库/重建/打标签）的进度与结果都在这里，切页也不会断。 */
const jobs = useJobsStore()

const detail = ref<DocumentDetail | null>(null)
const pages = ref<PageItem[]>([])
const chunks = ref<ChunkItem[]>([])
const strategies = ref<StrategyMeta[]>([])
const sampleStrategies = ref<Set<string>>(new Set())
const sampleOpen = ref(false)
const sampleStrategy = ref('')
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')
/** 页面上显示哪条任务：默认跟最近一次入队的（进度条与结果都读 store）。 */
const shownJobId = ref<number | null>(null)
const activeJob = computed<Job | null>(() =>
  shownJobId.value === null ? null : (jobs.byId[shownJobId.value] ?? null),
)
/** 当前是否有后台任务在跑（切页回来也要恢复成「进行中」的样子）。 */
const jobRunning = computed(() => (activeJob.value ? isActive(activeJob.value) : false))
const tagJobRunning = computed(() => jobRunning.value && activeJob.value?.type === 'tag')

/** 把任务交给全局 store：切页后它继续轮询，回来看到的是最新进度。 */
function followJob(job: Job): void {
  shownJobId.value = job.id
  void jobs.track(job)
}

/** 任务结束时刷新一次数据（状态、分块、标签都变了），失败则给出人话提示。 */
watch(
  () => activeJob.value?.status,
  (status) => {
    if (!status || status === 'pending' || status === 'running') return
    busy.value = ''
    if (status === 'failed') {
      error.value = activeJob.value?.error_msg || '任务失败，可以点「重试」再试一次'
    }
    void load()
  },
)

const options = reactive({
  strategy: 'recursive' as ChunkStrategy,
  chunk_size: 512,
  overlap: 64,
  semantic_threshold: 0.55,
  // 过滤发生在分块之前：原文过滤零成本，智能过滤只调 1 次模型
  filter_raw: false,
  filter_ai: false,
})

/** 当前策略的元信息，用于显示说明和"要不要花钱"的提示。 */
const currentStrategy = computed(() =>
  strategies.value.find((item) => item.value === options.strategy),
)

const needsApi = computed(() => currentStrategy.value?.needs_api ?? false)
const showThreshold = computed(() => options.strategy === 'semantic')

/** 智能过滤依赖大模型，未开启原文过滤时也允许单独用（两者取并集） */
const filtering = computed(() => options.filter_raw || options.filter_ai)

// 过滤预览：状态在 docs store 里（按文档 id 存），切页回来结果还在
const filterSlot = docs.filterState(docId)
const filterPreview = computed(() => filterSlot.data)
const filterLoading = computed(() => filterSlot.running)
const filterError = computed(() => filterSlot.error)
// 勾选状态沿用上次的，避免「结果还在、开关却重置了」的错位
options.filter_raw = filterSlot.raw
options.filter_ai = filterSlot.ai
// 之后每次改动都同步回 store（不重新跑预览也记着）
watch(
  () => [options.filter_raw, options.filter_ai],
  () => {
    filterSlot.raw = options.filter_raw
    filterSlot.ai = options.filter_ai
  },
)

function runFilterPreview() {
  void docs.runFilterPreview(docId, {
    filter_raw: options.filter_raw,
    filter_ai: options.filter_ai,
  })
}

/** 当前策略是否配了样例（配了才显示「看样例」按钮）。 */
const hasSample = computed(() => sampleStrategies.value.has(options.strategy))

function openSample() {
  sampleStrategy.value = options.strategy
  sampleOpen.value = true
}

const previewStats = ref<{ count: number; total: number; avg: number; strategy: string } | null>(null)

// 编辑态
const editingId = ref<number | null>(null)
const editingText = ref('')
const selected = ref<number[]>([])

const doc = computed(() => detail.value?.document ?? null)
const isIndexed = computed(() => doc.value?.status === 'indexed')
const hasPendingChunks = computed(() => chunks.value.some((c) => !c.is_confirmed))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [d, p, s, sm] = await Promise.all([
      docsApi.detail(docId),
      docsApi.pages(docId).catch(() => null),
      docsApi.strategies().catch(() => ({ items: [] as StrategyMeta[] })),
      docsApi.sampleIndex().catch(() => ({ items: [] as StrategySample[] })),
    ])
    detail.value = d
    chunks.value = d.chunks
    pages.value = p?.pages ?? []
    strategies.value = s.items
    sampleStrategies.value = new Set(sm.items.map((item) => item.strategy))
    previewStats.value = {
      count: d.chunks.length,
      total: d.chunks.reduce((sum, c) => sum + c.token_count, 0),
      avg: d.chunks.length
        ? Math.round(d.chunks.reduce((sum, c) => sum + c.token_count, 0) / d.chunks.length)
        : 0,
      strategy: '—',
    }
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function regenerate() {
  busy.value = 'chunk'
  error.value = ''
  notice.value = ''

  if (needsApi.value && !confirm('这个策略会真实调用模型接口，可能产生费用。继续？')) {
    busy.value = ''
    return
  }

  try {
    const result: PreviewResult = await docsApi.preview(docId, { ...options, save: true })
    chunks.value = result.chunks
    previewStats.value = {
      count: result.count,
      total: result.total_tokens,
      avg: result.avg_tokens,
      strategy: result.strategy,
    }
    notice.value = result.warnings.length ? result.warnings.join('；') : ''
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

function startEdit(chunk: ChunkItem) {
  if (chunk.is_confirmed) {
    notice.value = '这块已经入库，改动会让文字和向量不一致，请先用「重建索引」流程。'
    return
  }
  editingId.value = chunk.id
  editingText.value = chunk.content
}

async function saveEdit() {
  if (editingId.value === null) return
  busy.value = 'edit'
  try {
    const updated = await docsApi.updateChunk(editingId.value, editingText.value)
    const index = chunks.value.findIndex((c) => c.id === editingId.value)
    if (index >= 0) chunks.value[index] = updated
    editingId.value = null
    editingText.value = ''
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

function cancelEdit() {
  editingId.value = null
  editingText.value = ''
}

async function removeChunk(chunk: ChunkItem) {
  if (chunk.id === null) return
  if (chunk.is_confirmed) {
    notice.value = '已入库的分块不能单独删除，请重新生成预览。'
    return
  }
  if (!confirm('删除这一块？')) return
  busy.value = 'del'
  try {
    await docsApi.deleteChunk(chunk.id)
    chunks.value = chunks.value.filter((c) => c.id !== chunk.id)
    selected.value = selected.value.filter((id) => id !== chunk.id)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

function toggleSelect(chunk: ChunkItem) {
  if (chunk.id === null || chunk.is_confirmed) return
  const idx = selected.value.indexOf(chunk.id)
  if (idx >= 0) selected.value.splice(idx, 1)
  else selected.value.push(chunk.id)
}

async function mergeSelected() {
  if (selected.value.length < 2) {
    notice.value = '至少选中两块相邻的分块才能合并。'
    return
  }
  busy.value = 'merge'
  try {
    await docsApi.mergeChunks(selected.value)
    selected.value = []
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function indexNow() {
  busy.value = 'index'
  error.value = ''
  notice.value = ''
  try {
    const result = await docsApi.index(docId)
    if (result.deduplicated) {
      notice.value = '这篇文档已经在入库队列里了，进度见下方。'
    }
    followJob(result.job)
  } catch (e) {
    error.value = (e as Error).message
    busy.value = ''
  }
}

async function reindex() {
  if (!confirm('重建索引会用当前向量模型重新计算整篇文档，可能产生费用。继续？')) return
  busy.value = 'index'
  error.value = ''
  notice.value = ''
  try {
    const result = await docsApi.reindex(docId)
    if (result.deduplicated) notice.value = '重建任务已在队列中。'
    followJob(result.job)
  } catch (e) {
    error.value = (e as Error).message
    busy.value = ''
  }
}

/** 手动重试失败的任务。 */
async function retryJob() {
  const job = activeJob.value
  if (!job) return
  busy.value = 'index'
  error.value = ''
  try {
    const result = await jobsApi.retry(job.id)
    notice.value = '已重新排入队列。'
    followJob(result.job)
  } catch (e) {
    error.value = (e as Error).message
    busy.value = ''
  }
}

/** 手动重打标签；进度同样由全局任务 store 跟踪。 */
async function retag() {
  busy.value = 'tag'
  error.value = ''
  notice.value = ''
  try {
    const result = await tagsApi.tag(docId)
    followJob(result.job)
  } catch (e) {
    error.value = (e as Error).message
    busy.value = ''
  }
}

async function reparse() {
  busy.value = 'parse'
  try {
    await docsApi.reparse(docId, 'local')
    notice.value = '已重新解析'
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

/**
 * 进入本页时把还在跑的任务接上。
 *
 * 全局 store 会继续轮询（切页也没断），这里只需要把页面上要显示的那条任务
 * 指过去 —— 回来就能看到最新进度，而不是一篇停在旧状态的文档。
 */
onMounted(async () => {
  await load()
  await jobs.sync(docId)
  const running = jobs.activeFor(docId)
  if (running) shownJobId.value = running.id
})
</script>

<template>
  <div class="page">
    <div class="crumb">
      <button class="btn btn-sm btn-quiet" @click="router.push('/documents')">← 文档库</button>
    </div>

    <header v-if="doc" class="head">
      <div class="head-main">
        <h2 class="title">{{ doc.filename }}</h2>
        <div class="meta">
          <span class="tag" :class="STATUS_TONE[doc.status]">{{ STATUS_LABEL[doc.status] }}</span>
          <span>{{ doc.page_count }} 段 · {{ doc.char_count }} 字 · {{ formatSize(doc.size) }}</span>
          <span class="mono">解析方式 {{ doc.parse_mode === 'llm' ? '智能' : '本地' }}</span>
        </div>
      </div>
      <div class="head-actions">
        <button class="btn btn-sm btn-quiet" :disabled="!!busy || jobRunning" @click="reparse">
          重新解析
        </button>
        <button v-if="isIndexed" class="btn btn-sm" :disabled="!!busy || jobRunning" @click="reindex">
          重建索引
        </button>
        <button
          v-else
          class="btn btn-sm btn-primary"
          :disabled="!!busy || jobRunning || !hasPendingChunks"
          @click="indexNow"
        >
          {{ busy === 'index' ? '入库中…' : '确认入库' }}
        </button>
      </div>
    </header>

    <p v-if="error" class="notice bad">{{ error }}</p>
    <p v-if="notice" class="notice warn">{{ notice }}</p>

    <!-- 后台任务进度：入库/重建都是异步的 -->
    <section v-if="activeJob" class="jobbar card">
      <div class="jobbar-head">
        <span class="jobbar-label">
          {{ JOB_LABEL[activeJob.type] ?? activeJob.type }}
          <span v-if="activeJob.status === 'pending'" class="jobbar-state">排队中</span>
          <span v-else-if="activeJob.status === 'running'" class="jobbar-state">
            {{ activeJob.progress }}%
          </span>
          <span v-else-if="activeJob.status === 'done'" class="jobbar-state ok">已完成</span>
          <span v-else class="jobbar-state bad">失败</span>
        </span>
        <button
          v-if="activeJob.status === 'failed'"
          class="btn btn-sm btn-quiet"
          :disabled="!!busy || jobRunning"
          @click="retryJob"
        >
          重试
        </button>
        <button v-else-if="!isActive(activeJob)" class="btn btn-sm btn-quiet" @click="shownJobId = null">
          知道了
        </button>
      </div>
      <div class="jobbar-track">
        <div
          class="jobbar-fill"
          :class="{ ok: activeJob.status === 'done', bad: activeJob.status === 'failed' }"
          :style="{ width: `${activeJob.status === 'pending' ? 3 : activeJob.progress}%` }"
        />
      </div>
      <p v-if="activeJob.status === 'failed' && activeJob.error_msg" class="jobbar-msg">
        {{ activeJob.error_msg }}
      </p>
    </section>

    <!-- 摘要 / 分类 / 标签（由打标签任务生成） -->
    <section v-if="doc && (doc.summary || doc.tags.length || doc.category)" class="tagbox card">
      <div class="tagbox-head">
        <span class="tagbox-label">文档摘要</span>
        <button class="btn btn-sm btn-quiet" :disabled="!!busy || jobRunning" @click="retag">
          {{ busy === 'tag' || tagJobRunning ? '生成中…' : '重打标签' }}
        </button>
      </div>
      <p v-if="doc.summary" class="tagbox-summary">{{ doc.summary }}</p>
      <p v-else class="tagbox-summary muted">还没有摘要</p>
      <div class="tagbox-meta">
        <span v-if="doc.category" class="tag tag-cat">{{ doc.category }}</span>
        <span v-for="name in doc.tags" :key="name" class="chip">{{ name }}</span>
      </div>
    </section>

    <!-- 过滤：分块之前的清洗 -->
    <section class="filter card">
      <div class="filter-head">
        <span class="section-title">过滤</span>
        <span class="hint-text">
          在切分之前先决定哪些文字值得留下。删掉的都是整行，不改动行内一个字。
        </span>
      </div>

      <div class="filter-body">
        <label class="filter-opt">
          <input v-model="options.filter_raw" type="checkbox" />
          <span class="filter-name">原文过滤</span>
          <span class="filter-desc">
            规则清洗：页眉页脚、页码、孤儿碎片行、乱码。纯本地计算，毫秒级。
          </span>
        </label>
        <label class="filter-opt">
          <input v-model="options.filter_ai" type="checkbox" />
          <span class="filter-name">智能过滤</span>
          <span class="filter-desc">
            让模型判断一次哪些行没有检索价值（目录、版权声明、广告封面）。
            <b>只调 1 次接口</b>，与文档长度无关。
          </span>
        </label>
        <button
          class="btn btn-sm filter-preview-btn"
          :disabled="filterLoading || !filtering"
          @click="runFilterPreview"
        >
          {{ filterLoading ? '过滤中…' : '预览过滤效果' }}
        </button>
      </div>

      <p v-if="filterError" class="err">{{ filterError }}</p>

      <div v-if="filterPreview" class="filter-result">
        <div class="filter-stats">
          <span>{{ filterPreview.raw_chars }} 字</span>
          <span class="arrow">→</span>
          <span class="kept">{{ filterPreview.kept_chars }} 字</span>
          <span v-if="filterPreview.removed_count" class="tag tag-warn">
            删除 {{ filterPreview.removed_count }} 行
          </span>
          <span v-else class="tag tag-ok">没有删除任何行</span>
        </div>
        <ul v-if="filterPreview.removals.length" class="removal-list">
          <li v-for="item in filterPreview.removals" :key="`${item.page_no}-${item.line_no}`">
            <span class="removal-tag">{{ item.reason }}</span>
            <span class="removal-pos num">第{{ item.page_no }}段/第{{ item.line_no }}行</span>
            <span class="removal-text">{{ item.text }}</span>
          </li>
        </ul>
        <p v-for="w in filterPreview.warnings" :key="w" class="hint-text filter-warn">{{ w }}</p>
      </div>
    </section>

    <!-- 切分参数 -->
    <section class="toolbar card">
      <div class="tool tool-wide">
        <label>切分策略</label>
        <select v-model="options.strategy">
          <option v-for="item in strategies" :key="item.value" :value="item.value">
            {{ item.label }}{{ item.needs_api ? '（调用接口）' : '' }}
          </option>
        </select>
      </div>
      <button
        v-if="hasSample"
        class="btn btn-sm btn-quiet sample-btn"
        title="看看这种策略适合什么样的文档"
        @click="openSample"
      >
        看样例
      </button>
      <div class="tool">
        <label>块大小 <span class="num">{{ options.chunk_size }}</span> token</label>
        <input v-model.number="options.chunk_size" type="range" min="128" max="1024" step="32" />
      </div>
      <div class="tool">
        <label>重叠 <span class="num">{{ options.overlap }}</span> token</label>
        <input v-model.number="options.overlap" type="range" min="0" max="256" step="16" />
      </div>
      <div v-if="showThreshold" class="tool">
        <label>断开阈值 <span class="num">{{ options.semantic_threshold.toFixed(2) }}</span></label>
        <input
          v-model.number="options.semantic_threshold"
          type="range"
          min="0.2"
          max="0.9"
          step="0.05"
        />
      </div>
      <button class="btn btn-sm" :disabled="!!busy" @click="regenerate">
        {{ busy === 'chunk' ? '切分中…' : '重新切分' }}
      </button>
    </section>

    <p v-if="currentStrategy" class="strategy-hint">
      {{ currentStrategy.hint }}
      <span v-if="needsApi" class="tag tag-warn">会产生接口费用</span>
      <span v-if="filtering" class="tag tag-ok">已开启过滤</span>
    </p>

    <div v-if="previewStats" class="summary">
      <span>{{ previewStats.count }} 块</span>
      <span>共 {{ previewStats.total }} token</span>
      <span>平均 {{ previewStats.avg }} token/块</span>
      <span v-if="selected.length" class="tag tag-accent">已选 {{ selected.length }} 块</span>
      <button v-if="selected.length" class="btn btn-sm" @click="mergeSelected">合并所选</button>
    </div>

    <!-- 左原文 右分块 -->
    <section class="split">
      <div class="pane">
        <div class="pane-head">
          <span class="section-title">原文</span>
          <span class="hint-text">{{ pages.length }} 段</span>
        </div>
        <div class="pane-body">
          <article v-for="page in pages" :key="page.page_no" class="page-block">
            <div class="page-no">第 {{ page.page_no }} 段</div>
            <pre class="page-text">{{ page.text }}</pre>
          </article>
          <p v-if="!pages.length" class="empty">没有原文，请先解析。</p>
        </div>
      </div>

      <div class="pane">
        <div class="pane-head">
          <span class="section-title">分块</span>
          <span class="hint-text">{{ chunks.length }} 块</span>
        </div>
        <div class="pane-body">
          <div
            v-for="chunk in chunks"
            :key="chunk.id ?? chunk.seq"
            class="chunk"
            :class="{ picked: chunk.id !== null && selected.includes(chunk.id) }"
          >
            <div class="chunk-head">
              <label class="chunk-check">
                <input
                  type="checkbox"
                  :disabled="chunk.is_confirmed === 1"
                  :checked="chunk.id !== null && selected.includes(chunk.id)"
                  @change="toggleSelect(chunk)"
                />
                <span class="chunk-seq">#{{ chunk.seq + 1 }}</span>
              </label>
              <span class="chunk-meta">
                第 {{ chunk.page_no }} 段 · {{ chunk.token_count }} token
                <span v-if="chunk.is_confirmed" class="tag tag-ok">已入库</span>
              </span>
              <span class="chunk-actions">
                <button class="btn btn-sm btn-quiet" @click="startEdit(chunk)">编辑</button>
                <button
                  class="btn btn-sm btn-quiet btn-danger"
                  :disabled="chunk.is_confirmed === 1"
                  @click="removeChunk(chunk)"
                >
                  删除
                </button>
              </span>
            </div>

            <textarea
              v-if="editingId === chunk.id"
              v-model="editingText"
              class="chunk-edit"
              rows="6"
            />
            <pre v-else class="chunk-text">{{ chunk.content }}</pre>

            <div v-if="editingId === chunk.id" class="chunk-edit-actions">
              <button class="btn btn-sm btn-primary" :disabled="busy === 'edit'" @click="saveEdit">
                保存
              </button>
              <button class="btn btn-sm btn-quiet" @click="cancelEdit">取消</button>
            </div>
          </div>

          <p v-if="!chunks.length && !loading" class="empty">
            还没有分块。调整上面的参数后点「重新切分」。
          </p>
        </div>
      </div>
    </section>
  </div>

  <StrategySampleModal
    v-if="sampleOpen"
    :strategy="sampleStrategy"
    :label="currentStrategy?.label ?? ''"
    :chunk-size="options.chunk_size"
    :overlap="options.overlap"
    @close="sampleOpen = false"
  />
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  max-width: 1400px;
}

.crumb {
  display: flex;
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--line);
}
.title {
  font-size: 17px;
  margin-bottom: 6px;
  word-break: break-all;
}
.meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--text-2);
}
.mono {
  font-family: var(--font-mono);
  font-size: 11.5px;
}
.head-actions {
  display: flex;
  gap: 6px;
  flex: none;
}

.notice {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--r-md);
  font-size: 12.5px;
  line-height: 1.6;
}
.notice.warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.notice.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

/* 摘要 / 分类 / 标签 */
.tagbox {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
}
.tagbox-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.tagbox-label {
  font-size: 12px;
  color: var(--text-2);
}
.tagbox-summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-0);
}
.tagbox-summary.muted {
  color: var(--text-2);
}
.tagbox-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
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

/* 后台任务进度条 */
.jobbar {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
}
.jobbar-head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
}
.jobbar-label {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  font-size: 12.5px;
  color: var(--text-0);
}
.jobbar-state {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-1);
}
.jobbar-state.ok {
  color: var(--ok);
}
.jobbar-state.bad {
  color: var(--danger);
}
.jobbar-head .btn {
  margin-left: auto;
}
.jobbar-track {
  height: 4px;
  border-radius: 2px;
  background: var(--bg-3);
  overflow: hidden;
}
.jobbar-fill {
  height: 100%;
  background: var(--accent);
  transition: width var(--dur) var(--ease);
}
.jobbar-fill.ok {
  background: var(--ok);
}
.jobbar-fill.bad {
  background: var(--danger);
}
.jobbar-msg {
  margin: 0;
  font-size: 12px;
  color: var(--danger);
  line-height: 1.55;
}

/* 过滤面板 */
.filter {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-4);
}

.filter-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-2);
}

.filter-body {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: var(--sp-4);
}

.filter-opt {
  display: grid;
  grid-template-columns: auto 1fr;
  grid-template-rows: auto auto;
  column-gap: 7px;
  row-gap: 1px;
  max-width: 380px;
  cursor: pointer;
}
.filter-opt input {
  grid-row: span 2;
  align-self: center;
  accent-color: var(--accent);
  cursor: pointer;
}
.filter-name {
  font-size: 12.5px;
  color: var(--text-0);
  font-weight: 600;
}
.filter-desc {
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
}
.filter-desc b {
  color: var(--accent);
  font-weight: 600;
}

.filter-preview-btn {
  margin-left: auto;
  flex: none;
}

.filter-result {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--line);
}

.filter-stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  font-size: 12px;
  color: var(--text-1);
}
.filter-stats .arrow {
  color: var(--text-2);
}
.filter-stats .kept {
  color: var(--text-0);
  font-weight: 600;
}

.removal-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
  max-height: 220px;
  overflow-y: auto;
}
.removal-list li {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 11.5px;
  line-height: 1.6;
}

.removal-tag {
  flex: none;
  padding: 0 5px;
  font-size: 10px;
  line-height: 16px;
  color: var(--warn);
  background: var(--warn-soft);
  border-radius: 3px;
}
.removal-pos {
  flex: none;
  color: var(--text-2);
  font-size: 10.5px;
}
.removal-text {
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.filter-warn {
  margin: 0;
}

/* 工具条 */
.toolbar {
  display: flex;
  align-items: flex-end;
  flex-wrap: wrap;
  gap: var(--sp-4);
  padding: var(--sp-3) var(--sp-4);
}
.tool {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 150px;
}
.tool-wide {
  min-width: 200px;
}
.tool > label {
  font-size: 12px;
  color: var(--text-1);
}
.tool select {
  height: 30px;
}
input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
  height: 4px;
}
.sample-btn {
  align-self: flex-end;
  margin-bottom: 1px;
}

.strategy-hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.5;
}

.summary {
  display: flex;
  align-items: center;
  gap: var(--sp-4);
  font-size: 12px;
  color: var(--text-2);
  font-family: var(--font-mono);
}

/* 双栏 */
.split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
  align-items: start;
}
.pane {
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--bg-1);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px var(--sp-4);
  border-bottom: 1px solid var(--line);
  background: var(--bg-2);
}
.pane-body {
  padding: var(--sp-3) var(--sp-4);
  max-height: calc(100vh - 420px);
  min-height: 240px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}

.page-block {
  border-left: 2px solid var(--line-strong);
  padding-left: 10px;
}
.page-no {
  font-size: 11px;
  color: var(--text-2);
  margin-bottom: 3px;
  font-family: var(--font-mono);
}
.page-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.75;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font);
}

.chunk {
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--bg-0);
  padding: 10px 12px;
  transition: border-color var(--dur) var(--ease);
}
.chunk.picked {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.chunk-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}
.chunk-check {
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.chunk-check input {
  accent-color: var(--accent);
}
.chunk-seq {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-2);
}
.chunk-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
  margin-right: auto;
}
.chunk-actions {
  display: flex;
  gap: 2px;
}
.chunk-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font);
}
.chunk-edit {
  width: 100%;
  font-family: var(--font);
  font-size: 12.5px;
  line-height: 1.7;
}
.chunk-edit-actions {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}

.empty {
  margin: 0;
  padding: var(--sp-4);
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
}

@media (max-width: 1080px) {
  .split {
    grid-template-columns: 1fr;
  }
  .pane-body {
    max-height: 380px;
  }
}
</style>