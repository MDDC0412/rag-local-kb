<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const route = useRoute()

const nav = [
  { to: '/', index: '01', title: '总览', sub: 'Overview' },
  { to: '/providers', index: '02', title: '模型供应商', sub: 'Providers' },
  { to: '/documents', index: '03', title: '文档库', sub: 'Documents', milestone: 'M1' },
  { to: '/chat', index: '04', title: '对话', sub: 'Chat', milestone: 'M2' },
  { to: '/lab', index: '05', title: 'RAG 实验室', sub: 'Lab', milestone: 'M4' },
  { to: '/settings', index: '06', title: '设置', sub: 'Settings', milestone: 'M3' },
]

const currentTitle = computed(() => (route.meta.title as string) ?? '')
const currentIndex = computed(() => (route.meta.index as string) ?? '00')
</script>

<template>
  <div class="shell">
    <!-- 左侧导航 -->
    <aside class="rail">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" width="26" height="26">
            <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" stroke-width="1.2" />
            <circle cx="16" cy="16" r="5" fill="currentColor" opacity="0.9" />
            <path d="M16 3v7M16 22v7M3 16h7M22 16h7" stroke="currentColor" stroke-width="1.2" />
          </svg>
        </div>
        <div class="brand-text">
          <div class="brand-name">RAG·LOCAL</div>
          <div class="micro">个人知识库</div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink
          v-for="item in nav"
          :key="item.to"
          :to="item.to"
          class="nav-item"
          :class="{ active: route.path === item.to }"
        >
          <span class="nav-index">{{ item.index }}</span>
          <span class="nav-label">{{ item.title }}</span>
          <span v-if="item.milestone" class="nav-badge">{{ item.milestone }}</span>
          <span class="nav-sub">{{ item.sub }}</span>
        </RouterLink>
      </nav>

      <div class="rail-foot">
        <div class="rail-status">
          <span class="dot dot-idle" />
          <span class="micro">本地运行 · 单用户</span>
        </div>
        <button class="theme-toggle" @click="ui.toggle()">
          <span class="micro">{{ ui.theme === 'dark' ? '深色' : '浅色' }}</span>
          <span class="theme-track" :class="{ on: ui.theme === 'light' }">
            <span class="theme-thumb" />
          </span>
        </button>
      </div>
    </aside>

    <!-- 右侧主体 -->
    <div class="main">
      <header class="topbar">
        <div class="topbar-left">
          <span class="micro">{{ currentIndex }}</span>
          <h1 class="topbar-title">{{ currentTitle }}</h1>
        </div>
        <div class="topbar-right">
          <span class="micro">v0.1.0</span>
        </div>
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
  position: relative;
  z-index: 1;
}

/* ---------- 导航 ---------- */
.rail {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
  overflow: hidden;
  border-right: 1px solid var(--line);
  background: color-mix(in srgb, var(--bg-1) 82%, transparent);
  backdrop-filter: blur(12px);
}

.brand {
  flex: none;
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-4) var(--sp-5);
  border-bottom: 1px solid var(--line);
  color: var(--amber);
}
.brand-mark {
  color: var(--amber);
  display: flex;
}
.brand-name {
  font-family: var(--font-display);
  font-size: 15px;
  letter-spacing: 0.12em;
  color: var(--text-0);
}
.brand-text .micro {
  margin-top: 2px;
}

.nav {
  display: flex;
  flex-direction: column;
  padding: var(--sp-3) var(--sp-3) 0;
  gap: 1px;
  flex: 0 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.nav-item {
  display: grid;
  grid-template-columns: auto 1fr auto;
  grid-template-rows: auto auto;
  column-gap: var(--sp-3);
  align-items: center;
  padding: 7px var(--sp-3);
  border: 1px solid transparent;
  border-radius: var(--r-md);
  color: var(--text-1);
  text-decoration: none;
  position: relative;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.nav-item:hover {
  background: var(--bg-2);
  color: var(--text-0);
}
.nav-item.active {
  background: var(--bg-2);
  border-color: var(--line-strong);
  color: var(--text-0);
}
.nav-item.active::before {
  content: "";
  position: absolute;
  left: -1px;
  top: 8px;
  bottom: 8px;
  width: 2px;
  background: var(--amber);
  border-radius: 2px;
}
.nav-index {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-3);
  grid-row: 1 / 3;
  align-self: start;
  padding-top: 3px;
}
.nav-item.active .nav-index {
  color: var(--amber);
}
.nav-label {
  font-size: 13.5px;
}
.nav-sub {
  grid-column: 2;
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.08em;
  color: var(--text-3);
  text-transform: uppercase;
}
.nav-badge {
  grid-row: 1;
  grid-column: 3;
  font-family: var(--font-mono);
  font-size: 9px;
  padding: 0 4px;
  border: 1px solid var(--line-strong);
  border-radius: var(--r-sm);
  color: var(--text-3);
}

.rail-foot {
  flex: none;
  margin-top: auto;
  border-top: 1px solid var(--line);
  padding: var(--sp-3) var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.rail-status {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.theme-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: transparent;
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  padding: 6px 10px;
  cursor: pointer;
  color: var(--text-2);
}
.theme-toggle:hover {
  border-color: var(--line-accent);
}
.theme-track {
  width: 28px;
  height: 14px;
  border-radius: 99px;
  background: var(--bg-3);
  border: 1px solid var(--line-strong);
  position: relative;
  transition: background 0.2s;
}
.theme-thumb {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--amber);
  transition: transform 0.2s cubic-bezier(0.2, 0.7, 0.3, 1);
}
.theme-track.on .theme-thumb {
  transform: translateX(14px);
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
  height: var(--topbar-h);
  flex: none;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--sp-6);
  border-bottom: 1px solid var(--line);
  background: color-mix(in srgb, var(--bg-0) 72%, transparent);
  backdrop-filter: blur(10px);
}
.topbar-left {
  display: flex;
  align-items: baseline;
  gap: var(--sp-3);
}
.topbar-title {
  font-size: 15px;
  font-weight: 600;
  letter-spacing: 0.02em;
}

.content {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: var(--sp-6);
}
</style>