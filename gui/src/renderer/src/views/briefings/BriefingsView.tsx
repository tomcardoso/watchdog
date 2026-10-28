// Reports: everything Watchdog and Claude write for the journalist, as a reading list (the route
// and folder keep the older name, `briefings`). Two living pages are pinned at the top: the
// current state (built now from the investigation's records, for the reporter; the session primer
// Claude starts each conversation with, D285, is one click away as "What Claude is given") and the
// processing history. Below them, the pages Ask Claude saves (`queries/`, `wiki/`), then
// briefings and research memos by month.

import { Activity, ArrowLeft, Bot, FileText, History, MessageSquareQuote, Network, Search } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { BriefingRow } from '@shared/api'
import { Button, Callout, Empty, ErrorNote, Skeleton, cx } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { useRpc } from '@renderer/lib/rpc'
import { fmtDate, fmtDateTime } from '@renderer/lib/format'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import { NoteActions, stripFrontmatter } from './NoteView'
import './briefings.css'

// Not a file: built on request (`vault.currentState`, and `vault.sessionPrimer` for what Claude is
// given), as each session builds its primer.
const PRIMER = 'session-primer'

const PINNED: { path: string; label: string; sub: string; icon: LucideIcon }[] = [
  { path: PRIMER, label: 'Current state', sub: 'Where the whole investigation stands', icon: Activity },
  { path: 'log.md', label: 'Processing history', sub: 'What each run added', icon: History }
]

// Lead sweeps and watchlist alerts are also written to briefings/, but they are worked through in
// Review (Leads, Alerts), which their files say themselves, so they are not listed here.
const KINDS: { kind: BriefingRow['kind']; label: string; icon: LucideIcon }[] = [
  { kind: 'briefing', label: 'Briefings', icon: FileText },
  { kind: 'research', label: 'Research memos', icon: Search }
]

// Still opened from a link (Activity → Maintenance), so the reader names them.
const UNLISTED: Partial<Record<BriefingRow['kind'], string>> = { leads: 'Lead sweep', alerts: 'Watchlist alerts' }

const noteKind = (kind: string) => (kind === 'wiki' ? 'Thread page' : 'Saved answer')

const monthKey = (d: string | null) => (d && /^\d{4}-\d{2}/.test(d) ? d.slice(0, 7) : 'undated')
const monthLabel = (k: string) => (k === 'undated' ? 'Undated' : new Date(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, 1).toLocaleDateString('en-CA', { month: 'long', year: 'numeric' }))

export default function BriefingsView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const routePath = route.view === 'briefings' ? route.path : undefined
  const list = useRpc('vault.briefings', vault ? { vault } : null)
  const all = list.data ?? []
  const rows = all.filter((r) => KINDS.some((k) => k.kind === r.kind))
  const pages = useRpc('vault.notes', vault ? { vault } : null)
  const [filter, setFilter] = useState('')
  const f = filter.trim().toLowerCase()
  const notes = (pages.data ?? []).filter((n) => !f || n.title.toLowerCase().includes(f))

  const newest = rows.find((r) => r.kind === 'briefing') ?? rows[0]
  const selected = routePath ?? newest?.path ?? PINNED[0].path

  const groups = useMemo(() => {
    const f = filter.trim().toLowerCase()
    return KINDS.map((k) => {
      const items = rows.filter((r) => r.kind === k.kind && (!f || `${r.title ?? ''} ${r.name}`.toLowerCase().includes(f)))
      const months = new Map<string, BriefingRow[]>()
      items.forEach((r) => months.set(monthKey(r.date), [...(months.get(monthKey(r.date)) ?? []), r]))
      return { ...k, count: items.length, months: [...months.entries()] }
    }).filter((g) => g.count)
  }, [rows, filter])

  const pick = (path: string) => navigate({ view: 'briefings', path })

  return (
    <div className="bf">
      <aside className="bf-side">
        <div className="bf-side-head">
          <h1 className="bf-side-title">Reports</h1>
          <input className="input" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <div className="bf-scroll">
          <div className="bf-group-label">Pinned</div>
          {PINNED.map((p) => (
            <button key={p.path} className={cx('bf-item', selected === p.path && 'active')} onClick={() => pick(p.path)}>
              <p.icon />
              <span className="bf-item-text">
                <span className="bf-item-title">{p.label}</span>
                <span className="bf-item-sub">{p.sub}</span>
              </span>
            </button>
          ))}
          {notes.length > 0 && (
            <div>
              <div className="bf-group-label">Saved answers and threads<span className="count-pill quiet">{notes.length}</span></div>
              {notes.map((n) => (
                <button key={n.path} className={cx('bf-item', selected === n.path && 'active')} title={n.title} onClick={() => pick(n.path)}>
                  {n.kind === 'wiki' ? <Network /> : <MessageSquareQuote />}
                  <span className="bf-item-text">
                    <span className="bf-item-title">{n.title}</span>
                    <span className="bf-item-sub">{noteKind(n.kind)} · {fmtDate(n.modified)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {list.isLoading && <div style={{ padding: 12 }}><Skeleton h={14} /><Skeleton h={14} style={{ marginTop: 8 }} /><Skeleton w="70%" h={14} style={{ marginTop: 8 }} /></div>}
          {list.error && <div style={{ padding: 12 }}><ErrorNote error={list.error} retry={() => void list.refetch()} /></div>}
          {groups.map((g) => (
            <div key={g.kind}>
              <div className="bf-group-label">{g.label}<span className="count-pill quiet">{g.count}</span></div>
              {g.months.map(([m, items]) => (
                <div key={m}>
                  <div className="bf-month">{monthLabel(m)}</div>
                  {items.map((r) => (
                    <button key={r.path} className={cx('bf-item', selected === r.path && 'active')} title={r.title ?? r.name} onClick={() => pick(r.path)}>
                      <g.icon />
                      <span className="bf-item-text">
                        <span className="bf-item-title">{r.title ?? r.name}</span>
                        <span className="bf-item-sub">{(g.kind === 'briefing' && r.date?.includes('T') ? fmtDateTime(r.date) : fmtDate(r.date)) || r.name}</span>
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          ))}
          {list.data && !rows.length && <div className="faint" style={{ padding: '10px 14px', fontSize: 'var(--fs-sm)' }}>No briefings yet. The first is written when a run finishes.</div>}
        </div>
      </aside>
      <main className="bf-main">
        <Reader key={selected} path={selected} row={all.find((r) => r.path === selected)} note={(pages.data ?? []).find((n) => n.path === selected)} />
      </main>
    </div>
  )
}

function Reader({ path, row, note }: { path: string; row?: BriefingRow; note?: { kind: string; title: string } }) {
  const vault = useVault()
  const isPrimer = path === PRIMER
  const file = useRpc('vault.readFile', vault && !isPrimer ? { vault, path } : null, { staleTime: 5_000 })
  // Current state is written for the reporter; what Claude is given (the session primer, with its
  // instructions for the model) is a secondary view of the same records.
  const [asClaude, setAsClaude] = useState(false)
  const state = useRpc('vault.currentState', vault && isPrimer && !asClaude ? { vault } : null, { staleTime: 5_000 })
  const primer = useRpc('vault.sessionPrimer', vault && isPrimer && asClaude ? { vault } : null, { staleTime: 5_000 })
  const built = asClaude ? primer : state
  const q = isPrimer
    ? { ...built, data: built.data ? { text: built.data.text, exists: true } : undefined }
    : file
  const pinned = PINNED.find((p) => p.path === path)
  useEffect(() => {
    setAsClaude(false)
  }, [path])

  const text = q.data?.text ?? ''
  const body = stripFrontmatter(text)
  const hasChecks = /^\s*- \[[ xX]\]/m.test(body)

  return (
    <div className="bf-reader">
      <div className="bf-reader-bar">
        <div className="grow">
          <div className="eyebrow">{pinned ? 'Pinned' : note ? noteKind(note.kind) : KINDS.find((k) => k.kind === row?.kind)?.label ?? (row && UNLISTED[row.kind]) ?? 'Briefing'}</div>
          <div className="bf-reader-title">{isPrimer && asClaude ? 'What Claude is given' : pinned?.label ?? row?.title ?? note?.title ?? row?.name ?? path}</div>
        </div>
        {isPrimer && (
          <Button size="sm" variant="ghost" icon={asClaude ? ArrowLeft : Bot} aria-pressed={asClaude} onClick={() => setAsClaude(!asClaude)}>
            {asClaude ? 'Back to current state' : 'What Claude is given'}
          </Button>
        )}
        {!isPrimer && <NoteActions path={path} />}
      </div>
      <div className="bf-reader-scroll">
        <div className="bf-measure bf-article">
          {q.error ? (
            <ErrorNote error={q.error} retry={() => void q.refetch()} />
          ) : q.isLoading ? (
            <><Skeleton w="55%" h={28} /><Skeleton h={14} style={{ marginTop: 22 }} /><Skeleton h={14} style={{ marginTop: 8 }} /><Skeleton w="75%" h={14} style={{ marginTop: 8 }} /></>
          ) : !q.data?.exists || !body.trim() ? (
            <Empty icon={FileText} title={pinned ? `${pinned.label} is empty` : 'Nothing here'}>
              {path === 'log.md' ? 'This file is written at the end of a run that produces a briefing.' : 'This file does not exist yet.'}
            </Empty>
          ) : (
            <>
              {isPrimer && asClaude && (
                <Callout tone="info" style={{ marginBottom: 18 }}>
                  Every Ask Claude conversation starts with this text, word for word, built from the same records as Current state. It includes instructions written for Claude, such as how to cite a fact; you don't need to follow them. Lists are shortened so the text stays a fixed size.
                </Callout>
              )}
              {hasChecks && (
                <div className="bf-check-note">
                  Checkboxes here are display only. Mark items handled in <button className="srch-link" onClick={() => navigate({ view: 'review' })}>Review</button>, or tick them in the file and sync them from Review → Handled.
                </div>
              )}
              <Markdown text={body} />
            </>
          )}
        </div>
      </div>
    </div>
  )
}
