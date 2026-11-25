<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const title = computed(() => (route.meta.title as string) ?? '')
const milestone = computed(() => (route.meta.milestone as string) ?? '')
</script>

<template>
  <div class="holder">
    <h2 class="holder-title">{{ title }}</h2>
    <p class="holder-desc">
      这个模块还没有开放，计划在 <span class="tag">{{ milestone }}</span> 阶段交付。
    </p>
    <p class="holder-desc">现在可以先完成 <RouterLink to="/providers">模型供应商</RouterLink> 的配置，它是后面所有功能的前提。</p>
  </div>
</template>

<style scoped>
.holder {
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  gap: var(--sp-3);
  max-width: 560px;
  min-height: 40vh;
}
.holder-title {
  font-size: 18px;
  color: var(--text-0);
}
.holder-desc {
  margin: 0;
  color: var(--text-1);
  font-size: 13.5px;
  line-height: 1.75;
}
</style>