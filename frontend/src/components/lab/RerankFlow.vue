<script setup lang="ts">
/**
 * 精排前后对比：左列 RRF 顺序、中间箭头、右列精排后顺序。
 *
 * ⚠️ 为什么每行是一整个 grid row，而不是三个独立列各自 flex：
 * 三个独立列的行高会随摘要长度变化（有的摘要两行、有的一行），
 * 列一旦各自排布就**必然错位** —— 箭头会落到别的块旁边，看着像"这个块上升了"，
 * 实际上升的是另一块。用 grid 把"左 / 箭头 / 右"放进同一行，
 * 对齐就是布局本身保证的，与内容长短无关。
 */

import { computed } from 'vue'
import type { FusedHit, RerankHit } from '@/api/lab'

const props = defineProps<{
  fused: FusedHit[]
  reranked: RerankHit[]
  usedRerank: boolean
  rerankError: string
}>()

interface Row {
  chunk: number
  from: number
  to: number
  delta: number
  before: string
  after: string
  scoreBefore: number
  scoreAfter: number
}

/** 以精排结果为主轴，把精排前的名次拼上去 */
const rows = computed<Row[]>(() => {
  const beforeMap = new Map(props.fused.map((hit) => [hit.chunk_id, hit]))
  return props.reranked.map((hit) => {
    const before = beforeMap.get(hit.chunk_id)
    return {
      chunk: hit.chunk_id,
      from: hit.prev_rank,
      to: hit.rank,
      delta: hit.prev_rank - hit.rank,
      before: before?.snippet ?? '',
      after: hit.snippet,
      scoreBefore: before?.score ?? 0,
      scoreAfter: hit.score,
    }
  })
})

/** 被顶上来 / 被压下去 / 没动，三种状态各自的文案与颜色 */
function movement(delta: number): { arrow: string; label: string; cls: string } {
  if (delta > 0) return { arrow: '↑', label: `上升 ${delta} 位`, cls: 'up' }
  if (delta < 0) return { arrow: '↓', label: `下降 ${-delta} 位`, cls: 'down' }
  return { arrow: '→', label: '名次不变', cls: 'same' }
}

const movedCount = computed(() => rows.value.filter((row) => row.delta !== 0).length)
</script>

<template>
  <div class="flow">
    <div v-if="rerankError" class="flow-warn">
      <span class="tag tag-warn">精排降级</span>
      <p class="flow-warn-text">重排模型调用失败，本次按 RRF 融合分排序：{{ rerankError }}</p>
    </div>

    <p v-if="!usedRerank" class="hint-text flow-note">
      本次没有启用精排（或重排模型不可用），右列的分数就是 RRF 融合分，名次不会变化。
      勾选上方「启用精排」再跑一次就能看到两者的差别。
    </p>

    <template v-else>
      <div class="flow-grid">
        <div class="flow-head">精排前（RRF 顺序）</div>
        <div class="flow-head mid-head">变化</div>
        <div class="flow-head">精排后（交给模型）</div>

        <template v-for="row in rows" :key="row.chunk">
          <div class="flow-cell">
            <div class="flow-cell-head">
              <span class="flow-rank num">{{ row.from }}</span>
              <span class="flow-chunk num">#{{ row.chunk }}</span>
              <span class="flow-score num">{{ row.scoreBefore.toFixed(4) }}</span>
            </div>
            <p class="flow-snippet">{{ row.before }}</p>
          </div>

          <div class="flow-mid" :class="movement(row.delta).cls">
            <span class="arrow-mark">{{ movement(row.delta).arrow }}</span>
            <span class="arrow-label">{{ movement(row.delta).label }}</span>
          </div>

          <div class="flow-cell" :class="movement(row.delta).cls">
            <div class="flow-cell-head">
              <span class="flow-rank num">{{ row.to }}</span>
              <span class="flow-chunk num">#{{ row.chunk }}</span>
              <span class="flow-score num">{{ row.scoreAfter.toFixed(4) }}</span>
            </div>
            <p class="flow-snippet">{{ row.after }}</p>
          </div>
        </template>
      </div>

      <p class="hint-text flow-summary">
        {{ rows.length }} 个候选里有 {{ movedCount }} 个名次发生了变化
      </p>
    </template>
  </div>
</template>

<style scoped>
.flow {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
}

.flow-warn {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: var(--sp-2);
  background: var(--warn-soft);
  border-radius: var(--r-sm);
}
.flow-warn-text {
  margin: 0;
  font-size: 11.5px;
  color: var(--warn);
  line-height: 1.55;
  word-break: break-word;
}

.flow-note,
.flow-summary {
  margin: 0;
}

/* 三列共用一套 grid：每行的三个格子在同一行，天然对齐 */
.flow-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 72px minmax(0, 1fr);
  gap: 6px var(--sp-2);
  align-items: stretch;
}

@media (max-width: 760px) {
  .flow-grid {
    grid-template-columns: minmax(0, 1fr) 56px minmax(0, 1fr);
  }
}

.flow-head {
  font-size: 11px;
  color: var(--text-2);
  padding-bottom: 4px;
  border-bottom: 1px solid var(--line);
}
.mid-head {
  text-align: center;
}

.flow-cell {
  padding: var(--sp-2);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  min-width: 0;
}

.flow-cell-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 3px;
}

.flow-rank {
  font-size: 11px;
  color: var(--text-0);
  font-weight: 600;
}
.flow-chunk {
  font-size: 10.5px;
  color: var(--text-2);
}
.flow-score {
  margin-left: auto;
  font-size: 10.5px;
  color: var(--text-2);
}

.flow-snippet {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-1);
  word-break: break-word;
}

/* 中间列：上下居中，于是箭头始终对着它那一行 */
.flow-mid {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  min-width: 0;
}

.arrow-mark {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1;
}
.arrow-label {
  font-size: 9.5px;
  text-align: center;
  white-space: nowrap;
}

.up {
  color: var(--ok);
}
.down {
  color: var(--danger);
}
.same {
  color: var(--text-2);
}

/* 只有右列格子加边框着色（左边是原始顺序，不该被标记） */
.flow-cell.up {
  border-color: color-mix(in srgb, var(--ok) 45%, transparent);
}
.flow-cell.down {
  border-color: color-mix(in srgb, var(--danger) 40%, transparent);
}
</style>