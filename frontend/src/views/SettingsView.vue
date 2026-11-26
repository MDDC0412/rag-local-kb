<script setup lang="ts">
/** 设置页：检索默认值、生成参数、外观、数据目录。 */
import { computed, onMounted, reactive, ref } from 'vue'
import { ApiError } from '@/api/http'
import { SETTING_META, settingsApi, type SettingsPayload } from '@/api/prompts'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')

/** 表单值统一按字符串存，写库时原样提交（后端只认白名单键）。 */
const form = reactive<Record<string, string>>({})
const defaults = ref<Record<string, string>>({})

const groups = computed(() => [
  {
    title: '检索',
    hint: '影响「从资料里找出哪些片段」这一步。数字越大越慢、越可能召回更多相关内容。',
    items: SETTING_META.filter((item) => item.key.startsWith('retrieval.')),
  },
  {
    title: '生成',
    hint: '影响「把找到的资料交给模型」这一步。',
    items: SETTING_META.filter((item) => item.key.startsWith('chat.')),
  },
])

/** 有没有改动（用来决定保存按钮是否可点）。 */
const dirty = computed(() =>
  SETTING_META.some((item) => (form[item.key] ?? '') !== (defaults.value[item.key] ?? '')),
)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const payload: SettingsPayload = await settingsApi.get()
    defaults.value = { ...payload.items }
    Object.assign(form, payload.items)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  error.value = ''
  notice.value = ''
  try {
    const payload = await settingsApi.update({ ...form })
    defaults.value = { ...payload.items }
    Object.assign(form, payload.items)
    notice.value = '已保存。新设置在下一次提问时生效。'
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message
  } finally {
    saving.value = false
  }
}

function restoreDefaults() {
  Object.assign(form, defaults.value)
}

function resetItem(key: string) {
  form[key] = defaults.value[key] ?? ''
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="head">
      <p class="desc">
        这些是每次提问时的默认参数。单次提问仍可在「检索参数」抽屉里临时覆盖，不会改到这里。
      </p>
    </header>

    <p v-if="error" class="notice bad">{{ error }}</p>
    <p v-if="notice" class="notice ok">{{ notice }}</p>

    <div v-if="loading" class="loading">读取中…</div>

    <template v-else>
      <section v-for="group in groups" :key="group.title" class="card group">
        <div class="group-head">
          <h3 class="group-title">{{ group.title }}</h3>
          <p class="group-hint">{{ group.hint }}</p>
        </div>

        <div class="rows">
          <div v-for="item in group.items" :key="item.key" class="row">
            <div class="row-label">
              <span class="label">{{ item.label }}</span>
              <span class="hint">{{ item.hint }}</span>
            </div>

            <div class="row-control">
              <input
                v-if="item.type === 'number'"
                v-model="form[item.key]"
                type="number"
                :min="item.min"
                :max="item.max"
                class="mono-input num-input"
              />
              <select v-else-if="item.type === 'select'" v-model="form[item.key]">
                <option v-for="opt in item.options ?? []" :key="opt.value" :value="opt.value">
                  {{ opt.label }}
                </option>
              </select>
              <label v-else class="switch">
                <input v-model="form[item.key]" type="checkbox" true-value="true" false-value="false" />
                <span class="switch-text">{{ form[item.key] === 'true' ? '已开启' : '已关闭' }}</span>
              </label>

              <button
                v-if="form[item.key] !== defaults[item.key]"
                class="btn btn-sm btn-quiet reset-btn"
                title="改回上次保存的值"
                @click="resetItem(item.key)"
              >
                撤销
              </button>
            </div>
          </div>
        </div>
      </section>

      <div class="actions">
        <button class="btn btn-primary" :disabled="saving || !dirty" @click="save">
          {{ saving ? '保存中…' : dirty ? '保存设置' : '没有改动' }}
        </button>
        <button class="btn btn-quiet" :disabled="saving || !dirty" @click="restoreDefaults">
          全部撤销
        </button>
      </div>

      <!-- 外观 -->
      <section class="card group">
        <div class="group-head">
          <h3 class="group-title">外观</h3>
          <p class="group-hint">深色是默认。选择存在浏览器本地，不影响其他设备。</p>
        </div>
        <div class="rows">
          <div class="row">
            <div class="row-label">
              <span class="label">主题</span>
              <span class="hint">深色更适合长时间阅读文档</span>
            </div>
            <div class="row-control">
              <select
                :value="ui.theme"
                @change="ui.setTheme(($event.target as HTMLSelectElement).value as 'dark' | 'light')"
              >
                <option value="dark">深色</option>
                <option value="light">浅色</option>
              </select>
            </div>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  max-width: 860px;
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

.group {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.group-head {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-bottom: var(--sp-3);
  border-bottom: 1px solid var(--line);
}
.group-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-0);
}
.group-hint {
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
  line-height: 1.6;
}

.rows {
  display: flex;
  flex-direction: column;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-4);
  padding: 10px 0;
}
.row + .row {
  border-top: 1px solid var(--line);
}
.row-label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.label {
  font-size: 13px;
  color: var(--text-0);
}
.hint {
  font-size: 11.5px;
  color: var(--text-2);
  line-height: 1.5;
}
.row-control {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}
.row-control > select {
  width: 190px;
}
.num-input {
  width: 104px;
}
.switch {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
}
.switch input {
  width: 15px;
  height: 15px;
  accent-color: var(--accent);
  cursor: pointer;
}
.switch-text {
  font-size: 12.5px;
  color: var(--text-1);
  min-width: 42px;
}
.reset-btn {
  padding: 3px 8px;
  font-size: 11.5px;
}

.actions {
  display: flex;
  gap: var(--sp-2);
}
</style>