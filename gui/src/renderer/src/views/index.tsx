// Route → view component. Views are lazy-loaded so the shell paints immediately.

import { lazy } from 'react'
import type { ViewName } from '@renderer/lib/store'

export const VIEWS: Record<ViewName, React.LazyExoticComponent<React.ComponentType>> = {
  projects: lazy(() => import('./projects/ProjectsView')),
  home: lazy(() => import('./home/HomeView')),
  documents: lazy(() => import('./documents/DocumentsView')),
  document: lazy(() => import('./documents/DocumentView')),
  entities: lazy(() => import('./entities/EntitiesView')),
  entity: lazy(() => import('./entities/EntityView')),
  graph: lazy(() => import('./graph/GraphView')),
  timeline: lazy(() => import('./timeline/TimelineView')),
  search: lazy(() => import('./search/SearchView')),
  review: lazy(() => import('./review/ReviewView')),
  briefings: lazy(() => import('./briefings/BriefingsView')),
  note: lazy(() => import('./briefings/NoteView')),
  ask: lazy(() => import('./chat/AskView')),
  research: lazy(() => import('./chat/ResearchView')),
  activity: lazy(() => import('./activity/ActivityView')),
  settings: lazy(() => import('./settings/SettingsView'))
}
