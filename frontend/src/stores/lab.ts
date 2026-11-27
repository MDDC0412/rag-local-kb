/** RAG 实验室的实验状态：输入、进行中、结果。
 *
 * 这四个实验都是**真实调模型**（向量化、语义切分、检索链路），一次几秒到几十秒。
 * 状态留在视图里的话，切到别的页面回来结果就没了 —— 那次调用等于白花钱。
 * 挪进 store 之后，参数和结果都还在，回来接着看。
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  labApi,
  type ChunkLabResult,
  type EmbedResult,
  type PresetPair,
  type RetrieveResult,
  type SimilarityResult,
} from '@/api/lab'
import { errorMessage } from '@/api/http'

const DEFAULT_CHUNK_TEXT =
  '差旅报销管理办法。员工出差应遵循先审批、后出差的原则。未经审批产生的费用，公司不予报销。' +
  '员工应在出差结束后五个工作日内提交报销申请。逾期未提交的，须说明原因；超过三十日的，原则上不予受理。' +
  '一线城市每晚上限六百元，二线城市每晚上限四百五十元，其他城市每晚上限四百元。' +
  '报销须提交增值税发票原件、出差审批单打印件、行程单或登机牌，以及住宿费明细清单。' +
  '财务部在收到完整材料后的十个工作日内完成审核与打款。' +
  '单笔报销金额两千元以下的由部门经理审批，两千元至一万元的由部门经理与财务总监共同审批。'

/** 结果一次拿到，按节奏依次展开，让用户看清「先有什么、后有什么」。 */
const REVEAL_MS = 220

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export const useLabStore = defineStore('lab', () => {
  /** 当前停留在哪个 Tab / 高亮了哪块术语（切页回来还停在原处）。 */
  const activeTab = ref<'embed' | 'similarity' | 'chunk' | 'retrieve' | 'knowledge'>('embed')
  const activeAnchor = ref('')

  // ------------------------------------------------------------------ //
  // Tab ① 文本 → 向量
  // ------------------------------------------------------------------ //
  const embedText = ref('差旅报销需要提交发票')
  const embedLoading = ref(false)
  const embedError = ref('')
  const embedResult = ref<EmbedResult | null>(null)

  /** 把一段文字向量化并展示中间结果。 */
  async function runEmbed(): Promise<void> {
    if (!embedText.value.trim()) {
      embedError.value = '先输入一段文字'
      return
    }
    embedLoading.value = true
    embedError.value = ''
    try {
      embedResult.value = await labApi.embed(embedText.value)
    } catch (e) {
      embedError.value = errorMessage(e)
      embedResult.value = null
    } finally {
      embedLoading.value = false
    }
  }

  // ------------------------------------------------------------------ //
  // Tab ② 余弦相似度
  // ------------------------------------------------------------------ //
  const textA = ref('苹果手机')
  const textB = ref('iPhone')
  const simLoading = ref(false)
  const simError = ref('')
  const simResult = ref<SimilarityResult | null>(null)

  /** 算两段文字的余弦相似度，并给出逐项乘积的抽样。 */
  async function runSimilarity(): Promise<void> {
    if (!textA.value.trim() || !textB.value.trim()) {
      simError.value = '两个输入框都要填'
      return
    }
    simLoading.value = true
    simError.value = ''
    try {
      simResult.value = await labApi.similarity(textA.value, textB.value)
    } catch (e) {
      simError.value = errorMessage(e)
      simResult.value = null
    } finally {
      simLoading.value = false
    }
  }

  /** 套用预设对比组再算一次。 */
  function usePair(pair: PresetPair): void {
    textA.value = pair.a
    textB.value = pair.b
    void runSimilarity()
  }

  // ------------------------------------------------------------------ //
  // Tab ③ 分块实验台
  // ------------------------------------------------------------------ //
  const chunkText = ref(DEFAULT_CHUNK_TEXT)
  const chunkSize = ref(120)
  const chunkOverlap = ref(16)
  const semanticThreshold = ref(0.55)
  const chunkLoading = ref(false)
  const chunkError = ref('')
  const chunkResult = ref<ChunkLabResult | null>(null)
  const collapsedColumns = ref<Record<string, boolean>>({ recursive: true, semantic: true })

  /** 用几种策略切同一段文字，并排比较切得好不好。 */
  async function runChunk(): Promise<void> {
    if (!chunkText.value.trim()) {
      chunkError.value = '先粘贴一段文字'
      return
    }
    chunkLoading.value = true
    chunkError.value = ''
    try {
      chunkResult.value = await labApi.chunk({
        text: chunkText.value,
        chunk_size: chunkSize.value,
        overlap: Math.min(chunkOverlap.value, chunkSize.value - 1),
        semantic_threshold: semanticThreshold.value,
      })
    } catch (e) {
      chunkError.value = errorMessage(e)
      chunkResult.value = null
    } finally {
      chunkLoading.value = false
    }
  }

  function toggleColumn(strategy: string): void {
    collapsedColumns.value[strategy] = !collapsedColumns.value[strategy]
  }

  // ------------------------------------------------------------------ //
  // Tab ④ 检索链路
  // ------------------------------------------------------------------ //
  const retrieveQuery = ref('住宿标准是多少')
  const retrieveKbId = ref(1)
  const retrieveTopN = ref(10)
  const retrieveRerankTopN = ref(5)
  const useRerank = ref(true)
  const retrieveLoading = ref(false)
  const retrieveError = ref('')
  const retrieveResult = ref<RetrieveResult | null>(null)
  /** 四段依次出现的进度（0–4）；切页回来时保持已完成的样子。 */
  const revealedStage = ref(0)

  /** 跑一遍检索链路：向量 + BM25 → RRF → 精排。 */
  async function runRetrieve(): Promise<void> {
    if (!retrieveQuery.value.trim()) {
      retrieveError.value = '先输入一个问题'
      return
    }
    retrieveLoading.value = true
    retrieveError.value = ''
    revealedStage.value = 0
    try {
      retrieveResult.value = await labApi.retrieve({
        query: retrieveQuery.value,
        kb_id: retrieveKbId.value,
        vector_top_n: retrieveTopN.value,
        bm25_top_n: retrieveTopN.value,
        rerank_top_n: retrieveRerankTopN.value,
        use_rerank: useRerank.value,
      })
      // 结果一次拿到，但按节奏依次展开，用户能看清"先有什么、后有什么"
      for (let stage = 1; stage <= 4; stage += 1) {
        await sleep(REVEAL_MS)
        revealedStage.value = stage
      }
    } catch (e) {
      retrieveError.value = errorMessage(e)
      retrieveResult.value = null
    } finally {
      retrieveLoading.value = false
    }
  }

  /** 套用预设问题再跑一次。 */
  function usePreset(query: string): void {
    retrieveQuery.value = query
    void runRetrieve()
  }

  return {
    activeTab,
    activeAnchor,
    embedText,
    embedLoading,
    embedError,
    embedResult,
    runEmbed,
    textA,
    textB,
    simLoading,
    simError,
    simResult,
    runSimilarity,
    usePair,
    chunkText,
    chunkSize,
    chunkOverlap,
    semanticThreshold,
    chunkLoading,
    chunkError,
    chunkResult,
    collapsedColumns,
    runChunk,
    toggleColumn,
    retrieveQuery,
    retrieveKbId,
    retrieveTopN,
    retrieveRerankTopN,
    useRerank,
    retrieveLoading,
    retrieveError,
    retrieveResult,
    revealedStage,
    runRetrieve,
    usePreset,
  }
})
