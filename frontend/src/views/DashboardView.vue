<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { http } from '@/api/http'
import { useProvidersStore } from '@/stores/providers'

interface Health {
  ok: boolean
  data_dir: string
  version: string
}

const health = ref<Health | null>(null)
const healthError = ref('')
const store = useProvidersStore()

onMounted(async () => {
  try {
    health.value = await http.get<Health>('/health')
  } catch (e) {
    healthError.value = (e as Error).message
  }
  await store.load()
})

const counts = computed(() => ({
  llm: store.byKind('llm').length,
  embedding: store.byKind('embedding').length,
  rerank: store.byKind('rerank').length,
}))

const ready = computed(
  () => counts.value.llm > 0 && counts.value.embedding > 0 && counts.value.rerank > 0,
)

const steps = computed(() => [
  {
    title: '配置模型',
    desc: '添加对话、向量、重排三类供应商，各点一次测试连接确认可用。',
    to: '/providers',
    done: ready.value,
  },
  { title: '上传文档', desc: '支持 txt、md、pdf、docx，解析后可预览并修改分块。', to: '/documents', done: false },
  { title: '开始对话', desc: '混合检索加引用回答，每个结论都能点开看原文。', to: '/chat', done: false },
  { title: '了解原理', desc: '在实验室里看文本如何变成向量、相似度如何计算。', to: '/lab', done: false },
])
</script>

<template>
  <div class="dash">
    <section class="intro">
      <h2 class="intro-title">你的本地知识库</h2>
      <p class="intro-desc">
        文档、向量和会话都存放在本机，只有调用模型时才需要联网。所有环节都可以自己调参和查看。
      </p>
    </section>

    <section class="meters">
      <div class="meter">
        <div class="meter-label">对话模型</div>
        <div class="meter-value">
          <span class="num">{{ counts.llm }}</span>
          <span class="dot" :class="counts.llm ? 'dot-ok' : 'dot-idle'" />
        </div>
      </div>
      <div class="meter">
        <div class="meter-label">向量模型</div>
        <div class="meter-value">
          <span class="num">{{ counts.embedding }}</span>
          <span class="dot" :class="counts.embedding ? 'dot-ok' : 'dot-idle'" />
        </div>
      </div>
      <div class="meter">
        <div class="meter-label">重排模型</div>
        <div class="meter-value">
          <span class="num">{{ counts.rerank }}</span>
          <span class="dot" :class="counts.rerank ? 'dot-ok' : 'dot-idle'" />
        </div>
      </div>
      <div class="meter meter-wide">
        <div class="meter-label">状态</div>
        <div class="meter-value">
          <span class="tag" :class="ready ? 'tag-ok' : 'tag-warn'">
            {{ ready ? '可以开始使用' : '还缺供应商' }}
          </span>
        </div>
      </div>
    </section>

    <section class="steps">
      <RouterLink
        v-for="(step, i) in steps"
        :key="step.title"
        :to="step.to"
        class="step card rise"
        :style="{ animationDelay: `${i * 40}ms` }"
      >
        <div class="step-head">
          <h3 class="step-title">{{ step.title }}</h3>
          <span v-if="step.done" class="tag tag-ok">已完成</span>
        </div>
        <p class="step-desc">{{ step.desc }}</p>
      </RouterLink>
    </section>

    <section class="info card">
      <div class="info-row">
        <span class="info-label">后端</span>
        <span v-if="health" class="tag tag-ok">{{ health.version }}</span>
        <span v-else class="tag tag-danger">未连接</span>
      </div>
      <div class="info-row">
        <span class="info-label">数据目录</span>
        <span class="info-value mono">{{ health?.data_dir ?? '—' }}</span>
      </div>
      <p v-if="healthError" class="error">{{ healthError }}</p>
    </section>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: var(--sp-6);
  max-width: 1000px;
}

.intro-title {
  font-size: 22px;
  margin-bottom: 8px;
}
.intro-desc {
  margin: 0;
  max-width: 60ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.7;
}

.meters {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
  padding: var(--sp-4) 0;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.meter {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.meter-label {
  font-size: 12px;
  color: var(--text-2);
}
.meter-value {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 22px;
  font-weight: 600;
  line-height: 1.2;
}
.meter-value .tag {
  font-size: 12px;
  height: 22px;
}

.steps {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--sp-3);
}
.step {
  padding: var(--sp-4);
  color: inherit;
  transition: background var(--dur) var(--ease), border-color var(--dur) var(--ease);
}
.step:hover {
  background: var(--bg-2);
  border-color: var(--line-strong);
}
.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.step-title {
  font-size: 14px;
}
.step-desc {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-1);
}

.info {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
.info-row {
  display: grid;
  grid-template-columns: 80px 1fr;
  align-items: center;
  gap: var(--sp-3);
  font-size: 13px;
}
.info-row > .tag {
  justify-self: start;
}
.info-label {
  color: var(--text-2);
  font-size: 12.5px;
}
.info-value {
  color: var(--text-1);
  word-break: break-all;
  font-size: 12px;
}
.error {
  margin: 0;
  color: var(--danger);
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .meters {
    grid-template-columns: repeat(2, 1fr);
  }
  .steps {
    grid-template-columns: 1fr;
  }
}
</style>