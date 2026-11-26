import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    name: 'dashboard',
    component: () => import('@/views/DashboardView.vue'),
    meta: { title: '总览', index: '01' },
  },
  {
    path: '/providers',
    name: 'providers',
    component: () => import('@/views/ProvidersView.vue'),
    meta: { title: '模型供应商', index: '02' },
  },
  {
    path: '/documents',
    name: 'documents',
    component: () => import('@/views/DocumentsView.vue'),
    meta: { title: '文档库' },
  },
  {
    path: '/documents/:id',
    name: 'document-detail',
    component: () => import('@/views/DocumentDetailView.vue'),
    meta: { title: '分块预览' },
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('@/views/ChatView.vue'),
    meta: { title: '对话' },
  },
  {
    path: '/prompts',
    name: 'prompts',
    component: () => import('@/views/PromptsView.vue'),
    meta: { title: '提示词库' },
  },
  {
    path: '/lab',
    name: 'lab',
    component: () => import('@/views/PlaceholderView.vue'),
    meta: { title: 'RAG 实验室', index: '05', milestone: 'M4' },
  },
  {
    path: '/settings',
    name: 'settings',
    component: () => import('@/views/SettingsView.vue'),
    meta: { title: '设置', index: '06' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  const title = (to.meta.title as string) || '本地 RAG'
  document.title = `${title} · 本地 RAG 知识库`
})

export default router