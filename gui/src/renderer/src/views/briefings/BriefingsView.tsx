// Everything Watchdog writes for the journalist, as a reading list: briefings, lead
// sweeps, watch-list alerts and research memos, plus three living pages pinned above them: the
// current state (built now from the investigation's records, for the reporter; the session primer
// Claude starts each conversation with, D285, is one click away as "What Claude is given"), the
// processing history and the investigation context.

import { Activity, ArrowLeft, Bell, Bot, FileText, History, Lightbulb, MessageCircle, MessageSquareQuote, Network, Pencil, Save, Search, Target, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { BriefingRow } from '@shared/api'
import { Button, Callout, Empty, ErrorNote, Skeleton, cx } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { fmtDate } from '@renderer/lib/format'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import { NoteActions, stripFrontmatter } from './NoteView'
import './briefings.css'

// Not a file: built on request (`vault.currentState`, and `vault.sessionPrimer` for what Claude is
// given), as each session builds its primer.
const PRIMER = 'session-primer'

const PINNED: { path: string; label: string; sub: string; icon: LucideIcon }[] = [
  { path: PRIMER, label: 'Current state', sub: 'Where the whole investigation stands', icon: Activity },
  { path: 'log.md', label: 'Processing history', sub: 'What each run added', icon: History },
  { path: 'context.md', label: 'Investigation context', sub: 'Your questions and what you know', icon: Target }
]

const KINDS: { kind: BriefingRow['kind']; label: string; icon: LucideIcon }[] = [
  { kind: 'briefing', label: 'Briefings', icon: FileText },
  { kind: 'leads', label: 'Lead sweeps', icon: Lightbulb },
  { kind: 'alerts', label: 'Watch-list alerts', icon: Bell },
  { kind: 'research', label: 'Research memos', icon: Search }
]

const monthKey = (d: string | null) => (d && /^\d{4}-\d{2}/.test(d) ? d.slice(0, 7) : 'undated')
const monthLabel = (k: string) => (k === 'undated' ? 'Undated' : new Date(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, 1).toLocaleDateString('en-CA', { month: 'long', year: 'numeric' }))

export default function BriefingsView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const routePath = route.view === 'briefings' ? route.path : undefined
  const list = useRpc('vault.briefings', vault ? { vault } : null)
  const rows = list.data ?? []
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
          <h1 className="bf-side-title">Briefings</h1>
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
          {list.isLoading && <div style={{ padding: 12 }}><Skeleton h={14} /><Skeleton h={14} style={{ marginTop: 8 }} /><Skeleton w="70%" h={14} style={{ marginTop: 8 }} /></div>}
          {list.error && <div style={{ padding: 12 }}><ErrorNote error={list.error} retry={() => void list.refetch()} /></div>}
          {groups.map((g) => (
            <div key={g.kind}>
              <div className="bf-group-label">{g.label} <span className="faint tnum">{g.count}</span></div>
              {g.months.map(([m, items]) => (
                <div key={m}>
                  <div className="bf-month">{monthLabel(m)}</div>
                  {items.map((r) => (
                    <button key={r.path} className={cx('bf-item', selected === r.path && 'active')} onClick={() => pick(r.path)}>
                      <g.icon />
                      <span className="bf-item-text">
                        <span className="bf-item-title truncate">{r.title ?? r.name}</span>
                        <span className="bf-item-sub">{fmtDate(r.date) || r.name}</span>
                      </span>
                    </button>
                  ))}
                </div>
              ))}
            </div>
          ))}
          {notes.length > 0 && (
            <div>
              <div className="bf-group-label">Saved answers and threads <span className="faint tnum">{notes.length}</span></div>
              {notes.map((n) => (
                <button key={n.path} className={cx('bf-item', selected === n.path && 'active')} onClick={() => pick(n.path)}>
                  {n.kind === 'wiki' ? <Network /> : <MessageSquareQuote />}
                  <span className="bf-item-text">
                    <span className="bf-item-title truncate">{n.title}</span>
                    <span className="bf-item-sub">{n.kind === 'wiki' ? 'Wiki thread' : 'Saved answer'} · {fmtDate(n.modified)}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
          {list.data && !rows.length && <div className="faint" style={{ padding: '10px 14px', fontSize: 'var(--fs-sm)' }}>No briefings yet. The first is written when a run finishes.</div>}
        </div>
      </aside>
      <main className="bf-main">
        <Reader key={selected} path={selected} row={rows.find((r) => r.path === selected)} />
      </main>
    </div>
  )
}

function Reader({ path, row }: { path: string; row?: BriefingRow }) {
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
  const isContext = path === 'context.md'
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    setEditing(false)
    setAsClaude(false)
  }, [path])

  const text = q.data?.text ?? ''
  const body = stripFrontmatter(text)
  const hasChecks = /^\s*- \[[ xX]\]/m.test(body)

  const save = async () => {
    setSaving(true)
    try {
      await call('vault.writeFile', { vault, path, text: draft })
      invalidate('vault.readFile', 'vault.summary')
      setEditing(false)
      toast({ kind: 'success', title: 'Context saved', body: 'The next run and briefing will use it.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not save', body: errorMessage(e) })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bf-reader">
      <div className="bf-reader-bar">
        <div className="grow">
          <div className="eyebrow">{pinned ? 'Pinned' : KINDS.find((k) => k.kind === row?.kind)?.label ?? 'Briefing'}</div>
          <div className="bf-reader-title truncate">{isPrimer && asClaude ? 'What Claude is given' : pinned?.label ?? row?.title ?? row?.name ?? path}</div>
        </div>
        {isContext && !editing && (
          <>
            <Button size="sm" icon={MessageCircle} onClick={() => navigate({ view: 'ask' })}>Seed context with Claude</Button>
            <Button size="sm" icon={Pencil} onClick={() => { setDraft(text); setEditing(true) }}>Edit</Button>
          </>
        )}
        {isPrimer && (
          <Button size="sm" variant="ghost" icon={asClaude ? ArrowLeft : Bot} aria-pressed={asClaude} onClick={() => setAsClaude(!asClaude)}>
            {asClaude ? 'Back to current state' : 'What Claude is given'}
          </Button>
        )}
        {!editing && !isPrimer && <NoteActions path={path} />}
      </div>
      <div className="bf-reader-scroll">
        <div className="bf-measure bf-article">
          {q.error ? (
            <ErrorNote error={q.error} retry={() => void q.refetch()} />
          ) : q.isLoading ? (
            <><Skeleton w="55%" h={28} /><Skeleton h={14} style={{ marginTop: 22 }} /><Skeleton h={14} style={{ marginTop: 8 }} /><Skeleton w="75%" h={14} style={{ marginTop: 8 }} /></>
          ) : editing ? (
            <>
              <Callout tone="info">
                Plain Markdown. This page is sent to the AI model with every document you add, so leave out anything you would not send with them: source names, contact details, unpublished tips.
                Write open questions rather than conclusions, such as “I want to understand how the contract was awarded”. A page that states what you expect to find can lead the model to read the documents that way.
              </Callout>
              <textarea className="textarea bf-editor" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck autoFocus />
              <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
                <Button icon={X} variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
                <Button icon={Save} variant="primary" loading={saving} onClick={() => void save()}>Save</Button>
              </div>
            </>
          ) : !q.data?.exists || !body.trim() ? (
            <Empty icon={FileText} title={pinned ? `${pinned.label} is empty` : 'Nothing here'} action={isContext ? <Button icon={Pencil} onClick={() => { setDraft(text); setEditing(true) }}>Write context</Button> : undefined}>
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
