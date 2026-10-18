// Saving a reporter's Notes section (entity and document notes, saved pages) reliably: typing is
// saved a moment after it stops, when the editor loses focus, and when the reporter leaves the
// screen. A save lives here, outside the editor, so leaving never drops it: while Watchdog is
// writing the investigation's notes the save waits and retries, and if it fails the words are
// kept as a draft and the reporter is told, with a way back to them. Closing the window with a
// save outstanding asks first (the main process shows the question).

import { create } from 'zustand'
import { call, errorMessage, invalidate, type RpcFailure } from './rpc'
import { navigate, toast, useApp, type Route } from './store'

export type SaveStatus = 'dirty' | 'saving' | 'waiting' | 'error'

export interface Draft {
  vault: string
  path: string
  text: string
  status: SaveStatus
  error?: string
  label: string // what the notes are on, for a message shown after the reporter has left
  route: Route // where the editor was, to go back to it
}

const DELAY = 1200
const RETRY = 3000

interface DraftState {
  drafts: Record<string, Draft>
  /** The last time each note's notes were saved, for "Saved" in the editor. */
  savedAt: Record<string, number>
}

export const useDrafts = create<DraftState>(() => ({ drafts: {}, savedAt: {} }))

export const draftKey = (vault: string, path: string) => `${vault}\u0000${path}`
const timers = new Map<string, ReturnType<typeof setTimeout>>()
const inflight = new Set<string>()
const shown = new Map<string, number>() // how many editors show each note's notes now

function patch(key: string, d: Partial<Draft> | null): void {
  const drafts = { ...useDrafts.getState().drafts }
  if (d === null) delete drafts[key]
  else drafts[key] = { ...drafts[key], ...d }
  useDrafts.setState({ drafts })
}

function schedule(key: string, ms: number): void {
  clearTimeout(timers.get(key))
  timers.set(key, setTimeout(() => void flush(key), ms))
}

/** The reporter typed: remember the text and save it shortly. */
export function edit(vault: string, path: string, text: string, label: string): void {
  const key = draftKey(vault, path)
  const cur = useDrafts.getState().drafts[key]
  patch(key, { vault, path, text, label, status: cur?.status === 'saving' ? 'saving' : 'dirty', error: undefined, route: useApp.getState().route })
  schedule(key, DELAY)
}

/** Drop the draft without saving it (the text matches what is saved). */
export function discard(vault: string, path: string): void {
  const key = draftKey(vault, path)
  clearTimeout(timers.get(key))
  if (!inflight.has(key)) patch(key, null)
}

function notSaved(d: Draft): void {
  toast({
    kind: 'error',
    title: 'Your notes were not saved',
    body: `${d.label}: ${d.error ?? 'the save failed'}. Your text is kept until you go back to it.`,
    action: { label: 'Go back', run: () => navigate(d.route) }
  })
}

/** An editor shows this note's notes; the returned function is called when it goes away. */
export function mount(vault: string, path: string): () => void {
  const key = draftKey(vault, path)
  shown.set(key, (shown.get(key) ?? 0) + 1)
  return () => {
    shown.set(key, Math.max(0, (shown.get(key) ?? 1) - 1))
    const cur = useDrafts.getState().drafts[key]
    if (!cur) return
    if (cur.status === 'dirty') void flush(key) // leaving the screen saves at once
    else if (cur.status === 'error' && !shown.get(key)) notSaved(cur)
  }
}

/** Save now. Safe to call at any time; one save per note runs at once. */
export async function flush(key: string): Promise<void> {
  clearTimeout(timers.get(key))
  const d = useDrafts.getState().drafts[key]
  if (!d || inflight.has(key) || d.status === 'saving') return
  inflight.add(key)
  patch(key, { status: 'saving', error: undefined })
  const sent = d.text
  try {
    await call('vault.saveNotes', { vault: d.vault, path: d.path, text: sent })
    inflight.delete(key)
    const now = useDrafts.getState().drafts[key]
    if (now && now.text === sent) patch(key, null)
    else if (now) {
      patch(key, { status: 'dirty' })
      schedule(key, 0)
    }
    useDrafts.setState({ savedAt: { ...useDrafts.getState().savedAt, [key]: Date.now() } })
    invalidate('vault.entity', 'vault.document', 'vault.note', 'history.')
  } catch (e) {
    inflight.delete(key)
    const code = (e as RpcFailure).code
    if (code === 'busy') {
      patch(key, { status: 'waiting', error: errorMessage(e) })
      schedule(key, RETRY)
      return
    }
    patch(key, { status: 'error', error: errorMessage(e) })
    const cur = useDrafts.getState().drafts[key]
    if (cur && !shown.get(key)) notSaved(cur)
  }
}

export function hasUnsaved(): boolean {
  return Object.keys(useDrafts.getState().drafts).length > 0
}

// Closing the window with notes not yet saved: try to save them, and ask before closing (the
// main process turns a prevented unload into a question, since Electron shows none itself).
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', (e) => {
    if (!hasUnsaved()) return
    for (const key of Object.keys(useDrafts.getState().drafts)) void flush(key)
    e.preventDefault()
    e.returnValue = ''
  })
}
