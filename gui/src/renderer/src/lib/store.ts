// App-wide state: where we are (route + history), which investigation is open, toasts, theme,
// and the live view of running jobs. Server data itself lives in react-query, not here.

import { create } from 'zustand'
import type { Job, LogLine, Project, ReviewKind } from '@shared/api'

// ── routes ───────────────────────────────────────────────────────────────────
export type Route =
  | { view: 'projects' }
  | { view: 'home' }
  | { view: 'documents'; filter?: string }
  | { view: 'document'; sha: string; page?: number; tab?: string }
  | { view: 'entities'; type?: string }
  | { view: 'entity'; id: string }
  | { view: 'graph'; focus?: string }
  | { view: 'timeline'; entity?: string }
  | { view: 'search'; query?: string }
  | { view: 'review'; kind?: ReviewKind | 'handled' | 'watchlist' | 'requests' }
  | { view: 'briefings'; path?: string }
  | { view: 'note'; path: string }
  | { view: 'ask'; session?: string; prompt?: string }
  | { view: 'research'; session?: string }
  | { view: 'activity'; job?: string; tab?: string }
  | { view: 'settings'; tab?: string }

export type ViewName = Route['view']

export type Theme = 'system' | 'light' | 'dark'

export interface Toast {
  id: number
  kind: 'success' | 'error' | 'info'
  title: string
  body?: string
  action?: { label: string; run: () => void }
}

export interface JobView extends Job {
  log: LogLine[]
}

interface State {
  route: Route
  back: Route[]
  forward: Route[]
  project: Project | null
  theme: Theme
  paletteOpen: boolean
  addOpen: { paths: string[] } | null
  toasts: Toast[]
  jobs: Record<string, JobView>

  navigate: (r: Route, opts?: { replace?: boolean }) => void
  goBack: () => void
  goForward: () => void
  setProject: (p: Project | null) => void
  setTheme: (t: Theme) => void
  setPalette: (open: boolean) => void
  openAdd: (paths?: string[]) => void
  closeAdd: () => void
  toast: (t: Omit<Toast, 'id'>) => void
  dismissToast: (id: number) => void
  upsertJob: (j: Job) => void
  appendLog: (id: string, lines: LogLine[]) => void
  setJobs: (jobs: Job[]) => void
}

let toastId = 1
const sameRoute = (a: Route, b: Route) => JSON.stringify(a) === JSON.stringify(b)

export const useApp = create<State>((set, get) => ({
  route: { view: 'projects' },
  back: [],
  forward: [],
  project: null,
  theme: 'system',
  paletteOpen: false,
  addOpen: null,
  toasts: [],
  jobs: {},

  navigate: (r, opts) => {
    const cur = get().route
    if (sameRoute(cur, r)) return
    if (opts?.replace) set({ route: r })
    else set({ route: r, back: [...get().back.slice(-60), cur], forward: [] })
    document.querySelector('.main-scroll')?.scrollTo?.({ top: 0 })
  },
  goBack: () => {
    const { back, route, forward } = get()
    if (!back.length) return
    set({ route: back[back.length - 1], back: back.slice(0, -1), forward: [route, ...forward] })
  },
  goForward: () => {
    const { back, route, forward } = get()
    if (!forward.length) return
    set({ route: forward[0], forward: forward.slice(1), back: [...back, route] })
  },
  setProject: (p) => {
    set({ project: p, back: [], forward: [], route: p ? { view: 'home' } : { view: 'projects' } })
    void window.watchdog.prefs.set('lastProject', p?.slug ?? null)
  },
  setTheme: (t) => {
    set({ theme: t })
    applyTheme(t)
    void window.watchdog.prefs.set('theme', t)
  },
  setPalette: (open) => set({ paletteOpen: open }),
  openAdd: (paths = []) => set({ addOpen: { paths } }),
  closeAdd: () => set({ addOpen: null }),
  toast: (t) => {
    const id = toastId++
    set({ toasts: [...get().toasts.slice(-4), { ...t, id }] })
    setTimeout(() => get().dismissToast(id), t.kind === 'error' ? 9000 : 5000)
  },
  dismissToast: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  upsertJob: (j) => {
    const prev = get().jobs[j.id]
    set({ jobs: { ...get().jobs, [j.id]: { ...j, log: prev?.log ?? [] } } })
  },
  appendLog: (id, lines) => {
    const prev = get().jobs[id]
    if (!prev) return
    const log = prev.log.concat(lines)
    set({ jobs: { ...get().jobs, [id]: { ...prev, log: log.length > 5000 ? log.slice(-5000) : log } } })
  },
  setJobs: (jobs) => {
    const cur = get().jobs
    const next: Record<string, JobView> = {}
    for (const j of jobs) next[j.id] = { ...j, log: cur[j.id]?.log ?? [] }
    set({ jobs: next })
  }
}))

export function applyTheme(t: Theme): void {
  const dark = t === 'dark' || (t === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

/** The open investigation's vault path. Views under an investigation can rely on it. */
export function useVault(): string {
  const p = useApp((s) => s.project)
  return p?.path ?? ''
}

export const navigate = (r: Route, opts?: { replace?: boolean }) => useApp.getState().navigate(r, opts)
export const toast = (t: Omit<Toast, 'id'>) => useApp.getState().toast(t)
