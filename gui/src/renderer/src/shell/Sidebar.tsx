import {
  Activity, BookOpenText, ChevronsUpDown, Files, FolderOpen, Globe2, House, LayoutGrid, ListChecks,
  MessageSquareText, Network, Plus, Search, Settings, Shapes, CalendarRange
} from 'lucide-react'
import { CSSProperties } from 'react'
import { Dropdown } from '@renderer/components/ui'
import { LogoMark } from '@renderer/components/Logo'
import { useRpc } from '@renderer/lib/rpc'
import { Route, useApp, ViewName } from '@renderer/lib/store'
import { plural } from '@renderer/lib/format'
import type { Project } from '@shared/api'

/** Two-letter monogram and a stable hue per investigation, so each one is recognizable. */
export function ProjectMark({ project, size = 32 }: { project: Pick<Project, 'name' | 'slug'>; size?: number }) {
  const words = project.name.split(/\s+/).filter(Boolean)
  const initials = (words.length > 1 ? words[0][0] + words[1][0] : project.name.slice(0, 2)).toUpperCase()
  let h = 0
  for (const c of project.slug) h = (h * 31 + c.charCodeAt(0)) % 360
  const hues = [18, 200, 262, 160, 330, 40, 222, 4]
  const hue = hues[h % hues.length]
  return (
    <span
      className="project-mark"
      style={{ width: size, height: size, fontSize: size * 0.4, '--mark-a': `hsl(${hue} 62% 52%)`, '--mark-b': `hsl(${(hue + 20) % 360} 64% 36%)` } as CSSProperties}
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
  { view: 'review', label: 'Review', icon: ListChecks },
  { view: 'search', label: 'Search', icon: Search },
  { view: 'ask', label: 'Ask Claude', icon: MessageSquareText },
  { view: 'research', label: 'Web research', icon: Globe2 },
  { view: 'briefings', label: 'Briefings', icon: BookOpenText }
]

export function Sidebar() {
  const project = useApp((s) => s.project)
  const route = useApp((s) => s.route)
  const navigate = useApp((s) => s.navigate)
  const setProject = useApp((s) => s.setProject)
  const jobs = useApp((s) => s.jobs)
  const running = Object.values(jobs).filter((j) => j.state === 'running').length
  const { data: projects } = useRpc('projects.list', {})
  const { data: summary } = useRpc('vault.summary', project ? { vault: project.path } : null, { refetchInterval: 30_000 })
  const reviewCount = summary ? summary.contradictions + summary.leads + summary.near_duplicates + summary.alerts : 0
  const backend = useApp((s) => s.project) // re-render on project change
  void backend

  const current = (v: ViewName) => {
    const r = route.view
    if (v === r) return true
    if (v === 'documents' && r === 'document') return true
    if (v === 'entities' && r === 'entity') return true
    if (v === 'briefings' && r === 'note') return true
    return false
  }
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
        <span className="sidebar-brand">
          <LogoMark />
          Watchdog
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
          <button className="nav-item" aria-current="page">
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
          {item({ view: 'activity', label: 'Activity', icon: Activity }, running > 0 ? <span className="spinner nav-spin" /> : undefined)}
          {item({ view: 'settings', label: 'Settings', icon: Settings })}
        </div>
      </nav>
      <BackendFoot />
    </aside>
  )
}

function BackendFoot() {
  const { data: info } = useRpc('app.info', {}, { staleTime: Infinity })
  return (
    <div className="sidebar-foot">
      <span className="status-dot" />
      <span className="truncate">Watchdog {info?.version ?? ''}</span>
    </div>
  )
}
