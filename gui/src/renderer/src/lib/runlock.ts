// What a run lock means to the reporter (D293). Locks clear themselves when their run ends or dies,
// so the app never offers to release one: it only says whether documents are being added, here or
// on another computer.

import type { RunLocks } from '@shared/api'

export interface RunLockState {
  /** Short label for a badge. */
  badge: string
  title: string
  detail: string
}

export function runLockState(locks: RunLocks | null | undefined): RunLockState | null {
  if (!locks) return null
  const held = [locks.chew, locks.ingest].filter((l) => l !== null)
  if (!held.length) return null
  const remote = held.find((l) => l!.where === 'elsewhere')
  if (remote) {
    const on = remote.host ? ` on ${remote.host}` : ' on another computer'
    return {
      badge: 'being added on another computer',
      title: 'Being added on another computer',
      detail: `Documents are being added to this investigation${on}. You can add more here once that run ends. If that computer stopped without finishing, this clears about 15 minutes after it last checked in.`
    }
  }
  return {
    badge: 'being added',
    title: 'Documents are being added',
    detail: 'A run is working on this investigation. You can add more once it ends.'
  }
}
