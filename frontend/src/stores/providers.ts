import { defineStore } from 'pinia'
import { ref } from 'vue'
import { providerApi, type Provider, type ProviderDraft, type TestResult } from '@/api/providers'

export const useProvidersStore = defineStore('providers', () => {
  const items = ref<Provider[]>([])
  const loading = ref(false)
  const error = ref<string>('')

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

  function byKind(kind: string) {
    return items.value.filter((p) => p.kind === kind)
  }

  return { items, loading, error, load, create, update, remove, test, byKind }
})