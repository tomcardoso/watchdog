// `watchdog review` and everything under it: the five queues (contradictions, leads, watch-list
// hits, possible duplicates, possible same entities), what has been handled, the watch list
// itself, open document requests and the merge log. Built for triage by keyboard: J/K to move,
// H to mark handled, O to open, U to undo.

import {
  ArrowUpRight,
  Bell,
  Check,
  CheckCheck,
  CheckCircle2,
  FileQuestion,
  GitCompare,
  GitMerge,
  Lightbulb,
  MessageCircle,
  RefreshCw,
  Plus,
  ScanSearch,
  ShieldCheck,
  Swords,
  Trash2,
  Undo2,
  Zap
} from 'lucide-react'
import { Fragment, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DocumentRequest, DocumentRow, ReviewItem, ReviewKind } from '@shared/api'
import { Button, Callout, Empty, ErrorNote, Kbd, Skeleton, Tabs, cx } from '@renderer/components/ui'
import { HistoryButton } from '@renderer/components/FileHistory'
import { DocThumb } from '@renderer/components/DocThumb'
import { Markdown, useOpenWikilink } from '@renderer/components/Markdown'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { startJob } from '@renderer/lib/jobs'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import { fmtDate, fmtRelative, plural } from '@renderer/lib/format'
import { VerificationTab } from './VerificationTab'
import { MergeLogSection, SamePairView } from './MergesTab'
import MergeModal from '../entities/MergeModal'
import '../documents/documents.css'
import './review.css'

type Tab = ReviewKind | 'handled' | 'watchlist' | 'requests' | 'verification'
type QueueKind = ReviewKind | 'requests'

// The queues are tabs; these three views about the queues sit apart, in the page header, so the
// tab row stays short enough to read at a glance (it still wraps on a narrow window).
const TOOLS: { value: Tab; label: string; icon: typeof Swords; tip: string }[] = [
  { value: 'verification', label: 'Verification', icon: ShieldCheck, tip: 'Every fact, and whether you have checked it against its source' },
  { value: 'handled', label: 'Handled', icon: CheckCheck, tip: 'Items you have already dealt with' },
  { value: 'watchlist', label: 'Watch list', icon: ScanSearch, tip: 'Names and terms every new batch is scanned for' }
]

const KIND_META: Record<QueueKind, { label: string; icon: typeof Swords; blurb: ReactNode; empty: string }> = {
  contradictions: {
    label: 'Contradictions',
    icon: Swords,
    blurb: 'Two documents, or two places in one, that say different things about the same entity. Open the note to read both claims side by side. Handle it once you have decided which source to trust or have noted the conflict.',
    empty: 'No open contradictions. New ones appear here when processing finds documents that disagree.'
  },
  leads: {
    label: 'Leads',
    icon: Lightbulb,
    blurb: 'Places the record points but the investigation has not followed: entities named but never profiled, entities that recur with no relationships, entities whose notes carry contradictions, and inferred facts or unverified figures worth checking against the source.',
    empty: 'No open leads. A full sweep reads every entity note again and lists anything left unexamined.'
  },
  alerts: {
    label: 'Alerts',
    icon: Bell,
    blurb: 'Alerts: documents where a term from your watch list appeared. Each hit names the document, the page and the surrounding words.',
    empty: 'No open alerts. Add terms under Watch list, at the top right; every batch of new documents is scanned for them.'
  },
  duplicates: {
    label: 'Possible duplicates',
    icon: GitCompare,
    blurb: 'Documents the pipeline judged to be near-copies of an earlier one. Compare them, then confirm they are the same record or that the difference matters.',
    empty: 'No possible duplicate documents are waiting.'
  },
  merges: {
    label: 'Possible same entities',
    icon: GitMerge,
    blurb: 'Two records that may be the same person, organization or place, which Watchdog was not sure enough to merge: one name is an initialled or shortened form of the other, or the AI model compared their facts and was not confident. Merge them if they are one; mark them not the same and they will never be merged automatically.',
    empty: 'No possible same entities are waiting. Pairs appear here when two records share a name but nothing else ties them together.'
  },
  requests: {
    label: 'Document requests',
    icon: FileQuestion,
    blurb: 'Records the documents refer to but the vault does not hold: what is missing, why it matters, and where it likely sits. Mark a request handled once you have filed it or decided not to pursue it.',
    empty: 'No open document requests.'
  }
}

interface QItem extends ReviewItem {
  kind: ReviewKind
  request?: DocumentRequest
}

const nameFromTitle = (t: string) => t.split(' — ')[0].replace(/`/g, '')
const stripMd = (p: string) => p.replace(/\.md$/, '')

// ── triage state shared by the four queues and requests ─────────────────────────────────────
function useTriage(vault: string) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const [undoStack, setUndoStack] = useState<{ rids: string[]; label: string }[]>([])

  const resolve = useCallback(
    async (rids: string[], label: string) => {
      setHidden((h) => new Set([...h, ...rids]))
      setUndoStack((s) => [...s, { rids, label }])
      try {
        await call('review.resolve', { vault, rids })
        invalidate('review.', 'vault.summary', 'vault.requests', 'vault.entity')
      } catch (e) {
        setHidden((h) => {
          const n = new Set(h)
          rids.forEach((r) => n.delete(r))
          return n
        })
        setUndoStack((s) => s.filter((x) => x.rids !== rids))
        toast({ kind: 'error', title: 'Could not mark handled', body: errorMessage(e) })
      }
    },
    [vault]
  )

  const undo = useCallback(async () => {
    const last = undoStack[undoStack.length - 1]
    if (!last) return null
    setUndoStack((s) => s.slice(0, -1))
    try {
      await call('review.unresolve', { vault, rids: last.rids })
      setHidden((h) => {
        const n = new Set(h)
        last.rids.forEach((r) => n.delete(r))
        return n
      })
      invalidate('review.', 'vault.summary', 'vault.requests', 'vault.entity')
      return last
    } catch (e) {
      toast({ kind: 'error', title: 'Could not undo', body: errorMessage(e) })
      return null
    }
  }, [vault, undoStack])

  return { hidden, resolve, undo, lastUndo: undoStack[undoStack.length - 1] ?? null, canUndo: undoStack.length > 0 }
}
type Triage = ReturnType<typeof useTriage>

export default function ReviewView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const routeKind = route.view === 'review' ? route.kind : undefined
  const termFilter = route.view === 'review' && route.kind === 'alerts' ? route.filter : undefined
  const triage = useTriage(vault)

  const items = useRpc('review.items', vault ? { vault } : null, { staleTime: 5_000 })
  const requests = useRpc('vault.requests', vault ? { vault } : null, { staleTime: 5_000 })

  const counts = useMemo(() => {
    const c: Record<QueueKind, number> = { contradictions: 0, leads: 0, alerts: 0, duplicates: 0, merges: 0, requests: 0 }
    items.data?.items.forEach((i) => !triage.hidden.has(i.rid) && c[i.kind]++)
    requests.data?.open.forEach((r) => !triage.hidden.has(r.rid) && c.requests++)
    return c
  }, [items.data, requests.data, triage.hidden])

  // Default tab: the first queue with something in it.
  const defaultTab: Tab = (['contradictions', 'leads', 'alerts', 'duplicates', 'merges', 'requests'] as QueueKind[]).find((k) => counts[k] > 0) ?? 'contradictions'
  const [tab, setTab] = useState<Tab>(routeKind ?? defaultTab)
  const chosen = useRef(!!routeKind)
  useEffect(() => {
    if (routeKind) {
      setTab(routeKind)
      chosen.current = true
    }
  }, [routeKind])
  useEffect(() => {
    if (!chosen.current && items.data) setTab(defaultTab)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items.data && requests.data])

  const go = (t: Tab) => {
    chosen.current = true
    setTab(t)
    navigate({ view: 'review', kind: t }, { replace: true })
  }

  const total = counts.contradictions + counts.leads + counts.alerts + counts.duplicates + counts.merges

  return (
    <div className="page">
      <div className="page-inner rv">
        <div className="page-header" style={{ marginBottom: 14 }}>
          <div className="grow">
            <h1 className="page-title">Review</h1>
            <div className="page-sub">
              {items.data ? (total ? `${plural(total, 'item')} waiting on you.` : 'Nothing is waiting on you.') : 'Loading what is waiting on you…'} Handled items stop appearing in briefings and on the overview.
            </div>
          </div>
          <div className="rv-tools" role="group" aria-label="Review tools">
            {TOOLS.map((t) => (
              <button key={t.value} type="button" aria-pressed={tab === t.value} onClick={() => go(t.value)} data-tip={t.tip} data-tip-pos="bottom">
                <t.icon />
                {t.label}
              </button>
            ))}
          </div>
        </div>

        <Tabs<Tab>
          value={tab}
          onChange={go}
          wrap
          tabs={[
            { value: 'contradictions', label: 'Contradictions', icon: Swords, count: counts.contradictions },
            { value: 'leads', label: 'Leads', icon: Lightbulb, count: counts.leads },
            { value: 'alerts', label: 'Alerts', icon: Bell, count: counts.alerts },
            { value: 'duplicates', label: 'Duplicates', icon: GitCompare, count: counts.duplicates },
            { value: 'merges', label: 'Merges', icon: GitMerge, count: counts.merges },
            { value: 'requests', label: 'Requests', icon: FileQuestion, count: counts.requests }
          ]}
        />

        {tab === 'verification' ? (
          <VerificationTab />
        ) : tab === 'handled' ? (
          <HandledTab vault={vault} />
        ) : tab === 'watchlist' ? (
          <WatchlistTab vault={vault} />
        ) : (
          <>
            {tab === 'alerts' && termFilter && (
              <div className="rv-watch-filter">
                <span>
                  Showing hits for <code>{termFilter}</code>
                </span>
                <Button size="sm" onClick={() => navigate({ view: 'review', kind: 'alerts' }, { replace: true })}>
                  Show all hits
                </Button>
              </div>
            )}
          <QueueTab
            key={tab}
            kind={tab}
            triage={triage}
            counts={counts}
            go={go}
            loading={tab === 'requests' ? requests.isLoading : items.isLoading}
            error={tab === 'requests' ? requests.error : items.error}
            retry={() => void (tab === 'requests' ? requests.refetch() : items.refetch())}
            items={
              tab === 'requests'
                ? (requests.data?.open ?? []).map<QItem>((r) => ({
                    kind: 'leads',
                    rid: r.rid,
                    title: r.what,
                    detail: [r.why, r.likely_source ? `Likely source: ${r.likely_source}` : ''].filter(Boolean) as string[],
                    note: null,
                    request: r
                  }))
                : (items.data?.items ?? []).filter((i) => i.kind === tab && (tab !== 'alerts' || !termFilter || i.term === termFilter))
            }
          />
          </>
        )}
      </div>
    </div>
  )
}

// ── queue ────────────────────────────────────────────────────────────────────────────────────
function QueueTab({ kind, items, triage, counts, go, loading, error, retry }: { kind: QueueKind; items: QItem[]; triage: Triage; counts: Record<QueueKind, number>; go: (t: Tab) => void; loading: boolean; error: unknown; retry: () => void }) {
  const vault = useVault()
  const openLink = useOpenWikilink()
  const meta = KIND_META[kind]
  const visible = items.filter((i) => !triage.hidden.has(i.rid))
  const [focus, setFocus] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [leaving, setLeaving] = useState<Set<string>>(new Set())
  const [flash, setFlash] = useState<{ text: string; undo: boolean } | null>(null)
  const [handledHere, setHandledHere] = useState(0)
  const [sweeping, setSweeping] = useState(false)
  const flashTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const docs = useRpc('vault.documents', kind === 'duplicates' && vault ? { vault } : null)
  const [mergeFor, setMergeFor] = useState<{ keep: string; merge: string } | null>(null)
  const handledWord = kind === 'merges' ? 'marked not the same' : 'marked handled'

  const liveItems = visible.filter((i) => !leaving.has(i.rid))
  const focusId = focus && visible.some((i) => i.rid === focus) ? focus : liveItems[0]?.rid ?? null
  const focusIdx = visible.findIndex((i) => i.rid === focusId)

  const say = (text: string, undo = false) => {
    setFlash({ text, undo })
    clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setFlash(null), 6000)
  }
  useEffect(() => () => clearTimeout(flashTimer.current), [])

  const nextAfter = (rids: string[]) => {
    const gone = new Set(rids)
    const from = Math.max(0, visible.findIndex((i) => gone.has(i.rid)))
    return visible.slice(from).find((i) => !gone.has(i.rid) && !leaving.has(i.rid))?.rid ?? [...visible].slice(0, from).reverse().find((i) => !gone.has(i.rid) && !leaving.has(i.rid))?.rid ?? null
  }

  const handle = (rids: string[]) => {
    if (!rids.length) return
    const next = nextAfter(rids)
    setFocus(next)
    setSelected(new Set())
    setLeaving((l) => new Set([...l, ...rids]))
    setHandledHere((n) => n + rids.length)
    say(rids.length === 1 ? (kind === 'merges' ? 'Marked not the same' : 'Marked handled') : `${plural(rids.length, 'item')} ${handledWord}`, true)
    // Let the card finish collapsing, then drop it from the list and write the resolution.
    setTimeout(() => {
      void triage.resolve(rids, kind)
      setLeaving((l) => {
        const n = new Set(l)
        rids.forEach((r) => n.delete(r))
        return n
      })
    }, 170)
  }

  const undo = async () => {
    const last = await triage.undo()
    if (last) {
      setFocus(last.rids[0])
      setHandledHere((n) => Math.max(0, n - last.rids.length))
      say('Brought back')
    }
  }

  const move = (d: number) => {
    const list = liveItems
    if (!list.length) return
    const i = Math.max(0, list.findIndex((x) => x.rid === focusId))
    setFocus(list[Math.min(list.length - 1, Math.max(0, i + d))].rid)
  }

  const openItem = (it: QItem | undefined) => {
    if (!it) return
    if (it.note) void openLink(stripMd(it.note))
    else if (it.kind === 'leads' && !it.request) toast({ kind: 'info', title: 'No note to open', body: 'This entity has no page yet. Ask Claude about it, or add more documents to profile it.' })
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target as HTMLElement
      if (t?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      const cur = liveItems.find((i) => i.rid === focusId)
      const k = e.key.toLowerCase()
      if (k === 'j' || e.key === 'ArrowDown') {
        e.preventDefault()
        move(1)
      } else if (k === 'k' || e.key === 'ArrowUp') {
        e.preventDefault()
        move(-1)
      } else if ((k === 'h' || k === 'e') && cur) {
        e.preventDefault()
        handle(selected.size ? [...selected].filter((r) => liveItems.some((i) => i.rid === r)) : [cur.rid])
      } else if ((k === 'o' || e.key === 'Enter') && cur) {
        e.preventDefault()
        openItem(cur)
      } else if (k === 'x' && cur) {
        e.preventDefault()
        setSelected((s) => {
          const n = new Set(s)
          n.has(cur.rid) ? n.delete(cur.rid) : n.add(cur.rid)
          return n
        })
      } else if (k === 'u') {
        e.preventDefault()
        void undo()
      } else if (k === 's' && cur) {
        e.preventDefault()
        move(1)
        say('Kept open')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  useEffect(() => {
    if (focusId) document.querySelector(`[data-rid="${CSS.escape(focusId)}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [focusId])

  const sweep = async () => {
    setSweeping(true)
    try {
      await startJob(['leads'], 'Full lead sweep')
      toast({ kind: 'info', title: 'Lead sweep started', body: 'It reads every entity note again. Follow it in Activity.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the sweep', body: errorMessage(e) })
    } finally {
      setSweeping(false)
    }
  }

  const otherWithItems = (['contradictions', 'leads', 'alerts', 'duplicates', 'merges', 'requests'] as QueueKind[]).filter((k) => k !== kind && counts[k] > 0)

  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        <meta.icon />
        <p>{meta.blurb}</p>
      </div>

      {kind === 'duplicates' && (
        <div className="rv-note faint">
          Marking a pair handled only hides it from this list and from reports. Both documents stay in the vault, and the later one is still flagged as a near-duplicate on its own page and in Documents.
        </div>
      )}

      {kind === 'leads' && (
        <div className="rv-toolbar">
          <span className="faint">These lists are read when you open this tab. A full sweep rereads every entity note.</span>
          <span className="spacer" />
          <Button size="sm" icon={RefreshCw} loading={sweeping} onClick={() => void sweep()}>
            Run full lead sweep
          </Button>
        </div>
      )}

      {error ? (
        <ErrorNote error={error} retry={retry} />
      ) : loading ? (
        <div className="col" style={{ gap: 10 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="rv-card" style={{ cursor: 'default' }}>
              <Skeleton w="55%" h={16} />
              <Skeleton h={12} style={{ marginTop: 10 }} />
              <Skeleton w="70%" h={12} style={{ marginTop: 6 }} />
            </div>
          ))}
        </div>
      ) : !visible.length ? (
        <Empty
          icon={CheckCircle2}
          title={handledHere ? `All clear — ${plural(handledHere, 'item')} handled` : 'Nothing to review here'}
          action={
            otherWithItems.length ? (
              <Button variant="primary" iconRight={ArrowUpRight} onClick={() => go(otherWithItems[0])}>
                Go to {KIND_META[otherWithItems[0]].label.toLowerCase()} ({counts[otherWithItems[0]]})
              </Button>
            ) : undefined
          }
        >
          {handledHere ? 'Handled items are listed under Handled, where any of them can be brought back.' : meta.empty}
        </Empty>
      ) : (
        <div className="rv-list">
          {visible.map((it) => (
            <Card
              key={it.rid}
              item={it}
              kind={kind}
              focused={it.rid === focusId}
              selected={selected.has(it.rid)}
              leaving={leaving.has(it.rid)}
              onFocus={() => setFocus(it.rid)}
              onToggle={() =>
                setSelected((s) => {
                  const n = new Set(s)
                  n.has(it.rid) ? n.delete(it.rid) : n.add(it.rid)
                  return n
                })
              }
              onHandle={() => handle([it.rid])}
              onKeep={() => {
                move(1)
                say('Kept open')
              }}
              onOpen={() => openItem(it)}
              onMerge={it.pair ? () => setMergeFor({ keep: it.pair!.a.id, merge: it.pair!.b.id }) : undefined}
              docs={docs.data}
            />
          ))}
        </div>
      )}

      {kind === 'merges' && <MergeLogSection />}
      {kind === 'merges' && (
        <MergeModal open={!!mergeFor} onClose={() => setMergeFor(null)} initialKeep={mergeFor?.keep} initialMerge={mergeFor?.merge} />
      )}

      {/* the legend doubles as the bulk bar and the undo snackbar */}
      <div className="rv-legend">
        {selected.size > 0 ? (
          <>
            <b className="tnum">{selected.size}</b>
            <span className="muted">selected</span>
            <Button size="sm" variant="primary" icon={Check} onClick={() => handle([...selected])}>
              {kind === 'merges' ? `Mark ${selected.size} not the same` : `Mark ${selected.size} handled`}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
            <span className="spacer" />
            <Button size="sm" variant="ghost" onClick={() => setSelected(new Set(liveItems.map((i) => i.rid)))}>
              Select all {liveItems.length}
            </Button>
          </>
        ) : flash ? (
          <>
            <CheckCircle2 className="rv-flash-icon" />
            <span>{flash.text}</span>
            {flash.undo && triage.canUndo && (
              <Button size="sm" variant="soft" icon={Undo2} onClick={() => void undo()}>
                Undo
              </Button>
            )}
          </>
        ) : (
          <>
            <Key k="J" k2="K" label="move" />
            <Key k="H" label={kind === 'merges' ? 'not the same' : 'mark handled'} />
            <Key k="O" label="open" />
            <Key k="S" label="keep open, next" />
            <Key k="X" label="select" />
            <Key k="U" label="undo" />
            <span className="spacer" />
            {visible.length > 1 && <span className="faint tnum">{Math.max(1, focusIdx + 1)} of {visible.length}</span>}
          </>
        )}
      </div>
    </div>
  )
}

function Key({ k, k2, label }: { k: string; k2?: string; label: string }) {
  return (
    <span className="rv-key">
      <Kbd>{k}</Kbd>
      {k2 && <Kbd>{k2}</Kbd>}
      <span>{label}</span>
    </span>
  )
}

function renderTitle(title: string): ReactNode {
  const dash = title.indexOf(' — ')
  const [head, tail] = dash > 0 ? [title.slice(0, dash), title.slice(dash + 3)] : [title, '']
  const bits = (s: string) =>
    s.split(/(`[^`]+`)/).map((p, i) => (p.startsWith('`') && p.endsWith('`') ? <mark key={i} className="rv-term">{p.slice(1, -1)}</mark> : <Fragment key={i}>{p}</Fragment>))
  return (
    <>
      <span className="rv-name">{bits(head)}</span>
      {tail && <span className="rv-tail"> — {bits(tail)}</span>}
    </>
  )
}

function Card({ item, kind, focused, selected, leaving, onFocus, onToggle, onHandle, onKeep, onOpen, onMerge, docs }: {
  item: QItem
  kind: QueueKind
  focused: boolean
  selected: boolean
  leaving: boolean
  onFocus: () => void
  onToggle: () => void
  onHandle: () => void
  onKeep: () => void
  onOpen: () => void
  onMerge?: () => void
  docs?: DocumentRow[]
}) {
  const openLink = useOpenWikilink()
  const name = nameFromTitle(item.title)
  const showAsk = (kind === 'leads' || kind === 'contradictions') && !item.request
  const cited = item.request?.cited_in ?? []
  return (
    <div
      data-rid={item.rid}
      className={cx('rv-card', focused && 'focused', selected && 'selected', leaving && 'leaving')}
      onMouseDown={onFocus}
    >
      <label className="rv-check" onClick={(e) => e.stopPropagation()} data-tip="Select (X)" data-tip-pos="right">
        <input type="checkbox" checked={selected} onChange={onToggle} aria-label="Select" />
      </label>
      <div className="rv-body">
        <div className="rv-title">{renderTitle(item.title)}</div>
        {kind === 'duplicates' && <DuplicatePair item={item} docs={docs} />}
        {kind === 'merges' && item.pair && <SamePairView pair={item.pair} expanded={focused} />}
        {item.detail.length > 0 && kind !== 'duplicates' && (
          <div className={cx('rv-detail', !focused && 'clamped')}>
            {item.detail.length > 1 || /^\s*[-*]/.test(item.detail[0]) ? <Markdown compact text={item.detail.map((l) => (/^\s*([-*]|\d+\.)\s/.test(l) ? l : `- ${l}`)).join('\n')} /> : <Markdown compact text={item.detail[0]} />}
          </div>
        )}
        {cited.length > 0 && (
          <div className="rv-cited">
            <span className="faint">Cited in</span>
            {cited.slice(0, focused ? 12 : 3).map((c, ci) => {
              const o = c
              return (
                <button key={ci} className="rv-cite" onClick={() => (o.sha ? navigate({ view: 'document', sha: o.sha }) : o.note && void openLink(stripMd(o.note)))}>
                  {o.filename ?? o.note}
                </button>
              )
            })}
            {!focused && cited.length > 3 && <span className="faint">+{cited.length - 3}</span>}
          </div>
        )}
        {item.note && kind !== 'merges' && (
          <button
            className="rv-notelink"
            onClick={(e) => {
              e.stopPropagation()
              onOpen()
            }}
          >
            <ArrowUpRight />
            {noteLabel(stripMd(item.note))}
          </button>
        )}
        {focused && (
          <div className="rv-actions">
            {onMerge && (
              <Button size="sm" variant="primary" icon={GitMerge} onClick={onMerge}>
                Merge…
              </Button>
            )}
            <Button size="sm" variant={onMerge ? 'default' : 'primary'} icon={Check} onClick={onHandle}>
              {kind === 'merges' ? 'Not the same' : 'Mark handled'} <Kbd>H</Kbd>
            </Button>
            <Button size="sm" onClick={onKeep}>
              Keep open
            </Button>
            {item.note && kind !== 'merges' && (
              <Button size="sm" iconRight={ArrowUpRight} onClick={onOpen}>
                Open <Kbd>O</Kbd>
              </Button>
            )}
            {showAsk && (
              <Button
                size="sm"
                variant="ghost"
                icon={MessageCircle}
                onClick={() =>
                  navigate({
                    view: 'ask',
                    prompt: kind === 'contradictions' ? `Look into the contradiction recorded for ${name}: ${item.detail[0] ?? item.title}. Which documents say what, and which is more reliable?` : `What do the documents say about ${name}, and what should I verify first?`
                  })
                }
              >
                Ask Claude
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function DuplicatePair({ item, docs }: { item: QItem; docs?: DocumentRow[] }) {
  const prefix = item.rid.replace(/^duplicate:/, '')
  const mine = docs?.find((d) => d.sha.startsWith(prefix))
  const link = mine?.near_duplicate_of ?? null
  const target = link ? stripMd(link.replace(/^\[\[|\]\]$/g, '').split('|')[0]) : null
  const linkText = link?.replace(/^\[\[|\]\]$/g, '').split('|').pop() ?? null
  const stem = (d: DocumentRow) => stripMd(d.note ?? '').replace(/^.*?(documents\/)/, '$1')
  const other = docs?.find((d) => d.sha !== mine?.sha && ((target && stem(d) === target) || d.filename === linkText || d.filename === link))
  return (
    <>
      <div className="rv-pair">
        <PairDoc label="This document" doc={mine} />
        <div className="rv-pair-vs">
          <GitCompare />
        </div>
        <PairDoc label="Closely matches" doc={other} fallback={item.detail[0]} />
      </div>
      {item.detail[0] && <div className="rv-detail faint">{item.detail[0]}</div>}
    </>
  )
}

function PairDoc({ label, doc, fallback }: { label: string; doc?: DocumentRow; fallback?: string }) {
  if (!doc) return <div className="rv-pairdoc ghost"><span className="eyebrow">{label}</span><span className="faint">{fallback ? 'Not found in the library' : 'Loading…'}</span></div>
  return (
    <button
      className="rv-pairdoc"
      onClick={(e) => {
        e.stopPropagation()
        navigate({ view: 'document', sha: doc.sha })
      }}
    >
      <DocThumb sha={doc.sha} ext={doc.ext} original={doc.original} title={doc.title ?? doc.filename} summary={doc.summary} width={140} className="rv-thumb" />
      <div className="rv-pairdoc-text">
        <span className="eyebrow">{label}</span>
        <span className="rv-pairdoc-title clamp-2">{doc.title ?? doc.filename}</span>
        <span className="faint rv-pairdoc-meta">
          {[doc.page_count ? plural(doc.page_count, 'page') : '', fmtDate(doc.date_of_document)].filter(Boolean).join(' · ')}
        </span>
      </div>
    </button>
  )
}

// ── handled ──────────────────────────────────────────────────────────────────────────────────
function HandledTab({ vault }: { vault: string }) {
  const q = useRpc('review.resolved', vault ? { vault } : null)
  const [busy, setBusy] = useState<string | null>(null)

  const bringBack = async (rid: string) => {
    setBusy(rid)
    try {
      await call('review.unresolve', { vault, rids: [rid] })
      invalidate('review.', 'vault.summary', 'vault.requests', 'vault.entity')
      toast({ kind: 'success', title: 'Brought back', body: 'It is open again in its queue.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not bring it back', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  const groups = useMemo(() => {
    const g = new Map<string, NonNullable<typeof q.data>['items']>()
    q.data?.items.forEach((i) => g.set(i.kind, [...(g.get(i.kind) ?? []), i]))
    return [...g.entries()]
  }, [q.data])

  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        <CheckCheck />
        <p>Everything you have acknowledged, newest first. Bring one back and it returns to its queue and to the next briefing.</p>
      </div>
      {q.error ? (
        <ErrorNote error={q.error} retry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <Skeleton h={120} />
      ) : !q.data?.items.length ? (
        <Empty icon={CheckCheck} title="Nothing handled yet">
          Items you mark handled in the queues collect here.
        </Empty>
      ) : (
        groups.map(([kind, list]) => (
          <section key={kind} className="rv-hgroup">
            <div className="eyebrow">{KIND_META[kind as QueueKind]?.label ?? 'Other'} · {list.length}</div>
            <div className="rv-hlist">
              {list.map((i) => (
                <div key={i.rid} className="rv-hrow">
                  <div className="grow">
                    <div className="truncate rv-hlabel">{i.rid}</div>
                    <div className="faint rv-hmeta">
                      {i.resolved_at ? fmtRelative(i.resolved_at) : 'Handled'}
                      {i.label ? ` · via ${i.label}` : ''}
                    </div>
                  </div>
                  <Button size="sm" icon={Undo2} loading={busy === i.rid} onClick={() => void bringBack(i.rid)}>
                    Bring back
                  </Button>
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  )
}

// ── watch list ───────────────────────────────────────────────────────────────────────────────
function WatchlistTab({ vault }: { vault: string }) {
  const q = useRpc('review.watchlist', vault ? { vault } : null, { staleTime: 0 })
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const terms = q.data?.terms ?? []

  const add = async () => {
    const term = draft.trim()
    if (!term) return setProblem('Type a name or term to add.')
    if (terms.some((t) => t.term.toLowerCase() === term.toLowerCase())) return setProblem('That term is already on the list.')
    setBusy('add')
    try {
      await call('review.watchlistAdd', { vault, term })
      setDraft('')
      setProblem(null)
      await q.refetch()
      invalidate('history.')
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }
  const remove = async (term: string) => {
    setBusy(term)
    try {
      await call('review.watchlistRemove', { vault, term })
      await q.refetch()
      invalidate('history.')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not remove it', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  const sweep = async () => {
    setRunning(true)
    try {
      await startJob(['review', 'watchlist'], 'Check every document against the watch list')
      toast({ kind: 'info', title: 'Checking every document', body: 'Matches appear under Alerts when it finishes. Follow progress in Activity.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the check', body: errorMessage(e) })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        <ScanSearch />
        <p>
          Terms you want flagged whenever they appear in a document: a name, a company, an address, a phrase. Matching ignores capitals and respects whole words, so Ana does not match banana. Wrap a term in slashes, like <code>/Acme\s+(Ltd|Inc)/</code>, to use a regular expression, a pattern-matching syntax. Each new batch of documents is scanned automatically.
        </p>
      </div>
      <form
        className="rv-watch-add"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <input className="input" aria-label="Add a term" value={draft} placeholder="Add a term, such as a name or company" onChange={(e) => (setDraft(e.target.value), setProblem(null))} spellCheck={false} />
        <Button type="submit" variant="primary" icon={Plus} loading={busy === 'add'}>
          Add term
        </Button>
      </form>
      {problem && (
        <div className="rv-watch-problem" role="alert">
          {problem}
        </div>
      )}
      {q.error ? (
        <ErrorNote error={q.error} retry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <Skeleton h={160} />
      ) : !terms.length ? (
        <Empty icon={ScanSearch} title="No terms yet">
          An empty list scans for nothing. Add a name or company above.
        </Empty>
      ) : (
        <ul className="rv-watch-list" aria-label="Watch list terms">
          {terms.map((t) => (
            <li key={t.term} className="rv-watch-row">
              <span className="rv-watch-term truncate">{t.term}</span>
              {t.regex && <span className="faint rv-watch-kind">pattern</span>}
              <span className="spacer" />
              {t.hits > 0 ? (
                <button type="button" className="rv-watch-hits" onClick={() => navigate({ view: 'review', kind: 'alerts', filter: t.term })}>
                  {plural(t.hits, 'open hit')}
                </button>
              ) : (
                <span className="faint">No open hits</span>
              )}
              <Button size="sm" variant="ghost" icon={Trash2} loading={busy === t.term} aria-label={`Remove ${t.term}`} onClick={() => void remove(t.term)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="rv-toolbar">
        <span className="faint">{terms.length ? plural(terms.length, 'term') : 'No terms yet'}</span>
        <span className="spacer" />
        <HistoryButton vault={vault} path="watchlist.md" size="md" variant="ghost" />
      </div>
      <Callout tone="info" title="Check every document now" action={<Button icon={Zap} loading={running} disabled={!terms.length} onClick={() => void sweep()}>Check every document now</Button>}>
        The scan at the end of each run only sees that run&apos;s new documents, so a term added now is never compared with what is already in the vault. This sweeps the whole library against the current list. No model is called. Matches go to Alerts.
      </Callout>
    </div>
  )
}

/** A friendly label for the note an item links to, instead of its vault path. */
function noteLabel(path: string): string {
  if (path.startsWith('entities/')) return 'Open entity page'
  if (path.startsWith('documents/')) return 'Open document'
  if (path.startsWith('briefings/')) return 'Open briefing'
  return 'Open note'
}
