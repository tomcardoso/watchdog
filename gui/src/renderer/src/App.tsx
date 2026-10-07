import { Suspense, useEffect } from 'react'
import { Spinner } from '@renderer/components/ui'
import { call, invalidate, useEvent } from '@renderer/lib/rpc'
import { onJobFinished } from '@renderer/lib/jobs'
import { applyTheme, Route, Theme, useApp } from '@renderer/lib/store'
import { BackendGate } from './shell/BackendGate'
import { CommandPalette } from './shell/CommandPalette'
import { DropOverlay } from './shell/DropOverlay'
import { JobDock } from './shell/JobDock'
import { Sidebar } from './shell/Sidebar'
import { Toasts } from './shell/Toasts'
import { Topbar } from './shell/Topbar'
import { GlobalDialogs } from './views/dialogs/GlobalDialogs'
import { VIEWS } from './views'

const GO: Record<string, Route> = {
  'go:home': { view: 'home' },
  'go:documents': { view: 'documents' },
  'go:entities': { view: 'entities' },
  'go:timeline': { view: 'timeline' },
  'go:graph': { view: 'graph' },
  'go:review': { view: 'review' },
  'go:ask': { view: 'ask' },
  settings: { view: 'settings' },
  search: { view: 'search' }
}

/** Menu items, palette entries and shortcuts all arrive here as command names. */
function runCommand(name: string): void {
  const s = useApp.getState()
  const needsProject = name.startsWith('go:') || ['search', 'add-documents', 'show-folder', 'open-obsidian', 'fetch-links'].includes(name)
  if (needsProject && !s.project) {
    s.navigate({ view: 'projects' })
    return
  }
  if (GO[name]) return s.navigate(GO[name])
  switch (name) {
    case 'palette':
      return s.setPalette(true)
    case 'switch-investigation':
      return s.setProject(null)
    case 'add-documents':
      return s.openAdd()
    case 'show-folder':
      return void window.watchdog.shell.openPath(s.project!.path)
    case 'open-obsidian':
      return void window.watchdog.shell.openInObsidian(s.project!.path)
    default:
      // new-investigation, fetch-links: handled by GlobalDialogs
      window.dispatchEvent(new CustomEvent('wd:command', { detail: name }))
  }
}

function useBootstrap(): void {
  const setProject = useApp((s) => s.setProject)
  useEffect(() => {
    void (async () => {
      const theme = (await window.watchdog.prefs.get<Theme>('theme')) ?? 'system'
      useApp.setState({ theme })
      applyTheme(theme)
      window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => applyTheme(useApp.getState().theme))
      const last = await window.watchdog.prefs.get<string>('lastProject')
      if (last) {
        try {
          const p = await call('projects.get', { slug: last })
          if (p && !p.archived && !p.health) setProject(p)
        } catch {
          /* the investigation was removed or renamed; start on the list */
        }
      }
      try {
        useApp.getState().setJobs(await call('jobs.list', {}))
      } catch {
        /* jobs module not available */
      }
    })()
  }, [setProject])
}

function useShortcuts(): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        useApp.getState().setPalette(!useApp.getState().paletteOpen)
      } else if (mod && e.key === '[') {
        useApp.getState().goBack()
      } else if (mod && e.key === ']') {
        useApp.getState().goForward()
      } else if (e.altKey && e.key === 'ArrowLeft') {
        useApp.getState().goBack()
      } else if (e.altKey && e.key === 'ArrowRight') {
        useApp.getState().goForward()
      }
    }
    const onMouse = (e: MouseEvent) => {
      if (e.button === 3) useApp.getState().goBack()
      if (e.button === 4) useApp.getState().goForward()
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('mouseup', onMouse)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('mouseup', onMouse)
    }
  }, [])
}

function Shell() {
  const route = useApp((s) => s.route)
  const project = useApp((s) => s.project)
  useBootstrap()
  useShortcuts()
  useEvent('menu.command', ({ command }) => runCommand(command))
  useEvent('job.started', ({ job }) => useApp.getState().upsertJob(job))
  useEvent('job.progress', ({ id, progress }) => {
    const j = useApp.getState().jobs[id]
    if (j) useApp.getState().upsertJob({ ...j, progress })
  })
  useEvent('job.log', ({ id, lines }) => useApp.getState().appendLog(id, lines))
  useEvent('job.finished', ({ job }) => {
    useApp.getState().upsertJob(job)
    onJobFinished(job)
  })
  useEffect(() => {
    const onCmd = (e: Event) => {
      const name = (e as CustomEvent<string>).detail
      if (name !== 'new-investigation' && name !== 'fetch-links') runCommand(name)
    }
    window.addEventListener('wd:command', onCmd)
    return () => window.removeEventListener('wd:command', onCmd)
  }, [])
  useEffect(() => invalidate('vault.'), [project?.path])

  const view = project || ['projects', 'settings', 'activity'].includes(route.view) ? route.view : 'projects'
  const View = VIEWS[view]
  return (
    <div className={`app platform-${window.watchdog.platform}`}>
      <Sidebar />
      <main className="main">
        <Topbar />
        <div className="main-scroll">
          <Suspense
            fallback={
              <div style={{ display: 'grid', placeItems: 'center', height: '100%' }}>
                <Spinner size="lg" />
              </div>
            }
          >
            <View key={project?.slug ?? 'none'} />
          </Suspense>
          <JobDock />
        </div>
      </main>
      <CommandPalette />
      <GlobalDialogs />
      <DropOverlay />
      <Toasts />
    </div>
  )
}

export default function App() {
  return (
    <BackendGate>
      <Shell />
    </BackendGate>
  )
}
