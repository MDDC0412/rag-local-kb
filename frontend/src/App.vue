<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useChatStore } from '@/stores/chat'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const route = useRoute()
// 对话状态在 store 里，切页也活着：侧栏据此提示"后台还在生成"
const chat = useChatStore()

const nav = [
  { to: '/', title: '总览' },
  { to: '/providers', title: '模型供应商' },
  { to: '/documents', title: '文档库' },
  { to: '/chat', title: '对话' },
  { to: '/prompts', title: '提示词库' },
  { to: '/lab', title: 'RAG 实验室' },
  { to: '/settings', title: '设置' },
]

const currentTitle = computed(() => (route.meta.title as string) ?? '')
</script>

<template>
  <div class="shell">
    <aside class="rail">
      <RouterLink to="/" class="brand">
        <span class="brand-mark">
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true">
            <rect x="2" y="3" width="16" height="3" rx="1.5" fill="currentColor" opacity="0.9" />
            <rect x="2" y="8.5" width="10" height="3" rx="1.5" fill="currentColor" opacity="0.55" />
            <rect x="2" y="14" width="13" height="3" rx="1.5" fill="currentColor" opacity="0.3" />
          </svg>
        </span>
        <span class="brand-name">本地 RAG</span>
      </RouterLink>

      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: route.path === item.to }"
        >
          <span class="nav-label">{{ item.title }}</span>
          <span v-if="item.to === '/chat' && chat.streaming" class="nav-flag">生成中</span>
        </RouterLink>
      </nav>

      <div class="rail-foot">
        <button class="theme-btn" @click="ui.toggle()">
          {{ ui.theme === 'dark' ? '切换到浅色' : '切换到深色' }}
        </button>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <h1 class="topbar-title">{{ currentTitle }}</h1>
      </header>

      <main class="content">
        <RouterView v-slot="{ Component }">
          <Transition name="view" mode="out-in">
            <component :is="Component" />
          </Transition>
        </RouterView>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  display: grid;
  grid-template-columns: var(--rail-w) 1fr;
  height: 100vh;
  overflow: hidden;
}

/* ---------- 侧栏 ---------- */
.rail {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-1);
  border-right: 1px solid var(--line);
}

.brand {
  flex: none;
  display: flex;
  align-items: center;
  gap: 9px;
  height: var(--topbar-h);
  padding: 0 var(--sp-4);
  color: var(--text-0);
}
.brand-mark {
  display: flex;
  color: var(--accent);
}
.brand-name {
  font-size: 13.5px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.nav {
  flex: 0 1 auto;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: var(--sp-2) var(--sp-2);
}

.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 34px;
  padding: 0 10px;
  border-radius: var(--r-md);
  color: var(--text-1);
  font-size: 13.5px;
  text-decoration: none;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.nav-item:hover {
  background: var(--bg-2);
  color: var(--text-0);
}
.nav-item.active {
  background: var(--bg-2);
  color: var(--text-0);
  font-weight: 500;
}
.nav-item.active .nav-label {
  position: relative;
}
/* 后台生成中的提示：提示语很小，不抢焦点 */
.nav-flag {
  font-size: 10.5px;
  line-height: 1;
  color: var(--accent);
  font-family: var(--font-mono);
}
.nav-item.active::before {
  content: "";
  position: absolute;
  left: 0;
  width: 2px;
  height: 16px;
  border-radius: 2px;
  background: var(--accent);
  transform: translateX(-8px);
}

.rail-foot {
  flex: none;
  margin-top: auto;
  padding: var(--sp-3) var(--sp-2);
  border-top: 1px solid var(--line);
}
.theme-btn {
  width: 100%;
  height: 30px;
  border: 1px solid transparent;
  border-radius: var(--r-md);
  background: transparent;
  color: var(--text-1);
  font-family: inherit;
  font-size: 12.5px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.theme-btn:hover {
  background: var(--bg-2);
  color: var(--text-0);
}

/* ---------- 主体 ---------- */
.main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.topbar {
  flex: none;
  height: var(--topbar-h);
  display: flex;
  align-items: center;
  padding: 0 var(--sp-6);
  border-bottom: 1px solid var(--line);
}
.topbar-title {
  font-size: 15px;
  font-weight: 600;
}

.content {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: var(--sp-6);
}
</style>