<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  STATUS_LABEL,
  STATUS_TONE,
  formatSize,
  type DocumentItem,
} from '@/api/documents'
import { useDocsStore } from '@/stores/docs'

const store = useDocsStore()
const router = useRouter()

const dragging = ref(false)
const notice = ref<{ tone: 'ok' | 'warn' | 'bad'; text: string } | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const stats = computed(() => [
  { label: '文档', value: store.stats.documents },
  { label: '分块', value: store.stats.chunks },
  { label: '已入库分块', value: store.stats.indexed_chunks },
  { label: '向量', value: store.stats.vectors },
])

function pickFiles() {
  fileInput.value?.click()
}

async function handleFiles(files: FileList | null) {
  if (!files || !files.length) return
  notice.value = null
  const list = Array.from(files)

  try {
    const result = await store.upload(list)
    const parts: string[] = []
    if (result.documents.length) parts.push(`已上传 ${result.documents.length} 个`)
    if (result.duplicates.length) parts.push(`跳过重复 ${result.duplicates.length} 个`)
    if (result.warnings.length) parts.push(result.warnings.length + ' 条提示')
    notice.value = {
      tone: result.warnings.length ? 'warn' : 'ok',
      text: parts.join('，') || '没有文件被处理',
    }
    if (result.warnings.length) {
      notice.value.text += '：' + result.warnings.join('；')
    }
  } catch (e) {
    notice.value = { tone: 'bad', text: (e as Error).message }
  }
}

function onDrop(event: DragEvent) {
  dragging.value = false
  handleFiles(event.dataTransfer?.files ?? null)
}

async function removeDoc(doc: DocumentItem) {
  if (!confirm(`删除「${doc.filename}」？向量、分块和原始文件都会一起清掉。`)) return
  try {
    await store.remove(doc.id)
  } catch (e) {
    alert((e as Error).message)
  }
}

function openDetail(doc: DocumentItem) {
  router.push(`/documents/${doc.id}`)
}

function formatDate(value: string): string {
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`
}

onMounted(() => store.load())
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="desc">
        支持 PDF、Word、Markdown、纯文本。上传后会先解析成文字，再进入分块预览，确认后才写入向量库。
      </p>
    </header>

    <!-- 上传区 -->
    <section
      class="dropzone"
      :class="{ active: dragging }"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
      @click="pickFiles"
    >
      <input
        ref="fileInput"
        type="file"
        multiple
        accept=".pdf,.docx,.md,.markdown,.txt"
        hidden
        @change="(e) => handleFiles((e.target as HTMLInputElement).files)"
      />
      <div class="dz-text">
        <span class="dz-main">{{ store.uploading ? '上传并解析中…' : '把文件拖到这里，或点击选择' }}</span>
        <span class="dz-sub">pdf / docx / md / txt，单个不超过 50MB，重复文件会自动跳过</span>
      </div>
    </section>

    <p v-if="notice" class="notice" :class="notice.tone">{{ notice.text }}</p>
    <p v-if="store.error" class="notice bad">{{ store.error }}</p>

    <!-- 统计 -->
    <section class="stats">
      <div v-for="item in stats" :key="item.label" class="stat">
        <div class="stat-value num">{{ item.value }}</div>
        <div class="stat-label">{{ item.label }}</div>
      </div>
    </section>

    <!-- 文件列表 -->
    <section class="list-wrap">
      <div class="list-head">
        <span class="section-title">文档</span>
        <button class="btn btn-sm btn-quiet" :disabled="store.loading" @click="store.load()">
          {{ store.loading ? '刷新中' : '刷新' }}
        </button>
      </div>

      <p v-if="!store.items.length" class="empty">还没有文档。先上传一份，看看解析和分块的效果。</p>

      <div v-for="doc in store.items" :key="doc.id" class="row" @click="openDetail(doc)">
        <div class="row-main">
          <div class="row-title">
            <span class="file-name">{{ doc.filename }}</span>
            <span class="tag" :class="STATUS_TONE[doc.status]">
              {{ STATUS_LABEL[doc.status] }}
            </span>
          </div>
          <div class="row-meta">
            <span>{{ doc.page_count }} 段</span>
            <span>{{ doc.char_count }} 字</span>
            <span>{{ formatSize(doc.size) }}</span>
            <span>{{ formatDate(doc.created_at) }}</span>
            <span v-if="doc.parse_mode === 'llm'" class="tag tag-accent">智能解析</span>
          </div>
          <p v-if="doc.error_msg" class="row-error">{{ doc.error_msg }}</p>
        </div>

        <div class="row-actions" @click.stop>
          <button class="btn btn-sm" @click="openDetail(doc)">查看分块</button>
          <button class="btn btn-sm btn-quiet btn-danger" @click="removeDoc(doc)">删除</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  max-width: 1000px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.desc {
  margin: 0;
  max-width: 62ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.7;
}

/* 上传区 */
.dropzone {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 108px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-lg);
  background: var(--bg-1);
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.dropzone:hover,
.dropzone.active {
  border-color: var(--accent);
  background: var(--accent-soft);
}
.dz-text {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  text-align: center;
}
.dz-main {
  font-size: 13.5px;
  color: var(--text-0);
}
.dz-sub {
  font-size: 12px;
  color: var(--text-2);
}

.notice {
  margin: 0;
  padding: 9px 12px;
  border-radius: var(--r-md);
  font-size: 12.5px;
  line-height: 1.6;
}
.notice.ok {
  background: var(--ok-soft);
  color: var(--ok);
}
.notice.warn {
  background: var(--warn-soft);
  color: var(--warn);
}
.notice.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

/* 统计 */
.stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
  padding: var(--sp-4) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.stat-value {
  font-size: 21px;
  font-weight: 600;
  line-height: 1.2;
}
.stat-label {
  font-size: 12px;
  color: var(--text-2);
  margin-top: 2px;
}

/* 列表 */
.list-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}

.empty {
  margin: 0;
  padding: var(--sp-5);
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-md);
}

.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: 13px var(--sp-4);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  cursor: pointer;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.row:hover {
  border-color: var(--line-strong);
  background: var(--bg-2);
}
.row-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.row-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.file-name {
  font-size: 13.5px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.row-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
}
.row-error {
  margin: 0;
  font-size: 12px;
  color: var(--danger);
}
.row-actions {
  display: flex;
  gap: 4px;
  flex: none;
}

@media (max-width: 760px) {
  .stats {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>