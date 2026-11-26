<script setup lang="ts">
/** 全量维度的热力条：一排色块，颜色深浅表示数值大小，正负用色相区分。 */

import { computed } from 'vue'

const props = defineProps<{
  values: number[]
  /** 原始维度，用于文案「1024 维压缩成 256 格」 */
  dim: number
}>()

const scale = computed(() => Math.max(...props.values.map((v) => Math.abs(v)), 1e-9))

const cells = computed(() =>
  props.values.map((value) => {
    // 0 → 全透明，±峰值 → 满色
    const alpha = Math.min(1, Math.abs(value) / scale.value)
    return {
      alpha: 0.12 + alpha * 0.88,
      positive: value >= 0,
    }
  }),
)

const perCell = computed(() =>
  props.values.length ? Math.round(props.dim / props.values.length) : 0,
)
</script>

<template>
  <div class="heat-wrap">
    <div class="heat" role="img" :aria-label="`${dim} 维向量压缩成 ${values.length} 格的热力条`">
      <span
        v-for="(cell, index) in cells"
        :key="index"
        class="cell"
        :class="cell.positive ? 'pos' : 'neg'"
        :style="{ opacity: cell.alpha }"
      />
    </div>
    <p class="hint-text heat-caption">
      {{ dim }} 维压缩成 {{ values.length }} 格，每格约 {{ perCell }} 维的均值。
      蓝色 = 正值，红色 = 负值，越亮绝对值越大 —— 人眼读不出规律，机器却能算。
    </p>
  </div>
</template>

<style scoped>
.heat-wrap {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.heat {
  display: flex;
  gap: 1px;
  height: 34px;
  width: 100%;
  min-width: 0;
  padding: 2px;
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  overflow: hidden;
}

.cell {
  flex: 1 1 0;
  min-width: 0;
  border-radius: 1px;
}
.cell.pos {
  background: var(--accent);
}
.cell.neg {
  background: var(--danger);
}

.heat-caption {
  margin: 0;
}
</style>