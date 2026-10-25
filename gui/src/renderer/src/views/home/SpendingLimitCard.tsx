// Spending limit on the investigation's Overview (D324): a dollar figure at which adding documents,
// post-processing and the contradiction re-check stop. It counts the cost already recorded for this
// investigation's processing (what Activity → Usage shows), never an estimate, from the last Reset.
// Saved in the investigation's folder beside the choice of key (D290), so it travels with it.

import { Gauge } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Callout } from '@renderer/components/ui'
import { call, errorMessage, invalidate, queryClient, useRpc } from '@renderer/lib/rpc'
import { fmtDateTime } from '@renderer/lib/format'
import { toast } from '@renderer/lib/store'
import type { SpendingLimitInfo } from '@shared/api'

const usd = (n: number) => `$${n.toFixed(2)}`

export function SpendingLimitCard({ vault }: { vault: string }) {
  const { data } = useRpc('spend.limit', { vault }, { refetchInterval: 20_000 })
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    setText(data?.limit_usd != null ? String(data.limit_usd) : '')
  }, [data?.limit_usd])
  if (!data || data.sample) return null

  const apply = async (fn: () => Promise<SpendingLimitInfo>, failTitle: string) => {
    setBusy(true)
    setError('')
    try {
      queryClient.setQueryData(['spend.limit', { vault }], await fn())
      invalidate('ingest.preflight')
    } catch (e) {
      setError(errorMessage(e))
      toast({ kind: 'error', title: failTitle, body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const save = () => {
    const next = text.trim()
    if (next === (data.limit_usd != null ? String(data.limit_usd) : '')) return
    void apply(() => call('spend.setLimit', { vault, usd: next || null }), 'Could not set the limit')
  }
  const reset = () => void apply(() => call('spend.reset', { vault }), 'Could not reset the count')

  return (
    <section className="card home-panel home-billing">
      <h3>
        <Gauge />
        Spending limit
      </h3>
      <p className="home-billing-sub">Processing stops when this investigation has spent this much.</p>
      <div className="home-billing-row">
        <label className="home-billing-label" htmlFor="spend-limit">Limit (US$)</label>
        <input
          id="spend-limit"
          className="input"
          inputMode="decimal"
          placeholder="No limit"
          value={text}
          disabled={busy}
          onChange={(e) => setText(e.target.value)}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === 'Enter') save()
          }}
        />
        {error && <div className="home-billing-note" style={{ color: 'var(--danger)' }}>{error}</div>}
        <div className="home-billing-note">
          {usd(data.spent_usd)} recorded{data.reset_at ? ` since ${fmtDateTime(data.reset_at)}` : ''}
          {data.limit_usd != null && data.remaining_usd != null && !data.reached ? `, ${usd(data.remaining_usd)} left.` : '.'}
        </div>
        {data.reached && (
          <Callout tone="warning" style={{ marginTop: 6 }}>
            The limit is reached. Adding documents stops before anything more is sent. Raise the limit, or reset the count, to continue.
          </Callout>
        )}
        <div className="home-billing-note">
          The count is the cost already recorded in Activity → Usage, not an estimate. A call already under way still finishes, so a run can pass the limit by what those calls cost. Ask Claude and Research don’t count.
          {data.claude_mode === 'subscription' && ' Claude calls on your subscription aren’t charged per call, so they don’t count either.'}
        </div>
        <Button size="sm" variant="ghost" style={{ alignSelf: 'flex-start' }} disabled={busy} onClick={reset} tip="Count from now; earlier spending no longer counts towards the limit">
          Reset the count
        </Button>
      </div>
    </section>
  )
}
