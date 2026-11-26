<script setup lang="ts">
/**
 * 夹角图：把 A、B 两条向量画在单位圆上。
 *
 * 坐标由后端给出，且已归一化到单位圆（A 恒为 (1,0)），
 * 所以这个平面里的夹角与高维空间中的夹角**严格相等**，不是示意图。
 */

import { computed } from 'vue'

const props = defineProps<{
  a: number[]
  b: number[]
  cosine: number
  angleDeg: number
  labelA: string
  labelB: string
}>()

const SIZE = 260
const CX = SIZE / 2
const CY = SIZE / 2
const R = SIZE / 2 - 34 // 留出标签空间

function toPoint(vector: number[]): { x: number; y: number } {
  // SVG 的 y 轴向下，数学的 y 轴向上，所以取负
  return { x: CX + (vector[0] ?? 0) * R, y: CY - (vector[1] ?? 0) * R }
}

const pointA = computed(() => toPoint(props.a))
const pointB = computed(() => toPoint(props.b))

/** 夹角弧线：从 A 的角度扫到 B 的角度（都换算成 SVG 坐标下的角度） */
const arc = computed(() => {
  const radius = R * 0.42
  const angleA = Math.atan2(-(props.a[1] ?? 0), props.a[0] ?? 1)
  const angleB = Math.atan2(-(props.b[1] ?? 0), props.b[0] ?? 1)
  const start = {
    x: CX + radius * Math.cos(angleA),
    y: CY + radius * Math.sin(angleA),
  }
  const end = {
    x: CX + radius * Math.cos(angleB),
    y: CY + radius * Math.sin(angleB),
  }
  // 扫过角度小于 180° 用 0，否则用 1；这里夹角恒在 [0,180]，取 0 即可
  return {
    path: `M ${start.x} ${start.y} A ${radius} ${radius} 0 0 0 ${end.x} ${end.y}`,
    mid: {
      x: CX + (radius + 16) * Math.cos((angleA + angleB) / 2),
      y: CY + (radius + 16) * Math.sin((angleA + angleB) / 2),
    },
  }
})

/** x 轴参考线（A 的方向） */
const axisEnd = computed(() => ({ x: CX + R, y: CY }))

const colorOf = computed(() => {
  if (props.cosine >= 0.8) return 'var(--ok)'
  if (props.cosine >= 0.5) return 'var(--accent)'
  if (props.cosine >= 0.35) return 'var(--warn)'
  return 'var(--text-2)'
})

function truncate(text: string, limit = 9): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}
</script>

<template>
  <svg :viewBox="`0 0 ${SIZE} ${SIZE}`" class="angle-plot" role="img" :aria-label="`A 与 B 的夹角 ${angleDeg} 度`">
    <!-- 单位圆与坐标轴 -->
    <circle :cx="CX" :cy="CY" :r="R" class="grid-circle" />
    <line :x1="CX" :y1="CY" :x2="axisEnd.x" :y2="axisEnd.y" class="grid-axis" />
    <line :x1="CX" :y1="CY - R" :x2="CX" :y2="CY + R" class="grid-axis-faint" />

    <!-- 夹角弧线 -->
    <path :d="arc.path" class="arc" :style="{ stroke: colorOf }" />
    <text :x="arc.mid.x" :y="arc.mid.y" class="arc-label num" :style="{ fill: colorOf }">
      {{ angleDeg }}°
    </text>

    <!-- A 向量 -->
    <line :x1="CX" :y1="CY" :x2="pointA.x" :y2="pointA.y" class="vector vector-a" />
    <circle :cx="pointA.x" :cy="pointA.y" r="4" class="tip tip-a" />
    <text :x="pointA.x + 8" :y="pointA.y + 4" class="vec-label">A</text>

    <!-- B 向量 -->
    <line :x1="CX" :y1="CY" :x2="pointB.x" :y2="pointB.y" class="vector vector-b" />
    <circle :cx="pointB.x" :cy="pointB.y" r="4" class="tip tip-b" />
    <text :x="pointB.x + 8" :y="pointB.y + 4" class="vec-label">B</text>

    <circle :cx="CX" :cy="CY" r="2.5" class="origin" />
  </svg>

  <div class="legend">
    <div class="legend-row">
      <span class="legend-key">A</span>
      <span class="legend-text">{{ truncate(labelA) }}</span>
    </div>
    <div class="legend-row">
      <span class="legend-key">B</span>
      <span class="legend-text">{{ truncate(labelB) }}</span>
    </div>
    <div class="legend-row">
      <span class="legend-key">cos</span>
      <span class="legend-text num">{{ cosine.toFixed(4) }}</span>
    </div>
  </div>
</template>

<style scoped>
.angle-plot {
  width: 100%;
  max-width: 300px;
  height: auto;
  display: block;
}

.grid-circle {
  fill: none;
  stroke: var(--line);
  stroke-width: 1;
}
.grid-axis {
  stroke: var(--line-strong);
  stroke-width: 1;
}
.grid-axis-faint {
  stroke: var(--line);
  stroke-width: 1;
  stroke-dasharray: 3 4;
}

.arc {
  fill: none;
  stroke-width: 2;
}

.vector {
  stroke-width: 2.5;
  stroke-linecap: round;
}
.vector-a {
  stroke: var(--accent);
}
.vector-b {
  stroke: var(--ok);
}

.tip-a {
  fill: var(--accent);
}
.tip-b {
  fill: var(--ok);
}

.origin {
  fill: var(--text-2);
}

.vec-label {
  font-family: var(--font-mono);
  font-size: 11px;
  fill: var(--text-1);
}

.arc-label {
  font-size: 11px;
  text-anchor: middle;
  dominant-baseline: middle;
}

.legend {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: var(--sp-2);
}
.legend-row {
  display: flex;
  gap: 8px;
  font-size: 11.5px;
}
.legend-key {
  flex: none;
  width: 26px;
  font-family: var(--font-mono);
  color: var(--text-2);
}
.legend-text {
  color: var(--text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>