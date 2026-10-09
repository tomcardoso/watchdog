// The reporter's own check of a fact (D271): Verified, Disputed or Can't verify, with an optional
// note. Used on each fact row in a document and in Review → Verification. Marks are written
// through `verify.mark`, the library function `watchdog verify-fact` calls.

import { Check, CircleHelp, MessageSquareText, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { FactMark, VerifyStatus } from '@shared/api'
import { cx } from '@renderer/components/ui'
import { fmtDate } from '@renderer/lib/format'
import { call, errorMessage, invalidate } from '@renderer/lib/rpc'
import { toast, useVault } from '@renderer/lib/store'
import './factcheck.css'

export const STATUS_META: Record<VerifyStatus, { label: string; icon: typeof Check; key: string; tip: string }> = {
  verified: { label: 'Verified', icon: Check, key: 'V', tip: 'You checked this against the source and it holds' },
  disputed: { label: 'Disputed', icon: X, key: 'D', tip: 'The source, or another record, does not support this' },
  unverifiable: { label: "Can't verify", icon: CircleHelp, key: 'C', tip: 'You could not confirm or rule this out' }
}
export const STATUSES: VerifyStatus[] = ['verified', 'disputed', 'unverifiable']

/** Marks with optimistic updates: what the reporter just did shows at once, and is rolled back
 * with a message if it could not be saved. */
export function useMarks() {
  const vault = useVault()
  const [local, setLocal] = useState<Record<string, FactMark | null>>({})
  const get = useCallback((id: string, server: FactMark | null) => (id in local ? local[id] : server), [local])
  const set = useCallback(
    async (id: string, status: VerifyStatus | null, note: string | null | undefined, prev: FactMark | null) => {
      const optimistic: FactMark | null = status ? { status, note: note === undefined ? prev?.note ?? null : note || null, by: prev?.by ?? null, at: new Date().toISOString() } : null
      setLocal((m) => ({ ...m, [id]: optimistic }))
      try {
        const saved = await call('verify.mark', { vault, id, status, note: note === undefined ? prev?.note ?? null : note })
        setLocal((m) => ({ ...m, [id]: saved.status ? { status: saved.status, note: saved.note, by: saved.by, at: saved.at } : null }))
        invalidate('vault.document', 'vault.entity', 'vault.summary', 'vault.citations', 'verify.')
      } catch (e) {
        setLocal((m) => ({ ...m, [id]: prev }))
        toast({ kind: 'error', title: 'Could not save your check', body: errorMessage(e) })
      }
    },
    [vault]
  )
  return { get, set }
}
export type Marks = ReturnType<typeof useMarks>

/** Handle V / D / C / N on a focused fact row. Returns true when the key was used. */
export function handleMarkKey(e: React.KeyboardEvent | KeyboardEvent, id: string, mark: FactMark | null, marks: Marks, openNote: () => void): boolean {
  if (e.metaKey || e.ctrlKey || e.altKey) return false
  const k = e.key.toLowerCase()
  const status = k === 'v' ? 'verified' : k === 'd' ? 'disputed' : k === 'c' ? 'unverifiable' : null
  if (status) {
    void marks.set(id, mark?.status === status ? null : status, undefined, mark)
    return true
  }
  if (k === 'n' && mark) {
    openNote()
    return true
  }
  return false
}

export function MarkSummary({ mark }: { mark: FactMark }) {
  const m = STATUS_META[mark.status]
  return (
    <span className={cx('fcheck-badge', `is-${mark.status}`)}>
      <m.icon />
      {m.label}
      {(mark.by || mark.at) && <span className="fcheck-who">{[mark.by, mark.at ? fmtDate(mark.at.slice(0, 10)) : null].filter(Boolean).join(', ')}</span>}
    </span>
  )
}

export function FactCheck({ id, mark, marks, noteOpen, setNoteOpen }: { id: string; mark: FactMark | null; marks: Marks; noteOpen: boolean; setNoteOpen: (v: boolean) => void }) {
  const [draft, setDraft] = useState(mark?.note ?? '')
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (noteOpen) {
      setDraft(mark?.note ?? '')
      setTimeout(() => ref.current?.focus(), 0)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [noteOpen])
  const saveNote = () => {
    setNoteOpen(false)
    if (mark && draft.trim() !== (mark.note ?? '')) void marks.set(id, mark.status, draft.trim(), mark)
  }
  return (
    <div className="fcheck">
      <div className="fcheck-row">
        <div className="fcheck-btns" role="group" aria-label="Your check of this fact">
          {STATUSES.map((s) => {
            const m = STATUS_META[s]
            const on = mark?.status === s
            return (
              <button
                key={s}
                type="button"
                className={cx('fcheck-btn', `is-${s}`, on && 'on')}
                aria-pressed={on}
                data-tip={on ? `${m.label}. Click again to clear (${m.key})` : `${m.tip} (${m.key})`}
                onClick={(e) => {
                  e.stopPropagation()
                  void marks.set(id, on ? null : s, undefined, mark)
                }}
              >
                <m.icon />
                {m.label}
              </button>
            )
          })}
        </div>
        {mark && (
          <>
            <span className="fcheck-who">{[mark.by, mark.at ? fmtDate(mark.at.slice(0, 10)) : null].filter(Boolean).join(', ')}</span>
            {!noteOpen && (
              <button type="button" className="fcheck-note-btn" onClick={(e) => { e.stopPropagation(); setNoteOpen(true) }} data-tip="A note on what you checked, or why (N)">
                <MessageSquareText />
                {mark.note ? 'Edit note' : 'Add note'}
              </button>
            )}
          </>
        )}
      </div>
      {mark && noteOpen && (
        <textarea
          ref={ref}
          className="textarea fcheck-textarea"
          value={draft}
          rows={2}
          maxLength={2000}
          placeholder="What you checked it against, or why it is in doubt"
          onChange={(e) => setDraft(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            e.stopPropagation()
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              saveNote()
            } else if (e.key === 'Escape') {
              e.preventDefault()
              setNoteOpen(false)
            }
          }}
          onBlur={saveNote}
        />
      )}
      {mark?.note && !noteOpen && <div className="fcheck-note selectable">{mark.note}</div>}
    </div>
  )
}
