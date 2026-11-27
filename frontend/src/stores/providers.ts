import { defineStore } from 'pinia'
import { ref } from 'vue'
import { providerApi, type Provider, type ProviderDraft, type TestResult } from '@/api/providers'
import { errorMessage } from '@/api/http'

/** 一张卡片的「测试连接」状态。放 store 里：切页回来结果还在，不用重测。 */
export interface ProviderTestState {
  loading: boolean
  result?: TestResult
  error?: string
}

export const useProvidersStore = defineStore('providers', () => {
  const items = ref<Provider[]>([])
  const loading = ref(false)
  const error = ref<string>('')
  /** 按供应商 id 存测试结果（连通性测试要发网络请求，几秒到十几秒）。 */
  const testState = ref<Record<number, ProviderTestState>>({})

  async function load() {
    loading.value = true
    error.value = ''
    try {
      items.value = await providerApi.list()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function create(draft: ProviderDraft) {
    await providerApi.create(draft)
    await load()
  }

  async function update(id: number, patch: Partial<ProviderDraft>) {
    await providerApi.update(id, patch)
    await load()
  }

  async function remove(id: number) {
    await providerApi.remove(id)
    await load()
  }

  async function test(id: number): Promise<TestResult> {
    return providerApi.test(id)
  }

  /** 测试一张卡片的连接。结果写进 store：切页回来还在，不用重测一遍。 */
  async function runTest(id: number): Promise<void> {
    testState.value[id] = { loading: true }
    try {
      const result = await providerApi.test(id)
      testState.value[id] = { loading: false, result }
    } catch (e) {
      testState.value[id] = { loading: false, error: errorMessage(e) }
    }
  }

  function byKind(kind: string) {
    return items.value.filter((p) => p.kind === kind)
  }

  return {
    items,
    loading,
    error,
    testState,
    load,
    create,
    update,
    remove,
    test,
    runTest,
    byKind,
  }
})