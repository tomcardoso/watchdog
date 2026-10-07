import type { Route } from '@renderer/lib/store'

const TITLES: Record<string, string> = {
  projects: 'Investigations',
  home: 'Overview',
  documents: 'Documents',
  document: 'Document',
  entities: 'Entities',
  entity: 'Entity',
  graph: 'Network',
  timeline: 'Timeline',
  search: 'Search',
  review: 'Review',
  briefings: 'Briefings',
  note: 'Note',
  ask: 'Ask Claude',
  research: 'Web research',
  activity: 'Activity',
  settings: 'Settings'
}

export function routeTitle(r: Route): string {
  return TITLES[r.view] ?? r.view
}
