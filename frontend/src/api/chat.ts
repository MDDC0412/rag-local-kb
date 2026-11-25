/** 对话与 SSE 流式解析。 */

import { http } from './http'

export interface Citation {
  n: number
  chunk_id: number
  document_id: number
  doc_name: string
  page_no: number
  snippet: string
  score: number
}

export interface RetrievalChunk {
  n: number
  chunk_id: number
  doc_name: string
  page_no: number
  snippet: string
  score: number
}

export interface Conversation {
  id: number
  kb_id: number
  title: string
  created_at: string
  updated_at: string
}

export interface MessageItem {
  id: number
  conversation_id: number
  role: string
  content: string
  rewritten_query: string
  citations_json: string
  retrieval_json: string
  token_usage: number
  latency_ms: number
  created_at: string
}

export interface ConversationDetail {
  conversation: Conversation
  messages: MessageItem[]
}

export interface RetrievalDebugData {
  query: string
  used_rerank: boolean
  rerank_error: string
  expanded: boolean
  top_score: number
  vector: Array<{ chunk_id: number; rank: number; score: number; snippet?: string }>
  bm25: Array<{ chunk_id: number; rank: number; score: number }>
  fused: Array<{ chunk_id: number; rank: number; score: number; snippet?: string; page_no?: number }>
  reranked: Array<{ chunk_id: number; rank: number; score: number; snippet?: string; page_no?: number }>
}

export interface ChatOptions {
  use_rerank: boolean
  vector_top_n: number
  bm25_top_n: number
  rerank_top_n: number
  rrf_k: number
  score_threshold: number
  rewrite: 'off' | 'rewrite' | 'hyde'
  expand_retry: boolean
}

export const defaultOptions: ChatOptions = {
  use_rerank: true,
  vector_top_n: 20,
  bm25_top_n: 20,
  rerank_top_n: 5,
  rrf_k: 60,
  score_threshold: 0.35,
  rewrite: 'off',
  expand_retry: true,
}

export const chatApi = {
  list: (kbId = 1) => http.get<Conversation[]>(`/conversations?kb_id=${kbId}`),
  create: (kbId = 1, title = '') => http.post<Conversation>('/conversations', { kb_id: kbId, title }),
  detail: (id: number) => http.get<ConversationDetail>(`/conversations/${id}`),
  rename: (id: number, title: string) => http.put<Conversation>(`/conversations/${id}`, { title }),
  remove: (id: number) => http.del<void>(`/conversations/${id}`),
  retrieval: (messageId: number) =>
    http.get<{ message_id: number; retrieval: RetrievalDebugData }>(
      `/messages/${messageId}/retrieval`,
    ),
  exportMd: (id: number) =>
    http.get<{ title: string; filename: string; markdown: string }>(
      `/conversations/${id}/export`,
    ),
  optimize: (question: string, history: Array<{ role: string; content: string }>) =>
    http.post<{ optimized: string; rationale: string }>('/chat/optimize-prompt', {
      question,
      history,
    }),
}

/** SSE 事件回调。 */
export interface StreamHandlers {
  onRetrieval?: (data: {
    chunks: RetrievalChunk[]
    expanded: boolean
    low_score: boolean
    rewritten: string
  }) => void
  onDelta?: (text: string) => void
  onCitations?: (items: Citation[]) => void
  onDone?: (data: { message_id: number; usage: Record<string, number>; latency_ms: number }) => void
  onError?: (message: string) => void
}

/**
 * 发起流式问答。
 *
 * 用 fetch + ReadableStream 而不是 EventSource：
 * EventSource 只支持 GET，装不下长问题。按 \n\n 切事件、取 data: 载荷。
 */
export async function streamChat(
  payload: { conversation_id: number; question: string; options: ChatOptions },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let resp: Response
  try {
    resp = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal,
    })
  } catch {
    handlers.onError?.('无法连接后端，请确认服务已启动')
    return
  }

  if (!resp.ok || !resp.body) {
    handlers.onError?.(`请求失败（HTTP ${resp.status}）`)
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // 事件以空行分隔
    let split = buffer.indexOf('\n\n')
    while (split !== -1) {
      const block = buffer.slice(0, split)
      buffer = buffer.slice(split + 2)
      handleBlock(block, handlers)
      split = buffer.indexOf('\n\n')
    }
  }
}

function handleBlock(block: string, handlers: StreamHandlers) {
  let event = 'message'
  const dataLines: string[] = []

  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return

  let data: Record<string, unknown>
  try {
    data = JSON.parse(dataLines.join('\n'))
  } catch {
    return
  }

  switch (event) {
    case 'retrieval':
      handlers.onRetrieval?.(
        data as unknown as {
          chunks: RetrievalChunk[]
          expanded: boolean
          low_score: boolean
          rewritten: string
        },
      )
      break
    case 'delta':
      handlers.onDelta?.(String((data as { text?: string }).text ?? ''))
      break
    case 'citations':
      handlers.onCitations?.((data as { items: Citation[] }).items ?? [])
      break
    case 'done':
      handlers.onDone?.(
        data as unknown as { message_id: number; usage: Record<string, number>; latency_ms: number },
      )
      break
    case 'error':
      handlers.onError?.(String((data as { message?: string }).message ?? '未知错误'))
      break
  }
}