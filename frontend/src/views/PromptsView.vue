<script setup lang="ts">
/** 提示词模板库：按类型分组，可编辑自定义模板，内置模板需另存副本。 */
import { computed, onMounted, ref } from 'vue'
import { ApiError } from '@/api/http'
import {
  promptsApi,
  type PromptKind,
  type PromptKindGroup,
  type PromptTemplate,
} from '@/api/prompts'

const groups = ref<PromptKindGroup[]>([])
const loading = ref(false)
const busy = ref('')
const error = ref('')
const notice = ref('')

// 编辑态
const editingId = ref<number | null>(null)
const draftName = ref('')
const draftContent = ref('')
const editingIsBuiltin = computed(
  () => findById(editingId.value)?.is_builtin ?? false,
)

// 新建态
const creating = ref(false)
const createKind = ref<PromptKind>('system')
const createName = ref('')
const createContent = ref('')

const KIND_OPTIONS: Array<{ value: PromptKind; label: string }> = [
  { value: 'system', label: '系统提示词' },
  { value: 'optimize', label: '提问优化' },
  { value: 'query_rewrite', label: '查询改写' },
  { value: 'parse', label: '文档解析' },
  { value: 'chunk', label: '分块' },
  { value: 'tag', label: '标签分类' },
]

function findById(id: number | null): PromptTemplate | undefined {
  if (id === null) return undefined
  for (const group of groups.value) {
    const hit = group.items.find((item) => item.id === id)
    if (hit) return hit
  }
  return undefined
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    groups.value = await promptsApi.list()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function startEdit(template: PromptTemplate) {
  error.value = ''
  notice.value = ''
  editingId.value = template.id
  draftName.value = template.name
  draftContent.value = template.content
}

function cancelEdit() {
  editingId.value = null
  draftName.value = ''
  draftContent.value = ''
}

async function saveEdit() {
  if (editingId.value === null) return
  busy.value = 'save'
  error.value = ''
  try {
    await promptsApi.update(editingId.value, {
      name: draftName.value.trim(),
      content: draftContent.value,
    })
    notice.value = '已保存'
    cancelEdit()
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message
  } finally {
    busy.value = ''
  }
}

/** 另存为副本：内置模板要改，必须先走这条。 */
async function duplicate(template: PromptTemplate) {
  busy.value = `dup-${template.id}`
  error.value = ''
  notice.value = ''
  try {
    const copy = await promptsApi.duplicate(template.id)
    notice.value = `已创建副本「${copy.name}」，可以编辑它`
    await load()
    startEdit(copy)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function resetTemplate(template: PromptTemplate) {
  if (!confirm(`把「${template.name}」恢复成内置原文？当前改动会丢失。`)) return
  busy.value = `reset-${template.id}`
  error.value = ''
  notice.value = ''
  try {
    await promptsApi.reset(template.id)
    notice.value = '已恢复内置原文'
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

async function removeTemplate(template: PromptTemplate) {
  if (!confirm(`删除「${template.name}」？`)) return
  busy.value = `del-${template.id}`
  error.value = ''
  try {
    await promptsApi.remove(template.id)
    notice.value = '已删除'
    await load()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = ''
  }
}

function openCreate() {
  creating.value = true
  createName.value = ''
  createContent.value = ''
  error.value = ''
  notice.value = ''
}

async function submitCreate() {
  if (!createName.value.trim() || !createContent.value.trim()) {
    error.value = '名称和内容都不能为空'
    return
  }
  busy.value = 'create'
  error.value = ''
  try {
    await promptsApi.create({
      kind: createKind.value,
      name: createName.value.trim(),
      content: createContent.value,
    })
    notice.value = '已新建模板'
    creating.value = false
    await load()
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message
  } finally {
    busy.value = ''
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <div>
        <p class="desc">
          这些提示词决定各功能怎么跟模型说话。内置模板是兜底，只读 ——
          要改请先「另存为副本」，再把副本设为默认。
        </p>
      </div>
      <button class="btn btn-sm btn-primary" @click="openCreate">新建模板</button>
    </header>

    <p v-if="error" class="notice bad">{{ error }}</p>
    <p v-if="notice" class="notice ok">{{ notice }}</p>

    <!-- 新建表单 -->
    <section v-if="creating" class="card editor">
      <div class="editor-head">
        <h3 class="editor-title">新建模板</h3>
        <button class="btn btn-sm btn-quiet" @click="creating = false">取消</button>
      </div>
      <div class="editor-fields">
        <div class="field">
          <label>类型</label>
          <select v-model="createKind">
            <option v-for="opt in KIND_OPTIONS" :key="opt.value" :value="opt.value">
              {{ opt.label }}
            </option>
          </select>
        </div>
        <div class="field">
          <label>名称</label>
          <input v-model="createName" type="text" placeholder="例如：严谨模式" />
        </div>
      </div>
      <div class="field">
        <label>内容</label>
        <textarea v-model="createContent" rows="10" placeholder="提示词正文…" />
      </div>
      <div class="editor-actions">
        <button class="btn btn-primary" :disabled="busy === 'create'" @click="submitCreate">
          {{ busy === 'create' ? '创建中…' : '创建' }}
        </button>
      </div>
    </section>

    <div v-if="loading" class="loading">读取中…</div>

    <!-- 模板分组 -->
    <section v-for="group in groups" :key="group.kind" class="group">
      <div class="group-head">
        <h3 class="group-title">{{ group.label }}</h3>
        <span class="group-count">{{ group.items.length }} 条</span>
      </div>

      <div v-for="tpl in group.items" :key="tpl.id" class="card item">
        <!-- 编辑态 -->
        <template v-if="editingId === tpl.id">
          <div class="item-head">
            <input v-model="draftName" type="text" class="name-input" />
            <div class="item-actions">
              <button class="btn btn-sm btn-primary" :disabled="busy === 'save'" @click="saveEdit">
                {{ busy === 'save' ? '保存中…' : '保存' }}
              </button>
              <button class="btn btn-sm btn-quiet" @click="cancelEdit">取消</button>
            </div>
          </div>
          <textarea v-model="draftContent" rows="12" class="content-input" />
          <p v-if="editingIsBuiltin" class="warn-line">内置模板不可直接保存</p>
        </template>

        <!-- 阅读态 -->
        <template v-else>
          <div class="item-head">
            <div class="item-title">
              <span class="name">{{ tpl.name }}</span>
              <span v-if="tpl.is_builtin" class="tag">内置 · 只读</span>
              <span v-if="tpl.is_default" class="tag tag-accent">默认</span>
            </div>
            <div class="item-actions">
              <button
                v-if="!tpl.is_builtin"
                class="btn btn-sm btn-quiet"
                :disabled="!!busy"
                @click="startEdit(tpl)"
              >
                编辑
              </button>
              <button
                class="btn btn-sm btn-quiet"
                :disabled="!!busy"
                :title="tpl.is_builtin ? '复制一份可编辑的副本' : '复制一份副本'"
                @click="duplicate(tpl)"
              >
                {{ busy === `dup-${tpl.id}` ? '复制中…' : '另存副本' }}
              </button>
              <button
                v-if="!tpl.is_builtin"
                class="btn btn-sm btn-quiet"
                :disabled="!!busy"
                title="恢复成内置同名模板的内容"
                @click="resetTemplate(tpl)"
              >
                重置
              </button>
              <button
                v-if="!tpl.is_builtin"
                class="btn btn-sm btn-quiet btn-danger"
                :disabled="!!busy"
                @click="removeTemplate(tpl)"
              >
                删除
              </button>
            </div>
          </div>
          <pre class="content">{{ tpl.content }}</pre>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.page {
  max-width: 900px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}

.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-4);
}
.desc {
  margin: 0;
  max-width: 62ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.7;
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
.notice.bad {
  background: var(--danger-soft);
  color: var(--danger);
}

.loading {
  padding: var(--sp-5);
  color: var(--text-2);
  font-size: 13px;
}

/* 新建 / 编辑表单 */
.editor {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.editor-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.editor-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
}
.editor-fields {
  display: flex;
  gap: var(--sp-3);
}
.editor-actions {
  display: flex;
  gap: var(--sp-2);
}

/* 分组 */
.group {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.group-head {
  display: flex;
  align-items: baseline;
  gap: var(--sp-2);
  padding-bottom: 6px;
  border-bottom: 1px solid var(--line);
}
.group-title {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-0);
}
.group-count {
  font-size: 11.5px;
  color: var(--text-2);
  font-family: var(--font-mono);
}

/* 单条模板 */
.item {
  padding: var(--sp-3) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-3);
}
.item-title {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}
.name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-actions {
  display: flex;
  gap: 4px;
  flex: none;
}

.name-input {
  flex: 1;
  min-width: 0;
}

.content {
  margin: 0;
  padding: 10px 12px;
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 240px;
  overflow-y: auto;
}
.content-input {
  height: auto;
  min-height: 200px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.7;
  padding: 10px 12px;
  resize: vertical;
}

.warn-line {
  margin: 0;
  font-size: 11.5px;
  color: var(--warn);
}
</style>