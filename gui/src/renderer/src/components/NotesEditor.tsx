// The reporter's Notes section of an entity note, a document note or a saved page: shown as
// rendered Markdown (wikilinks and fact citations work), edited as plain Markdown text. Saving is
// automatic and survives leaving the screen (lib/notesSaver). Only this section is ever written:
// Watchdog never overwrites it, and the rest of the note is not touched by a save. Give it
// `key={path}` so moving to another note starts a fresh editor.

import { AlertTriangle, Check, NotebookPen, Pencil, RotateCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Markdown } from './Markdown'
import { Button } from './ui'
import { discard, draftKey, edit, flush, mount, useDrafts } from '@renderer/lib/notesSaver'
import './notes.css'

/** Split a note body at its `## Notes` section (the journalist's own). */
export function splitNotes(body: string): { rest: string; notes: string } {
  const lines = body.split('\n')
  let fence = false
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*(```|~~~)/.test(lines[i])) fence = !fence
    if (!fence && /^##[ \t]+Notes[ \t]*$/.test(lines[i])) {
      const notes = lines.slice(i + 1).join('\n').replace(/<!--[\s\S]*?-->/g, '').trim()
      return { rest: lines.slice(0, i).join('\n').trim(), notes }
    }
  }
  return { rest: body.trim(), notes: '' }
}

/** A note's Notes section as the editor shows it: placeholder comments removed, trimmed. */
export function cleanNotes(raw: string | null | undefined): string {
  return (raw ?? '').replace(/<!--[\s\S]*?-->/g, '').trim()
}

export function NotesEditor({
  vault,
  path,
  saved,
  label,
  promise,
  placeholder = 'Your own observations, questions and follow-ups.',
  heading = true
}: {
  vault: string
  path: string
  saved: string // the section as it is on disk, cleaned
  label: string // what the notes are on, for a message after the reporter has left
  promise: string // who else writes this file, and that they leave this section alone
  placeholder?: string
  heading?: boolean
}) {
  const key = draftKey(vault, path)
  const draft = useDrafts((s) => s.drafts[key])
  const savedAt = useDrafts((s) => s.savedAt[key])
  // Back on a note whose save failed or never started: open it for editing, with the words kept.
  const [editing, setEditing] = useState(draft?.status === 'error' || draft?.status === 'dirty')
  const [text, setText] = useState(draft?.text ?? saved)
  const area = useRef<HTMLTextAreaElement>(null)

  useEffect(() => mount(vault, path), [vault, path])
  // New text from disk (a save landed, Obsidian changed the file) replaces what is shown, unless
  // the reporter has words not saved yet.
  useEffect(() => {
    if (!useDrafts.getState().drafts[key]) setText(saved)
  }, [saved, key])
  useEffect(() => {
    if (editing) area.current?.focus()
  }, [editing])

  const change = (v: string) => {
    setText(v)
    const pending = useDrafts.getState().drafts[key]
    if (!pending && v.trim() === saved.trim()) return
    if (pending?.status === 'dirty' && v.trim() === saved.trim()) discard(vault, path)
    else edit(vault, path, v, label)
  }
  const done = () => {
    void flush(key)
    setEditing(false)
  }

  const status = draft?.status
  const rows = Math.min(24, Math.max(6, text.split('\n').length + 1))
  return (
    <section className="notes">
      {heading && (
        <div className="notes-head">
          <NotebookPen className="notes-icon" aria-hidden />
          <h3>Your notes</h3>
          <span className="spacer" />
          {editing ? (
            <Button size="sm" variant="primary" icon={Check} onClick={done}>Done</Button>
          ) : (
            <Button size="sm" icon={Pencil} onClick={() => setEditing(true)}>{text.trim() ? 'Edit notes' : 'Add notes'}</Button>
          )}
        </div>
      )}
      {editing ? (
        <textarea
          ref={area}
          className="notes-input"
          value={text}
          rows={rows}
          placeholder={placeholder}
          aria-label="Your notes"
          onChange={(e) => change(e.target.value)}
          onBlur={() => void flush(key)}
          onKeyDown={(e) => {
            if (e.key === 'Escape') {
              e.preventDefault()
              e.stopPropagation()
              done()
            }
          }}
        />
      ) : text.trim() ? (
        <div className="notes-view">
          <Markdown text={text} />
        </div>
      ) : (
        <button type="button" className="notes-empty" onClick={() => setEditing(true)}>
          <Pencil aria-hidden />
          <span>{placeholder} Click to write.</span>
        </button>
      )}
      <div className={'notes-foot' + (status === 'error' ? ' error' : '')} aria-live="polite">
        {status === 'dirty' && <span>Not saved yet. Saves when you pause.</span>}
        {status === 'saving' && <span>Saving…</span>}
        {status === 'waiting' && <span>Watchdog is writing this investigation’s notes. Your notes will be saved when it has finished.</span>}
        {status === 'error' && (
          <>
            <AlertTriangle aria-hidden />
            <span>Not saved: {draft?.error}</span>
            <Button size="sm" variant="ghost" icon={RotateCw} onClick={() => void flush(key)}>Try again</Button>
          </>
        )}
        {!status && savedAt && (
          <span className="ok">
            <Check aria-hidden />
            Saved
          </span>
        )}
        <span className="notes-owner">
          {editing ? 'Markdown works here: **bold**, lists, and [[links]] to notes. ' : ''}
          Only you write here. {promise}
        </span>
      </div>
    </section>
  )
}
