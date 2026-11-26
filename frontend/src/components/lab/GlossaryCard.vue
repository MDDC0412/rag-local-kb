<script setup lang="ts">
/** 术语卡：可折叠，点词条会通知父组件高亮对应可视化区块。 */

import { ref } from 'vue'
import { GLOSSARY } from '@/api/lab'

const props = defineProps<{ activeAnchor: string }>()
const emit = defineEmits<{ focus: [anchor: string] }>()

const open = ref(true)

function focus(anchor?: string) {
  if (anchor) emit('focus', anchor)
}
</script>

<template>
  <aside class="glossary" :class="{ open }">
    <button class="glossary-head" @click="open = !open">
      <span class="section-title">术语卡</span>
      <span class="glossary-count num">{{ GLOSSARY.length }}</span>
      <span class="glossary-chevron">{{ open ? '−' : '+' }}</span>
    </button>

    <ul v-if="open" class="glossary-list">
      <li v-for="entry in GLOSSARY" :key="entry.term" class="entry">
        <button
          class="entry-term"
          :class="{ 'is-active': entry.anchor === props.activeAnchor }"
          @click="focus(entry.anchor)"
        >
          {{ entry.term }}
        </button>
        <p class="entry-plain">{{ entry.plain }}</p>
      </li>
    </ul>
  </aside>
</template>

<style scoped>
.glossary {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  min-width: 0;
  padding: var(--sp-3);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  align-self: start;
  max-height: calc(100vh - 140px);
}

.glossary-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  cursor: pointer;
  text-align: left;
}

.glossary-count {
  font-size: 10.5px;
  color: var(--text-2);
}

.glossary-chevron {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-2);
}

.glossary.open .glossary-list {
  overflow-y: auto;
}

.glossary-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-height: 0;
}

.entry {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-left: 7px;
  border-left: 2px solid var(--line);
}
.entry:hover {
  border-left-color: var(--line-strong);
}

.entry-term {
  padding: 0;
  border: none;
  background: transparent;
  color: var(--text-0);
  font-family: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  text-align: left;
  cursor: pointer;
}
.entry-term:hover {
  color: var(--accent);
}
.entry-term.is-active {
  color: var(--accent);
}

.entry-plain {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--text-2);
  word-break: break-word;
}
</style>