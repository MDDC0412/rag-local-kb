/** 把回答里的 [n] 变成可点击角标，同时防止 HTML 注入。 */

import MarkdownIt, { type MarkdownIt as MarkdownItInstance } from 'markdown-it'
import hljs from 'highlight.js'

/** 纯文本转义（不依赖 md 实例，避免初始化器自引用导致的循环类型推断）。 */
export function escapeHtml(text: string): string {
  return new MarkdownIt().utils.escapeHtml(text)
}

const md: MarkdownItInstance = new MarkdownIt({
  html: false, // 用户文档内容不可信，禁止裸 HTML
  linkify: true,
  breaks: true,
  highlight(code: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return `<pre class="code"><code>${hljs.highlight(code, { language: lang }).value}</code></pre>`
      } catch {
        /* 落到下面的转义分支 */
      }
    }
    return `<pre class="code"><code>${escapeHtml(code)}</code></pre>`
  },
})

/** 引用编号渲染成 sup 标签，class 供点击处理。 */
function renderCitations(html: string): string {
  return html.replace(
    /\[(\d{1,2})\]/g,
    (_match, num: string) =>
      `<sup class="cite" data-cite="${num}" title="查看引用 ${num}">${num}</sup>`,
  )
}

export function renderMarkdown(text: string): string {
  return renderCitations(md.render(text || ''))
}