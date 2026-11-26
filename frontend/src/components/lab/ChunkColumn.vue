<script setup lang="ts">
/** 分块栏：一种策略切出来的块列表，被硬切断的块末尾打红标。 */

import type { ChunkColumn } from '@/api/lab'

const props = defineProps<{
  column: ChunkColumn
  /** 展开的块太多时折叠，避免页面无限长 */
  collapsed: boolean
}>()

const emit = defineEmits<{ toggle: [] }>()

const VISIBLE = 6

function visibleCount(): number {
  return props.collapsed ? Math.min(VISIBLE, props.column.chunks.length) : props.column.chunks.length
}

function tail(content: string): string {
  // 展示块尾 12 个字符，让用户看到"断在哪儿"
  return content.slice(-12)
}
</script>

<template>
  <div class="col">
    <header class="col-head">
      <span class="col-label">{{ column.label }}</span>
      <span class="col-stat num">
        {{ column.count }} 块 · 均 {{ column.avg_tokens }} token
      </span>
    </header>

    <div v-if="column.error" class="col-error">
      <span class="tag tag-danger">这一栏算不出来</span>
      <p class="col-error-text">{{ column.error }}</p>
    </div>

    <template v-else>
      <div v-if="column.cut_count" class="col-cut">
        <span class="tag tag-danger">{{ column.cut_count }} 块被硬切断</span>
        <span class="hint-text">结尾不是句末标点，意思被截断在半句上</span>
      </div>
      <div v-else class="col-cut">
        <span class="tag tag-ok">没有被切断</span>
        <span class="hint-text">每块都在句末或段落边界收住</span>
      </div>

      <ol class="chunk-list">
        <li v-for="chunk in column.chunks.slice(0, visibleCount())" :key="chunk.seq" class="chunk">
          <div class="chunk-head">
            <span class="chunk-seq num">#{{ chunk.seq + 1 }}</span>
            <span class="chunk-meta num">{{ chunk.token_count }} token</span>
            <span v-if="chunk.cut" class="chunk-flag" title="结尾被硬切断">硬切</span>
          </div>
          <p class="chunk-body">
            {{ chunk.content }}
            <span v-if="chunk.cut" class="cut-mark" :title="`结尾是「${tail(chunk.content)}」`">
              此处被切断
            </span>
          </p>
        </li>
      </ol>

      <button
        v-if="column.chunks.length > VISIBLE"
        class="btn btn-quiet btn-sm col-toggle"
        @click="emit('toggle')"
      >
        {{ collapsed ? `展开全部 ${column.chunks.length} 块` : '收起' }}
      </button>
    </template>
  </div>
</template>

<style scoped>
.col {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  padding: var(--sp-3);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}

.col-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}

.col-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-0);
}

.col-stat {
  flex: none;
  font-size: 11px;
  color: var(--text-2);
}

.col-cut {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.col-error {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  align-items: flex-start;
}
.col-error-text {
  margin: 0;
  font-size: 12px;
  color: var(--danger);
  line-height: 1.6;
  word-break: break-word;
}

.chunk-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.chunk {
  padding: var(--sp-2);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}

.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.chunk-seq {
  font-size: 10.5px;
  color: var(--accent);
}
.chunk-meta {
  font-size: 10.5px;
  color: var(--text-2);
}

.chunk-flag {
  flex: none;
  padding: 0 5px;
  font-size: 10px;
  line-height: 15px;
  color: var(--danger);
  background: var(--danger-soft);
  border-radius: 3px;
}

.chunk-body {
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-1);
  word-break: break-word;
  white-space: pre-wrap;
}

.cut-mark {
  display: inline-block;
  margin-left: 4px;
  padding: 0 5px;
  font-size: 10.5px;
  color: var(--danger);
  background: var(--danger-soft);
  border-radius: 3px;
  white-space: nowrap;
}

.col-toggle {
  align-self: flex-start;
}
</style>