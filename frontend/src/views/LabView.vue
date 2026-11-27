<script setup lang="ts">
/**
 * RAG 实验室：四个 Tab，把一个「问题 → 答案」的黑箱拆开给用户看。
 *
 * 设计取向：这是教学页面，不是产品页面。
 * - 优先「看得懂」：每个数字旁边都有人话解释，公式里填的是真实数字
 * - 分步展开：Tab④ 的四段是依次出现的，不是一次性甩结果
 * - 不堆功能：默认值就给好，用户点一下就能看到效果
 *
 * 实验状态（参数、进行中、结果）都在 useLabStore 里：这四个实验都要真调模型，
 * 切到别的页面回来结果不能丢 —— 丢了等于白花一次钱。视图只负责显示。
 */

import { computed, onMounted, ref } from 'vue'
import { storeToRefs } from 'pinia'
import { errorMessage } from '@/api/http'
import { PRESET_PAIRS, labApi, type Overview, type PresetPair } from '@/api/lab'
import { useLabStore } from '@/stores/lab'
import AnglePlot from '@/components/lab/AnglePlot.vue'
import ChunkColumn from '@/components/lab/ChunkColumn.vue'
import GlossaryCard from '@/components/lab/GlossaryCard.vue'
import KnowledgeBase from '@/components/lab/KnowledgeBase.vue'
import RerankFlow from '@/components/lab/RerankFlow.vue'
import VectorBar from '@/components/lab/VectorBar.vue'
import VectorHeat from '@/components/lab/VectorHeat.vue'

const lab = useLabStore()
const {
  activeTab,
  activeAnchor,
  embedText,
  embedLoading,
  embedError,
  embedResult,
  textA,
  textB,
  simLoading,
  simError,
  simResult,
  chunkText,
  chunkSize,
  chunkOverlap,
  semanticThreshold,
  chunkLoading,
  chunkError,
  chunkResult,
  collapsedColumns,
  retrieveQuery,
  retrieveKbId,
  retrieveTopN,
  retrieveRerankTopN,
  useRerank,
  retrieveLoading,
  retrieveError,
  retrieveResult,
  revealedStage,
} = storeToRefs(lab)

// --------------------------------------------------------------------------- //
// 页面骨架
// --------------------------------------------------------------------------- //
type TabKey = 'embed' | 'similarity' | 'chunk' | 'retrieve' | 'knowledge'

const TABS: Array<{ key: TabKey; index: string; title: string; subtitle: string }> = [
  { key: 'embed', index: '01', title: '文本 → 向量', subtitle: '一段话怎么变成一串数字' },
  { key: 'similarity', index: '02', title: '余弦相似度', subtitle: '两段话「有多像」怎么算' },
  { key: 'chunk', index: '03', title: '分块实验台', subtitle: '长文档怎么切才不切坏' },
  { key: 'retrieve', index: '04', title: '检索链路', subtitle: '四步各自干了什么' },
  { key: 'knowledge', index: '05', title: '术语与面试题', subtitle: '名词解释 + 常见追问' },
]

const overview = ref<Overview | null>(null)
const bootError = ref('')

async function loadOverview() {
  try {
    overview.value = await labApi.overview()
  } catch (e) {
    bootError.value = errorMessage(e)
  }
}

/** 术语卡点词条 → 高亮对应区块 */
function focusAnchor(anchor: string) {
  activeAnchor.value = anchor
  const target: Record<string, TabKey> = {
    similarity: 'similarity',
    chunk: 'chunk',
    retrieve: 'retrieve',
    embed: 'embed',
  }
  activeTab.value = target[anchor] ?? 'embed'
}

onMounted(loadOverview)

// --------------------------------------------------------------------------- //
// Tab ① 文本 → 向量
// --------------------------------------------------------------------------- //
/** token 方块：半个字单独标注，这正是"中文被拆开"的画面 */
const tokenView = computed(() => {
  const tokens = embedResult.value?.tokens ?? []
  return tokens.map((token) => ({
    id: token.id,
    text: token.text,
    partial: token.partial,
  }))
})

// --------------------------------------------------------------------------- //
// Tab ② 余弦相似度
// --------------------------------------------------------------------------- //
/** 点积分步展示用的前 5 项 */
const sampleView = computed(() => simResult.value?.samples ?? [])

/** 判语颜色统一走语义色变量 */
function verdictColor(color: string): string {
  return `var(--${color})`
}

// --------------------------------------------------------------------------- //
// Tab ③ 分块实验台
// --------------------------------------------------------------------------- //
/** 语义栏要调模型，先给用户看"这一步会花钱" */
const semanticCost = computed(() => {
  const chars = chunkText.value.length
  return `这段文本约 ${chars} 字，语义切分会逐句调用向量模型（每批 32 句一次请求）。算过的句子走缓存，只改阈值重算通常不再产生费用。`
})

// --------------------------------------------------------------------------- //
// Tab ④ 检索链路
// --------------------------------------------------------------------------- //
function stageVisible(stage: number): boolean {
  return revealedStage.value >= stage
}

/** RRF 逐项展开：把每个候选的贡献项拼成一行算式，形如 1/(60+1) + 1/(60+2) */
function rrfFormula(terms: Array<{ source: string; rank: number; value: number }>): string {
  const k = retrieveResult.value?.rrf_k ?? 60
  return terms.map((term) => `1/(${k}+${term.rank})`).join(' + ')
}

/** 与上一行对应的数值，形如 = 0.016393 + 0.016129 */
function rrfValues(terms: Array<{ value: number }>): string {
  return terms.map((term) => term.value.toFixed(6)).join(' + ')
}
</script>

<template>
  <div class="lab">
    <!-- 顶部：Tab 切换 -->
    <nav class="tabs">
      <button
        v-for="tab in TABS"
        :key="tab.key"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        <span class="tab-index num">{{ tab.index }}</span>
        <span class="tab-text">
          <span class="tab-title">{{ tab.title }}</span>
          <span class="tab-sub">{{ tab.subtitle }}</span>
        </span>
      </button>
    </nav>

    <p v-if="bootError" class="boot-error">{{ bootError }}</p>

    <div class="body" :class="{ 'body-wide': activeTab === 'knowledge' }">
      <div class="stage">
        <!-- ============================ Tab ① ============================ -->
        <section v-if="activeTab === 'embed'" class="panel" :class="{ focus: activeAnchor === 'embed' }">
          <header class="panel-head">
            <h2 class="panel-title">一段话是怎么变成 1024 个数字的</h2>
            <p class="panel-desc">
              模型先把文字切成 token，再把每个 token 查表得到一串数字，最后整段求和归一化。
              下面从上到下就是这三步的结果。
            </p>
          </header>

          <div class="input-row">
            <textarea
              v-model="embedText"
              class="embed-input"
              rows="2"
              placeholder="输入一段中文，比如：差旅报销需要提交发票"
              @keydown.ctrl.enter="lab.runEmbed()"
            />
            <button class="btn btn-primary" :disabled="embedLoading" @click="lab.runEmbed()">
              {{ embedLoading ? '向量化中…' : '向量化' }}
            </button>
          </div>
          <p v-if="embedError" class="err">{{ embedError }}</p>

          <template v-if="embedResult">
            <div class="meta-row">
              <span v-if="embedResult.cached" class="tag tag-ok">缓存命中 · 本次没调接口</span>
              <span v-else class="tag tag-accent">刚刚调用了模型</span>
              <span class="meta-item">模型 <b class="num">{{ embedResult.model }}</b></span>
              <span class="meta-item">维度 <b class="num">{{ embedResult.dim }}</b></span>
              <span class="meta-item">token <b class="num">{{ embedResult.token_count }}</b></span>
            </div>

            <!-- 第 1 步：token 序列 -->
            <div class="block">
              <div class="block-head">
                <span class="section-title">第 1 步 · 切成 token</span>
                <span class="hint-text">
                  模型眼里文字是这些碎片。灰色方块是「半个字」——
                  一个汉字常被拆进两个 token，单独解不出字。
                </span>
              </div>
              <div class="tokens">
                <span
                  v-for="(token, index) in tokenView"
                  :key="index"
                  class="token"
                  :class="{ partial: token.partial }"
                  :title="`id=${token.id}`"
                >
                  <span v-if="token.partial" class="token-half">◐</span>
                  <span v-else class="token-text">{{ token.text }}</span>
                  <span class="token-id num">{{ token.id }}</span>
                </span>
              </div>
            </div>

            <!-- 第 2 步：前 32 维条形图 -->
            <div class="block">
              <div class="block-head">
                <span class="section-title">第 2 步 · 前 32 维长什么样</span>
                <span class="hint-text">横轴是数值，正值向右（蓝）、负值向左（红）。</span>
              </div>
              <VectorBar :values="embedResult.vector_head" />
            </div>

            <!-- 第 3 步：全量热力条 -->
            <div class="block">
              <div class="block-head">
                <span class="section-title">第 3 步 · 全部 {{ embedResult.dim }} 维</span>
              </div>
              <VectorHeat :values="embedResult.vector_heat" :dim="embedResult.dim" />
            </div>

            <!-- 统计数字 -->
            <div class="stats">
              <div class="stat">
                <span class="stat-label">L2 范数</span>
                <span class="stat-value num">{{ embedResult.norm.toFixed(4) }}</span>
                <span class="stat-hint">归一化后≈1，所以余弦就是点积</span>
              </div>
              <div class="stat">
                <span class="stat-label">均值</span>
                <span class="stat-value num">{{ embedResult.stats.mean.toFixed(4) }}</span>
                <span class="stat-hint">正负基本抵消，接近 0</span>
              </div>
              <div class="stat">
                <span class="stat-label">最大值</span>
                <span class="stat-value num">{{ embedResult.stats.max.toFixed(4) }}</span>
                <span class="stat-hint">这一维的权重最强</span>
              </div>
              <div class="stat">
                <span class="stat-label">最小值</span>
                <span class="stat-value num">{{ embedResult.stats.min.toFixed(4) }}</span>
                <span class="stat-hint">负得最多的那一维</span>
              </div>
              <div class="stat">
                <span class="stat-label">正值占比</span>
                <span class="stat-value num">{{ (embedResult.stats.positive_ratio * 100).toFixed(1) }}%</span>
                <span class="stat-hint">越接近 50% 说明分布越均衡</span>
              </div>
            </div>
          </template>
        </section>

        <!-- ============================ Tab ② ============================ -->
        <section
          v-else-if="activeTab === 'similarity'"
          class="panel"
          :class="{ focus: activeAnchor === 'similarity' }"
        >
          <header class="panel-head">
            <h2 class="panel-title">两段话「有多像」是怎么算出来的</h2>
            <p class="panel-desc">
              向量化之后，"像不像"就是一个纯算术问题：对应位相乘再相加（点积），
              除以两个向量的长度，得到夹角的余弦。下面是真实的数字。
            </p>
          </header>

          <div class="pair-inputs">
            <div class="field">
              <label>A</label>
              <input v-model="textA" type="text" placeholder="第一段文字" @keydown.enter="lab.runSimilarity()" />
            </div>
            <div class="field">
              <label>B</label>
              <input v-model="textB" type="text" placeholder="第二段文字" @keydown.enter="lab.runSimilarity()" />
            </div>
            <button class="btn btn-primary pair-btn" :disabled="simLoading" @click="lab.runSimilarity()">
              {{ simLoading ? '计算中…' : '计算' }}
            </button>
          </div>
          <p v-if="simError" class="err">{{ simError }}</p>

          <div class="presets">
            <span class="presets-label">预置对比组</span>
            <button
              v-for="pair in PRESET_PAIRS"
              :key="pair.a + pair.b"
              class="btn btn-sm preset-btn"
              :title="pair.why"
              @click="lab.usePair(pair)"
            >
              {{ pair.a }} / {{ pair.b }}
              <span class="preset-expect">预期{{ pair.expect }}</span>
            </button>
          </div>

          <template v-if="simResult">
            <div class="sim-grid">
              <!-- 逐步计算 -->
              <div class="block sim-calc">
                <div class="block-head">
                  <span class="section-title">逐步计算</span>
                </div>
                <div class="formula">
                  <div class="formula-line">
                    <span class="formula-label">点积 A·B</span>
                    <span class="formula-body num">
                      <span
                        v-for="(sample, index) in sampleView"
                        :key="sample.index"
                        class="product-term"
                      >
                        ({{ sample.a.toFixed(4) }} × {{ sample.b.toFixed(4) }})
                        <span v-if="index < sampleView.length - 1" class="plus">+</span>
                      </span>
                      <span class="ellipsis">+ … 共 {{ simResult.a.dim }} 维</span>
                      <span class="equals">= {{ simResult.dot.toFixed(4) }}</span>
                    </span>
                  </div>
                  <div class="formula-line">
                    <span class="formula-label">‖A‖ 长度</span>
                    <span class="formula-body num">= {{ simResult.norm_a.toFixed(4) }}</span>
                  </div>
                  <div class="formula-line">
                    <span class="formula-label">‖B‖ 长度</span>
                    <span class="formula-body num">= {{ simResult.norm_b.toFixed(4) }}</span>
                  </div>
                  <div class="formula-line highlight">
                    <span class="formula-label">cos θ</span>
                    <span class="formula-body num">
                      = {{ simResult.dot.toFixed(4) }} /
                      ({{ simResult.norm_a.toFixed(4) }} × {{ simResult.norm_b.toFixed(4) }})
                      = <b>{{ simResult.cosine.toFixed(4) }}</b>
                    </span>
                  </div>
                </div>
                <p class="hint-text formula-note">
                  实际只列了前 {{ sampleView.length }} 维做示范，其余 {{ simResult.a.dim - sampleView.length }} 维
                  同样逐项相乘再累加。
                </p>
              </div>

              <!-- 夹角图 -->
              <div class="block sim-plot">
                <div class="block-head">
                  <span class="section-title">夹角</span>
                </div>
                <AnglePlot
                  :a="simResult.projection.a"
                  :b="simResult.projection.b"
                  :cosine="simResult.cosine"
                  :angle-deg="simResult.angle_deg"
                  :label-a="simResult.a.text"
                  :label-b="simResult.b.text"
                />
              </div>
            </div>

            <!-- 人话解读 -->
            <div class="verdict" :style="{ borderColor: verdictColor(simResult.verdict.color) }">
              <div class="verdict-head">
                <span
                  class="verdict-badge"
                  :style="{
                    color: verdictColor(simResult.verdict.color),
                    background: 'var(--bg-2)',
                  }"
                >
                  {{ simResult.verdict.label }}
                </span>
                <span class="verdict-cos num">
                  cos = {{ simResult.cosine.toFixed(4) }} · 夹角 {{ simResult.angle_deg }}°
                </span>
              </div>
              <p class="verdict-sentence">{{ simResult.verdict.sentence }}</p>
              <p class="verdict-note">{{ simResult.verdict.threshold_note }}</p>
            </div>
          </template>
        </section>

        <!-- ============================ Tab ③ ============================ -->
        <section v-else-if="activeTab === 'chunk'" class="panel" :class="{ focus: activeAnchor === 'chunk' }">
          <header class="panel-head">
            <h2 class="panel-title">同一段文字，三种切法</h2>
            <p class="panel-desc">
              分块是 RAG 里最容易被忽略、又最影响效果的一步。切坏了，
              检索到的那一块就是半句话，模型只能靠猜。下面三栏同时切给你看。
            </p>
          </header>

          <textarea
            v-model="chunkText"
            class="chunk-input"
            rows="5"
            placeholder="粘贴一段文字，或用下面的默认文本"
          />

          <div class="controls">
            <div class="control">
              <label>块大小 <b class="num">{{ chunkSize }}</b> token</label>
              <input v-model.number="chunkSize" type="range" min="64" max="512" step="8" />
            </div>
            <div class="control">
              <label>重叠 <b class="num">{{ chunkOverlap }}</b> token</label>
              <input v-model.number="chunkOverlap" type="range" min="0" max="128" step="4" />
            </div>
            <div class="control">
              <label>语义阈值 <b class="num">{{ semanticThreshold.toFixed(2) }}</b></label>
              <input v-model.number="semanticThreshold" type="range" min="0.2" max="0.9" step="0.05" />
            </div>
            <button class="btn btn-primary" :disabled="chunkLoading" @click="lab.runChunk()">
              {{ chunkLoading ? '切分中…' : '切一遍' }}
            </button>
          </div>

          <p class="hint-text cost-note">{{ semanticCost }}</p>
          <p v-if="chunkError" class="err">{{ chunkError }}</p>

          <template v-if="chunkResult">
            <p v-if="chunkResult.semantic_note" class="hint-text semantic-note">
              {{ chunkResult.semantic_note }}
            </p>
            <p v-for="warning in chunkResult.warnings" :key="warning" class="hint-text">
              {{ warning }}
            </p>

            <div class="columns">
              <ChunkColumn
                v-for="column in chunkResult.columns"
                :key="column.strategy"
                :column="column"
                :collapsed="collapsedColumns[column.strategy] ?? true"
                @toggle="lab.toggleColumn(column.strategy)"
              />
            </div>
          </template>
        </section>

        <!-- ============================ Tab ④ ============================ -->
        <section
          v-else-if="activeTab === 'retrieve'"
          class="panel"
          :class="{ focus: activeAnchor === 'retrieve' }"
        >
          <header class="panel-head">
            <h2 class="panel-title">一个问题进系统后，四步各自干了什么</h2>
            <p class="panel-desc">
              向量召回管"意思相近"，BM25 管"字面命中"，RRF 把两路排名合成一个顺序，
              精排再做最后裁判。四步依次展开。
            </p>
          </header>

          <div class="input-row">
            <input
              v-model="retrieveQuery"
              type="text"
              class="retrieve-input"
              placeholder="输入一个问题，或用下面的预设"
              @keydown.enter="lab.runRetrieve()"
            />
            <button class="btn btn-primary" :disabled="retrieveLoading" @click="lab.runRetrieve()">
              {{ retrieveLoading ? '演示中…' : '演示' }}
            </button>
          </div>

          <div class="presets">
            <span class="presets-label">预设问题</span>
            <button
              v-for="preset in overview?.preset_queries ?? []"
              :key="preset.query"
              class="btn btn-sm preset-btn"
              @click="lab.usePreset(preset.query)"
            >
              {{ preset.label }}
            </button>
          </div>

          <div class="controls retrieve-controls">
            <div class="control">
              <label>每路召回 <b class="num">{{ retrieveTopN }}</b> 条</label>
              <input v-model.number="retrieveTopN" type="range" min="3" max="20" step="1" />
            </div>
            <div class="control">
              <label>精排保留 <b class="num">{{ retrieveRerankTopN }}</b> 条</label>
              <input v-model.number="retrieveRerankTopN" type="range" min="1" max="10" step="1" />
            </div>
            <label class="check">
              <input v-model="useRerank" type="checkbox" />
              <span>启用精排</span>
            </label>
          </div>

          <p v-if="overview" class="hint-text kb-note">
            知识库：{{ overview.kbs.find((kb) => kb.id === retrieveKbId)?.name ?? '默认' }} ·
            当前向量库共 {{ overview.vector_count }} 条 ·
            "没找到"阈值 {{ overview.score_threshold }}
          </p>
          <p v-if="retrieveError" class="err">{{ retrieveError }}</p>

          <template v-if="retrieveResult">
            <p class="retrieve-note">
              <span class="num">耗时 {{ retrieveResult.latency_ms }}ms</span>
              <span>{{ retrieveResult.note }}</span>
            </p>

            <!-- 阶段 1：向量召回 -->
            <div v-show="stageVisible(1)" class="block stage" :class="{ 'rise': stageVisible(1) }">
              <div class="block-head">
                <span class="stage-index num">1</span>
                <span class="section-title">向量召回</span>
                <span class="hint-text">
                  {{ retrieveResult.vector.length }} 条。分数是余弦相似度，0~1，越大越像。
                </span>
              </div>
              <table class="hit-table">
                <tbody>
                  <tr v-for="hit in retrieveResult.vector" :key="'v' + hit.chunk_id">
                    <td class="rank num">{{ hit.rank }}</td>
                    <td class="chunk num">#{{ hit.chunk_id }}</td>
                    <td class="score num">{{ hit.score.toFixed(4) }}</td>
                    <td class="snippet">{{ hit.snippet }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-if="!retrieveResult.vector.length" class="hint-text empty">没有召回任何内容。</p>
            </div>

            <!-- 阶段 2：BM25 -->
            <div v-show="stageVisible(2)" class="block stage" :class="{ 'rise': stageVisible(2) }">
              <div class="block-head">
                <span class="stage-index num">2</span>
                <span class="section-title">BM25 召回</span>
                <span class="hint-text">
                  {{ retrieveResult.bm25.length }} 条。分数没有 0~1 范围，只是相对高低 ——
                  所以它不能直接和上面的余弦相加。
                </span>
              </div>
              <table class="hit-table">
                <tbody>
                  <tr v-for="hit in retrieveResult.bm25" :key="'b' + hit.chunk_id">
                    <td class="rank num">{{ hit.rank }}</td>
                    <td class="chunk num">#{{ hit.chunk_id }}</td>
                    <td class="score num">{{ hit.score.toFixed(2) }}</td>
                    <td class="snippet">{{ hit.snippet }}</td>
                  </tr>
                </tbody>
              </table>
              <p v-if="!retrieveResult.bm25.length" class="hint-text empty">
                这个词在知识库里没有字面命中 —— 换个说法试试。
              </p>
            </div>

            <!-- 阶段 3：RRF 逐项计算 -->
            <div v-show="stageVisible(3)" class="block stage" :class="{ 'rise': stageVisible(3) }">
              <div class="block-head">
                <span class="stage-index num">3</span>
                <span class="section-title">RRF 融合（逐项展开）</span>
                <span class="hint-text">
                  score = Σ 1/(k+名次)，k={{ retrieveResult.rrf_k }}。
                  两路都命中的块分数会累加，所以排前面。
                </span>
              </div>
              <table class="rrf-table">
                <thead>
                  <tr>
                    <th class="rank">融合名次</th>
                    <th class="chunk">块</th>
                    <th>算式</th>
                    <th class="score">合计</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in retrieveResult.rrf_rows" :key="'r' + row.chunk_id">
                    <td class="rank num">{{ row.rank }}</td>
                    <td class="chunk num">#{{ row.chunk_id }}</td>
                    <td class="formula-cell">
                      <span class="rrf-formula num">{{ rrfFormula(row.terms) }}</span>
                      <span class="rrf-terms num">= {{ rrfValues(row.terms) }}</span>
                      <span v-if="row.terms.length > 1" class="tag tag-accent rrf-both">两路都命中</span>
                      <span v-else-if="row.terms[0]?.source === 'vector'" class="tag rrf-one">只有向量</span>
                      <span v-else class="tag rrf-one">只有 BM25</span>
                    </td>
                    <td class="score num">{{ row.total.toFixed(5) }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <!-- 阶段 4：精排前后 -->
            <div v-show="stageVisible(4)" class="block stage" :class="{ 'rise': stageVisible(4) }">
              <div class="block-head">
                <span class="stage-index num">4</span>
                <span class="section-title">精排重排</span>
                <span class="hint-text">
                  精排模型把 query 和每个块放在一起读，重新打分。
                  看左边哪些被顶上来、哪些被压下去。
                </span>
              </div>
              <RerankFlow
                :fused="retrieveResult.fused"
                :reranked="retrieveResult.reranked"
                :used-rerank="retrieveResult.used_rerank"
                :rerank-error="retrieveResult.rerank_error"
              />
            </div>
          </template>
        </section>

        <!-- ============================ Tab ⑤ ============================ -->
        <section v-else-if="activeTab === 'knowledge'" class="panel">
          <KnowledgeBase />
        </section>
      </div>

      <!-- 术语卡：只在可视化四个 Tab 里显示；Tab⑤ 本身就是词典，再挂一张卡是重复 -->
      <GlossaryCard v-if="activeTab !== 'knowledge'" :active-anchor="activeAnchor" @focus="focusAnchor" />
    </div>
  </div>
</template>

<style scoped>
.lab {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
}

/* ---------- Tab 切换 ---------- */
.tabs {
  display: flex;
  gap: var(--sp-2);
  flex-wrap: wrap;
}

.tab {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  flex: none;
  padding: 9px 13px;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--bg-1);
  color: var(--text-1);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease),
    color var(--dur) var(--ease);
}
.tab:hover {
  background: var(--bg-2);
  color: var(--text-0);
}
.tab.active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--text-0);
}

.tab-index {
  font-size: 10.5px;
  color: var(--text-2);
  line-height: 1.5;
}
.tab.active .tab-index {
  color: var(--accent);
}

.tab-text {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.tab-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.4;
}
.tab-sub {
  font-size: 11px;
  color: var(--text-2);
  line-height: 1.4;
}

.boot-error {
  margin: 0;
  color: var(--danger);
  font-size: 12.5px;
}

/* ---------- 两栏布局 ---------- */
.body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 264px;
  gap: var(--sp-4);
  align-items: start;
}

/* Tab⑤ 没有右侧术语卡，铺满整行 */
.body-wide {
  grid-template-columns: minmax(0, 1fr);
}

@media (max-width: 1180px) {
  .body {
    grid-template-columns: minmax(0, 1fr);
  }
}

.stage {
  min-width: 0;
}

/* ---------- 面板 ---------- */
.panel {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
  padding: var(--sp-5);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  transition: border-color var(--dur) var(--ease);
}
.panel.focus {
  border-color: var(--accent-border);
}

.panel-head {
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.panel-title {
  font-size: 16px;
  color: var(--text-0);
}
.panel-desc {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
  max-width: 68ch;
}

/* ---------- 通用输入 ---------- */
.input-row {
  display: flex;
  gap: var(--sp-2);
  align-items: flex-start;
}
.embed-input {
  flex: 1 1 auto;
  min-width: 0;
}
.input-row .btn {
  flex: none;
}

.err {
  margin: 0;
  font-size: 12.5px;
  color: var(--danger);
  line-height: 1.6;
}

/* ---------- 区块 ---------- */
.block {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  padding: var(--sp-3);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}
.block-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px;
}

/* ---------- Tab① token ---------- */
.meta-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-3);
}
.meta-item {
  font-size: 11.5px;
  color: var(--text-2);
}
.meta-item b {
  color: var(--text-0);
  font-weight: 500;
}

.tokens {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.token {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 26px;
  padding: 0 6px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  font-size: 12px;
  color: var(--text-0);
}
.token.partial {
  background: transparent;
  border-style: dashed;
  border-color: var(--line-strong);
}
.token-half {
  color: var(--text-2);
  font-size: 11px;
}
.token-text {
  white-space: pre;
}
.token-id {
  font-size: 9.5px;
  color: var(--text-2);
}

/* ---------- Tab① 统计 ---------- */
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: var(--sp-2);
}
.stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: var(--sp-2);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}
.stat-label {
  font-size: 11px;
  color: var(--text-2);
}
.stat-value {
  font-size: 15px;
  color: var(--text-0);
}
.stat-hint {
  font-size: 10.5px;
  color: var(--text-2);
  line-height: 1.5;
}

/* ---------- Tab② ---------- */
.pair-inputs {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: var(--sp-2);
  align-items: end;
}
.pair-btn {
  flex: none;
  height: 34px;
}

@media (max-width: 720px) {
  .pair-inputs {
    grid-template-columns: minmax(0, 1fr);
  }
}

.presets {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.presets-label {
  font-size: 11.5px;
  color: var(--text-2);
}
.preset-btn {
  gap: 6px;
}
.preset-expect {
  font-size: 10px;
  color: var(--text-2);
}

.sim-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: var(--sp-3);
  align-items: start;
}
@media (max-width: 900px) {
  .sim-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.formula {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.formula-line {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  padding: 5px 0;
  border-bottom: 1px dashed var(--line);
}
.formula-line.highlight {
  border-bottom: none;
  border-left: 2px solid var(--accent);
  padding-left: 8px;
}
.formula-label {
  flex: none;
  width: 82px;
  font-size: 11.5px;
  color: var(--text-2);
}
.formula-body {
  font-size: 12px;
  line-height: 1.7;
  color: var(--text-1);
  word-break: break-word;
}
.formula-body b {
  color: var(--accent);
  font-weight: 600;
}
.product-term {
  white-space: nowrap;
}
.plus {
  color: var(--text-2);
  margin: 0 2px;
}
.ellipsis {
  color: var(--text-2);
  margin-left: 2px;
}
.equals {
  margin-left: 6px;
  color: var(--text-0);
  font-weight: 600;
}
.formula-note {
  margin: 0;
}

.sim-plot {
  align-items: center;
}

.verdict {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--sp-3);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-left-width: 3px;
  border-radius: var(--r-md);
}
.verdict-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--sp-2);
}
.verdict-badge {
  padding: 1px 8px;
  border-radius: var(--r-sm);
  font-size: 12px;
  font-weight: 600;
}
.verdict-cos {
  font-size: 11.5px;
  color: var(--text-2);
}
.verdict-sentence {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-0);
}
.verdict-note {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.65;
  color: var(--text-2);
}

/* ---------- Tab③ ---------- */
.chunk-input {
  width: 100%;
  font-size: 12.5px;
  line-height: 1.7;
}

.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: var(--sp-4);
}
.control {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 160px;
  flex: 1 1 160px;
}
.control label {
  font-size: 11.5px;
  color: var(--text-1);
}
.control label b {
  color: var(--text-0);
}
.control input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
}
.controls .btn {
  flex: none;
}

.check {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-1);
  cursor: pointer;
  white-space: nowrap;
}
.check input {
  accent-color: var(--accent);
  cursor: pointer;
}

.cost-note {
  margin: 0;
}
.semantic-note {
  margin: 0;
  color: var(--text-1);
}

.columns {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--sp-3);
  align-items: start;
}
@media (max-width: 1080px) {
  .columns {
    grid-template-columns: minmax(0, 1fr);
  }
}

/* ---------- Tab④ ---------- */
.retrieve-input {
  flex: 1 1 auto;
  min-width: 0;
}
.retrieve-controls {
  gap: var(--sp-4);
}
.kb-note {
  margin: 0;
}

.retrieve-note {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin: 0;
  padding: var(--sp-3);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-left: 3px solid var(--accent);
  border-radius: var(--r-md);
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
}
.retrieve-note .num {
  font-size: 11px;
  color: var(--text-2);
}

.stage-index {
  flex: none;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--accent-soft);
  color: var(--accent);
  border-radius: 50%;
  font-size: 10.5px;
}

.hit-table,
.rrf-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
}
.hit-table td,
.rrf-table td,
.rrf-table th {
  padding: 5px 6px;
  border-bottom: 1px solid var(--line);
  text-align: left;
  vertical-align: top;
}
.rrf-table th {
  font-weight: 500;
  font-size: 10.5px;
  color: var(--text-2);
  border-bottom-color: var(--line-strong);
}

.hit-table .rank,
.rrf-table .rank {
  width: 46px;
  color: var(--text-2);
}
.hit-table .chunk,
.rrf-table .chunk {
  width: 48px;
  color: var(--accent);
}
.hit-table .score {
  width: 62px;
  color: var(--text-0);
}
.rrf-table .score {
  width: 84px;
  color: var(--text-0);
}
.hit-table .snippet {
  color: var(--text-1);
  line-height: 1.55;
  word-break: break-word;
}

.formula-cell {
  line-height: 1.6;
}
.rrf-formula {
  display: block;
  color: var(--text-2);
  font-size: 10.5px;
}
.rrf-terms {
  color: var(--text-1);
  font-size: 11px;
  word-break: break-word;
}
.rrf-both,
.rrf-one {
  margin-left: 6px;
}
.rrf-one {
  font-size: 10px;
  color: var(--text-2);
  background: var(--bg-2);
}

.empty {
  margin: 0;
}
</style>