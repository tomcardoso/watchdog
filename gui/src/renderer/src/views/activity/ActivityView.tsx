// Activity: jobs and maintenance. Jobs work with no investigation open; the vault-specific tabs
// (maintenance, ingest history, usage) appear only inside one.

import { Activity, History, Wrench, Coins, Terminal } from 'lucide-react'
import { lazy, Suspense } from 'react'
import { Skeleton, Tabs } from '@renderer/components/ui'
import { useApp } from '@renderer/lib/store'
import JobsPanel from './JobsPanel'
import './activity.css'

const MaintenancePanel = lazy(() => import('./MaintenancePanel'))
const HistoryPanel = lazy(() => import('./HistoryPanel'))
const UsagePanel = lazy(() => import('./UsagePanel'))

type Tab = 'jobs' | 'maintenance' | 'history' | 'usage'

export default function ActivityView() {
  const route = useApp((s) => s.route)
  const navigate = useApp((s) => s.navigate)
  const project = useApp((s) => s.project)
  const running = useApp((s) => Object.values(s.jobs).filter((j) => j.state === 'running').length)
  const r = route.view === 'activity' ? route : { view: 'activity' as const }
  let tab = (r.tab as Tab) || 'jobs'
  if (!project && tab !== 'jobs') tab = 'jobs'
  const go = (t: Tab) => navigate({ view: 'activity', tab: t })

  const tabs: { value: Tab; label: string; icon: typeof Activity; count?: number }[] = [{ value: 'jobs', label: 'Jobs', icon: Terminal, count: running }]
  if (project)
    tabs.push(
      { value: 'maintenance', label: 'Maintenance', icon: Wrench },
      { value: 'history', label: 'Ingest history', icon: History },
      { value: 'usage', label: 'Usage', icon: Coins }
    )

  return (
    <div className="page">
      <div className="page-inner wide">
        <div className="page-header">
          <div className="grow">
            <h1 className="page-title">Activity</h1>
            <div className="page-sub">
              {project ? `Running work, maintenance and cost for ${project.name}.` : 'Running and recent work. Open an investigation for maintenance, history and usage.'}
            </div>
          </div>
        </div>
        {tabs.length > 1 && <Tabs value={tab} onChange={go} tabs={tabs} style={{ marginBottom: 20 }} />}
        <Suspense fallback={<Skeleton h={240} />}>
          {tab === 'jobs' && <JobsPanel selected={r.job} onSelect={(id) => navigate({ view: 'activity', tab: 'jobs', job: id }, { replace: true })} />}
          {tab === 'maintenance' && project && <MaintenancePanel />}
          {tab === 'history' && project && <HistoryPanel />}
          {tab === 'usage' && project && <UsagePanel />}
        </Suspense>
      </div>
    </div>
  )
}
