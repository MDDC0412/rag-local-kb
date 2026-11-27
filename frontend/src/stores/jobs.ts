/** 后台任务（入库/重建/打标签/解析）的进度与结果。
 *
 * 为什么放在 store：这些任务在后端队列里跑，但轮询原本长在页面里，
 * 切走页面就断，回来只看到一篇停在旧状态的文档，用户会以为「入库被中断了」。
 * 现在任务一入队就交给这里轮询，进度、结果、失败原因都在 store 里，
 * 切到哪个页面都还在跑；回来时看的是同一份状态，不用重新进一次页面等它刷新。
 */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { isActive, jobsApi, type Job } from '@/api/jobs'

/** 轮询间隔（毫秒）。后端进度本来就是秒级更新，再快只是浪费请求。 */
const POLL_MS = 800

/** 最多保留多少个任务的状态，避免一次长会话里无限增长。 */
const MAX_TRACKED = 40

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export const useJobsStore = defineStore('jobs', () => {
  /** 按 id 记任务。页面读它拿最新进度（pending → running → done/failed）。 */
  const byId = ref<Record<number, Job>>({})
  /** 最近结束的一个任务：页面据此决定要不要刷新自己的数据。 */
  const lastFinished = ref<Job | null>(null)
  /** 正在轮询的任务 id（不进响应式：只用于去重，页面不需要看）。 */
  const polling = new Set<number>()

  /** 正在跑的任务列表（侧栏/页面用它判断「有没有活在跑」）。 */
  const running = computed(() => Object.values(byId.value).filter(isActive))

  const runningCount = computed(() => running.value.length)

  /** 某个对象（文档）身上正在跑的任务；`type` 可限定任务类型。 */
  function activeFor(targetId: number, type?: string): Job | null {
    const hit = running.value.filter(
      (job) => job.target_id === targetId && (!type || job.type === type),
    )
    return hit.length ? hit[hit.length - 1] : null
  }

  function remember(job: Job): Job {
    byId.value[job.id] = job
    prune(job.id)
    return byId.value[job.id]
  }

  /** 只留最近的一批：先丢已结束的旧任务，正在跑的一律不动。 */
  function prune(keepId: number): void {
    const ids = Object.keys(byId.value).map(Number)
    if (ids.length <= MAX_TRACKED) return
    for (const id of ids) {
      if (Object.keys(byId.value).length <= MAX_TRACKED) return
      if (id === keepId || id === lastFinished.value?.id) continue
      if (isActive(byId.value[id])) continue
      delete byId.value[id]
    }
  }

  /** 开始跟踪一个任务：立刻记住状态，之后轮询到结束。 */
  async function track(job: Job): Promise<void> {
    remember(job)
    if (isActive(job)) await poll(job.id)
  }

  /** 轮询一个任务直到它结束；同一任务只跑一条轮询链。 */
  async function poll(id: number): Promise<void> {
    if (polling.has(id)) return
    polling.add(id)
    try {
      for (;;) {
        await sleep(POLL_MS)
        const latest = await jobsApi.get(id)
        remember(latest)
        if (!isActive(latest)) {
          lastFinished.value = latest
          return
        }
      }
    } catch {
      // 拉不到就停下（后端重启/网络抖动）。下次进页面 sync() 会重新拉起，
      // 别在这里死循环刷日志。
    } finally {
      polling.delete(id)
    }
  }

  /**
   * 页面进入时把该对象还在跑的任务接回来。
   *
   * 应用刷新、或任务是在别的页面入队的，本地没有轮询链，靠这里补上。
   */
  async function sync(targetId: number, limit = 10): Promise<void> {
    try {
      const rows = await jobsApi.list({ target_id: targetId, limit })
      for (const job of rows) remember(job)
      for (const job of rows) if (isActive(job)) void poll(job.id)
    } catch {
      // 后端不可用时静默：页面主体还能显示缓存的数据
    }
  }

  /** 同上，但不限定对象（文档库列表要找出所有在跑的打标签任务）。 */
  async function syncAll(limit = 30): Promise<void> {
    try {
      const rows = await jobsApi.list({ limit })
      for (const job of rows) remember(job)
      for (const job of rows) if (isActive(job)) void poll(job.id)
    } catch {
      // 同上
    }
  }

  return {
    byId,
    lastFinished,
    running,
    runningCount,
    activeFor,
    track,
    sync,
    syncAll,
  }
})
