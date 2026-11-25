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

const steps = [
  { index: '01', title: '接入模型', desc: '配置对话 / 向量 / 重排三类供应商，逐一点"测试连接"确认可用。', to: '/providers', done: ready.value },
  { index: '02', title: '上传文档', desc: '支持 txt / md / pdf / docx，解析后可预览、可手改分块。', to: '/documents', done: false },
  { index: '03', title: '开始对话', desc: '混合检索（向量 + BM25 + 重排）后带引用作答。', to: '/chat', done: false },
  { index: '04', title: '看原理', desc: '在实验室里把文本变成向量，亲手算一次余弦相似度。', to: '/lab', done: false },
]
</script>

<template>
  <div class="dash">
    <!-- 首屏：状态 -->
    <section class="hero rise">
      <div class="hero-main">
        <div class="micro">SYSTEM STATUS</div>
        <h2 class="hero-title">
          本地知识库
          <span class="hero-accent">{{ ready ? '已就绪' : '待配置' }}</span>
        </h2>
        <p class="hero-sub">
          所有数据都在你自己的电脑上：文档、向量、会话都存在本地 SQLite 与 Chroma，
          只有调用模型时才会出网。
        </p>
      </div>

      <div class="hero-meters">
        <div class="meter">
          <div class="meter-num num">{{ counts.llm }}</div>
          <div class="micro">对话模型</div>
          <span class="dot" :class="counts.llm ? 'dot-ok' : 'dot-idle'" />
        </div>
        <div class="meter">
          <div class="meter-num num">{{ counts.embedding }}</div>
          <div class="micro">向量模型</div>
          <span class="dot" :class="counts.embedding ? 'dot-ok' : 'dot-idle'" />
        </div>
        <div class="meter">
          <div class="meter-num num">{{ counts.rerank }}</div>
          <div class="micro">重排模型</div>
          <span class="dot" :class="counts.rerank ? 'dot-ok' : 'dot-idle'" />
        </div>
      </div>
    </section>

    <!-- 步骤 -->
    <section class="steps">
      <RouterLink
        v-for="(step, i) in steps"
        :key="step.index"
        :to="step.to"
        class="step card rise"
        :style="{ animationDelay: `${80 + i * 60}ms` }"
      >
        <div class="step-head">
          <span class="step-index">{{ step.index }}</span>
          <span v-if="step.done" class="tag tag-green">完成</span>
        </div>
        <h3 class="step-title">{{ step.title }}</h3>
        <p class="step-desc">{{ step.desc }}</p>
        <span class="step-arrow">→</span>
      </RouterLink>
    </section>

    <!-- 环境信息 -->
    <section class="panel card rise" style="animation-delay: 340ms">
      <div class="panel-head">
        <span class="micro">运行环境</span>
        <span v-if="health" class="tag tag-green">后端在线</span>
        <span v-else class="tag tag-red">后端未连接</span>
      </div>
      <dl class="kv">
        <div>
          <dt>接口版本</dt>
          <dd class="num">{{ health?.version ?? '—' }}</dd>
        </div>
        <div>
          <dt>数据目录</dt>
          <dd class="path">{{ health?.data_dir ?? '—' }}</dd>
        </div>
      </dl>
      <p v-if="healthError" class="err">{{ healthError }}</p>
    </section>
  </div>
</template>

<style scoped>
.dash {
  display: flex;
  flex-direction: column;
  gap: var(--sp-5);
  max-width: 1180px;
}

/* ---------- hero ---------- */
.hero {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: var(--sp-6);
  align-items: end;
  padding-bottom: var(--sp-5);
  border-bottom: 1px solid var(--line);
}
.hero-title {
  font-size: 34px;
  line-height: 1.15;
  margin: var(--sp-2) 0 var(--sp-3);
  letter-spacing: -0.02em;
}
.hero-accent {
  color: var(--amber);
  position: relative;
}
.hero-accent::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: 2px;
  height: 1px;
  background: var(--amber);
  opacity: 0.4;
}
.hero-sub {
  margin: 0;
  max-width: 52ch;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.75;
}

.hero-meters {
  display: flex;
  gap: var(--sp-2);
}
.meter {
  min-width: 84px;
  padding: var(--sp-3);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  background: var(--bg-1);
  display: flex;
  flex-direction: column;
  gap: 4px;
  position: relative;
}
.meter-num {
  font-size: 26px;
  line-height: 1;
  color: var(--text-0);
  font-weight: 600;
}
.meter .dot {
  position: absolute;
  top: 8px;
  right: 8px;
}

/* ---------- steps ---------- */
.steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--sp-3);
}
.step {
  padding: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: inherit;
  transition: border-color 0.18s, background 0.18s, transform 0.18s;
  position: relative;
}
.step:hover {
  border-color: var(--line-accent);
  background: var(--bg-2);
  transform: translateY(-2px);
}
.step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.step-index {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.12em;
  color: var(--amber);
}
.step-title {
  font-size: 14px;
}
.step-desc {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-2);
}
.step-arrow {
  position: absolute;
  right: 12px;
  bottom: 10px;
  color: var(--text-3);
  font-family: var(--font-mono);
}
.step:hover .step-arrow {
  color: var(--amber);
}

/* ---------- panel ---------- */
.panel {
  padding: var(--sp-4) var(--sp-5);
}
.panel-head {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}
.kv {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
  margin: 0;
}
.kv dt {
  font-family: var(--font-mono);
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-3);
  margin-bottom: 3px;
}
.kv dd {
  margin: 0;
  font-size: 13px;
  color: var(--text-1);
  word-break: break-all;
}
.path {
  font-family: var(--font-mono);
  font-size: 12px !important;
  color: var(--text-2) !important;
}
.err {
  margin: var(--sp-3) 0 0;
  color: var(--red);
  font-size: 12.5px;
}

@media (max-width: 1080px) {
  .steps {
    grid-template-columns: repeat(2, 1fr);
  }
  .hero {
    grid-template-columns: 1fr;
  }
}
</style>