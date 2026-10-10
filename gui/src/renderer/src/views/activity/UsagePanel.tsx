// Usage: what each processing run and each Ask Claude or Web research session cost, with a
// per-stage breakdown. Bars are drawn with plain CSS.

import { BadgeInfo, Coins, Wallet } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Badge, Callout, Empty, ErrorNote, Skeleton, Stat, cx } from '@renderer/components/ui'
import { useRpc } from '@renderer/lib/rpc'
import { fmtCost, fmtDateTime, fmtDuration, fmtNum, fmtTokens, plural } from '@renderer/lib/format'
import type { KeyCost, UsageKind } from '@shared/api'
import { useApp } from '@renderer/lib/store'

const STAGE_COLOR: Record<string, string> = {
  classifier: 'var(--info)',
  extractor: 'var(--accent)',
  verifier: 'var(--organization)',
  finalizer: 'var(--public-body)',
  ask: 'var(--person)',
  research: 'var(--place)'
}
const stageColor = (s: string) => STAGE_COLOR[s] ?? 'var(--text-3)'
// Stage names as a reader sees them; the pipeline's stages keep their own (capitalised) names.
const STAGE_LABEL: Record<string, string> = { ask: 'Ask Claude', research: 'Web research' }
const stageLabel = (s: string) => STAGE_LABEL[s] ?? s.charAt(0).toUpperCase() + s.slice(1)
const kindLabel = (k: UsageKind | undefined) => (k === 'ask' || k === 'research' ? STAGE_LABEL[k] : 'Processing run')
const tsLabel = (ts: string) => {
  // 20260809T152301Z or 2026-08-09T15-23-01 — try to make a Date of it, else show as written.
  const m = /^(\d{4})-?(\d{2})-?(\d{2})[T_-]?(\d{2}):?(\d{2}):?(\d{2})/.exec(ts)
  return m ? fmtDateTime(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}`) : ts
}

function stageCosts(stages: unknown): [string, number][] {
  if (!stages || Array.isArray(stages) || typeof stages !== 'object') return []
  return Object.entries(stages as Record<string, unknown>)
    .map(([k, v]) => [k, Number(v) || 0] as [string, number])
    .filter(([, v]) => v > 0)
}

/** Cost per labelled key (D290), when any call names one — the account that paid. */
function ByKey({ rows, title }: { rows?: KeyCost[]; title: string }) {
  if (!rows || !rows.some((k) => k.label)) return null
  return (
    <div className="act-bykey">
      <span className="eyebrow">{title}</span>
      {rows.map((k) => (
        <span key={k.label ?? ''} className="act-bykey-item">
          <Wallet />
          <span className={k.label ? undefined : 'muted'}>{k.label ?? 'No stored key'}</span>
          <b className="tnum">{fmtCost(k.cost_usd)}</b>
          <span className="faint">{plural(k.calls, 'call')}</span>
        </span>
      ))}
    </div>
  )
}

function sumByKey(runs: { by_key?: KeyCost[] }[]): KeyCost[] {
  const m = new Map<string | null, KeyCost>()
  for (const r of runs) for (const k of r.by_key ?? []) {
    const cur = m.get(k.label) ?? { label: k.label, cost_usd: 0, calls: 0 }
    cur.cost_usd += k.cost_usd
    cur.calls += k.calls
    m.set(k.label, cur)
  }
  return [...m.values()].sort((a, b) => b.cost_usd - a.cost_usd)
}

function RunDetail({ vault, ts }: { vault: string; ts: string }) {
  const q = useRpc('usage.run', { vault, ts })
  if (q.isLoading) return <Skeleton h={220} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const r = q.data!
  const t = r.totals as Record<string, number>
  const corpus = (r as unknown as { corpus?: { pages: number } | null }).corpus
  const perPage = (r as unknown as { cost_per_page?: number | null }).cost_per_page
  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="act-stats">
        <Stat label="Cost" value={fmtCost(t.cost_usd)} sub={r.subscription_note ? 'at published rates' : perPage ? `${fmtCost(perPage)} a page` : undefined} icon={Coins} />
        <Stat label={r.kind === 'ask' || r.kind === 'research' ? 'Turns' : 'Model calls'} value={fmtNum(t.calls)} />
        <Stat label="Tokens in" value={fmtTokens(t.input_tokens)} sub={t.cache_read_tokens ? `${fmtTokens(t.cache_read_tokens)} read from cache` : undefined} />
        <Stat label="Tokens out" value={fmtTokens(t.output_tokens)} />
        <Stat
          label="Time"
          value={fmtDuration(t.wall_seconds ?? t.latency_s)}
          sub={t.wall_seconds && t.latency_s ? `${fmtDuration(t.latency_s)} summed across calls` : undefined}
        />
      </div>
      {r.subscription_note && (
        <Callout tone="info" title="Not billed">
          Costs shown are what the work would cost at published per-token rates, not money that was charged. A Claude subscription has no per-token charge.
        </Callout>
      )}
      <ByKey rows={r.by_key} title="Paid by" />
      <div className="card" style={{ overflow: 'hidden' }}>
        <table className="table">
          <thead>
            <tr>
              <th>Stage</th>
              <th>Model</th>
              <th>Backend</th>
              <th style={{ textAlign: 'right' }}>Calls</th>
              <th style={{ textAlign: 'right' }}>In</th>
              <th style={{ textAlign: 'right' }}>Out</th>
              <th style={{ textAlign: 'right' }}>Cost</th>
              <th style={{ textAlign: 'right' }} title="Summed time of every call, and the real elapsed time when calls overlapped">Time</th>
            </tr>
          </thead>
          <tbody>
            {r.stages.map((s) => {
              const st = s.totals as Record<string, number>
              const overlap = s.wall_seconds && st.latency_s && s.wall_seconds < st.latency_s * 0.9
              return (
                <tr key={s.stage}>
                  <td>
                    <span className="act-dot" style={{ background: stageColor(s.stage) }} />
                    <span style={{ fontWeight: 560 }}>{stageLabel(s.stage)}</span>
                  </td>
                  <td className="mono">{s.model || '—'}</td>
                  <td className="muted">{s.backend || '—'}</td>
                  <td className="tnum" style={{ textAlign: 'right' }}>{fmtNum(st.calls ?? s.calls.length)}</td>
                  <td className="tnum" style={{ textAlign: 'right' }}>{fmtTokens(st.input_tokens)}</td>
                  <td className="tnum" style={{ textAlign: 'right' }}>{fmtTokens(st.output_tokens)}</td>
                  <td className="tnum" style={{ textAlign: 'right' }}>{fmtCost(st.cost_usd)}</td>
                  <td className="tnum" style={{ textAlign: 'right' }}>
                    {overlap ? (
                      <span title="Calls ran at the same time, so the wall-clock time is shorter than the sum">
                        {fmtDuration(s.wall_seconds)} <span className="faint">of {fmtDuration(st.latency_s)}</span>
                      </span>
                    ) : (
                      fmtDuration(st.latency_s)
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>
        “Time” is the summed latency of each stage’s calls; where calls overlapped, the real elapsed time is shown first.
        {corpus?.pages ? ` This vault holds ${fmtNum(corpus.pages)} pages in total.` : ''}
      </div>
    </div>
  )
}

export default function UsagePanel() {
  const project = useApp((s) => s.project)!
  const q = useRpc('usage.runs', { vault: project.path })
  const [sel, setSel] = useState<string | null>(null)
  const runs = q.data?.runs ?? []
  useEffect(() => {
    if (!sel && runs.length) setSel(runs[0].ts)
  }, [runs, sel])
  const byTokens = useMemo(() => runs.every((r) => !r.cost_usd), [runs])
  const metric = (r: (typeof runs)[number]) => (byTokens ? r.input_tokens + r.output_tokens : r.cost_usd)
  const max = useMemo(() => Math.max(1e-9, ...runs.map(metric)), [runs, byTokens]) // eslint-disable-line react-hooks/exhaustive-deps

  if (q.isLoading) return <Skeleton h={260} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  if (!runs.length)
    return (
      <Empty icon={Coins} title="No runs recorded yet">
        After documents are processed, each run’s model calls, tokens and cost are listed here, and so is each Ask Claude or Web research session. Nothing is recorded for the local steps, which cost nothing.
      </Empty>
    )
  const present = [...new Set(runs.flatMap((r) => stageCosts(r.stages).map(([k]) => k)))]
  return (
    <div className="col" style={{ gap: 18 }}>
      <ByKey rows={sumByKey(runs)} title="All runs and sessions, by key" />
      <div className="card act-runs">
        <div className="act-runs-head">
          <div className="card-title">Runs and sessions</div>
          <div className="act-legend">
            {present.map((s) => (
              <span key={s}>
                <span className="act-dot" style={{ background: stageColor(s) }} />
                {stageLabel(s)}
              </span>
            ))}
          </div>
        </div>
        {runs.map((r) => {
          const parts = stageCosts(r.stages)
          const total = parts.reduce((a, [, v]) => a + v, 0) || r.cost_usd
          return (
            <button key={r.ts} className={cx('act-run', sel === r.ts && 'on')} onClick={() => setSel(r.ts)}>
              <div className="act-run-when">
                <div>{tsLabel(r.ts)}</div>
                <div className="faint" style={{ fontSize: 'var(--fs-xs)' }} title={r.title ?? undefined}>
                  {r.kind === 'ask' || r.kind === 'research' ? `${kindLabel(r.kind)}${r.title ? ` · ${r.title}` : ''}` : r.backends || '—'}
                </div>
              </div>
              <div className="act-bar" title={`${fmtTokens(r.input_tokens)} tokens in, ${fmtTokens(r.output_tokens)} out`}>
                <div className="act-bar-fill" style={{ width: `${Math.max(2, (metric(r) / max) * 100)}%` }}>
                  {parts.length
                    ? parts.map(([k, v]) => <span key={k} style={{ flexGrow: v / total, background: stageColor(k) }} />)
                    : <span style={{ flexGrow: 1, background: 'var(--text-3)' }} />}
                </div>
              </div>
              <div className="act-run-nums tnum">
                <span>{fmtCost(r.cost_usd)}</span>
                <span className="faint">{r.kind === 'ask' || r.kind === 'research' ? plural(r.calls, 'turn') : `${fmtNum(r.calls)} calls`} · {fmtTokens(r.input_tokens)} in · {fmtTokens(r.output_tokens)} out</span>
              </div>
              {r.subscription ? <Badge tip="Costs are what this would cost at published rates — not billed">Subscription</Badge> : <span />}
            </button>
          )
        })}
      </div>
      {sel && (
        <div className="col" style={{ gap: 10 }}>
          <div className="section-title" style={{ margin: 0 }}>
            <BadgeInfo style={{ width: 16, height: 16, color: 'var(--text-3)' }} />
            {kindLabel(runs.find((r) => r.ts === sel)?.kind)} of {tsLabel(sel)}
          </div>
          <RunDetail vault={project.path} ts={sel} />
        </div>
      )}
    </div>
  )
}
