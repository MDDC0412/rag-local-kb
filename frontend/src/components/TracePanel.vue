<script setup lang="ts">
/** 右侧轨迹面板：显示本次问答走到哪一步。 */

import { computed } from 'vue'

export interface TraceStep {
  name: string
  label: string
  status: 'running' | 'done' | 'skipped' | 'failed'
  detail: string
  elapsed_ms: number
}

const props = defineProps<{
  steps: TraceStep[]
  streaming: boolean
  models?: {
    llm?: { name: string; model: string } | null
    embedding?: { name: string; model: string } | null
    rerank?: { name: string; model: string } | null
  } | null
  expanded?: boolean
  rewritten?: string
}>()

const emit = defineEmits<{ collapse: [] }>()

/** 固定的流程顺序，用于显示"还没走到"的步骤（灰色） */
const PIPELINE = [
  { name: 'rewrite', label: '查询改写' },
  { name: 'embed', label: '查询向量化' },
  { name: 'recall', label: '向量 + BM25 召回' },
  { name: 'fuse', label: 'RRF 融合' },
  { name: 'rerank', label: '精排' },
  { name: 'grade', label: '质量判定' },
  { name: 'answer', label: '组织答案' },
  { name: 'generate', label: '模型生成' },
] as const

/** 把已完成的轨迹按名字聚起来（扩召回会重跑 recall/fuse/rerank/grade） */
const byName = computed(() => {
  const map = new Map<string, TraceStep[]>()
  for (const step of props.steps) {
    const list = map.get(step.name) ?? []
    list.push(step)
    map.set(step.name, list)
  }
  return map
})

const totalMs = computed(() =>
  props.steps.reduce((sum, step) => sum + (step.elapsed_ms || 0), 0),
)

function statusOf(name: string): 'pending' | 'running' | 'done' | 'skipped' | 'failed' {
  const list = byName.value.get(name)
  if (!list || !list.length) return 'pending'
  const last = list[list.length - 1]
  if (last.status === 'running') return 'running'
  if (props.streaming && name === lastStepName.value) {
    // 流式中，最后一步若已 done 但还在等后续，视为完成
    return last.status as 'done'
  }
  return last.status as 'done' | 'skipped' | 'failed'
}

const lastStepName = computed(() => {
  const list = props.steps
  return list.length ? list[list.length - 1].name : ''
})

const dotClass = (name: string) => {
  const status = statusOf(name)
  return {
    'dot-ok': status === 'done',
    'dot-idle': status === 'pending',
    'dot-bad': status === 'failed',
    'dot-warn': status === 'running',
    'dot-skip': status === 'skipped',
  }
}
</script>

<template>
  <aside class="trace">
    <header class="trace-head">
      <span class="section-title">执行过程</span>
      <div class="trace-head-right">
        <span v-if="steps.length" class="trace-total">{{ totalMs }}ms</span>
        <button class="collapse-btn" title="收起面板" @click="emit('collapse')">—</button>
      </div>
    </header>

    <div v-if="!steps.length" class="trace-empty">
      发送问题后，这里会显示检索与生成的每一步。
    </div>

    <ol v-else class="trace-list">
      <li v-for="item in PIPELINE" :key="item.name" class="trace-item">
        <span class="trace-dot" :class="dotClass(item.name)" />
        <div class="trace-body">
          <div class="trace-line">
            <span class="trace-label">{{ item.label }}</span>
            <span
              v-for="(record, index) in byName.get(item.name) ?? []"
              :key="item.name + index"
              class="trace-time"
            >
              {{ record.elapsed_ms }}ms
            </span>
          </div>
          <p
            v-for="(record, index) in byName.get(item.name) ?? []"
            :key="'d' + item.name + index"
            class="trace-detail"
            :class="record.status"
          >
            <span v-if="(byName.get(item.name)?.length ?? 0) > 1" class="trace-round">
              {{ index + 1 }}.
            </span>
            {{ record.detail }}
          </p>
        </div>
      </li>
    </ol>

    <div v-if="rewritten" class="trace-note">
      <span class="trace-note-label">检索用的问题</span>
      <p class="trace-note-text">{{ rewritten }}</p>
    </div>

    <div v-if="expanded" class="trace-note warn">
      <span class="trace-note-label">注意</span>
      <p class="trace-note-text">首次检索分数偏低，已自动扩大召回范围重试一次。</p>
    </div>

    <div v-if="models" class="trace-models">
      <div class="section-title models-title">本次使用的模型</div>
      <div v-if="models.llm" class="model-row">
        <span class="model-kind">对话</span>
        <span class="model-name">{{ models.llm.model }}</span>
      </div>
      <div v-if="models.embedding" class="model-row">
        <span class="model-kind">向量</span>
        <span class="model-name">{{ models.embedding.model }}</span>
      </div>
      <div class="model-row">
        <span class="model-kind">重排</span>
        <span v-if="models.rerank" class="model-name">{{ models.rerank.model }}</span>
        <span v-else class="model-name muted">未启用</span>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.trace {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  background: var(--bg-1);
  padding: var(--sp-3);
  gap: var(--sp-3);
}

.trace-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}
.trace-head-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.collapse-btn {
  width: 20px;
  height: 20px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.collapse-btn:hover {
  background: var(--bg-2);
  border-color: var(--line-strong);
  color: var(--text-0);
}

.trace-total {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}

.trace-empty {
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.7;
  padding: var(--sp-2) 0;
}

.trace-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.trace-item {
  display: flex;
  gap: 9px;
  align-items: flex-start;
}
.trace-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  margin-top: 6px;
  flex: none;
}
.trace-body {
  min-width: 0;
  flex: 1;
}
.trace-line {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.trace-label {
  font-size: 12.5px;
  color: var(--text-0);
}
.trace-time {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-2);
}
.trace-detail {
  margin: 2px 0 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--text-2);
  word-break: break-word;
}
.trace-detail.failed {
  color: var(--danger);
}
.trace-detail.skipped {
  color: var(--text-3, var(--text-2));
  font-style: italic;
}
.trace-round {
  font-family: var(--font-mono);
  color: var(--text-2);
}

/* 状态点。
   ⚠️ 这几个 class 必须在这里定义：本组件是 scoped 样式，
   全局 theme.css 里的 .dot-ok / .dot-idle / .dot-bad 挂不到
   这里的 .trace-dot 上（之前只写了 dot-warn / dot-skip，
   结果每步跑完都不变色，用户看到的就是「跑完了也没变绿」）。 */
.dot-ok {
  background: var(--ok);
}
.dot-idle {
  background: var(--text-2);
}
.dot-bad {
  background: var(--danger);
}
.dot-warn {
  background: var(--warn);
  animation: pulse 1s ease-in-out infinite;
}
.dot-skip {
  background: transparent;
  border: 1px solid var(--line-strong);
}
@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.trace-note {
  border-left: 2px solid var(--line-strong);
  padding-left: 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.trace-note.warn {
  border-color: var(--warn);
}
.trace-note-label {
  font-size: 10.5px;
  color: var(--text-2);
}
.trace-note-text {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-1);
  line-height: 1.55;
  word-break: break-word;
}

.trace-models {
  margin-top: auto;
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.models-title {
  margin-bottom: 2px;
}
.model-row {
  display: flex;
  gap: 8px;
  font-size: 11px;
}
.model-kind {
  color: var(--text-2);
  width: 28px;
  flex: none;
}
.model-name {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.model-name.muted {
  color: var(--text-2);
}
</style>