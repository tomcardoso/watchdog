import {
  Activity, BookOpenText, ChevronsUpDown, Files, FolderOpen, Globe2, House, LayoutGrid, ListChecks,
  MessageSquareText, Network, Plus, ScanSearch, Search, Settings, Shapes, CalendarRange, Target
} from 'lucide-react'
import { CSSProperties } from 'react'
import { AlertTriangle, RotateCw } from 'lucide-react'
import { Button, Dropdown, Progress } from '@renderer/components/ui'
import { LogoMark } from '@renderer/components/Logo'
import { useRpc } from '@renderer/lib/rpc'
import { finishSetup, useEngineGate } from '@renderer/lib/engine'
import { Route, useApp, ViewName } from '@renderer/lib/store'
import { plural } from '@renderer/lib/format'
import type { Project } from '@shared/api'

/** Two-letter monogram and a stable colour pair per investigation, so each one is recognizable. */
export function ProjectMark({ project, size = 32 }: { project: Pick<Project, 'name' | 'slug'>; size?: number }) {
  const words = project.name.split(/\s+/).filter(Boolean)
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : project.name.slice(0, 2)).toUpperCase()
  let h = 0
  for (const c of project.slug) h = (h * 31 + c.charCodeAt(0)) % 997
  const n = (h % 8) + 1 // one of the eight --mark-N pairs in tokens.css
  return (
    <span
      className="project-mark"
      style={{ width: size, height: size, fontSize: size * 0.4, '--mark-a': `var(--mark-${n}a)`, '--mark-b': `var(--mark-${n}b)` } as CSSProperties}
    >
      {initials}
    </span>
  )
}

interface NavDef { view: ViewName; label: string; icon: typeof House; route?: Route }
const INVESTIGATION: NavDef[] = [
  { view: 'home', label: 'Overview', icon: House },
  { view: 'documents', label: 'Documents', icon: Files },
  { view: 'entities', label: 'Entities', icon: Shapes },
  { view: 'timeline', label: 'Timeline', icon: CalendarRange },
  { view: 'graph', label: 'Network', icon: Network }
]
const WORK: NavDef[] = [
  { view: 'context', label: 'Context', icon: Target },
  { view: 'watchlist', label: 'Watchlist', icon: ScanSearch },
  { view: 'review', label: 'Review', icon: ListChecks },
  { view: 'search', label: 'Search', icon: Search },
  { view: 'ask', label: 'Ask Claude', icon: MessageSquareText },
  { view: 'research', label: 'Web research', icon: Globe2 },
  { view: 'briefings', label: 'Reports', icon: BookOpenText }
]

const WATCHDOG: NavDef[] = [
  { view: 'activity', label: 'Activity', icon: Activity },
  { view: 'settings', label: 'Settings', icon: Settings }
]

/** The sidebar's buttons in the order they appear, for Ctrl+Tab / Ctrl+Shift+Tab (App.tsx). */
export function sidebarViews(hasProject: boolean): ViewName[] {
  const list = hasProject ? [...INVESTIGATION, ...WORK, ...WATCHDOG] : [{ view: 'projects' as ViewName }, ...WATCHDOG]
  return list.map((n) => n.view)
}

/** The sidebar button a screen belongs to: a document is under Documents, and so on. */
export function sidebarViewOf(view: ViewName): ViewName {
  return ({ document: 'documents', entity: 'entities', note: 'briefings' } as Partial<Record<ViewName, ViewName>>)[view] ?? view
}

export function Sidebar() {
  const project = useApp((s) => s.project)
  const route = useApp((s) => s.route)
  const navigate = useApp((s) => s.navigate)
  const setProject = useApp((s) => s.setProject)
  const jobs = useApp((s) => s.jobs)
  const running = Object.values(jobs).filter((j) => j.state === 'running').length
  const { data: projects } = useRpc('projects.list', {})
  const { data: summary } = useRpc('vault.summary', project ? { vault: project.path } : null, { refetchInterval: 30_000 })
  // Judgement items only, like the Overview's headline number (D327); tidy-up is counted in Review.
  const reviewCount = summary ? summary.judgement ?? 0 : 0
  const backend = useApp((s) => s.project) // re-render on project change
  void backend

  const current = (v: ViewName) => v === sidebarViewOf(route.view)
  const item = (n: NavDef, extra?: React.ReactNode) => (
    <button key={n.view} className="nav-item" aria-current={current(n.view) ? 'page' : undefined} onClick={() => navigate(n.route ?? ({ view: n.view } as Route))}>
      <n.icon />
      <span>{n.label}</span>
      {extra}
    </button>
  )

  return (
    <aside className="sidebar">
      <div className="sidebar-drag">
        <span className="sidebar-brand logo-wag">
          <LogoMark />
          <span className="sidebar-brand-text">Watchdog</span>
        </span>
      </div>
      {project ? (
        <Dropdown
          trigger={(open) => (
            <button className="project-switch" onClick={open}>
              <ProjectMark project={project} />
              <span className="grow">
                <div className="name truncate">{project.name}</div>
                <div className="sub truncate">{plural(summary?.totals.documents ?? project.stats.documents, 'document')}</div>
              </span>
              <ChevronsUpDown className="chev" />
            </button>
          )}
          items={[
            ...(projects ?? [])
              .filter((p) => p.slug !== project.slug && !p.archived)
              .slice(0, 8)
              .map((p) => ({ label: p.name, icon: FolderOpen, onClick: () => setProject(p) })),
            { separator: true, label: '' },
            { label: 'All investigations', icon: LayoutGrid, onClick: () => setProject(null) },
            { label: 'New investigation…', icon: Plus, onClick: () => window.dispatchEvent(new CustomEvent('wd:command', { detail: 'new-investigation' })) }
          ]}
        />
      ) : (
        <div style={{ padding: '0 10px 10px' }}>
          <button className="nav-item" aria-current={route.view === 'projects' ? 'page' : undefined} onClick={() => navigate({ view: 'projects' })}>
            <LayoutGrid />
            <span>All investigations</span>
          </button>
        </div>
      )}
      <nav className="nav">
        {project && (
          <>
            <div className="nav-group">
              <div className="nav-label">Investigation</div>
              {INVESTIGATION.map((n) => item(n))}
            </div>
            <div className="nav-group">
              <div className="nav-label">Work</div>
              {WORK.map((n) => item(n, n.view === 'review' && reviewCount > 0 ? <span className="count-pill">{reviewCount}</span> : undefined))}
            </div>
          </>
        )}
        <div className="nav-group">
          {project && <div className="nav-label">Watchdog</div>}
          {item(WATCHDOG[0], running > 0 ? <span className="spinner nav-spin" /> : undefined)}
          {item(WATCHDOG[1])}
        </div>
      </nav>
      <BackendFoot />
    </aside>
  )
}

function BackendFoot() {
  const { data: info } = useRpc('app.info', {}, { staleTime: Infinity })
  return (
    <>
      <SetupProgress />
      <div className="sidebar-foot">
        <span className="status-dot" />
        <span className="truncate" title={info?.version ? `Engine ${info.version}` : undefined}>Watchdog {window.watchdog.appVersion}</span>
      </div>
    </>
  )
}

/** While the engine's background phase runs (D272): a slim bar that opens Settings → Setup. A
 * stopped run becomes a calm warning with Retry. Gone once setup has finished. */
function SetupProgress() {
  const gate = useEngineGate()
  const navigate = useApp((s) => s.navigate)
  if (gate.ready) return null
  const open = () => navigate({ view: 'settings', tab: 'setup' })
  if (gate.stalled) {
    return (
      <div className="setup-foot stalled" role="status">
        <button className="setup-foot-main" onClick={open} title="Open Settings, Setup">
          <AlertTriangle />
          <span className="grow">
            <span className="setup-foot-title">Setup did not finish</span>
            <span className="setup-foot-sub">Adding documents waits for it.</span>
          </span>
        </button>
        <Button size="sm" variant="soft" icon={RotateCw} onClick={finishSetup}>Retry</Button>
      </div>
    )
  }
  const pct = Math.round(gate.fraction * 100)
  return (
    <button className="setup-foot" onClick={open} role="status" aria-label={`Finishing setup, ${pct} percent. Open Settings, Setup for details.`}>
      <span className="setup-foot-row">
        <span className="grow truncate">Finishing setup…</span>
        <span className="tnum">{pct}%</span>
      </span>
      <Progress value={gate.fraction} indeterminate={!gate.finishing} />
    </button>
  )
}
