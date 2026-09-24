import { onBeforeUnmount, onMounted, watch, type Ref } from 'vue'

export interface PollerOptions {
  /** 额外的激活条件（如弹窗是否打开）；为 false 时暂停，恢复 true 时立即补一次 */
  active?: Ref<boolean>
  /** start 时是否立刻执行一次；默认 true */
  immediate?: boolean
}

export interface Poller {
  start(): void
  stop(): void
  readonly running: boolean
}

/**
 * 可见性感知的单飞轮询器。
 *
 * 解决的问题：工作台多个面板用 setInterval 恒定轮询后端，弹窗关着、浏览器
 * 切到后台时仍持续发请求（引擎状态/日志、运行时事件、GPU 等），且慢请求
 * 可能与下一次轮询重叠堆积。
 *
 * 行为：
 * - document.hidden（标签页切后台/最小化）时暂停，回到前台立即补一次再恢复节奏；
 * - active ref 为 false 时暂停，变 true 时立即补一次；
 * - 上一次回调还没返回则跳过本次 tick（单飞），请求永不重叠；
 * - usePolling 封装会在组件卸载时自动 stop() 清理。
 */
export function createPoller(
  job: () => unknown | Promise<unknown>,
  intervalMs: number,
  options: PollerOptions = {},
): Poller {
  const { active, immediate = true } = options

  let timer: number | undefined
  let started = false
  let inFlight = false

  const isActive = (): boolean =>
    started && (active ? active.value : true) && !document.hidden

  const tick = (): void => {
    if (!isActive() || inFlight) return
    inFlight = true
    Promise.resolve()
      .then(job)
      .catch(() => { /* 轮询异常由 job 内部自行处理，这里保证单飞锁一定释放 */ })
      .finally(() => { inFlight = false })
  }

  const clear = (): void => {
    if (timer !== undefined) {
      window.clearInterval(timer)
      timer = undefined
    }
  }

  const arm = (): void => {
    clear()
    if (!isActive()) return
    if (immediate) tick()
    timer = window.setInterval(tick, intervalMs)
  }

  const onVisibility = (): void => {
    if (!started) return
    if (document.hidden) {
      clear()
    } else {
      arm()
    }
  }

  // active 监听只注册一次（工厂在 setup 中调用），随组件卸载自动回收；
  // 回调靠 started 守卫，避免 start/stop 反复切换时重复注册 watcher
  if (active) {
    watch(active, (v) => {
      if (!started) return
      if (v) arm(); else clear()
    })
  }

  return {
    start(): void {
      if (started) return
      started = true
      document.addEventListener('visibilitychange', onVisibility)
      arm()
    },
    stop(): void {
      started = false
      clear()
      document.removeEventListener('visibilitychange', onVisibility)
    },
    get running(): boolean {
      return started && (active ? active.value : true) && !document.hidden
    },
  } satisfies Poller
}

/**
 * 便捷封装：随组件挂载/卸载自动启停的轮询。
 */
export function usePolling(
  job: () => unknown | Promise<unknown>,
  intervalMs: number,
  options: PollerOptions = {},
): Poller {
  const poller = createPoller(job, intervalMs, options)
  onMounted(() => poller.start())
  onBeforeUnmount(() => poller.stop())
  return poller
}
