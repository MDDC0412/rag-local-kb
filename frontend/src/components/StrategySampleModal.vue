<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { docsApi, type StrategySampleDetail } from '@/api/documents'

const props = defineProps<{
  strategy: string
  label: string
  chunkSize: number
  overlap: number
}>()

const emit = defineEmits<{ close: [] }>()

const detail = ref<StrategySampleDetail | null>(null)
const loading = ref(true)
const error = ref('')

/** 样例里也允许调块大小，让用户直接看到同一个排版下切分的变化。 */
const size = ref(props.chunkSize)
const over = ref(props.overlap)

async function fetchSample() {
  loading.value = true
  error.value = ''
  try {
    detail.value = await docsApi.sample(props.strategy, size.value, over.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

const docLines = computed(() => (detail.value?.document ?? '').split('\n'))

function isHeading(line: string): boolean {
  return /^#{1,4}\s/.test(line.trim())
}

watch([size, over], fetchSample, { immediate: true })
</script>

<template>
  <Teleport to="body">
    <div class="overlay" @click.self="emit('close')">
      <div class="modal">
        <header class="modal-head">
          <div class="head-text">
            <h3>「{{ label }}」适合的文档长这样</h3>
            <p v-if="detail" class="head-title">{{ detail.title }}</p>
          </div>
          <button class="btn btn-sm btn-quiet" @click="emit('close')">关闭</button>
        </header>

        <div class="modal-body">
          <p v-if="error" class="error">{{ error }}</p>
          <p v-if="loading" class="loading">加载中…</p>

          <template v-if="detail && !loading">
            <section class="why">
              <p class="why-text">{{ detail.why }}</p>
              <ul class="traits">
                <li v-for="trait in detail.traits" :key="trait">{{ trait }}</li>
              </ul>
            </section>

            <section class="controls">
              <div class="ctrl">
                <label>块大小 <span class="num">{{ size }}</span> token</label>
                <input v-model.number="size" type="range" min="128" max="512" step="32" />
              </div>
              <div class="ctrl">
                <label>重叠 <span class="num">{{ over }}</span> token</label>
                <input v-model.number="over" type="range" min="0" max="128" step="16" />
              </div>
              <div v-if="detail.count" class="ctrl-stat">
                <span class="num">{{ detail.count }}</span> 块 ·
                <span class="num">{{ detail.avg_tokens }}</span> token/块
              </div>
            </section>

            <p v-if="detail.note" class="note">{{ detail.note }}</p>

            <section class="panes">
              <div class="pane">
                <div class="pane-head">
                  <span class="section-title">文档排版</span>
                  <span class="hint-text">{{ detail.char_count }} 字</span>
                </div>
                <div class="pane-body">
                  <div class="doc-view">
                    <div
                      v-for="(line, i) in docLines"
                      :key="i"
                      class="doc-line"
                      :class="{
                        h1: /^#\s/.test(line.trim()),
                        h2: /^##\s/.test(line.trim()),
                        h3: /^###\s/.test(line.trim()),
                        'is-blank': !line.trim(),
                        'is-table': line.trim().startsWith('|'),
                      }"
                    >
                      {{ line || ' ' }}
                    </div>
                  </div>
                </div>
              </div>

              <div class="pane">
                <div class="pane-head">
                  <span class="section-title">按此策略切分</span>
                  <span class="hint-text">{{ detail.count }} 块</span>
                </div>
                <div class="pane-body">
                  <div v-for="chunk in detail.chunks" :key="chunk.seq" class="chunk">
                    <div class="chunk-head">
                      <span class="chunk-seq">#{{ chunk.seq + 1 }}</span>
                      <span class="chunk-meta">{{ chunk.token_count }} token</span>
                      <span v-if="chunk.section_path" class="chunk-path">
                        {{ chunk.section_path }}
                      </span>
                    </div>
                    <pre class="chunk-text">{{ chunk.content }}</pre>
                  </div>
                  <p v-if="!detail.chunks.length && !detail.note" class="empty">
                    这个策略没有可展示的切分结果。
                  </p>
                </div>
              </div>
            </section>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(1, 4, 9, 0.66);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: var(--sp-4);
}
.modal {
  width: min(1200px, 100%);
  max-height: 90vh;
  overflow: auto;
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-lg);
  box-shadow: 0 16px 48px rgba(1, 4, 9, 0.6);
  animation: fade-up 200ms var(--ease) both;
}
.modal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  background: var(--bg-1);
  z-index: 2;
}
.head-text h3 {
  font-size: 14.5px;
  margin-bottom: 3px;
}
.head-title {
  margin: 0;
  font-size: 12.5px;
  color: var(--text-2);
}
.modal-body {
  padding: var(--sp-4) var(--sp-5) var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.why {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.why-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--text-1);
  max-width: 78ch;
}
.traits {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  font-size: 12.5px;
  color: var(--text-2);
}

.controls {
  display: flex;
  align-items: flex-end;
  gap: var(--sp-5);
  padding: var(--sp-3) var(--sp-4);
  background: var(--bg-2);
  border-radius: var(--r-md);
}
.ctrl {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 150px;
}
.ctrl > label {
  font-size: 12px;
  color: var(--text-1);
}
input[type="range"] {
  width: 100%;
  accent-color: var(--accent);
  height: 4px;
}
.ctrl-stat {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-2);
  font-family: var(--font-mono);
}

.note {
  margin: 0;
  padding: 8px 12px;
  background: var(--warn-soft);
  color: var(--warn);
  border-radius: var(--r-md);
  font-size: 12.5px;
}

.panes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3);
  align-items: start;
}
.pane {
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--bg-0);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.pane-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px var(--sp-3);
  border-bottom: 1px solid var(--line);
  background: var(--bg-2);
}
.pane-body {
  padding: var(--sp-3);
  max-height: 46vh;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 文档排版预览：标题按层级加粗，表格用等宽 */
.doc-view {
  display: flex;
  flex-direction: column;
}
.doc-line {
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
}
.doc-line.h1 {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-0);
  margin: 6px 0 3px;
}
.doc-line.h2 {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-0);
  margin: 5px 0 2px;
}
.doc-line.h3 {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--text-0);
  margin: 4px 0 2px;
}
.doc-line.is-blank {
  height: 8px;
}
.doc-line.is-table {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-2);
}

.chunk {
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  padding: 8px 10px;
  background: var(--bg-1);
}
.chunk-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.chunk-seq {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}
.chunk-meta {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-2);
}
.chunk-path {
  margin-left: auto;
  font-size: 11px;
  color: var(--accent);
  background: var(--accent-soft);
  padding: 0 6px;
  border-radius: var(--r-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 55%;
}
.chunk-text {
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font);
}

.loading,
.empty {
  margin: 0;
  padding: var(--sp-4);
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
}
.error {
  margin: 0;
  padding: 8px 12px;
  background: var(--danger-soft);
  color: var(--danger);
  border-radius: var(--r-md);
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .panes {
    grid-template-columns: 1fr;
  }
}
</style>