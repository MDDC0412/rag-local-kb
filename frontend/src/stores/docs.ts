/** 文档库状态。 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import {
  docsApi,
  type DocumentItem,
  type KbStats,
  type UploadResult,
} from '@/api/documents'

const KB_ID = 1

export const useDocsStore = defineStore('docs', () => {
  const items = ref<DocumentItem[]>([])
  const stats = ref<KbStats>({ documents: 0, chunks: 0, indexed_chunks: 0, vectors: 0 })
  const loading = ref(false)
  const uploading = ref(false)
  const error = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const [list, s] = await Promise.all([docsApi.list(KB_ID), docsApi.stats(KB_ID)])
      items.value = list
      stats.value = s
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  async function upload(files: File[]): Promise<UploadResult> {
    uploading.value = true
    error.value = ''
    try {
      const result = await docsApi.upload(KB_ID, files)
      await load()
      return result
    } finally {
      uploading.value = false
    }
  }

  async function remove(id: number) {
    await docsApi.remove(id)
    await load()
  }

  return { items, stats, loading, uploading, error, load, upload, remove, KB_ID }
})