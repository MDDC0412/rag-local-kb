<script setup lang="ts">
import { computed, reactive, onMounted, ref } from 'vue'
import { ApiError } from '@/api/http'
import {
  KIND_LABEL,
  PRESETS,
  PROTOCOL_LABEL,
  providerApi,
  type Preset,
  type Provider,
  type ProviderDraft,
  type ProviderKind,
  type Protocol,
  type TestResult,
} from '@/api/providers'
import { useProvidersStore } from '@/stores/providers'

const store = useProvidersStore()

const kinds: ProviderKind[] = ['llm', 'embedding', 'rerank']

const protocolOptions = computed<Protocol[]>(() =>
  form.kind === 'rerank'
    ? ['dashscope_rerank', 'siliconflow_rerank', 'cohere_rerank']
    : ['openai'],
)

// ---------------- 列表 ----------------
const openGroups = reactive<Record<ProviderKind, boolean>>({
  llm: true,
  embedding: true,
  rerank: true,
})

// ---------------- 表单 ----------------
const editingId = ref<number | null>(null)
const formOpen = ref(false)
const saving = ref(false)
const formError = ref('')

const form = reactive<ProviderDraft>({
  kind: 'llm',
  name: '',
  base_url: '',
  api_key: '',
  model: '',
  protocol: 'openai',
  is_default: false,
})

const testState = ref<Record<number, { loading: boolean; result?: TestResult; error?: string }>>({})
const draftTest = reactive<{ loading: boolean; result?: TestResult; error: string }>({
  loading: false,
  result: undefined,
  error: '',
})

function resetForm() {
  form.kind = 'llm'
  form.name = ''
  form.base_url = ''
  form.api_key = ''
  form.model = ''
  form.protocol = 'openai'
  form.is_default = false
  formError.value = ''
  draftTest.result = undefined
  draftTest.error = ''
  editingId.value = null
}

function openCreate(kind?: ProviderKind) {
  resetForm()
  if (kind) {
    form.kind = kind
    form.protocol = kind === 'rerank' ? 'dashscope_rerank' : 'openai'
  }
  formOpen.value = true
}

function openEdit(p: Provider) {
  resetForm()
  editingId.value = p.id
  form.kind = p.kind
  form.name = p.name
  form.base_url = p.base_url
  form.api_key = ''
  form.model = p.model
  form.protocol = p.protocol
  form.is_default = p.is_default
  formOpen.value = true
}

function applyPreset(preset: Preset) {
  form.kind = preset.kind
  form.base_url = preset.base_url
  form.protocol = preset.protocol
  form.model = preset.model
  if (!form.name) form.name = preset.label.replace(/（.*?）/g, '')
}

function onKindChange() {
  form.protocol = form.kind === 'rerank' ? 'dashscope_rerank' : 'openai'
}

async function save() {
  formError.value = ''
  if (!form.name.trim()) return (formError.value = '请填写名称')
  if (!form.base_url.trim()) return (formError.value = '请填写接口地址')
  if (!form.model.trim()) return (formError.value = '请填写模型名')
  if (editingId.value === null && !form.api_key.trim()) {
    return (formError.value = '新建时必须填写 API Key')
  }

  saving.value = true
  try {
    if (editingId.value === null) {
      await store.create({ ...form })
    } else {
      const patch: Partial<ProviderDraft> = { ...form }
      if (!patch.api_key) delete patch.api_key
      await store.update(editingId.value, patch)
    }
    formOpen.value = false
  } catch (e) {
    formError.value = (e as Error).message
  } finally {
    saving.value = false
  }
}

async function runTest(id: number) {
  testState.value[id] = { loading: true }
  try {
    const result = await providerApi.test(id)
    testState.value[id] = { loading: false, result }
  } catch (e) {
    testState.value[id] = { loading: false, error: (e as Error).message }
  }
}

async function runDraftTest() {
  draftTest.error = ''
  draftTest.result = undefined
  if (!form.base_url || !form.model) {
    draftTest.error = '先填写接口地址和模型名'
    return
  }
  if (!form.api_key && editingId.value !== null) {
    draftTest.error = '编辑已有供应商时，请直接点卡片上的「测试连接」'
    return
  }
  draftTest.loading = true
  try {
    draftTest.result = await providerApi.testDraft({ ...form })
  } catch (e) {
    draftTest.error = e instanceof ApiError ? e.message : (e as Error).message
  } finally {
    draftTest.loading = false
  }
}

async function remove(p: Provider) {
  if (!confirm(`删除供应商「${p.name}」？`)) return
  try {
    await store.remove(p.id)
  } catch (e) {
    alert((e as Error).message)
  }
}

async function makeDefault(p: Provider) {
  try {
    await store.update(p.id, { is_default: true })
  } catch (e) {
    alert((e as Error).message)
  }
}

onMounted(() => store.load())
</script>

<template>
  <div class="page">
    <header class="page-head">
      <p class="page-desc">
        对话模型负责回答；向量模型把文本转成数字向量，建索引和检索都靠它；重排模型在候选结果里再精挑一遍。
        三者可以是同一家平台，也可以分开配。
      </p>
      <button class="btn btn-primary" @click="openCreate()">添加供应商</button>
    </header>

    <p v-if="store.error" class="alert">{{ store.error }}</p>

    <section v-for="kind in kinds" :key="kind" class="group">
      <div class="group-head">
        <button class="group-toggle" @click="openGroups[kind] = !openGroups[kind]">
          <span class="caret" :class="{ open: openGroups[kind] }">›</span>
          <span class="section-title">{{ KIND_LABEL[kind] }}</span>
          <span class="count">{{ store.byKind(kind).length }}</span>
        </button>
        <button class="btn btn-sm btn-quiet" @click="openCreate(kind)">添加</button>
      </div>

      <template v-if="openGroups[kind]">
        <p v-if="!store.byKind(kind).length" class="empty">
          {{
            kind === 'embedding'
              ? '还没有向量模型。没有它，文档无法入库，问题也无法检索。'
              : kind === 'rerank'
                ? '还没有重排模型。可以先跳过，检索会退化成向量加 BM25 的融合排序。'
                : '还没有对话模型。没有它，系统无法回答问题。'
          }}
        </p>

        <div v-for="p in store.byKind(kind)" :key="p.id" class="pcard">
          <div class="pcard-main">
            <div class="pcard-title">
              <span class="dot" :class="p.enabled ? 'dot-ok' : 'dot-idle'" />
              <span class="pcard-name">{{ p.name }}</span>
              <span v-if="p.is_default" class="tag tag-accent">默认</span>
            </div>
            <div class="pcard-fields">
              <div class="pf">
                <span class="pf-label">模型</span>
                <span class="pf-value mono">{{ p.model }}</span>
              </div>
              <div class="pf">
                <span class="pf-label">接口地址</span>
                <span class="pf-value mono ellipsis" :title="p.base_url">{{ p.base_url }}</span>
              </div>
              <div class="pf">
                <span class="pf-label">协议</span>
                <span class="pf-value">{{ PROTOCOL_LABEL[p.protocol] ?? p.protocol }}</span>
              </div>
              <div class="pf">
                <span class="pf-label">密钥</span>
                <span class="pf-value mono">{{ p.has_api_key ? p.api_key_masked : '未配置' }}</span>
              </div>
            </div>
          </div>

          <div class="pcard-side">
            <button class="btn btn-sm" :disabled="testState[p.id]?.loading" @click="runTest(p.id)">
              {{ testState[p.id]?.loading ? '测试中' : '测试连接' }}
            </button>
            <button v-if="!p.is_default" class="btn btn-sm btn-quiet" @click="makeDefault(p)">
              设为默认
            </button>
            <button class="btn btn-sm btn-quiet" @click="openEdit(p)">编辑</button>
            <button class="btn btn-sm btn-quiet btn-danger" @click="remove(p)">删除</button>
          </div>

          <div v-if="testState[p.id]?.error" class="result bad">{{ testState[p.id]?.error }}</div>
          <div v-else-if="testState[p.id]?.result" class="result good">
            <span class="dot dot-ok" />
            <span>{{ testState[p.id]?.result?.message }}</span>
            <span
              v-for="m in testState[p.id]?.result?.models?.slice(0, 6)"
              :key="m"
              class="tag"
              :class="{ 'tag-accent': m === p.model }"
            >
              {{ m }}
            </span>
            <span v-if="(testState[p.id]?.result?.model_count ?? 0) > 6" class="tag">
              +{{ (testState[p.id]?.result?.model_count ?? 0) - 6 }}
            </span>
            <span v-for="r in testState[p.id]?.result?.results ?? []" :key="r.index" class="tag mono">
              #{{ r.index }} {{ r.score.toFixed(3) }}
            </span>
          </div>
        </div>
      </template>
    </section>

    <!-- 表单弹层 -->
    <Teleport to="body">
      <div v-if="formOpen" class="overlay" @click.self="formOpen = false">
        <div class="modal">
          <header class="modal-head">
            <h3>{{ editingId === null ? '添加供应商' : '编辑供应商' }}</h3>
            <button class="btn btn-sm btn-quiet" @click="formOpen = false">关闭</button>
          </header>

          <div class="modal-body">
            <div v-if="editingId === null" class="presets">
              <div class="presets-label">常用配置</div>
              <div class="preset-row">
                <button v-for="p in PRESETS" :key="p.key" class="preset" @click="applyPreset(p)">
                  <span class="preset-name">{{ p.label }}</span>
                  <span class="preset-note">{{ p.note }}</span>
                </button>
              </div>
            </div>

            <div class="form-grid">
              <div class="field">
                <label>类型</label>
                <select v-model="form.kind" @change="onKindChange">
                  <option v-for="k in kinds" :key="k" :value="k">{{ KIND_LABEL[k] }}</option>
                </select>
              </div>

              <div class="field">
                <label>名称</label>
                <input v-model="form.name" type="text" placeholder="例如：阿里百炼-向量" />
              </div>

              <div class="field span-2">
                <label>接口地址</label>
                <input v-model="form.base_url" type="text" class="mono-input" placeholder="https://…/v1" />
                <span class="hint">
                  多写一层路径也没关系，系统会按协议拼出正确的接口地址；重排若用阿里原生协议，会自动取域名根。
                </span>
              </div>

              <div class="field span-2">
                <label>API Key{{ editingId !== null ? '（留空表示不修改）' : '' }}</label>
                <input
                  v-model="form.api_key"
                  type="password"
                  class="mono-input"
                  :placeholder="editingId !== null ? '已保存的密钥不回显' : 'sk-…'"
                  autocomplete="off"
                />
                <span class="hint">用 Fernet 加密后存入本地数据库，接口只返回打码后的形式。</span>
              </div>

              <div class="field">
                <label>模型名</label>
                <input v-model="form.model" type="text" class="mono-input" placeholder="模型 ID" />
              </div>

              <div class="field">
                <label>协议</label>
                <select v-model="form.protocol">
                  <option v-for="p in protocolOptions" :key="p" :value="p">
                    {{ PROTOCOL_LABEL[p] ?? p }}
                  </option>
                </select>
              </div>
            </div>

            <label class="check">
              <input v-model="form.is_default" type="checkbox" />
              <span>设为该类型的默认供应商</span>
            </label>

            <p v-if="formError" class="alert">{{ formError }}</p>
          </div>

          <footer class="modal-foot">
            <div class="draft-test foot-left">
              <button class="btn btn-sm" :disabled="draftTest.loading" @click="runDraftTest">
                {{ draftTest.loading ? '测试中…' : '测试连接' }}
              </button>
              <span v-if="draftTest.error" class="foot-msg bad">{{ draftTest.error }}</span>
              <template v-else-if="draftTest.result">
                <span class="foot-msg good">{{ draftTest.result.message }}</span>
                <span v-for="r in draftTest.result.results ?? []" :key="r.index" class="tag mono">
                  #{{ r.index }} {{ r.score.toFixed(3) }}
                </span>
                <span
                  v-if="draftTest.result.results?.length"
                  class="tag"
                  :class="draftTest.result.sanity_passed ? 'tag-ok' : 'tag-danger'"
                >
                  {{ draftTest.result.sanity_passed ? '排序正常' : '排序异常' }}
                </span>
              </template>
            </div>
            <button class="btn btn-quiet" @click="formOpen = false">取消</button>
            <button class="btn btn-primary" :disabled="saving" @click="save">
              {{ saving ? '保存中…' : '保存' }}
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.page {
  max-width: 1000px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--sp-5);
}
.page-desc {
  margin: 0;
  max-width: 62ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.7;
}

/* ---- 分组 ---- */
.group {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.group-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
}
.group-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: var(--text-0);
  font-family: inherit;
}
.caret {
  display: inline-block;
  color: var(--text-2);
  font-size: 14px;
  transform: rotate(0deg);
  transition: transform var(--dur) var(--ease);
  width: 10px;
}
.caret.open {
  transform: rotate(90deg);
}
.count {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-2);
  background: var(--bg-3);
  border-radius: 99px;
  padding: 0 6px;
  line-height: 18px;
}

.empty {
  margin: 0;
  padding: var(--sp-3) var(--sp-4);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-md);
  color: var(--text-2);
  font-size: 12.5px;
}

/* ---- 供应商卡片 ---- */
.pcard {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: var(--sp-4) var(--sp-5);
  padding: 18px var(--sp-5);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  transition: border-color var(--dur) var(--ease);
}
.pcard:hover {
  border-color: var(--line-strong);
}

.pcard-main {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.pcard-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.pcard-name {
  font-size: 13.5px;
  font-weight: 500;
}

.pcard-fields {
  display: grid;
  grid-template-columns: minmax(0, 0.9fr) minmax(0, 1.6fr) minmax(0, 0.8fr) minmax(0, 0.7fr);
  gap: var(--sp-2) var(--sp-5);
}
.pf {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}
.pf-label {
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.4;
}
.pf-value {
  font-size: 12.5px;
  color: var(--text-0);
  overflow-wrap: anywhere;
  line-height: 1.5;
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pcard-side {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}

/* ---- 测试结果 ---- */
.result {
  grid-column: 1 / -1;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-radius: var(--r-md);
  font-size: 12.5px;
  line-height: 1.5;
}
.result.good {
  background: var(--ok-soft);
  color: var(--text-1);
}
.result.bad {
  background: var(--danger-soft);
  color: var(--danger);
}
.result.wrap {
  flex-wrap: wrap;
}

.alert {
  margin: 0;
  padding: 8px 12px;
  border-radius: var(--r-md);
  background: var(--danger-soft);
  color: var(--danger);
  font-size: 12.5px;
}

/* ---- 弹层 ---- */
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(1, 4, 9, 0.6);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: var(--sp-5);
}
.modal {
  width: min(680px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-lg);
  box-shadow: 0 16px 48px rgba(1, 4, 9, 0.6);
  animation: fade-up 200ms var(--ease) both;
}
.modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  background: var(--bg-1);
  z-index: 2;
}
.modal-head h3 {
  font-size: 14.5px;
}
.modal-body {
  padding: var(--sp-5);
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
  padding: var(--sp-3) var(--sp-5);
  border-top: 1px solid var(--line);
  position: sticky;
  bottom: 0;
  background: var(--bg-1);
}

.presets {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.presets-label {
  font-size: 12.5px;
  color: var(--text-1);
}
.preset-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-2);
}
.preset {
  display: flex;
  flex-direction: column;
  gap: 3px;
  text-align: left;
  padding: 9px 11px;
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  cursor: pointer;
  color: var(--text-1);
  font-family: inherit;
  transition: border-color var(--dur) var(--ease), background var(--dur) var(--ease);
}
.preset:hover {
  border-color: var(--accent-border);
  background: var(--bg-3);
}
.preset-name {
  font-size: 12.5px;
  font-weight: 500;
  color: var(--text-0);
}
.preset-note {
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.5;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-3) var(--sp-4);
}
.span-2 {
  grid-column: 1 / -1;
}

.draft-test {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-start;
}

/* 底部常驻的测试区：按钮 + 一行结果，不挤占正文滚动区 */
.modal-foot {
  align-items: center;
}
.foot-left {
  flex-direction: row;
  align-items: center;
  gap: 10px;
  margin-right: auto;
  min-width: 0;
}
.foot-msg {
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 42ch;
}
.foot-msg.good {
  color: var(--ok);
}
.foot-msg.bad {
  color: var(--danger);
}

.check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--text-1);
  cursor: pointer;
}
.check input {
  accent-color: var(--accent);
  width: 14px;
  height: 14px;
}
</style>