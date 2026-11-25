/** 供应商接口与类型定义。密钥只以打码形式回传，前端永远拿不到明文。 */

import { http } from './http'

export type ProviderKind = 'llm' | 'embedding' | 'rerank'

export type Protocol =
  | 'openai'
  | 'siliconflow_rerank'
  | 'cohere_rerank'
  | 'dashscope_rerank'

export interface Provider {
  id: number
  kind: ProviderKind
  name: string
  base_url: string
  model: string
  protocol: Protocol
  params: Record<string, unknown>
  is_default: boolean
  enabled: boolean
  has_api_key: boolean
  api_key_masked: string
  created_at: string
}

export interface ProviderDraft {
  kind: ProviderKind
  name: string
  base_url: string
  api_key: string
  model: string
  protocol?: Protocol
  params?: Record<string, unknown>
  is_default?: boolean
  enabled?: boolean
}

export interface TestResult {
  ok: boolean
  kind: string
  latency_ms: number
  message: string
  models?: string[]
  model_count?: number
  model_available?: boolean | null
  dim?: number | null
  results?: Array<{ index: number; score: number }>
  sanity_passed?: boolean | null
  protocol?: string | null
}

export const KIND_LABEL: Record<ProviderKind, string> = {
  llm: '对话模型',
  embedding: '向量模型',
  rerank: '重排模型',
}

export const PROTOCOL_LABEL: Record<string, string> = {
  openai: 'OpenAI 兼容',
  siliconflow_rerank: '硅基流动 /rerank',
  cohere_rerank: 'Cohere /v1/rerank',
  dashscope_rerank: '阿里 DashScope 原生',
}

/** 常见服务商预设，填表时一键套用（只填地址与协议，密钥和模型由用户决定）。 */
export interface Preset {
  key: string
  label: string
  kind: ProviderKind
  base_url: string
  protocol: Protocol
  model: string
  note: string
}

export const PRESETS: Preset[] = [
  {
    key: 'xiaopang-llm',
    label: '第三方 OpenAI 兼容网关 网关（对话）',
    kind: 'llm',
    base_url: 'http://gateway.example.com/v1',
    protocol: 'openai',
    model: 'deepseek-v4.1-flash',
    note: 'OpenAI 兼容网关，模型列表可在填好后点"测试连接"查看',
  },
  {
    key: 'ali-embed',
    label: '阿里百炼（向量）',
    kind: 'embedding',
    base_url: 'https://maas.qianwenaiapi.com/compatible-mode/v1',
    protocol: 'openai',
    model: 'qwen3.7-text-embedding-flash',
    note: '兼容模式提供 /embeddings，输出 1024 维',
  },
  {
    key: 'ali-rerank',
    label: '阿里百炼（重排）',
    kind: 'rerank',
    base_url: 'https://maas.qianwenaiapi.com/compatible-mode/v1',
    protocol: 'dashscope_rerank',
    model: 'qwen3.7-text-rerank',
    note: '重排走 DashScope 原生路径，base_url 填兼容模式地址即可（会自动截取域名根）',
  },
  {
    key: 'siliconflow',
    label: '硅基流动（三家通吃）',
    kind: 'llm',
    base_url: 'https://api.siliconflow.cn/v1',
    protocol: 'openai',
    model: 'deepseek-ai/DeepSeek-V3',
    note: '同一 key 可提供对话、bge-m3 向量、bge-reranker 重排',
  },
]

export const providerApi = {
  list: () => http.get<Provider[]>('/providers'),
  create: (body: ProviderDraft) => http.post<Provider>('/providers', body as unknown as Record<string, unknown>),
  update: (id: number, body: Partial<ProviderDraft>) =>
    http.put<Provider>(`/providers/${id}`, body as Record<string, unknown>),
  remove: (id: number) => http.del<void>(`/providers/${id}`),
  test: (id: number) => http.post<TestResult>(`/providers/${id}/test`),
  testDraft: (body: ProviderDraft) =>
    http.post<TestResult>('/providers/test', body as unknown as Record<string, unknown>),
  models: (id: number) => http.get<string[]>(`/providers/${id}/models`),
}