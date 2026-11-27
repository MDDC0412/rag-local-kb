/** 对话状态：会话列表、消息、流式回答、检索参数与提问优化。
 *
 * 为什么放在 Pinia 而不是 ChatView 里？
 * 之前所有状态和 AbortController 都长在视图上：切到别的页面 → 组件卸载 →
 * abort 请求 → 后端生成被打断、助手消息一个字都没落库。用户看到的就是
 * 「切一下页面，这次对话就被当成终止了」。
 *
 * 状态挪进 store 之后，流式请求跟着应用走，切页只是换掉视图：
 * 回来能看到还在吐字的回答，回答完也照常落库。
 */

import { defineStore } from 'pinia'
import { computed, reactive, ref } from 'vue'
import {
  chatApi,
  defaultOptions,
  streamChat,
  type ChatOptions,
  type Citation,
  type Conversation,
  type MessageItem,
  type ModelOptions,
  type TraceStep,
} from '@/api/chat'

/** 本次回答用的三个模型（轨迹面板顶部展示）。 */
export interface LiveModels {
  llm?: { name: string; model: string } | null
  embedding?: { name: string; model: string } | null
  rerank?: { name: string; model: string } | null
}

/** 提问优化的建议卡片。 */
export interface PromptSuggestion {
  optimized: string
  rationale: string
}

/** 是不是「主动中止」（停止按钮或调用方取消）造成的异常。 */
function isAbort(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError'
}

export const useChatStore = defineStore('chat', () => {
  // ---------------- 会话与消息 ----------------
  const conversations = ref<Conversation[]>([])
  const activeId = ref<number | null>(null)
  const messages = ref<MessageItem[]>([])
  const loading = ref(false)
  const error = ref('')
  /** 首次进入是否已经拉过会话列表（切页回来不再重拉，保留现场）。 */
  const booted = ref(false)
  let booting: Promise<void> | null = null

  // ---------------- 输入与流式 ----------------
  const draft = ref('')
  const streaming = ref(false)
  /** 这次流式属于哪个会话：用户可能边生成边翻别的会话。 */
  const streamConversationId = ref<number | null>(null)
  const streamText = ref('')
  const liveChunks = ref<Citation[]>([])
  const liveInfo = reactive({ expanded: false, rewritten: '', lowScore: false })
  const traceSteps = ref<TraceStep[]>([])
  const liveModels = ref<LiveModels | null>(null)
  let abortController: AbortController | null = null
  /** 第几次提问。用来识别「这一轮已经被停止/被新一轮接管」，避免旧请求回来清掉新状态。 */
  let streamToken = 0

  // ---------------- 检索参数 ----------------
  const options = reactive<ChatOptions>({ ...defaultOptions })

  // ---------------- 模型选择 ----------------
  const modelOptions = ref<ModelOptions | null>(null)
  const modelsLoading = ref(false)

  // ---------------- 提问优化 ----------------
  const optimizing = ref(false)
  const suggestion = ref<PromptSuggestion | null>(null)
  /** 优化前的原文，用于「撤销」。 */
  const preOptimizeDraft = ref('')

  /** 当前会话（会话列表里的一条）。 */
  const activeConversation = computed(
    () => conversations.value.find((conv) => conv.id === activeId.value) ?? null,
  )

  /** 用户此刻看到的会话里，有没有一条正在生成的回答。 */
  const liveVisible = computed(
    () => streaming.value && streamConversationId.value === activeId.value,
  )

  // ------------------------------------------------------------------ //
  // 会话
  // ------------------------------------------------------------------ //
  /** 首次进入对话页：拉会话列表，没有会话就新建一个。 */
  async function bootstrap(): Promise<void> {
    if (booted.value) return
    // 快速来回切页会重复触发 onMounted，只让一次初始化真正跑
    booting ??= (async () => {
      await loadConversations()
      if (!conversations.value.length) await newConversation()
      // 一条会话都没建起来（后端不可用）时不算初始化完成，下次进来还能再试
      booted.value = conversations.value.length > 0
    })()
    try {
      await booting
    } finally {
      booting = null
    }
  }

  /** 拉会话列表；当前没有选中会话时才自动选中第一条。 */
  async function loadConversations(): Promise<void> {
    try {
      conversations.value = await chatApi.list()
    } catch (e) {
      error.value = (e as Error).message
      return
    }
    if (activeId.value === null && conversations.value.length) {
      await selectConversation(conversations.value[0].id)
    }
  }

  /** 选中会话并拉全部消息。 */
  async function selectConversation(id: number): Promise<void> {
    activeId.value = id
    loading.value = true
    error.value = ''
    try {
      const detail = await chatApi.detail(id)
      messages.value = detail.messages
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  /** 新建会话并切过去。 */
  async function newConversation(): Promise<void> {
    try {
      const conv = await chatApi.create(1, '')
      conversations.value.unshift(conv)
      activeId.value = conv.id
      messages.value = []
      draft.value = ''
      error.value = ''
    } catch (e) {
      error.value = (e as Error).message
    }
  }

  /** 删除会话；正在给它生成回答时先停掉。 */
  async function removeConversation(conv: Conversation): Promise<void> {
    if (streamConversationId.value === conv.id) stop()
    try {
      await chatApi.remove(conv.id)
    } catch (e) {
      error.value = (e as Error).message
      return
    }
    conversations.value = conversations.value.filter((item) => item.id !== conv.id)
    if (activeId.value === conv.id) {
      activeId.value = null
      messages.value = []
      if (conversations.value.length) await selectConversation(conversations.value[0].id)
    }
  }

  // ------------------------------------------------------------------ //
  // 提问
  // ------------------------------------------------------------------ //
  /** 清空上一轮的流式现场。 */
  function resetLive(conversationId: number): void {
    streamConversationId.value = conversationId
    streamText.value = ''
    liveChunks.value = []
    liveInfo.expanded = false
    liveInfo.rewritten = ''
    liveInfo.lowScore = false
    traceSteps.value = []
    liveModels.value = null
  }

  /**
   * 发送问题并消费 SSE。
   *
   * 这个函数**不绑在视图生命周期上**：即使中途切页，它也继续跑到底，
   * 结束后把正式消息拉回来。视图回来时读的是同一份状态。
   */
  async function send(): Promise<void> {
    const question = draft.value.trim()
    const conversationId = activeId.value
    if (!question || streaming.value || conversationId === null) return

    draft.value = ''
    suggestion.value = null
    error.value = ''
    resetLive(conversationId)
    streaming.value = true
    const token = ++streamToken
    abortController = new AbortController()

    // 先把问题显示出来（后端也会落库，结束后重新拉取）
    messages.value.push({
      id: -Date.now(),
      conversation_id: conversationId,
      role: 'user',
      content: question,
      rewritten_query: '',
      citations_json: '[]',
      retrieval_json: '{}',
      token_usage: 0,
      latency_ms: 0,
      created_at: new Date().toISOString(),
    })

    try {
      await streamChat(
        { conversation_id: conversationId, question, options: { ...options } },
        {
          onStarted(data) {
            liveModels.value = data.models
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
          },
          onDone(data) {
            if (data.steps?.length) traceSteps.value = data.steps
          },
          onError(message) {
            error.value = message
          },
        },
        abortController.signal,
      )
    } catch (e) {
      // 点「停止」也会走到这里，那不是错误
      if (!isAbort(e)) error.value = (e as Error).message || '对话中断，请重试'
    }

    // 这一轮已经被「停止」或新一轮提问接管：别再动状态，否则会清掉新那一轮
    if (token !== streamToken) return

    // 先把落库后的正式消息拉回来，再收起「生成中」，
    // 否则会有一瞬间看到问题下面空着（正是这次要修掉的那种观感）
    const failure = error.value
    if (activeId.value === conversationId) await selectConversation(conversationId)
    // 重新拉取会清掉页面级错误，把这次失败的人话提示补回去
    if (failure) error.value = failure

    streaming.value = false
    streamConversationId.value = null
    streamText.value = ''
    abortController = null

    // 会话标题、更新时间可能变了
    await loadConversations()
  }

  /** 用户点「停止」。 */
  function stop(): void {
    if (!streaming.value) return
    // 让还在飞的那一轮回来时认得出自己已作废，别覆盖后续状态
    streamToken += 1
    abortController?.abort()
    abortController = null
    streaming.value = false
  }

  // ------------------------------------------------------------------ //
  // 轨迹
  // ------------------------------------------------------------------ //
  /** 从历史消息里还原某条回答的轨迹（retrieval_json 里存了 steps）。 */
  function showMessageTrace(msg: MessageItem): void {
    const data = parseJson(msg.retrieval_json)
    const steps = Array.isArray(data.steps) ? (data.steps as TraceStep[]) : []
    if (!steps.length) return
    traceSteps.value = steps
    liveInfo.expanded = Boolean(data.expanded)
    liveInfo.rewritten = msg.rewritten_query
  }

  function parseJson(text: string): Record<string, unknown> {
    try {
      const data = JSON.parse(text || '{}')
      return typeof data === 'object' && data !== null ? (data as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }

  // ------------------------------------------------------------------ //
  // 模型选择
  // ------------------------------------------------------------------ //
  /** 拉三类模型的可选清单（会话级模型选择用）。 */
  async function loadModelOptions(): Promise<void> {
    if (modelOptions.value || modelsLoading.value) return
    modelsLoading.value = true
    try {
      modelOptions.value = await chatApi.modelOptions()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      modelsLoading.value = false
    }
  }

  /** 保存会话的模型选择。 */
  async function saveModels(payload: {
    llm_provider_id: number | null
    embedding_provider_id: number | null
    rerank_provider_id: number | null
    use_rerank: boolean
  }): Promise<boolean> {
    const id = activeId.value
    if (id === null) return false
    try {
      const updated = await chatApi.updateModels(id, payload)
      const index = conversations.value.findIndex((conv) => conv.id === updated.id)
      if (index >= 0) conversations.value[index] = updated
      return true
    } catch (e) {
      error.value = (e as Error).message
      return false
    }
  }

  // ------------------------------------------------------------------ //
  // 提问优化
  // ------------------------------------------------------------------ //
  /** 让模型把口语化提问改清楚，只给建议，不覆盖输入框。 */
  async function optimize(): Promise<void> {
    if (!draft.value.trim() || optimizing.value) return
    optimizing.value = true
    // 记住采纳前的原文，撤销时才能还原
    preOptimizeDraft.value = draft.value
    suggestion.value = null
    try {
      suggestion.value = await chatApi.optimize(
        draft.value,
        messages.value
          .filter((msg) => msg.role === 'user' || msg.role === 'assistant')
          .slice(-6)
          .map((msg) => ({ role: msg.role, content: msg.content })),
      )
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      optimizing.value = false
    }
  }

  /** 采用建议。 */
  function adoptSuggestion(): void {
    if (!suggestion.value) return
    draft.value = suggestion.value.optimized
    suggestion.value = null
  }

  /** 忽略：关掉建议卡片，输入框保持原样。 */
  function dismissSuggestion(): void {
    suggestion.value = null
    preOptimizeDraft.value = ''
  }

  /** 撤销：把输入框还原成优化前的内容。 */
  function undoAdopt(): void {
    if (!preOptimizeDraft.value) return
    draft.value = preOptimizeDraft.value
    preOptimizeDraft.value = ''
  }

  /** 再改：以当前建议为草稿再优化一次。 */
  async function refineSuggestion(): Promise<void> {
    if (!suggestion.value) return
    draft.value = suggestion.value.optimized
    suggestion.value = null
    await optimize()
  }

  return {
    conversations,
    activeId,
    messages,
    loading,
    error,
    booted,
    draft,
    streaming,
    streamConversationId,
    streamText,
    liveChunks,
    liveInfo,
    traceSteps,
    liveModels,
    options,
    modelOptions,
    modelsLoading,
    optimizing,
    suggestion,
    preOptimizeDraft,
    activeConversation,
    liveVisible,
    bootstrap,
    loadConversations,
    selectConversation,
    newConversation,
    removeConversation,
    send,
    stop,
    showMessageTrace,
    loadModelOptions,
    saveModels,
    optimize,
    adoptSuggestion,
    dismissSuggestion,
    undoAdopt,
    refineSuggestion,
  }
})
