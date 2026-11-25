<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
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

const protocolOptions = computed<Protocol[]>(() => {
  if (form.kind === 'rerank') {
    return ['dashscope_rerank', 'siliconflow_rerank', 'cohere_rerank']
  }
  return ['openai']
})

// ---------------- 表单状态 ----------------
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

function openCreate() {
  resetForm()
  formOpen.value = true
}

function openEdit(p: Provider) {
  resetForm()
  editingId.value = p.id
  form.kind = p.kind
  form.name = p.name
  form.base_url = p.base_url
  form.api_key = '' // 留空=不修改
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
  if (!form.base_url.trim()) return (formError.value = '请填写 base_url')
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
      if (!patch.api_key) delete patch.api_key // 空字符串表示不改
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
    draftTest.error = '先填 base_url 和模型名'
    return
  }
  if (!form.api_key && editingId.value !== null) {
    draftTest.error = '编辑已有供应商时，测试请直接点卡片上的「测试」'
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
  if (!confirm(`删除供应商「${p.name}」？已有文档的向量不受影响，但需要重新配置才能继续向量化。`)) return
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
  <div class="providers">
    <header class="head rise">
      <div>
        <p class="lead">
          三类供应商各司其职：<b>对话模型</b>负责回答，<b>向量模型</b>把文本变成向量（建索引和检索都要用），
          <b>重排模型</b>在候选结果里精挑细选。同一家平台可以同时提供三类，也可以分开配。
        </p>
      </div>
      <button class="btn btn-primary" @click="openCreate">+ 新增供应商</button>
    </header>

    <p v-if="store.error" class="alert">{{ store.error }}</p>

    <!-- 分组列表 -->
    <section v-for="(kind, gi) in kinds" :key="kind" class="group rise" :style="{ animationDelay: `${gi * 70}ms` }">
      <div class="group-head">
        <span class="group-dot" />
        <h2 class="group-title">{{ KIND_LABEL[kind] }}</h2>
        <span class="micro">{{ kind }}</span>
        <span class="group-count num">{{ store.byKind(kind).length }}</span>
      </div>

      <p v-if="!store.byKind(kind).length" class="empty">
        {{ kind === 'embedding'
          ? '还没有向量模型。没有它，文档无法入库、问题无法检索。'
          : kind === 'rerank'
            ? '还没有重排模型。可以先跳过，检索会退化为向量 + BM25 融合排序。'
            : '还没有对话模型。没有它，系统无法回答问题。' }}
      </p>

      <div class="cards">
        <article v-for="p in store.byKind(kind)" :key="p.id" class="pcard">
          <div class="pcard-top">
            <div class="pcard-name">
              <span class="dot" :class="p.enabled ? 'dot-ok' : 'dot-idle'" />
              <strong>{{ p.name }}</strong>
              <span v-if="p.is_default" class="tag tag-amber">默认</span>
            </div>
            <div class="pcard-actions">
              <button class="btn btn-sm btn-ghost" @click="openEdit(p)">编辑</button>
              <button class="btn btn-sm btn-ghost btn-danger" @click="remove(p)">删除</button>
            </div>
          </div>

          <dl class="pcard-meta">
            <div class="col-model">
              <dt>模型</dt>
              <dd class="mono">{{ p.model }}</dd>
            </div>
            <div class="col-url">
              <dt>地址</dt>
              <dd class="mono ellipsis" :title="p.base_url">{{ p.base_url }}</dd>
            </div>
            <div class="col-proto">
              <dt>协议</dt>
              <dd class="mono">{{ PROTOCOL_LABEL[p.protocol] ?? p.protocol }}</dd>
            </div>
            <div class="col-key">
              <dt>密钥</dt>
              <dd class="mono">{{ p.has_api_key ? p.api_key_masked : '未配置' }}</dd>
            </div>
          </dl>

          <div class="pcard-foot">
            <button class="btn btn-sm" :disabled="testState[p.id]?.loading" @click="runTest(p.id)">
              {{ testState[p.id]?.loading ? '测试中…' : '测试连接' }}
            </button>
            <button v-if="!p.is_default" class="btn btn-sm btn-ghost" @click="makeDefault(p)">设为默认</button>
          </div>

          <div v-if="testState[p.id]?.error" class="result bad">{{ testState[p.id]?.error }}</div>
          <div v-else-if="testState[p.id]?.result" class="result good">
            <div class="result-line">
              <span class="dot dot-ok" />
              <span>{{ testState[p.id]?.result?.message }}</span>
            </div>
            <div v-if="testState[p.id]?.result?.models?.length" class="result-models">
              <span
                v-for="m in testState[p.id]?.result?.models?.slice(0, 8)"
                :key="m"
                class="tag"
                :class="{ 'tag-cyan': m === p.model }"
              >
                {{ m }}
              </span>
              <span v-if="(testState[p.id]?.result?.model_count ?? 0) > 8" class="more">
                +{{ (testState[p.id]?.result?.model_count ?? 0) - 8 }}
              </span>
            </div>
            <div v-if="testState[p.id]?.result?.results?.length" class="result-models">
              <span v-for="r in testState[p.id]?.result?.results" :key="r.index" class="tag">
                #{{ r.index }} → {{ r.score.toFixed(4) }}
              </span>
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- 新增/编辑 弹层 -->
    <Teleport to="body">
      <div v-if="formOpen" class="overlay" @click.self="formOpen = false">
        <div class="modal">
          <header class="modal-head">
            <h3>{{ editingId === null ? '新增供应商' : '编辑供应商' }}</h3>
            <button class="btn btn-sm btn-ghost" @click="formOpen = false">关闭</button>
          </header>

          <div class="modal-body">
            <div v-if="editingId === null" class="presets">
              <div class="micro">快速套用</div>
              <div class="preset-row">
                <button v-for="p in PRESETS" :key="p.key" class="preset" @click="applyPreset(p)">
                  <strong>{{ p.label }}</strong>
                  <span>{{ p.note }}</span>
                </button>
              </div>
            </div>

            <div class="form-grid">
              <div class="field">
                <label>类型</label>
                <div class="seg">
                  <button
                    v-for="k in kinds"
                    :key="k"
                    class="seg-btn"
                    :class="{ on: form.kind === k }"
                    @click="(form.kind = k), onKindChange()"
                  >
                    {{ KIND_LABEL[k] }}
                  </button>
                </div>
              </div>

              <div class="field">
                <label>名称</label>
                <input v-model="form.name" type="text" placeholder="例如：阿里百炼-向量" />
              </div>

              <div class="field span-2">
                <label>Base URL</label>
                <input v-model="form.base_url" type="text" placeholder="https://.../v1" />
                <span class="hint">
                  多填一层路径没关系，系统会按协议自行拼出正确的接口地址；重排用 DashScope 协议时只取域名根。
                </span>
              </div>

              <div class="field span-2">
                <label>API Key{{ editingId !== null ? '（留空=不修改）' : '' }}</label>
                <input
                  v-model="form.api_key"
                  type="password"
                  :placeholder="editingId !== null ? '已保存的密钥不会显示' : 'sk-...'"
                  autocomplete="off"
                />
                <span class="hint">密钥会用 Fernet 加密后存进本地数据库，接口只回传打码后的形式。</span>
              </div>

              <div class="field">
                <label>模型名</label>
                <input v-model="form.model" type="text" placeholder="例如：qwen3.7-text-embedding-flash" />
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

            <div class="draft-test">
              <button class="btn" :disabled="draftTest.loading" @click="runDraftTest">
                {{ draftTest.loading ? '测试中…' : '测试连接（不保存）' }}
              </button>
              <div v-if="draftTest.error" class="result bad">{{ draftTest.error }}</div>
              <div v-else-if="draftTest.result" class="result good">
                <div class="result-line">
                  <span class="dot dot-ok" />
                  <span>{{ draftTest.result.message }}</span>
                </div>
                <div v-if="draftTest.result.models?.length" class="result-models">
                  <span v-for="m in draftTest.result.models.slice(0, 10)" :key="m" class="tag">{{ m }}</span>
                </div>
                <div v-if="draftTest.result.results?.length" class="result-models">
                  <span v-for="r in draftTest.result.results" :key="r.index" class="tag">
                    #{{ r.index }} → {{ r.score.toFixed(4) }}
                  </span>
                  <span class="tag" :class="draftTest.result.sanity_passed ? 'tag-green' : 'tag-red'">
                    {{ draftTest.result.sanity_passed ? '排序正常' : '排序异常，检查协议' }}
                  </span>
                </div>
              </div>
            </div>

            <label class="check">
              <input v-model="form.is_default" type="checkbox" />
              <span>设为该类型的默认供应商</span>
            </label>

            <p v-if="formError" class="alert">{{ formError }}</p>
          </div>

          <footer class="modal-foot">
            <button class="btn btn-ghost" @click="formOpen = false">取消</button>
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
.providers {
  max-width: 1080px;
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--sp-5);
}
.lead {
  margin: 0;
  max-width: 64ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.8;
}
.lead b {
  color: var(--text-0);
  font-weight: 600;
}

.group-head {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding-bottom: var(--sp-2);
  border-bottom: 1px solid var(--line);
  margin-bottom: var(--sp-3);
}
.group-dot {
  width: 3px;
  height: 14px;
  background: var(--amber);
  border-radius: 1px;
}
.group-title {
  font-size: 14px;
  margin-right: 6px;
}
.group-count {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-2);
}

.empty {
  margin: 0 0 var(--sp-3);
  padding: var(--sp-3) var(--sp-4);
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-md);
  color: var(--text-2);
  font-size: 12.5px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
  gap: var(--sp-3);
  align-items: start;
}

.pcard {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-lg);
  padding: var(--sp-3) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.pcard-top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: var(--sp-2);
}
.pcard-name {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 13.5px;
  min-width: 0;
}
.pcard-actions {
  display: flex;
  gap: 2px;
  flex: none;
}

.pcard-meta {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1.9fr) minmax(0, 0.9fr) minmax(0, 0.75fr);
  gap: var(--sp-2) var(--sp-4);
  margin: 0;
}
.pcard-meta > div {
  min-width: 0;
}
.pcard-meta dt {
  font-family: var(--font-mono);
  font-size: 9.5px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-3);
}
.pcard-meta dd {
  margin: 1px 0 0;
  font-size: 12px;
  color: var(--text-1);
  overflow-wrap: anywhere;
}
.mono {
  font-family: var(--font-mono);
}
.ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pcard-foot {
  display: flex;
  gap: var(--sp-2);
  padding-top: var(--sp-2);
  border-top: 1px solid var(--line);
}

.result {
  font-size: 12px;
  line-height: 1.6;
  border-radius: var(--r-sm);
  padding: var(--sp-2) var(--sp-3);
}
.result.good {
  border: 1px solid color-mix(in srgb, var(--green) 30%, transparent);
  background: color-mix(in srgb, var(--green) 8%, transparent);
  color: var(--text-1);
}
.result.bad {
  border: 1px solid color-mix(in srgb, var(--red) 30%, transparent);
  background: color-mix(in srgb, var(--red) 8%, transparent);
  color: var(--red);
}
.result-line {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.result-models {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}
.more {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-3);
}

.alert {
  margin: 0;
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid color-mix(in srgb, var(--red) 35%, transparent);
  background: color-mix(in srgb, var(--red) 8%, transparent);
  border-radius: var(--r-sm);
  color: var(--red);
  font-size: 12.5px;
}

/* ---------- modal ---------- */
.overlay {
  position: fixed;
  inset: 0;
  background: color-mix(in srgb, #000 62%, transparent);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: var(--sp-5);
}
.modal {
  width: min(720px, 100%);
  max-height: 88vh;
  overflow: auto;
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--r-lg);
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  animation: rise 0.2s ease both;
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--line);
  position: sticky;
  top: 0;
  background: var(--bg-1);
  z-index: 2;
}
.modal-head h3 {
  font-size: 15px;
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
  gap: var(--sp-2);
}
.preset-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-2);
}
.preset {
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: var(--sp-2) var(--sp-3);
  background: var(--bg-2);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  cursor: pointer;
  color: var(--text-1);
  transition: border-color 0.15s, background 0.15s;
}
.preset:hover {
  border-color: var(--amber);
  background: var(--bg-3);
}
.preset strong {
  font-size: 12.5px;
  color: var(--text-0);
}
.preset span {
  font-size: 11px;
  color: var(--text-3);
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

.seg {
  display: inline-flex;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-md);
  overflow: hidden;
}
.seg-btn {
  padding: 5px 12px;
  background: var(--bg-0);
  border: none;
  color: var(--text-2);
  font-size: 12.5px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.seg-btn + .seg-btn {
  border-left: 1px solid var(--line-strong);
}
.seg-btn.on {
  background: var(--amber);
  color: var(--accent-contrast);
  font-weight: 600;
}

.draft-test {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  align-items: flex-start;
}

.check {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: 12.5px;
  color: var(--text-1);
  cursor: pointer;
}
.check input {
  accent-color: var(--amber);
}
</style>