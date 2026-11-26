<script setup lang="ts">
/** 前 32 维的横向条形图：正值向右、负值向左。 */

import { computed } from 'vue'

const props = defineProps<{
  values: number[]
}>()

/** 以绝对值最大的一维定标，这样小向量也看得见形状 */
const scale = computed(() => {
  const peak = Math.max(...props.values.map((v) => Math.abs(v)), 1e-9)
  return peak
})

function widthOf(value: number): number {
  return (Math.abs(value) / scale.value) * 50
}

const rows = computed(() =>
  props.values.map((value, index) => ({
    index,
    value,
    width: widthOf(value),
    positive: value >= 0,
  })),
)
</script>

<template>
  <div class="bars">
    <div v-for="row in rows" :key="row.index" class="bar-row">
      <span class="bar-idx num">{{ row.index }}</span>
      <div class="bar-track">
        <span class="bar-axis" />
        <span
          class="bar-fill"
          :class="row.positive ? 'pos' : 'neg'"
          :style="{
            width: `${row.width}%`,
            left: row.positive ? '50%' : `${50 - row.width}%`,
          }"
        />
      </div>
      <span class="bar-val num" :class="row.positive ? 'pos' : 'neg'">
        {{ row.value >= 0 ? '+' : '' }}{{ row.value.toFixed(4) }}
      </span>
    </div>
  </div>
</template>

<style scoped>
.bars {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.bar-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.bar-idx {
  flex: none;
  width: 22px;
  text-align: right;
  font-size: 10px;
  color: var(--text-2);
}

.bar-track {
  position: relative;
  flex: 1 1 auto;
  height: 12px;
  min-width: 0;
  background: var(--bg-2);
  border-radius: 2px;
  overflow: hidden;
}

.bar-axis {
  position: absolute;
  left: 50%;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--line-strong);
}

.bar-fill {
  position: absolute;
  top: 2px;
  bottom: 2px;
  border-radius: 1px;
}
.bar-fill.pos {
  background: var(--accent);
}
.bar-fill.neg {
  background: var(--danger);
}

.bar-val {
  flex: none;
  width: 62px;
  text-align: right;
  font-size: 10.5px;
}
.bar-val.pos {
  color: var(--accent);
}
.bar-val.neg {
  color: var(--danger);
}
</style>