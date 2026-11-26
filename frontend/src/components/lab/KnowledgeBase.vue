<script setup lang="ts">
/**
 * 术语与面试题：RAG 学习资料的索引页。
 *
 * 结构上分两块，但共用一套筛选：
 * - 术语：按链路分组（文档处理 / 向量 / 检索 / 生成 / 工程）
 * - 面试题：按难度和主题分组
 *
 * 搜索同时命中两者 —— 用户往往记得"RRF 这个词"但不确定它是术语还是面试题。
 */

import { computed, ref } from 'vue'
import { TERM_GROUPS, TERM_COUNT, type Term } from './terms'
import { QUESTION_GROUPS, QUESTION_COUNT } from './questions'

type Mode = 'terms' | 'questions'

const mode = ref<Mode>('terms')
const keyword = ref('')
const level = ref<0 | 1 | 2 | 3>(0)
const expandAll = ref(false)
const openIds = ref<Set<string>>(new Set())

function toggle(id: string) {
  const next = new Set(openIds.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  openIds.value = next
}

function isOpen(id: string): boolean {
  return expandAll.value || openIds.value.has(id)
}

const normalized = computed(() => keyword.value.trim().toLowerCase())

/** 术语：按关键词过滤（命中名称、简介、说明、项目用法） */
const filteredGroups = computed(() => {
  const q = normalized.value
  if (!q) return TERM_GROUPS
  return TERM_GROUPS.map((group) => ({
    ...group,
    terms: group.terms.filter((term) =>
      [term.name, term.brief, term.detail, term.project, term.pitfall]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(q),
    ),
  })).filter((group) => group.terms.length > 0)
})

const filteredQuestions = computed(() => {
  const q = normalized.value
  return QUESTION_GROUPS.map((group) => ({
    ...group,
    questions: group.questions.filter((item) => {
      if (level.value && item.level !== level.value) return false
      if (!q) return true
      return [item.question, ...item.answer, item.follow ?? '']
        .join(' ')
        .toLowerCase()
        .includes(q)
    }),
  })).filter((group) => group.questions.length > 0)
})

const hitCount = computed(() =>
  mode.value === 'terms'
    ? filteredGroups.value.reduce((sum, g) => sum + g.terms.length, 0)
    : filteredQuestions.value.reduce((sum, g) => sum + g.questions.length, 0),
)

const total = computed(() => (mode.value === 'terms' ? TERM_COUNT : QUESTION_COUNT))

/** 点术语里的"相关词"直接在词条间跳转 */
function jumpTo(termName: string) {
  keyword.value = termName
  mode.value = 'terms'
}

function termId(term: Term): string {
  return `term-${term.name}`
}

const LEVEL_LABEL: Record<number, string> = { 1: '基础', 2: '进阶', 3: '深水区' }

/**
 * 极简行内强调解析：把 `**重点**` 拆成 [{text, bold}]。
 *
 * 为什么不用 v-html + markdown-it：这段内容是**静态文件里自己写的**，
 * 但 v-html 一开就等于给未来埋了个 XSS 口子，而这里只需要加粗一种样式，
 * 手写 6 行比引一个渲染器划算得多。
 */
interface Segment {
  text: string
  bold: boolean
}

function rich(text: string): Segment[] {
  const parts = text.split('**')
  return parts
    .map((part, index) => ({ text: part, bold: index % 2 === 1 }))
    .filter((segment) => segment.text.length > 0)
}
</script>

<template>
  <section class="knowledge">
    <header class="panel-head">
      <h2 class="panel-title">RAG 术语与面试题</h2>
      <p class="panel-desc">
        左边是这个词"是什么"，右边是面试官会怎么问它。
        术语按 RAG 链路的顺序排（文档 → 向量 → 检索 → 生成 → 工程），
        每个词都写了本项目里对应的实现文件，可以对着代码看。
      </p>
    </header>

    <!-- 工具条 -->
    <div class="kbar">
      <div class="kmodes">
        <button class="kmode" :class="{ active: mode === 'terms' }" @click="mode = 'terms'">
          术语词典
          <span class="kmode-count num">{{ TERM_COUNT }}</span>
        </button>
        <button class="kmode" :class="{ active: mode === 'questions' }" @click="mode = 'questions'">
          面试题
          <span class="kmode-count num">{{ QUESTION_COUNT }}</span>
        </button>
      </div>

      <div class="ksearch">
        <span class="ksearch-icon">⌕</span>
        <input
          v-model="keyword"
          type="search"
          class="ksearch-input"
          :placeholder="mode === 'terms' ? '搜术语，如 RRF、分块、Chunk' : '搜题目或答案关键词'"
        />
        <button v-if="keyword" class="ksearch-clear" @click="keyword = ''">×</button>
      </div>

      <div v-if="mode === 'questions'" class="levels">
        <button
          v-for="item in ([0, 1, 2, 3] as const)"
          :key="item"
          class="level-btn"
          :class="{ active: level === item }"
          @click="level = item"
        >
          {{ item === 0 ? '全部' : LEVEL_LABEL[item] }}
        </button>
      </div>

      <button v-if="mode === 'questions'" class="btn btn-sm btn-quiet" @click="expandAll = !expandAll">
        {{ expandAll ? '全部收起' : '全部展开' }}
      </button>
    </div>

    <p v-if="normalized" class="hit-line">
      「{{ keyword }}」命中 <b class="num">{{ hitCount }}</b> / {{ total }} 条
      <button class="btn btn-sm btn-quiet" @click="keyword = ''">清空</button>
    </p>

    <!-- ====================== 术语 ====================== -->
    <template v-if="mode === 'terms'">
      <p v-if="!filteredGroups.length" class="empty">没有匹配的术语，换个说法试试。</p>

      <div v-for="group in filteredGroups" :key="group.key" class="group">
        <div class="group-head">
          <h3 class="group-title">{{ group.title }}</h3>
          <span class="group-count num">{{ group.terms.length }}</span>
        </div>
        <p class="group-summary">{{ group.summary }}</p>

        <div class="term-list">
          <article v-for="term in group.terms" :key="term.name" :id="termId(term)" class="term">
            <div class="term-head">
              <h4 class="term-name">{{ term.name }}</h4>
              <p class="term-brief">
                <template v-for="(seg, i) in rich(term.brief)" :key="i">
                  <strong v-if="seg.bold">{{ seg.text }}</strong>
                  <template v-else>{{ seg.text }}</template>
                </template>
              </p>
            </div>

            <p class="term-detail">
              <template v-for="(seg, i) in rich(term.detail)" :key="i">
                <strong v-if="seg.bold">{{ seg.text }}</strong>
                <template v-else>{{ seg.text }}</template>
              </template>
            </p>

            <div v-if="term.project" class="term-block project">
              <span class="block-label">本项目</span>
              <p class="block-text">
                <template v-for="(seg, i) in rich(term.project)" :key="i">
                  <strong v-if="seg.bold">{{ seg.text }}</strong>
                  <template v-else>{{ seg.text }}</template>
                </template>
              </p>
            </div>

            <div v-if="term.pitfall" class="term-block pitfall">
              <span class="block-label">注意</span>
              <p class="block-text">
                <template v-for="(seg, i) in rich(term.pitfall)" :key="i">
                  <strong v-if="seg.bold">{{ seg.text }}</strong>
                  <template v-else>{{ seg.text }}</template>
                </template>
              </p>
            </div>

            <div v-if="term.related?.length" class="term-related">
              <span class="related-label">相关</span>
              <button
                v-for="name in term.related"
                :key="name"
                class="related-chip"
                @click="jumpTo(name)"
              >
                {{ name }}
              </button>
            </div>
          </article>
        </div>
      </div>
    </template>

    <!-- ====================== 面试题 ====================== -->
    <template v-else>
      <p v-if="!filteredQuestions.length" class="empty">没有匹配的题目，换个关键词或难度试试。</p>

      <div v-for="group in filteredQuestions" :key="group.key" class="group">
        <div class="group-head">
          <h3 class="group-title">{{ group.title }}</h3>
          <span class="group-count num">{{ group.questions.length }}</span>
        </div>
        <p class="group-summary">{{ group.summary }}</p>

        <div class="q-list">
          <article
            v-for="item in group.questions"
            :key="item.id"
            class="q"
            :class="{ open: isOpen(item.id) }"
          >
            <button class="q-head" @click="toggle(item.id)">
              <span class="q-level" :class="`lv${item.level}`">{{ LEVEL_LABEL[item.level] }}</span>
              <span class="q-text">{{ item.question }}</span>
              <span class="q-chevron">{{ isOpen(item.id) ? '−' : '+' }}</span>
            </button>

            <div v-if="isOpen(item.id)" class="q-body">
              <ol class="q-answer">
                <li v-for="(line, index) in item.answer" :key="index">
                  <template v-for="(seg, i) in rich(line)" :key="i">
                    <strong v-if="seg.bold">{{ seg.text }}</strong>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </li>
              </ol>

              <div v-if="item.follow" class="q-follow">
                <span class="block-label">追问</span>
                <p class="block-text">
                  <template v-for="(seg, i) in rich(item.follow)" :key="i">
                    <strong v-if="seg.bold">{{ seg.text }}</strong>
                    <template v-else>{{ seg.text }}</template>
                  </template>
                </p>
              </div>

              <div v-if="item.terms?.length" class="term-related">
                <span class="related-label">相关术语</span>
                <button
                  v-for="name in item.terms"
                  :key="name"
                  class="related-chip"
                  @click="jumpTo(name)"
                >
                  {{ name }}
                </button>
              </div>
            </div>
          </article>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.knowledge {
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
  min-width: 0;
}

/* ---------- 工具条 ---------- */
.kbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2);
  background: var(--bg-0);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}

.kmodes {
  display: flex;
  gap: 2px;
  padding: 2px;
  background: var(--bg-2);
  border-radius: var(--r-sm);
}

.kmode {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border: none;
  border-radius: 3px;
  background: transparent;
  color: var(--text-1);
  font-family: inherit;
  font-size: 12px;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease);
}
.kmode:hover {
  color: var(--text-0);
}
.kmode.active {
  background: var(--bg-1);
  color: var(--text-0);
  box-shadow: 0 1px 2px rgb(0 0 0 / 20%);
}
.kmode-count {
  font-size: 10px;
  color: var(--text-2);
}
.kmode.active .kmode-count {
  color: var(--accent);
}

.ksearch {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 220px;
  min-width: 180px;
  height: 30px;
  padding: 0 8px;
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
}
.ksearch:focus-within {
  border-color: var(--accent-border);
}
.ksearch-icon {
  font-size: 13px;
  color: var(--text-2);
}
.ksearch-input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: none;
  background: transparent;
  color: var(--text-0);
  font-family: inherit;
  font-size: 12px;
  outline: none;
}
.ksearch-input::placeholder {
  color: var(--text-2);
}
.ksearch-input::-webkit-search-cancel-button {
  display: none;
}
.ksearch-clear {
  border: none;
  background: transparent;
  color: var(--text-2);
  font-size: 14px;
  cursor: pointer;
}
.ksearch-clear:hover {
  color: var(--text-0);
}

.levels {
  display: flex;
  gap: 2px;
  flex: none;
}
.level-btn {
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: var(--r-sm);
  background: transparent;
  color: var(--text-2);
  font-family: inherit;
  font-size: 11px;
  cursor: pointer;
}
.level-btn:hover {
  color: var(--text-0);
  border-color: var(--line-strong);
}
.level-btn.active {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
}

.hit-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  font-size: 12px;
  color: var(--text-2);
}
.hit-line b {
  color: var(--text-0);
}

/* ---------- 分组 ---------- */
.group {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.group-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding-bottom: 5px;
  border-bottom: 1px solid var(--line);
}
.group-title {
  margin: 0;
  font-size: 14px;
  color: var(--text-0);
}
.group-count {
  font-size: 10.5px;
  color: var(--text-2);
}
.group-summary {
  margin: 0;
  font-size: 12px;
  line-height: 1.65;
  color: var(--text-2);
}

/* ---------- 术语卡 ---------- */
.term-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
  gap: var(--sp-3);
  align-items: start;
}

.term {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: var(--sp-3);
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
}
.term:hover {
  border-color: var(--line-strong);
}

.term-head {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.term-name {
  margin: 0;
  font-size: 13px;
  color: var(--text-0);
  font-weight: 600;
}
.term-brief {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--accent);
}

.term-detail {
  margin: 0;
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-1);
  white-space: pre-wrap;
  word-break: break-word;
}

.term-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  border-radius: var(--r-sm);
  border-left: 2px solid var(--line-strong);
}
.term-block.project {
  background: var(--bg-0);
  border-left-color: var(--accent);
}
.term-block.pitfall {
  background: var(--warn-soft);
  border-left-color: var(--warn);
}

.block-label {
  font-size: 10px;
  letter-spacing: 0.04em;
  color: var(--text-2);
}
.term-block.pitfall .block-label {
  color: var(--warn);
}

.block-text {
  margin: 0;
  font-size: 11.5px;
  line-height: 1.7;
  color: var(--text-1);
  word-break: break-word;
}

.term-detail strong,
.block-text strong,
.q-answer strong,
.term-brief strong {
  color: var(--text-0);
  font-weight: 600;
}

.term-related {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding-top: 2px;
}
.related-label {
  font-size: 10px;
  color: var(--text-2);
  margin-right: 2px;
}
.related-chip {
  padding: 1px 7px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: transparent;
  color: var(--text-1);
  font-family: inherit;
  font-size: 11px;
  line-height: 1.7;
  cursor: pointer;
  transition: background var(--dur) var(--ease), color var(--dur) var(--ease),
    border-color var(--dur) var(--ease);
}
.related-chip:hover {
  background: var(--accent-soft);
  border-color: var(--accent-border);
  color: var(--accent);
}

/* ---------- 面试题 ---------- */
.q-list {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}

.q {
  background: var(--bg-1);
  border: 1px solid var(--line);
  border-radius: var(--r-md);
  overflow: hidden;
}
.q.open {
  border-color: var(--line-strong);
}

.q-head {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 10px var(--sp-3);
  border: none;
  background: transparent;
  color: inherit;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
}
.q-head:hover {
  background: var(--bg-2);
}

.q-level {
  flex: none;
  margin-top: 1px;
  padding: 0 6px;
  font-size: 10px;
  line-height: 17px;
  border-radius: 3px;
}
.q-level.lv1 {
  color: var(--ok);
  background: var(--ok-soft);
}
.q-level.lv2 {
  color: var(--accent);
  background: var(--accent-soft);
}
.q-level.lv3 {
  color: var(--warn);
  background: var(--warn-soft);
}

.q-text {
  flex: 1;
  min-width: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-0);
}

.q-chevron {
  flex: none;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-2);
}

.q-body {
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
  padding: 0 var(--sp-3) var(--sp-3);
  border-top: 1px solid var(--line);
  padding-top: var(--sp-2);
}

.q-answer {
  margin: 0;
  padding-left: 22px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.q-answer li {
  padding-left: 3px;
  font-size: 12px;
  line-height: 1.75;
  color: var(--text-1);
  word-break: break-word;
}
.q-answer li::marker {
  color: var(--text-2);
  font-family: var(--font-mono);
  font-size: 11px;
}

.q-follow {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 6px 8px;
  background: var(--bg-0);
  border-left: 2px solid var(--accent);
  border-radius: var(--r-sm);
}

.empty {
  margin: 0;
  padding: var(--sp-5);
  text-align: center;
  color: var(--text-2);
  font-size: 12.5px;
  border: 1px dashed var(--line-strong);
  border-radius: var(--r-md);
}

@media (max-width: 900px) {
  .term-list {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>