import { Suspense, useEffect } from 'react'
import { Spinner } from '@renderer/components/ui'
import { call, invalidate, useEvent } from '@renderer/lib/rpc'
import { onJobFinished, onJobProgress, onJobStarted } from '@renderer/lib/jobs'
import { useEngineSync } from '@renderer/lib/engine'
import { applyTheme, Route, Theme, useApp } from '@renderer/lib/store'
import { AccessGate } from './shell/AccessGate'
import { BackendGate } from './shell/BackendGate'
import { CommandPalette } from './shell/CommandPalette'
import { DropOverlay } from './shell/DropOverlay'
import { JobDock } from './shell/JobDock'
import { Sidebar, sidebarViewOf, sidebarViews } from './shell/Sidebar'
import { Toasts } from './shell/Toasts'
import { ViewBoundary } from './shell/ViewBoundary'
import { TooltipLayer } from './components/TooltipLayer'
import { Topbar } from './shell/Topbar'
import { GlobalDialogs } from './views/dialogs/GlobalDialogs'
import { exploreSample, takeTour, TourLayer } from './tour/Tour'
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
/** Go to the next (1) or previous (-1) sidebar button, in order, wrapping. */
function cycleSidebar(step: 1 | -1): void {
  const s = useApp.getState()
  const views = sidebarViews(!!s.project)
  const at = views.indexOf(sidebarViewOf(s.route.view))
  const next = at < 0 ? (step > 0 ? 0 : views.length - 1) : (at + step + views.length) % views.length
  s.navigate({ view: views[next] } as Route)
}

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
    case 'nav-next':
      return cycleSidebar(1)
    case 'nav-prev':
      return cycleSidebar(-1)
    case 'switch-investigation':
      return s.setProject(null)
    case 'add-documents':
      return s.openAdd()
    case 'show-folder':
      return void window.watchdog.shell.openPath(s.project!.path)
    case 'open-obsidian':
      return void window.watchdog.shell.openInObsidian(s.project!.path)
    case 'take-tour':
      return void takeTour()
    case 'explore-sample':
      return void exploreSample()
    case 'new-investigation':
    case 'fetch-links':
      // Handled by GlobalDialogs. Only these two are re-dispatched: the wd:command listener
      // below hands every other name back here, so re-dispatching an unknown one would loop.
      window.dispatchEvent(new CustomEvent('wd:command', { detail: name }))
      return
    default:
      console.warn('Unknown command', name)
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

const isMac = window.watchdog.platform === 'darwin'

/** Whether a key press lands in something being typed into. */
function editing(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
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
      } else if (e.ctrlKey && !e.metaKey && !e.altKey && e.key === 'Tab') {
        // Next or previous sidebar button, in order, wrapping (Ctrl+Tab on every platform, as
        // between a browser's tabs). It does nothing in a text field, so it is safe there too.
        e.preventDefault()
        cycleSidebar(e.shiftKey ? -1 : 1)
      } else if (e.altKey && !mod && (e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !isMac && !editing(e.target)) {
        // Back and forward as in a Windows or Linux browser. Not on a Mac, where Option+arrow
        // moves by word in text and ⌘[ / ⌘] are the convention, and never while typing.
        if (e.key === 'ArrowLeft') useApp.getState().goBack()
        else useApp.getState().goForward()
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
  useEngineSync()
  useEvent('menu.command', ({ command }) => runCommand(command))
  useEvent('job.started', ({ job }) => {
    useApp.getState().upsertJob(job)
    onJobStarted()
  })
  useEvent('job.progress', ({ id, progress }) => {
    const j = useApp.getState().jobs[id]
    if (j) useApp.getState().upsertJob({ ...j, progress })
    onJobProgress(id)
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
  // A screen that needs an investigation, with none open, shows the list: the route says so too,
  // so the title and the sidebar's current item match what is on screen.
  useEffect(() => {
    if (view !== route.view) useApp.getState().navigate({ view: 'projects' }, { replace: true })
  }, [view, route.view])
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
            {project && project.access === false && !['projects', 'settings', 'activity'].includes(view) ? (
              <AccessGate project={project} />
            ) : (
              <ViewBoundary resetKey={JSON.stringify(route)}>
                <View key={project?.slug ?? 'none'} />
              </ViewBoundary>
            )}
          </Suspense>
          <JobDock />
        </div>
      </main>
      <CommandPalette />
      <GlobalDialogs />
      <TourLayer />
      <DropOverlay />
      <Toasts />
    </div>
  )
}

export default function App() {
  return (
    <>
      <BackendGate>
        <Shell />
      </BackendGate>
      <TooltipLayer />
    </>
  )
}
