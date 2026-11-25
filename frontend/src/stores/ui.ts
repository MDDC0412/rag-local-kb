import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

type Theme = 'dark' | 'light'

const STORAGE_KEY = 'rag-theme'

export const useUiStore = defineStore('ui', () => {
  const stored = localStorage.getItem(STORAGE_KEY)
  const theme = ref<Theme>(stored === 'light' ? 'light' : 'dark')

  function apply(value: Theme) {
    document.documentElement.setAttribute('data-theme', value)
  }

  function toggle() {
    theme.value = theme.value === 'dark' ? 'light' : 'dark'
  }

  function setTheme(value: Theme) {
    theme.value = value
  }

  watch(
    theme,
    (value) => {
      localStorage.setItem(STORAGE_KEY, value)
      apply(value)
    },
    { immediate: true },
  )

  return { theme, toggle, setTheme }
})