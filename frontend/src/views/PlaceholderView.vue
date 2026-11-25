<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => (route.meta.title as string) ?? '')
const milestone = computed(() => (route.meta.milestone as string) ?? '')
</script>

<template>
  <div class="holder">
    <div class="holder-mark">{{ milestone || '—' }}</div>
    <h2 class="holder-title">{{ title }} 尚未开放</h2>
    <p class="holder-desc">
      这个模块在里程碑 <span class="num">{{ milestone }}</span> 交付。
      当前先完成 <RouterLink to="/providers">模型供应商</RouterLink> 配置，它是后面所有功能的前提。
    </p>
    <div class="holder-meta micro">模块建设中</div>
  </div>
</template>

<style scoped>
.holder {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  height: 100%;
  min-height: 46vh;
  gap: var(--sp-3);
  max-width: 620px;
}
.holder-mark {
  font-family: var(--font-mono);
  font-size: 64px;
  line-height: 1;
  color: var(--line-strong);
  letter-spacing: 0.04em;
}
.holder-title {
  font-size: 20px;
  color: var(--text-0);
}
.holder-desc {
  margin: 0;
  color: var(--text-2);
  font-size: 13.5px;
  line-height: 1.8;
}
.holder-meta {
  padding-top: var(--sp-3);
  border-top: 1px solid var(--line);
  width: 100%;
}
</style>