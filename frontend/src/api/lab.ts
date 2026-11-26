/** RAG 实验室接口与前端用到的常量（术语、预设对比组）。 */

import { http } from './http'

// --------------------------------------------------------------------------- //
// Tab ① 文本 → 向量
// --------------------------------------------------------------------------- //
export interface TokenPiece {
  id: number
  text: string
  /** 半个字：中文常被拆进两个 token，前半个单独解不出字 */
  partial: boolean
}

export interface EmbedStats {
  mean: number
  max: number
  min: number
  positive_ratio: number
}

export interface EmbedResult {
  text: string
  /** 命中缓存 = 这次没有调用模型接口 */
  cached: boolean
  model: string
  dim: number
  norm: number
  token_count: number
  tokens: TokenPiece[]
  /** 前 32 维，画条形图 */
  vector_head: number[]
  /** 压缩到 256 格的全量概览，画热力条 */
  vector_heat: number[]
  stats: EmbedStats
}

// --------------------------------------------------------------------------- //
// Tab ② 余弦相似度
// --------------------------------------------------------------------------- //
export interface SimilaritySide {
  text: string
  cached: boolean
  dim: number
  norm: number
  vector_head: number[]
}

export interface ProductSample {
  index: number
  a: number
  b: number
  product: number
}

export interface Projection {
  a: [number, number] | number[]
  b: [number, number] | number[]
  cosine_2d: number
}

export interface Verdict {
  level: 'high' | 'related' | 'weak' | 'unrelated'
  label: string
  color: string
  sentence: string
  threshold_note: string
}

export interface SimilarityResult {
  a: SimilaritySide
  b: SimilaritySide
  dot: number
  norm_a: number
  norm_b: number
  cosine: number
  angle_deg: number
  samples: ProductSample[]
  projection: Projection
  verdict: Verdict
}

// --------------------------------------------------------------------------- //
// Tab ③ 分块实验台
// --------------------------------------------------------------------------- //
export interface LabChunk {
  seq: number
  content: string
  token_count: number
  char_count: number
  /** 结尾不是句末标点 = 被硬切断 */
  cut: boolean
}

export interface ChunkColumn {
  strategy: string
  label: string
  count: number
  avg_tokens: number
  total_tokens: number
  cut_count: number
  chunks: LabChunk[]
  error: string
}

export interface ChunkLabResult {
  columns: ChunkColumn[]
  semantic_note: string
  warnings: string[]
}

// --------------------------------------------------------------------------- //
// Tab ④ 检索链路
// --------------------------------------------------------------------------- //
export interface VectorHit {
  chunk_id: number
  rank: number
  score: number
  snippet: string
}

export interface Bm25Hit {
  chunk_id: number
  rank: number
  score: number
  snippet: string
}

export interface FusedHit {
  chunk_id: number
  rank: number
  score: number
  rank_vector: number | null
  rank_bm25: number | null
  score_vector: number
  score_bm25: number
  snippet: string
}

export interface RrfTerm {
  source: 'vector' | 'bm25'
  rank: number
  value: number
}

export interface RrfRow {
  chunk_id: number
  rank: number
  terms: RrfTerm[]
  total: number
}

export interface RerankHit {
  chunk_id: number
  rank: number
  /** 精排前的名次，用来画"被顶上来/被压下去" */
  prev_rank: number
  score: number
  snippet: string
}

export interface RetrieveResult {
  query: string
  vector: VectorHit[]
  bm25: Bm25Hit[]
  fused: FusedHit[]
  rrf_rows: RrfRow[]
  rrf_k: number
  reranked: RerankHit[]
  used_rerank: boolean
  rerank_error: string
  top_score: number
  latency_ms: number
  score_threshold: number
  note: string
}

export interface Overview {
  kbs: Array<{ id: number; name: string }>
  vector_count: number
  preset_queries: Array<{ label: string; query: string }>
  score_threshold: number
  api_strategies: string[]
}

// --------------------------------------------------------------------------- //
// 接口封装
// --------------------------------------------------------------------------- //
export const labApi = {
  overview: () => http.get<Overview>('/lab/overview'),

  embed: (text: string, providerId: number | null = null) =>
    http.post<EmbedResult>('/lab/embed', { text, provider_id: providerId }),

  similarity: (textA: string, textB: string, providerId: number | null = null) =>
    http.post<SimilarityResult>('/lab/similarity', {
      text_a: textA,
      text_b: textB,
      provider_id: providerId,
    }),

  chunk: (payload: {
    text: string
    strategy?: string
    chunk_size: number
    overlap: number
    semantic_threshold: number
    provider_id?: number | null
  }) => http.post<ChunkLabResult>('/lab/chunk', payload),

  retrieve: (payload: {
    query: string
    kb_id?: number
    vector_top_n?: number
    bm25_top_n?: number
    rerank_top_n?: number
    rrf_k?: number
    use_rerank?: boolean
  }) => http.post<RetrieveResult>('/lab/retrieve', payload),
}

// --------------------------------------------------------------------------- //
// 预设数据（文案集中在这里，组件只负责渲染）
// --------------------------------------------------------------------------- //
export interface PresetPair {
  a: string
  b: string
  expect: string
  /** 为什么这一对值得看 —— 这是教学重点，不是装饰 */
  why: string
}

export const PRESET_PAIRS: PresetPair[] = [
  {
    a: '苹果手机',
    b: 'iPhone',
    expect: '高',
    why: '同一个东西的两种叫法。字面完全不同，向量却几乎重合 —— 这正是向量检索比关键词检索强的地方。',
  },
  {
    a: '苹果手机',
    b: '香蕉',
    expect: '低',
    why: '都带「苹果/香蕉」这类水果词，但说的是不同东西。用来检验模型有没有被字面带偏。',
  },
  {
    a: '差旅报销',
    b: '出差费用怎么报',
    expect: '高',
    why: '换个说法问同一件事。关键词检索这里会漏（没有共同词），向量检索能召回。',
  },
  {
    a: '住宿标准',
    b: '每晚上限多少钱',
    expect: '中高',
    why: '概念相关但用词不同，属于「能召回但不是最佳」的那一档。',
  },
  {
    a: '报销流程',
    b: '量子纠缠实验',
    expect: '极低',
    why: '完全不搭界。这个分数是「检索不到相关内容」的实证 —— 低于 0.35 阈值就会被判为没找到。',
  },
]

export interface GlossaryEntry {
  term: string
  plain: string
  /** 对应到页面哪一块可视化，点词条时高亮 */
  anchor?: string
}

/** 术语卡内容：直接对应 DESIGN.md §13，并补上与这一页可视化相关的补充说明。 */
export const GLOSSARY: GlossaryEntry[] = [
  {
    term: 'embedding（向量化）',
    plain:
      '把一段文字变成一串数字（这个模型是 1024 个浮点数）。语义相近的文字，数字也相近。Tab① 展示的就是这个过程。',
    anchor: 'embed',
  },
  {
    term: '向量',
    plain:
      '上一步得到的那串数字。人眼读不懂，但机器可以用它做算术 —— 相减得距离、相乘得相似度。',
    anchor: 'embed',
  },
  {
    term: '余弦相似度',
    plain:
      '两个向量夹角的余弦值。1 表示方向完全一致，0 表示垂直（无关），-1 表示完全相反。Tab② 就是它的计算器。',
    anchor: 'similarity',
  },
  {
    term: 'token',
    plain:
      '模型计费和上下文长度的单位。中文一个字常被切成 2 个 token，所以「500 token」不等于 500 个汉字。Tab① 能看到真实的切分。',
    anchor: 'embed',
  },
  {
    term: 'chunk（分块）',
    plain:
      '把长文档切成小段再入库。切太大 → 塞不进上下文、噪声多；切太小 → 语义不完整。Tab③ 用来对比三种切法。',
    anchor: 'chunk',
  },
  {
    term: '向量检索',
    plain:
      '把你的问题也变成向量，找数字最接近的若干块。擅长「意思相近」，不擅长「字面精确」。',
    anchor: 'retrieve',
  },
  {
    term: 'BM25',
    plain:
      '经典关键词检索算法（TF-IDF 的改进版）。擅长精确命中词，不擅长同义词。它的分数没有 0~1 范围，只是相对高低。',
    anchor: 'retrieve',
  },
  {
    term: '混合检索',
    plain: '向量 + BM25 两路都查，再合并结果。互补：一路管语义，一路管字面。',
    anchor: 'retrieve',
  },
  {
    term: 'RRF',
    plain:
      'Reciprocal Rank Fusion：只用「排名」融合两路结果，公式 1/(k+rank)。好处是不用管两路分数根本不是一个量纲。Tab④ 会展开逐项计算。',
    anchor: 'retrieve',
  },
  {
    term: 'Rerank（精排）',
    plain:
      '用一个更贵更准的模型，把 query 和每个候选块放在一起打分，重新排序。先粗排 20 个再精排 5 个，是成本与效果的平衡。',
    anchor: 'retrieve',
  },
  {
    term: 'Top-K',
    plain: '取前 K 个结果。K 越大召回越全、噪声也越多，精排就是在帮你把这个 K 压小。',
    anchor: 'retrieve',
  },
  {
    term: '阈值（0.35）',
    plain:
      '低于这个相似度就认为「没找到相关内容」，宁可如实说不知道，也不硬凑一段出来。Tab② 里每档判语都标注了它与这条线的关系。',
    anchor: 'similarity',
  },
]