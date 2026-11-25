<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  chatApi,
  defaultOptions,
  streamChat,
  type ChatOptions,
  type Citation,
  type Conversation,
  type MessageItem,
  type ModelOptions,
  type RetrievalDebugData,
  type TraceStep,
} from '@/api/chat'
import { renderMarkdown } from '@/api/markdown'
import TracePanel from '@/components/TracePanel.vue'

const conversations = ref<Conversation[]>([])
const activeId = ref<number | null>(null)
const messages = ref<MessageItem[]>([])
const loading = ref(false)
const error = ref('')

const draft = ref('')
const streaming = ref(false)
const streamText = ref('')
const liveChunks = ref<Citation[]>([])
const liveInfo = reactive({ expanded: false, rewritten: '', lowScore: false })

// 轨迹
const traceSteps = ref<TraceStep[]>([])
const liveModels = ref<{
  llm?: { name: string; model: string } | null
  embedding?: { name: string; model: string } | null
  rerank?: { name: string; model: string } | null
} | null>(null)

// 轨迹面板：可收起、可拖宽（上限为对话区一半）
const TRACE_MIN = 200
const TRACE_DEFAULT = 280
const traceWidth = ref(TRACE_DEFAULT)
const traceCollapsed = ref(false)
const dragging = ref(false)

const abortController = ref<AbortController | null>(null)

const options = reactive<ChatOptions>({ ...defaultOptions })
const showOptions = ref(false)

// 模型选择
const modelOptions = ref<ModelOptions | null>(null)
const showModels = ref(false)
const modelsLoading = ref(false)

// 引用弹层
const openCitation = ref<Citation | null>(null)
// 检索详情
const debugData = ref<RetrievalDebugData | null>(null)
// 提示词优化
const optimizing = ref(false)
const suggestion = ref<{ optimized: string; rationale: string } | null>(null)

const listEl = ref<HTMLElement | null>(null)

const isEmpty = computed(() => !messages.value.length && !streamText.value)
const activeConversation = computed(
  () => conversations.value.find((conv) => conv.id === activeId.value) ?? null,
)

/** 整个聊天容器的引用：拖拽与上限都以它为基准（需先声明再被引用）。 */
const chatRootEl = ref<HTMLElement | null>(null)

/** 轨迹面板宽度上限：chat 容器总宽的一半（不随拖拽变化，所以稳定）。 */
const maxTraceWidth = computed(() => {
  const total = chatRootEl.value?.clientWidth ?? 900
  return Math.max(TRACE_MIN, Math.floor(total / 2))
})

// ---------------- 拖拽 ------------
/** 拖拽起点快照：用 delta 计算宽度，避免"面板变宽→基准变化→互相追逐"。 */
let dragStartX = 0
let dragStartWidth = TRACE_DEFAULT

function startDrag(event: MouseEvent) {
  event.preventDefault()
  dragging.value = true
  dragStartX = event.clientX
  dragStartWidth = traceWidth.value
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('mousemove', onDrag)
  window.addEventListener('mouseup', stopDrag)
}

function onDrag(event: MouseEvent) {
  if (!dragging.value) return
  // 鼠标往左移动（clientX 变小）→ delta 为负 → 面板变宽
  const delta = event.clientX - dragStartX
  const next = dragStartWidth - delta
  traceWidth.value = Math.min(maxTraceWidth.value, Math.max(TRACE_MIN, Math.round(next)))
}

function stopDrag() {
  dragging.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('mousemove', onDrag)
  window.removeEventListener('mouseup', stopDrag)
}

onBeforeUnmount(() => {
  stopDrag()
  abortController.value?.abort()
})

async function loadConversations() {
  conversations.value = await chatApi.list()
  if (!activeId.value && conversations.value.length) {
    await selectConversation(conversations.value[0].id)
  }
}

async function openModels() {
  showModels.value = true
  if (modelOptions.value) return
  modelsLoading.value = true
  try {
    modelOptions.value = await chatApi.modelOptions()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    modelsLoading.value = false
  }
}

/** 从历史消息里还原某条回答的轨迹（retrieval_json 里存了 steps）。 */
function traceOf(msg: MessageItem): TraceStep[] {
  try {
    const data = JSON.parse(msg.retrieval_json || '{}')
    return Array.isArray(data.steps) ? (data.steps as TraceStep[]) : []
  } catch {
    return []
  }
}

async function selectMessageTrace(msg: MessageItem) {
  const steps = traceOf(msg)
  if (steps.length) {
    traceSteps.value = steps
    liveInfo.expanded = Boolean(JSON.parse(msg.retrieval_json || '{}').expanded)
    liveInfo.rewritten = msg.rewritten_query
  }
}

/** 保存会话的模型选择。 */
async function saveModels(payload: {
  llm_provider_id: number | null
  embedding_provider_id: number | null
  rerank_provider_id: number | null
  use_rerank: boolean
}) {
  if (!activeId.value) return
  try {
    const updated = await chatApi.updateModels(activeId.value, payload)
    const index = conversations.value.findIndex((conv) => conv.id === updated.id)
    if (index >= 0) conversations.value[index] = updated
    showModels.value = false
  } catch (e) {
    error.value = (e as Error).message
  }
}

function modelName(kind: 'llm' | 'embedding' | 'rerank', id: number | null | undefined): string {
  if (!modelOptions.value || id == null) return '跟随默认'
  const found = modelOptions.value[kind].find((item) => item.id === id)
  return found ? found.model : '跟随默认'
}

async function selectConversation(id: number) {
  activeId.value = id
  loading.value = true
  error.value = ''
  try {
    const detail = await chatApi.detail(id)
    messages.value = detail.messages
    await scrollToBottom()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function newConversation() {
  const conv = await chatApi.create(1, '')
  conversations.value.unshift(conv)
  activeId.value = conv.id
  messages.value = []
  draft.value = ''
  streamText.value = ''
}

async function removeConversation(conv: Conversation) {
  if (!confirm(`删除会话「${conv.title}」？`)) return
  await chatApi.remove(conv.id)
  conversations.value = conversations.value.filter((c) => c.id !== conv.id)
  if (activeId.value === conv.id) {
    activeId.value = null
    messages.value = []
    if (conversations.value.length) await selectConversation(conversations.value[0].id)
  }
}

async function send() {
  const question = draft.value.trim()
  if (!question || streaming.value || !activeId.value) return

  draft.value = ''
  suggestion.value = null
  streaming.value = true
  streamText.value = ''
  liveChunks.value = []
  liveInfo.expanded = false
  liveInfo.rewritten = ''
  liveInfo.lowScore = false
  traceSteps.value = []
  liveModels.value = null
  error.value = ''

  // 先把用户消息显示出来（后端也会落库，返回后重新拉取）
  messages.value.push({
    id: -Date.now(),
    conversation_id: activeId.value,
    role: 'user',
    content: question,
    rewritten_query: '',
    citations_json: '[]',
    retrieval_json: '{}',
    token_usage: 0,
    latency_ms: 0,
    created_at: new Date().toISOString(),
  })
  await scrollToBottom()

  abortController.value = new AbortController()

  await streamChat(
    { conversation_id: activeId.value, question, options: { ...options } },
    {
      onStarted(data) {
        liveModels.value = data.models
        if (data.note) error.value = ''
      },
      onStep(step) {
        traceSteps.value = [...traceSteps.value, step]
      },
      onRetrieval(data) {
        liveChunks.value = data.chunks.map((chunk) => ({
          n: chunk.n,
          chunk_id: chunk.chunk_id,
          document_id: 0,
          doc_name: chunk.doc_name,
          page_no: chunk.page_no,
          snippet: chunk.snippet,
          score: chunk.score,
        }))
        liveInfo.expanded = data.expanded
        liveInfo.rewritten = data.rewritten
        liveInfo.lowScore = data.low_score
      },
      onDelta(text) {
        streamText.value += text
        scrollToBottom()
      },
      onDone(data) {
        streaming.value = false
        if (data.steps?.length) traceSteps.value = data.steps
      },
      onError(message) {
        error.value = message
        streaming.value = false
      },
    },
    abortController.value.signal,
  )

  streaming.value = false
  streamText.value = ''

  // 重新拉取，拿到带 id 的正式消息（含引用与检索详情）
  if (activeId.value) await selectConversation(activeId.value)
  await loadConversations()
}

function stop() {
  abortController.value?.abort()
  streaming.value = false
}

async function optimize() {
  if (!draft.value.trim() || optimizing.value) return
  optimizing.value = true
  suggestion.value = null
  try {
    suggestion.value = await chatApi.optimize(
      draft.value,
      messages.value
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .slice(-6)
        .map((m) => ({ role: m.role, content: m.content })),
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    optimizing.value = false
  }
}

function adoptSuggestion() {
  if (suggestion.value) {
    draft.value = suggestion.value.optimized
    suggestion.value = null
  }
}

function citationsOf(msg: MessageItem): Citation[] {
  try {
    return JSON.parse(msg.citations_json || '[]')
  } catch {
    return []
  }
}

async function showDebug(msg: MessageItem) {
  try {
    const result = await chatApi.retrieval(msg.id)
    debugData.value = result.retrieval
    // 同时把这条回答的轨迹回填到右侧面板，方便对照"当次检索"与"步骤"
    await selectMessageTrace(msg)
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function exportMarkdown() {
  if (!activeId.value) return
  const data = await chatApi.exportMd(activeId.value)
  const blob = new Blob([data.markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = data.filename
  link.click()
  URL.revokeObjectURL(url)
}

/** 点击正文里的角标 → 打开引用卡片。 */
function onContentClick(event: MouseEvent) {
  const target = event.target as HTMLElement
  if (!target.classList.contains('cite')) return
  const num = Number(target.dataset.cite)
  const msgEl = target.closest('[data-msg-id]') as HTMLElement | null
  if (!msgEl) return
  const msgId = Number(msgEl.dataset.msgId)
  const msg = messages.value.find((m) => m.id === msgId)
  if (!msg) return
  const found = citationsOf(msg).find((item) => item.n === num)
  if (found) openCitation.value = found
}

async function scrollToBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

function timeOf(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(async () => {
  await loadConversations()
  if (!conversations.value.length) await newConversation()
})
</script>

<template>
  <div ref="chatRootEl" class="chat">
    <!-- 会话列表 -->
    <aside class="side">
      <button class="btn btn-sm new-btn" @click="newConversation">新会话</button>
      <div class="conv-list">
        <div
          v-for="conv in conversations"
          :key="conv.id"
          class="conv"
          :class="{ active: conv.id === activeId }"
          @click="selectConversation(conv.id)"
        >
          <span class="conv-title">{{ conv.title }}</span>
          <button class="conv-del" title="删除" @click.stop="removeConversation(conv)">×</button>
        </div>
      </div>
      <div class="side-foot">
        <button class="btn btn-sm btn-quiet" @click="openModels">模型选择</button>
        <button class="btn btn-sm btn-quiet" @click="showOptions = !showOptions">
          {{ showOptions ? '收起检索参数' : '检索参数' }}
        </button>
      </div>
    </aside>

    <!-- 主对话区 -->
    <section class="main">
      <div ref="listEl" class="messages">
        <div v-if="isEmpty" class="welcome">
          <h2>问点什么</h2>
          <p>答案只来自你上传的文档，每句话后面的角标可以点开看原文出处。</p>
        </div>

        <div
          v-for="msg in messages"
          :key="msg.id"
          class="msg"
          :class="msg.role"
          :data-msg-id="msg.id"
        >
          <div class="msg-head">
            <span class="who">{{ msg.role === 'user' ? '我' : '助手' }}</span>
            <span class="meta">{{ timeOf(msg.created_at) }}</span>
            <span v-if="msg.latency_ms" class="meta">{{ (msg.latency_ms / 1000).toFixed(1) }}s</span>
            <span v-if="msg.token_usage" class="meta">{{ msg.token_usage }} tok</span>
          </div>

          <p v-if="msg.rewritten_query" class="rewritten">
            检索改写：{{ msg.rewritten_query }}
          </p>

          <div
            v-if="msg.role === 'assistant'"
            class="content markdown"
            @click="onContentClick"
            v-html="renderMarkdown(msg.content)"
          />
          <div v-else class="content">{{ msg.content }}</div>

          <div v-if="msg.role === 'assistant'" class="msg-foot">
            <button class="btn btn-sm btn-quiet" @click="showDebug(msg)">检索详情</button>
            <span
              v-for="cite in citationsOf(msg)"
              :key="cite.n"
              class="cite-chip"
              @click="openCitation = cite"
            >
              [{{ cite.n }}] {{ cite.doc_name }} 第{{ cite.page_no }}段
            </span>
          </div>
        </div>

        <!-- 流式中 -->
        <div v-if="streaming" class="msg assistant">
          <div class="msg-head">
            <span class="who">助手</span>
            <span class="meta">生成中…</span>
          </div>
          <p v-if="liveInfo.rewritten" class="rewritten">检索改写：{{ liveInfo.rewritten }}</p>
          <p v-if="liveInfo.expanded" class="notice-inline">首次检索分数偏低，已自动扩大召回重试</p>
          <div v-if="liveChunks.length" class="live-chunks">
            参考 {{ liveChunks.length }} 段：
            <span v-for="chunk in liveChunks" :key="chunk.n" class="cite-chip">
              [{{ chunk.n }}] {{ chunk.doc_name }}
            </span>
          </div>
          <div
            v-if="streamText"
            class="content markdown"
            v-html="renderMarkdown(streamText)"
          />
          <p v-else class="typing">正在检索并组织答案…</p>
        </div>

        <p v-if="error" class="error">{{ error }}</p>
      </div>

      <!-- 输入区 -->
      <div class="composer">
        <div v-if="suggestion" class="suggest">
          <div class="suggest-head">
            <span class="section-title">优化建议</span>
            <span class="hint-text">{{ suggestion.rationale }}</span>
          </div>
          <p class="suggest-text">{{ suggestion.optimized }}</p>
          <div class="suggest-actions">
            <button class="btn btn-sm btn-primary" @click="adoptSuggestion">采用</button>
            <button class="btn btn-sm btn-quiet" @click="suggestion = null">忽略</button>
          </div>
        </div>

        <div class="input-wrap">
          <textarea
            v-model="draft"
            rows="2"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="send"
          />
          <div class="input-actions">
            <button
              class="btn btn-sm btn-quiet"
              :disabled="!draft.trim() || optimizing"
              @click="optimize"
            >
              {{ optimizing ? '优化中…' : '优化提问' }}
            </button>
            <button v-if="streaming" class="btn btn-sm" @click="stop">停止</button>
            <button
              v-else
              class="btn btn-sm btn-primary"
              :disabled="!draft.trim()"
              @click="send"
            >
              发送
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- 右侧轨迹面板 -->
    <div
      v-if="!traceCollapsed"
      class="trace-resizer"
      :class="{ active: dragging }"
      title="拖动调整宽度，最多到对话区的一半"
      @mousedown="startDrag"
    />
    <TracePanel
      v-if="!traceCollapsed"
      class="trace-pane"
      :style="{ width: traceWidth + 'px' }"
      :steps="traceSteps"
      :streaming="streaming"
      :models="liveModels"
      :expanded="liveInfo.expanded"
      :rewritten="liveInfo.rewritten"
      @collapse="traceCollapsed = true"
    />
    <button
      v-else
      class="trace-open"
      title="展开执行过程面板"
      @click="traceCollapsed = false"
    >
      <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true">
        <path
          d="M6 3.5 10.5 8 6 12.5"
          fill="none"
          stroke="currentColor"
          stroke-width="1.6"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <span>过程</span>
    </button>


    <!-- 检索参数抽屉 -->
    <Teleport to="body">
      <div v-if="showOptions" class="overlay right" @click.self="showOptions = false">
        <div class="drawer">
          <header class="drawer-head">
            <h3>检索参数</h3>
            <button class="btn btn-sm btn-quiet" @click="showOptions = false">关闭</button>
          </header>
          <div class="drawer-body">
            <label class="row">
              <span>启用重排</span>
              <input v-model="options.use_rerank" type="checkbox" />
            </label>
            <div class="row-col">
              <span>向量召回 {{ options.vector_top_n }}</span>
              <input v-model.number="options.vector_top_n" type="range" min="5" max="60" step="5" />
            </div>
            <div class="row-col">
              <span>BM25 召回 {{ options.bm25_top_n }}</span>
              <input v-model.number="options.bm25_top_n" type="range" min="5" max="60" step="5" />
            </div>
            <div class="row-col">
              <span>精排取前 {{ options.rerank_top_n }}</span>
              <input v-model.number="options.rerank_top_n" type="range" min="1" max="10" step="1" />
            </div>
            <div class="row-col">
              <span>RRF k = {{ options.rrf_k }}</span>
              <input v-model.number="options.rrf_k" type="range" min="10" max="200" step="10" />
            </div>
            <div class="row-col">
              <span>低分阈值 {{ options.score_threshold.toFixed(2) }}</span>
              <input
                v-model.number="options.score_threshold"
                type="range"
                min="0.1"
                max="0.8"
                step="0.05"
              />
            </div>
            <div class="row-col">
              <span>查询改写</span>
              <select v-model="options.rewrite">
                <option value="off">关闭（默认）</option>
                <option value="rewrite">追问改写</option>
                <option value="hyde">HyDE</option>
              </select>
            </div>
            <label class="row">
              <span>低分时扩召回重试</span>
              <input v-model="options.expand_retry" type="checkbox" />
            </label>
            <p class="drawer-hint">
              阈值判断用的是向量相似度：实测无关查询约 0.29、相关约 0.63，所以 0.35 是个合理下限。
            </p>
            <button class="btn btn-sm" @click="exportMarkdown">导出本会话 Markdown</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 模型选择抽屉 -->
    <Teleport to="body">
      <div v-if="showModels" class="overlay right" @click.self="showModels = false">
        <div class="drawer">
          <header class="drawer-head">
            <h3>本会话使用的模型</h3>
            <button class="btn btn-sm btn-quiet" @click="showModels = false">关闭</button>
          </header>
          <div class="drawer-body">
            <p v-if="modelsLoading" class="hint-text">加载中…</p>
            <p v-else-if="!modelOptions" class="hint-text">没有取到模型列表。</p>

            <template v-else>
              <p class="drawer-hint">
                「跟随默认」表示用该类型里标记为默认的供应商。选择只影响本会话，
                历史对话仍按它当时用的模型解释。
              </p>

              <div class="model-group">
                <label>对话模型</label>
                <select
                  :value="activeConversation?.llm_provider_id ?? ''"
                  @change="
                    (e) =>
                      saveModels({
                        llm_provider_id: Number((e.target as HTMLSelectElement).value) || null,
                        embedding_provider_id: activeConversation?.embedding_provider_id ?? null,
                        rerank_provider_id: activeConversation?.rerank_provider_id ?? null,
                        use_rerank: activeConversation?.use_rerank ?? true,
                      })
                  "
                >
                  <option value="">跟随默认</option>
                  <option v-for="item in modelOptions.llm" :key="item.id" :value="item.id">
                    {{ item.name }} · {{ item.model }}{{ item.is_default ? '（默认）' : '' }}
                  </option>
                </select>
              </div>

              <div class="model-group">
                <label>向量模型</label>
                <select
                  :value="activeConversation?.embedding_provider_id ?? ''"
                  @change="
                    (e) =>
                      saveModels({
                        llm_provider_id: activeConversation?.llm_provider_id ?? null,
                        embedding_provider_id: Number((e.target as HTMLSelectElement).value) || null,
                        rerank_provider_id: activeConversation?.rerank_provider_id ?? null,
                        use_rerank: activeConversation?.use_rerank ?? true,
                      })
                  "
                >
                  <option value="">跟随默认</option>
                  <option v-for="item in modelOptions.embedding" :key="item.id" :value="item.id">
                    {{ item.name }} · {{ item.model }}{{ item.is_default ? '（默认）' : '' }}
                  </option>
                </select>
                <span class="hint-text">
                  注意：换向量模型会导致已入库向量与新查询维度不一致，检索会失效。
                </span>
              </div>

              <div class="model-group">
                <label>重排模型</label>
                <select
                  :value="activeConversation?.rerank_provider_id ?? ''"
                  @change="
                    (e) =>
                      saveModels({
                        llm_provider_id: activeConversation?.llm_provider_id ?? null,
                        embedding_provider_id: activeConversation?.embedding_provider_id ?? null,
                        rerank_provider_id: Number((e.target as HTMLSelectElement).value) || null,
                        use_rerank: activeConversation?.use_rerank ?? true,
                      })
                  "
                >
                  <option value="">跟随默认</option>
                  <option v-for="item in modelOptions.rerank" :key="item.id" :value="item.id">
                    {{ item.name }} · {{ item.model }}{{ item.is_default ? '（默认）' : '' }}
                  </option>
                </select>
              </div>

              <label class="row">
                <span>启用重排</span>
                <input
                  type="checkbox"
                  :checked="activeConversation?.use_rerank ?? true"
                  @change="
                    (e) =>
                      saveModels({
                        llm_provider_id: activeConversation?.llm_provider_id ?? null,
                        embedding_provider_id: activeConversation?.embedding_provider_id ?? null,
                        rerank_provider_id: activeConversation?.rerank_provider_id ?? null,
                        use_rerank: (e.target as HTMLInputElement).checked,
                      })
                  "
                />
              </label>

              <div class="model-summary">
                <div class="model-summary-row">
                  <span>对话</span>
                  <span class="mono">{{ modelName('llm', activeConversation?.llm_provider_id) }}</span>
                </div>
                <div class="model-summary-row">
                  <span>向量</span>
                  <span class="mono">{{ modelName('embedding', activeConversation?.embedding_provider_id) }}</span>
                </div>
                <div class="model-summary-row">
                  <span>重排</span>
                  <span class="mono">{{ modelName('rerank', activeConversation?.rerank_provider_id) }}</span>
                </div>
              </div>
            </template>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 引用卡片 -->
    <Teleport to="body">
      <div v-if="openCitation" class="overlay" @click.self="openCitation = null">
        <div class="cite-card">
          <header class="cite-head">
            <span class="section-title">
              [{{ openCitation.n }}] {{ openCitation.doc_name }} · 第
              {{ openCitation.page_no }} 段
            </span>
            <button class="btn btn-sm btn-quiet" @click="openCitation = null">关闭</button>
          </header>
          <p class="cite-score">相关度 {{ openCitation.score.toFixed(4) }}</p>
          <pre class="cite-text">{{ openCitation.snippet }}</pre>
        </div>
      </div>
    </Teleport>

    <!-- 检索详情 -->
    <Teleport to="body">
      <div v-if="debugData" class="overlay" @click.self="debugData = null">
        <div class="debug">
          <header class="debug-head">
            <div>
              <h3>检索详情</h3>
              <p class="hint-text">
                查询「{{ debugData.query }}」· 重排{{ debugData.used_rerank ? '已启用' : '未启用' }}·
                {{ debugData.expanded ? '扩召回重试过' : '一次命中' }}
              </p>
            </div>
            <button class="btn btn-sm btn-quiet" @click="debugData = null">关闭</button>
          </header>
          <div class="debug-body">
            <div class="debug-col">
              <div class="debug-col-head">向量召回 {{ debugData.vector.length }}</div>
              <div v-for="item in debugData.vector" :key="'v' + item.rank" class="debug-item">
                <span class="num">#{{ item.rank }}</span>
                <span class="score">{{ item.score.toFixed(4) }}</span>
                <span class="snip">{{ (item.snippet ?? '').slice(0, 40) }}</span>
              </div>
            </div>
            <div class="debug-col">
              <div class="debug-col-head">BM25 召回 {{ debugData.bm25.length }}</div>
              <div v-for="item in debugData.bm25" :key="'b' + item.rank" class="debug-item">
                <span class="num">#{{ item.rank }}</span>
                <span class="score">{{ item.score.toFixed(3) }}</span>
                <span class="snip">chunk {{ item.chunk_id }}</span>
              </div>
            </div>
            <div class="debug-col">
              <div class="debug-col-head">RRF 融合 {{ debugData.fused.length }}</div>
              <div v-for="item in debugData.fused" :key="'f' + item.rank" class="debug-item">
                <span class="num">#{{ item.rank }}</span>
                <span class="score">{{ item.score.toFixed(5) }}</span>
                <span class="snip">{{ (item.snippet ?? '').slice(0, 34) }}</span>
              </div>
            </div>
            <div class="debug-col">
              <div class="debug-col-head">
                精排后 {{ debugData.reranked.length }}
                <span v-if="debugData.rerank_error" class="tag tag-danger">重排失败已降级</span>
              </div>
              <div v-for="item in debugData.reranked" :key="'r' + item.rank" class="debug-item">
                <span class="num">#{{ item.rank }}</span>
                <span class="score">{{ item.score.toFixed(4) }}</span>
                <span class="snip">{{ (item.snippet ?? '').slice(0, 34) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
/* 三列：会话列表 | 对话 | 轨迹（最后一列固定在最右） */
.chat {
  display: flex;
  align-items: stretch;
  gap: var(--sp-3);
  height: calc(100vh - var(--topbar-h) - var(--sp-6) * 2);
  min-height: 0;
}

/* 轨迹面板 */
.trace-pane {
  flex: none;
  min-width: 0;
  transition: width 0.12s var(--ease);
}
.trace-resizer {
  position: relative;
  z-index: 5;
  flex: none;
  width: 7px;
  cursor: col-resize;
  border-radius: 3px;
  background: var(--line);
  transition: background 0.12s var(--ease);
  align-self: stretch;
}
.trace-resizer:hover,
.trace-resizer.active {
  background: var(--accent-border);
}
.trace-open {
  /* flex:none 必加：chat 是 flex 容器，按钮默认可被压缩，会把"过程"挤成两行 */
  flex: none;
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  height: 30px;
  padding: 0 10px;
  white-space: nowrap;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  background: var(--bg-1);
  color: var(--text-1);
  font-family: inherit;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
}
.trace-open:hover {
  background: var(--bg-2);
  color: var(--text-0);
  border-color: var(--text-2);
}

/* 会话列表 */
.side {
  flex: none;
  width: 190px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-height: 0;
}
.new-btn {
  width: 100%;
}
.conv-list {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
}
.conv {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 8px;
  border-radius: var(--r-md);
  font-size: 12.5px;
  color: var(--text-1);
  cursor: pointer;
}
.conv:hover {
  background: var(--bg-2);
}
.conv.active {
  background: var(--bg-2);
  color: var(--text-0);
}
.conv-title {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-del {
  border: none;
  background: none;
  color: var(--text-2);
  cursor: pointer;
  font-size: 15px;
  line-height: 1;
  padding: 0 2px;
  opacity: 0;
}
.conv:hover .conv-del {
  opacity: 1;
}
.side-foot {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-2);
}

/* 主对话区 */
.main {
  flex: 1 1 auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--bg-1);
  overflow: hidden;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: var(--sp-4) var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.welcome {
  margin: auto;
  text-align: center;
  max-width: 420px;
}
.welcome h2 {
  font-size: 18px;
  margin-bottom: 8px;
}
.welcome p {
  margin: 0;
  font-size: 13px;
  color: var(--text-2);
  line-height: 1.7;
}

.msg {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 100%;
}
.msg.user {
  align-items: flex-end;
}
.msg-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11.5px;
  color: var(--text-2);
}
.who {
  color: var(--text-1);
  font-weight: 500;
}
.meta {
  font-family: var(--font-mono);
}
.msg.user .content {
  background: var(--bg-3);
  padding: 9px 13px;
  border-radius: var(--r-lg);
  font-size: 13.5px;
  line-height: 1.7;
  max-width: 74%;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg.assistant .content {
  font-size: 13.5px;
  line-height: 1.8;
  color: var(--text-0);
}

.rewritten {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
}
.notice-inline {
  margin: 0;
  font-size: 12px;
  color: var(--warn);
}
.live-chunks {
  font-size: 11.5px;
  color: var(--text-2);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
}
.typing {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-2);
}

.msg-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.cite-chip {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--accent);
  background: var(--accent-soft);
  border-radius: var(--r-sm);
  padding: 1px 6px;
  cursor: pointer;
}
.cite-chip:hover {
  background: var(--accent-border);
}

.error {
  margin: 0;
  padding: 8px 12px;
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--r-md);
  font-size: 12.5px;
}

/* 输入区 */
.composer {
  border-top: 1px solid var(--line);
  padding: var(--sp-3) var(--sp-4);
  background: var(--bg-1);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.input-wrap {
  display: flex;
  gap: var(--sp-2);
  align-items: flex-end;
}
.input-wrap textarea {
  flex: 1;
  resize: none;
  min-height: 44px;
  max-height: 160px;
  font-size: 13.5px;
}
.input-actions {
  display: flex;
  gap: 6px;
  flex: none;
}

.suggest {
  border: 1px solid var(--accent-border);
  background: var(--accent-soft);
  border-radius: var(--r-md);
  padding: var(--sp-2) var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.suggest-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.suggest-text {
  margin: 0;
  font-size: 13px;
  color: var(--text-0);
  line-height: 1.6;
}
.suggest-actions {
  display: flex;
  gap: 6px;
}

/* 弹层通用 */
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(1, 4, 9, 0.62);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: var(--sp-4);
}
.overlay.right {
  justify-content: flex-end;
  align-items: stretch;
  padding: 0;
}

/* 抽屉 */
.drawer {
  width: 340px;
  background: var(--bg-1);
  border-left: 1px solid var(--line-strong);
  display: flex;
  flex-direction: column;
}
.drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-4);
  border-bottom: 1px solid var(--line);
}
.drawer-head h3 {
  font-size: 14px;
}
.drawer-body {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  overflow-y: auto;
}
.model-group {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.model-group > label {
  font-size: 12px;
  color: var(--text-1);
}
.model-group select {
  height: 32px;
  font-size: 12px;
}
.model-summary {
  border-top: 1px solid var(--line);
  padding-top: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.model-summary-row {
  display: flex;
  justify-content: space-between;
  gap: var(--sp-3);
  font-size: 11.5px;
  color: var(--text-2);
}
.model-summary-row .mono {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12.5px;
  color: var(--text-1);
}
.row input[type="checkbox"] {
  accent-color: var(--accent);
}
.row-col {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-1);
}
.row-col input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
  height: 4px;
}
.drawer-hint {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.6;
}

/* 引用卡片 */
.cite-card {
  width: min(560px, 100%);
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-lg);
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cite-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
}
.cite-score {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
}
.cite-text {
  margin: 0;
  max-height: 50vh;
  overflow: auto;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font);
  background: var(--bg-0);
  padding: var(--sp-3);
  border-radius: var(--r-md);
}

/* 检索详情 */
.debug {
  width: min(1180px, 100%);
  max-height: 88vh;
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-lg);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.debug-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: var(--sp-4);
  border-bottom: 1px solid var(--line);
}
.debug-head h3 {
  font-size: 14px;
  margin-bottom: 3px;
}
.debug-body {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1px;
  background: var(--line);
  overflow: auto;
}
.debug-col {
  background: var(--bg-1);
  padding: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.debug-col-head {
  font-size: 11.5px;
  color: var(--text-1);
  padding-bottom: 5px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  gap: 6px;
}
.debug-item {
  display: grid;
  grid-template-columns: 26px 58px 1fr;
  gap: 6px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-2);
  align-items: baseline;
}
.debug-item .score {
  color: var(--accent);
}
.debug-item .snip {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Markdown 内容 */
.markdown :deep(p) {
  margin: 0 0 8px;
}
.markdown :deep(p:last-child) {
  margin-bottom: 0;
}
.markdown :deep(ul),
.markdown :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}
.markdown :deep(li) {
  margin: 2px 0;
}
.markdown :deep(strong) {
  color: var(--text-0);
}
.markdown :deep(code) {
  background: var(--bg-3);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}
.markdown :deep(pre.code) {
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: var(--sp-3);
  overflow-x: auto;
  margin: 0 0 8px;
}
.markdown :deep(pre.code code) {
  background: none;
  padding: 0;
}
.markdown :deep(.cite) {
  color: var(--accent);
  cursor: pointer;
  font-family: var(--font-mono);
  font-size: 0.82em;
  padding: 0 2px;
  vertical-align: super;
  line-height: 0;
}
.markdown :deep(.cite:hover) {
  background: var(--accent-soft);
  border-radius: 3px;
}
</style>
