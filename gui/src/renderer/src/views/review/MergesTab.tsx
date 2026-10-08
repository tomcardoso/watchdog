// Review → Merges (D285): a "possible same" pair shown side by side, and the merge log — every
// merge Watchdog's rules, the AI model or a reporter made, with the evidence for it. The log is
// read-only here: splitting a merge back apart is not possible yet, and the page says so.

import { ArrowRight, Bot, ChevronDown, ChevronRight, GitMerge, ListChecks, Undo2, UserRound } from 'lucide-react'
import { useMemo, useState } from 'react'
import type { MergeLogEntry, MergeTier, SamePair, SameSide } from '@shared/api'
import { EntityAvatar } from '@renderer/components/EntityChip'
import { Badge, Button, Callout, Empty, ErrorNote, Skeleton, Switch, cx } from '@renderer/components/ui'
import { typeMeta } from '@renderer/lib/entityTypes'
import { fmtDate, fmtRelative, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useVault } from '@renderer/lib/store'

const TIER: Record<MergeTier, { label: string; tone?: 'success' | 'warning' | 'info' | 'accent' }> = {
  high: { label: 'High confidence', tone: 'success' },
  medium: { label: 'Medium confidence', tone: 'info' },
  low: { label: 'Low confidence', tone: 'warning' },
  manual: { label: 'Chosen by hand', tone: 'accent' }
}

const SCHEME: Record<string, string> = {
  registration: 'registration number',
  licence: 'licence number',
  'court-file': 'court file number',
  parcel: 'parcel identifier'
}

export function TierBadge({ tier }: { tier: MergeTier }) {
  const t = TIER[tier] ?? { label: tier }
  return <Badge tone={t.tone}>{t.label}</Badge>
}

// ── a possible-same pair ─────────────────────────────────────────────────────────────────────
export function SamePairView({ pair, expanded }: { pair: SamePair; expanded: boolean }) {
  const shared = pair.evidence?.shared ?? []
  return (
    <div className="mg-pair-wrap">
      <div className="mg-pair-meta">
        <TierBadge tier={pair.tier} />
        {pair.model_declined && (
          <Badge tone="info" icon={Bot}>
            AI model not confident
          </Badge>
        )}
        {pair.evidence?.identifier && <span className="faint">Same {SCHEME[pair.evidence.identifier.scheme] ?? pair.evidence.identifier.scheme}: {pair.evidence.identifier.value}</span>}
        {shared.length > 0 && <span className="faint">Both {shared[0].relationship} {shared[0].target_name}</span>}
      </div>
      <div className="mg-pair">
        <Side side={pair.a} expanded={expanded} />
        <div className="mg-pair-vs" aria-hidden>
          <GitMerge />
        </div>
        <Side side={pair.b} expanded={expanded} />
      </div>
    </div>
  )
}

function Side({ side, expanded }: { side: SameSide; expanded: boolean }) {
  const facts = side.facts.slice(0, expanded ? 6 : 2)
  return (
    <div className="mg-side">
      <button
        className="mg-side-head"
        onClick={(e) => {
          e.stopPropagation()
          navigate({ view: 'entity', id: side.id })
        }}
      >
        <EntityAvatar type={side.type} size={28} />
        <span className="mg-side-name">
          <span className="truncate">{side.name}</span>
          <span className="faint mg-side-sub">
            {typeMeta(side.type).label} · {plural(side.doc_count || side.documents.length, 'document')}
          </span>
        </span>
      </button>
      {side.aliases.length > 0 && expanded && <div className="faint mg-side-aka">Also written {side.aliases.slice(0, 4).join(', ')}</div>}
      {side.roles.length > 0 && expanded && (
        <div className="mg-side-roles">
          {side.roles.slice(0, 3).map((r, i) => (
            <span key={i} className="mg-role">
              {r.relationship} {r.target}
            </span>
          ))}
        </div>
      )}
      {facts.length > 0 ? (
        <ul className="mg-facts">
          {facts.map((f) => (
            <li key={f.id}>
              <span>{f.fact}</span>{' '}
              <button
                className="mg-cite"
                onClick={(e) => {
                  e.stopPropagation()
                  navigate({ view: 'document', sha: f.sha, page: f.page ?? undefined })
                }}
              >
                {f.document}
                {f.page ? `, p. ${f.page}` : ''}
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="faint mg-nofacts">No facts recorded about this record yet.</div>
      )}
    </div>
  )
}

// ── the merge log ────────────────────────────────────────────────────────────────────────────
export function MergeLogSection() {
  const vault = useVault()
  const q = useRpc('review.mergeLog', vault ? { vault } : null, { staleTime: 5_000 })
  const [showRoutine, setShowRoutine] = useState(false)
  const all = q.data?.merges ?? []
  const routine = useMemo(() => all.filter((m) => m.same_name && m.decided_by === 'rule' && m.keep.type !== 'person'), [all])
  const shown = showRoutine ? all : all.filter((m) => !routine.includes(m))

  return (
    <section className="mg-log">
      <div className="mg-log-head">
        <div className="grow">
          <h2 className="mg-log-title">Recent merges</h2>
          <p className="faint mg-log-sub">
            Every time two records were treated as one entity, newest first, with who decided and why. The same list is kept in <code>merges.md</code> in the investigation folder.
          </p>
        </div>
        {routine.length > 0 && (
          <label className="mg-switch">
            <Switch checked={showRoutine} onChange={setShowRoutine} label="Include exact-name recognitions" />
            <span>Include {plural(routine.length, 'exact-name recognition')}</span>
          </label>
        )}
      </div>
      <Callout tone="info" title="Undo is not available yet">
        A merge cannot yet be split back into two entities from the app: the merged page&apos;s summary and your notes on it cannot be divided reliably. Each entry keeps what a later version needs to do it. If a merge is wrong, note it on the entity&apos;s page.
      </Callout>
      {q.error ? (
        <ErrorNote error={q.error} retry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <Skeleton h={90} />
      ) : !shown.length ? (
        <Empty icon={ListChecks} title={all.length ? 'Only exact-name recognitions so far' : 'No merges yet'}>
          {all.length ? 'Turn on the switch above to list them.' : 'Merges are listed here as documents are added.'}
        </Empty>
      ) : (
        <div className="mg-log-list">
          {shown.map((m) => (
            <LogRow key={m.id} m={m} />
          ))}
        </div>
      )}
      {q.data?.too_new && <div className="faint">This log was written by a newer version of Watchdog. It is shown, but this version will not change it.</div>}
    </section>
  )
}

function decidedBy(m: MergeLogEntry): { icon: typeof Bot; text: string } {
  if (m.decided_by === 'model') return { icon: Bot, text: m.model ? `the AI model (${m.model})` : 'the AI model' }
  if (m.decided_by === 'reporter') return { icon: UserRound, text: m.reporter || 'a reporter' }
  return { icon: ListChecks, text: "Watchdog's rules" }
}

function LogRow({ m }: { m: MergeLogEntry }) {
  const [open, setOpen] = useState(false)
  const who = decidedBy(m)
  const keepName = m.keep.name || m.keep.id
  return (
    <div className={cx('mg-row', open && 'open')}>
      <button className="mg-row-main" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? <ChevronDown className="mg-chev" /> : <ChevronRight className="mg-chev" />}
        <EntityAvatar type={m.keep.type || m.merged.type} size={26} />
        <span className="mg-row-text">
          <span className="mg-row-title">
            {m.same_name ? (
              <>
                Two records named <b>{keepName}</b> merged into one
              </>
            ) : (
              <>
                <b>{m.merged.name}</b> <ArrowRight className="mg-arrow" /> <b>{keepName}</b>
              </>
            )}
          </span>
          <span className="faint mg-row-sub">
            <who.icon className="mg-who" /> Decided by {who.text} · {m.at ? fmtRelative(m.at) : 'date unknown'} · {plural(m.document_count, 'document')}
          </span>
        </span>
        <TierBadge tier={m.tier} />
      </button>
      {open && (
        <div className="mg-row-detail">
          <p className="mg-reason">{m.reason}</p>
          {m.identifier && (
            <div>
              <span className="eyebrow">Matching identifier</span> {SCHEME[m.identifier.scheme] ?? m.identifier.scheme} {m.identifier.value}
            </div>
          )}
          {m.shared.length > 0 && (
            <div>
              <span className="eyebrow">In common</span> {m.shared.map((s) => `${s.relationship} ${s.target_name}`).join('; ')}
            </div>
          )}
          {m.facts.length > 0 && (
            <div>
              <div className="eyebrow">Facts that came with {m.merged.name}</div>
              <ul className="mg-facts">
                {m.facts.map((f) => (
                  <li key={f.id}>
                    {f.fact}{' '}
                    <button className="mg-cite" onClick={() => navigate({ view: 'document', sha: f.sha, page: f.page ?? undefined })}>
                      {f.title}
                      {f.page ? `, p. ${f.page}` : ''}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {m.documents.length > 0 && (
            <div className="mg-docs">
              <span className="eyebrow">Documents</span>
              {m.documents.slice(0, 12).map((d) => (
                <button key={d.sha} className="rv-cite" onClick={() => navigate({ view: 'document', sha: d.sha })}>
                  {d.title}
                </button>
              ))}
              {m.document_count > 12 && <span className="faint">+{m.document_count - 12}</span>}
            </div>
          )}
          <div className="mg-row-actions">
            {m.keep.exists && (
              <Button size="sm" iconRight={ArrowRight} onClick={() => navigate({ view: 'entity', id: m.keep.id })}>
                Open {keepName}
              </Button>
            )}
            <Button size="sm" icon={Undo2} disabled tip="Not available yet. Splitting a merged entity needs a later version of Watchdog.">
              Undo merge
            </Button>
            {m.first_at && fmtDate(m.first_at) !== fmtDate(m.at) && <span className="faint">First merged {fmtDate(m.first_at)}</span>}
          </div>
        </div>
      )}
    </div>
  )
}
