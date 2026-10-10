// Which investigation a background job belongs to, by name, and a way back to it. A run keeps
// going in its own investigation when the reporter opens another, so anything it raises later
// (the public-records pause, a "finished" toast, a done view) must say which investigation it is
// about whenever that is not the one open.

import type { Project } from '@shared/api'
import { call, queryClient } from './rpc'
import { toast, useApp } from './store'

/** The investigation at `path`, from the open one or the cached investigations list. */
export function knownProject(path: string | null | undefined): Project | null {
  if (!path) return null
  const open = useApp.getState().project
  if (open?.path === path) return open
  for (const [, data] of queryClient.getQueriesData<Project[]>({ queryKey: ['projects.list'] })) {
    const hit = (data ?? []).find((p) => p.path === path)
    if (hit) return hit
  }
  return null
}

/** The investigation's name, or its folder's name when it is not in the list. */
export function investigationName(path: string | null | undefined): string {
  const p = knownProject(path)
  if (p) return p.name
  const parts = (path ?? '').split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? 'another investigation'
}

/** True when `path` names an investigation other than the open one. */
export function isElsewhere(path: string | null | undefined): boolean {
  return !!path && useApp.getState().project?.path !== path
}

/** Open the investigation at `path`. */
export async function switchTo(path: string): Promise<boolean> {
  let p = knownProject(path)
  if (!p) {
    try {
      p = (await call('projects.list', { all: true })).find((x) => x.path === path) ?? null
    } catch {
      p = null
    }
  }
  if (!p) {
    toast({ kind: 'error', title: 'That investigation is not in the list', body: 'It may have been removed or moved.' })
    return false
  }
  useApp.getState().setProject(p)
  return true
}
