// Re-check contradictions (D287): every recorded fact about an entity, or about every entity with
// two or more facts (one named in a single document included), is compared with every other by the AI model. The estimate comes first
// (`contradictions.estimate`); nothing is sent until the person confirms.

import { ScanSearch } from 'lucide-react'
import { Button, Callout, Modal, Skeleton } from '@renderer/components/ui'
import { EngineWait } from '@renderer/components/EngineWait'
import { BillingNote, billingBlocked } from '@renderer/components/BillingNote'
import { useEngineGate } from '@renderer/lib/engine'
import type { RecheckEstimate } from '@shared/api'
import { fmtCost, fmtNum, fmtTokens, plural } from '@renderer/lib/format'
import { call, errorMessage, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import { useState } from 'react'

function costText(e: RecheckEstimate): string {
  if (e.subscription) return 'Uses your Claude subscription, so there is no separate charge.'
  if (e.cost_low === null || e.cost_high === null) return 'Watchdog has no price for this model, so it cannot estimate the cost.'
  const low = fmtCost(e.cost_low)
  const high = fmtCost(e.cost_high)
  const range = e.cost_high < 0.01 ? 'Less than one cent' : low === high || e.cost_low < 0.01 ? `At most about ${high}` : `About ${low} to ${high}`
  const rate = e.price_multiplier > 1 ? ' at this model’s peak rate' : e.price_multiplier < 1 ? ' at this model’s off-peak rate' : ''
  return `${range}${rate}, at list price.`
}

function skippedText(s: RecheckEstimate['skipped'][number], max: number): string {
  if (s.reason === 'too_few_facts') return `${s.name} has fewer than two recorded facts, so there is nothing to compare.`
  if (s.reason === 'too_large')
    return `${s.name} has ${fmtNum(s.facts ?? 0)} facts. Comparing every pair would take ${fmtNum(s.calls ?? 0)} model calls, more than the limit of ${max} for one entity, so it is not checked. Contradictions are still looked for each time documents about it are added.`
  return `${s.name} is no longer in this investigation.`
}

export default function RecheckModal({ open, onClose, ids, name }: { open: boolean; onClose: () => void; ids?: string[]; name?: string }) {
  const vault = useVault()
  const all = !ids
  const q = useRpc('contradictions.estimate', open ? (all ? { vault, all: true } : { vault, ids }) : null)
  const engine = useEngineGate()
  const [busy, setBusy] = useState(false)
  const e = q.data
  const subject = all ? 'every entity with two or more facts' : name ?? 'this entity'
  const blocked = !e || e.calls === 0 || e.busy || !e.auth.ok || !engine.ready || !!billingBlocked(e.billing)

  const start = async () => {
    setBusy(true)
    try {
      const job = await call('jobs.recheckContradictions', all ? { vault, all: true } : { vault, ids })
      useApp.getState().upsertJob(job)
      toast({ kind: 'info', title: 'Re-checking contradictions', body: 'Follow it in Activity. Anything new appears under Contradictions and in Review when it finishes.' })
      onClose()
      if (all) navigate({ view: 'activity', job: job.id })
    } catch (err) {
      toast({ kind: 'error', title: 'Could not start the re-check', body: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        <span className="row" style={{ gap: 8 }}>
          <ScanSearch style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Re-check contradictions
        </span>
      }
      sub={all ? 'Across the whole investigation.' : `On ${(name ?? 'this entity').replace(/\.$/, '')}.`}
      footer={
        <>
          <Button autoFocus onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={ScanSearch} loading={busy} disabled={blocked} onClick={() => void start()}>
            {e && !e.subscription && e.cost_high !== null ? (e.cost_high < 0.01 ? 'Re-check, under one cent' : `Re-check, up to about ${fmtCost(e.cost_high)}`) : 'Re-check'}
          </Button>
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <p className="muted" style={{ margin: 0 }}>
          When documents are added, their facts are compared with the facts already recorded. Facts already recorded are not compared with each other again, so a conflict between two earlier documents can be missed, and a conflict within a single document is not looked for at all. This sends every recorded fact about {subject} to the AI model to look for conflicts among them. What it finds is added to Contradictions and to Review, like any other. Contradictions already recorded, including ones you marked handled, are not added again.
        </p>
        <EngineWait />
        {q.isError && <Callout tone="danger" title="Could not work out the estimate">{errorMessage(q.error)}</Callout>}
        {!e && !q.isError && (
          <div className="col" style={{ gap: 8 }}>
            <Skeleton w="60%" />
            <Skeleton w="40%" />
          </div>
        )}
        {e && (
          <>
            {e.calls > 0 && (
              <div className="rc-stats">
                <div><span className="v">{fmtNum(e.facts)}</span><span className="l">facts</span></div>
                <div><span className="v">{fmtNum(e.entities.length)}</span><span className="l">{e.entities.length === 1 ? 'entity' : 'entities'}</span></div>
                <div><span className="v">{fmtNum(e.calls)}</span><span className="l">{e.calls === 1 ? 'model call' : 'model calls'}</span></div>
                <div><span className="v">{fmtTokens(e.real_tokens ?? e.est_tokens)}</span><span className="l">tokens sent, estimated</span></div>
              </div>
            )}
            {e.calls > 0 && (
              <div className="col" style={{ gap: 4 }}>
                <div className="eyebrow">Estimated cost</div>
                <div>{costText(e)}</div>
                <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>
                  Model: {e.model.name}
                  {e.model.effort ? `, ${e.model.effort} effort` : ''}, the post-processing model in Settings. Every fact pair is shown to the model at least once; {e.calls > 1 ? 'large entities are split across several calls to do that.' : 'this fits in one call.'}
                </div>
              </div>
            )}
            {e.calls > 0 && <BillingNote billing={e.billing} onSettings={onClose} />}
            {e.calls === 0 && e.skipped.length === 0 && <Callout tone="info">No entity in this investigation has two or more facts yet, so there is nothing to re-check.</Callout>}
            {e.skipped.length > 0 && (
              <Callout tone={e.calls === 0 ? 'warning' : 'info'} title={e.calls === 0 ? 'Nothing to re-check' : `${plural(e.skipped.length, 'entity', 'entities')} not checked`}>
                {e.skipped.slice(0, 4).map((s) => (
                  <div key={s.id}>{skippedText(s, e.max_entity_calls)}</div>
                ))}
                {e.skipped.length > 4 && <div>And {e.skipped.length - 4} more.</div>}
              </Callout>
            )}
            {e.busy && <Callout tone="warning" title="Documents are being added">Re-check contradictions when that has finished.</Callout>}
            {!e.auth.ok && (
              <Callout tone="danger" title="This cannot start">
                {e.auth.reason ?? 'The model’s provider is not set up.'} Fix it under Settings, then try again.
              </Callout>
            )}
          </>
        )}
      </div>
    </Modal>
  )
}
