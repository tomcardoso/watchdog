// The find box every document viewer shows in its toolbar (D289): always visible, Ctrl/Cmd+F
// focuses it, Enter and Shift+Enter step through matches, Esc clears it. Matching is the shared
// matcher's (lib/findText), so every viewer finds the same things the same way.

import { ChevronDown, ChevronUp, Search, X } from 'lucide-react'
import { ReactNode, RefObject, useEffect, useMemo, useRef, useState } from 'react'
import { Button } from '@renderer/components/ui'
import { findRanges, foldQuery, Range, splitByRanges } from '@renderer/lib/findText'

/** Focus the find box on Ctrl/Cmd+F, unless the reader is typing in the right-hand pane. */
export function useFindShortcut(input: RefObject<HTMLInputElement | null>): void {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'f') return
      if ((e.target as HTMLElement | null)?.closest?.('.docv-pane-right')) return
      e.preventDefault()
      input.current?.focus()
      input.current?.select()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [input])
}

export function countLabel(active: boolean, total: number, current: number, scanning = false): string {
  if (!active) return ''
  if (scanning && !total) return 'Searching…'
  if (!total) return 'No matches'
  return `${current + 1} of ${total}${scanning ? '+' : ''}`
}

interface FindBoxProps {
  inputRef: RefObject<HTMLInputElement | null>
  query: string
  onQuery: (q: string) => void
  /** Whether the (debounced) query is being matched at all. */
  active: boolean
  total: number
  current: number
  scanning?: boolean
  onStep: (delta: number) => void
  disabled?: boolean
}

export function FindBox({ inputRef, query, onQuery, active, total, current, scanning, onStep, disabled }: FindBoxProps) {
  useFindShortcut(inputRef)
  const label = countLabel(active, total, current, scanning)
  return (
    <div className="find-box" role="search">
      <div className="input-group find-input">
        <Search />
        <input
          ref={inputRef}
          className="input"
          placeholder="Find in document"
          aria-label="Find in document"
          value={query}
          disabled={disabled}
          onChange={(e) => onQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              onStep(e.shiftKey ? -1 : 1)
            } else if (e.key === 'Escape') {
              e.preventDefault()
              onQuery('')
            }
          }}
          style={{ userSelect: 'text' }}
        />
        {query && <button className="find-clear" aria-label="Clear" data-tip="Clear (Esc)" onClick={() => { onQuery(''); inputRef.current?.focus() }}><X /></button>}
      </div>
      {label && <span className="find-count" aria-live="polite">{label}</span>}
      <Button variant="ghost" size="sm" icon={ChevronUp} tip="Previous match (Shift+Enter)" onClick={() => onStep(-1)} disabled={!total} />
      <Button variant="ghost" size="sm" icon={ChevronDown} tip="Next match (Enter)" onClick={() => onStep(1)} disabled={!total} />
    </div>
  )
}

/** A plain line under the toolbar: why a match isn't highlighted, or an offer to look elsewhere. */
export function FindNote({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <div className="find-note" role="status">
      <span className="grow">{children}</span>
      {action}
    </div>
  )
}

/** The offer shown when a viewer finds nothing: the same words in the Text tab. */
export function SearchTextInstead({ query, onSearchText }: { query: string; onSearchText?: (q: string) => void }) {
  if (!onSearchText) return null
  return (
    <Button size="sm" variant="soft" onClick={() => onSearchText(query)}>
      Search the extracted text instead
    </Button>
  )
}

/** The query, debounced, plus the current match and a stepper that wraps around. */
export function useFindQuery(initial = '') {
  const [query, setQuery] = useState(initial)
  const [debounced, setDebounced] = useState(initial)
  const [current, setCurrent] = useState(0)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(query), 220)
    return () => clearTimeout(t)
  }, [query])
  useEffect(() => setCurrent(0), [debounced])
  const active = foldQuery(debounced) !== ''
  return { query, setQuery, debounced, active, current, setCurrent }
}

/** Matches of `query` in each of `texts`, numbered across them in order. */
export function useTextMatches(texts: string[], query: string) {
  return useMemo(() => {
    const ranges: Range[][] = texts.map((t) => (query ? findRanges(t, query) : []))
    const offsets: number[] = []
    let total = 0
    ranges.forEach((r, i) => {
      offsets[i] = total
      total += r.length
    })
    return { ranges, offsets, total }
  }, [texts, query])
}

/** Scroll the current match (a `mark[data-hit]` inside `root`) into view when it changes. */
export function useRevealCurrent(root: RefObject<HTMLElement | null>, current: number, total: number, deps: unknown[] = []) {
  const last = useRef(-1)
  useEffect(() => {
    if (!total) {
      last.current = -1
      return
    }
    const mark = root.current?.querySelector(`mark.find-hit[data-hit="${current}"]`)
    if (!mark) return
    if (last.current !== current) {
      mark.scrollIntoView({ block: 'center', inline: 'nearest' })
      last.current = current
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current, total, ...deps])
}

/** `text` with its matches marked; `offset` numbers them within the whole document, and the
 * match numbered `current` is marked as the current one. */
export function Marked({ text, ranges, offset = 0, current = -1 }: { text: string; ranges: Range[]; offset?: number; current?: number }): ReactNode {
  if (!ranges.length) return text
  return splitByRanges(text, ranges).map((p, i) =>
    p.hit === null ? (
      p.text
    ) : (
      <mark key={i} className={'find-hit' + (offset + p.hit === current ? ' current' : '')} data-hit={offset + p.hit}>
        {p.text}
      </mark>
    )
  )
}
