// Bring this investigation up to date (D336): the pending steps, each with what it will do and
// one estimate for the whole run (`migrations.estimate`); nothing is sent until the person confirms.

import { RefreshCcw } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Callout, Modal, Skeleton } from '@renderer/components/ui'
import { EngineWait } from '@renderer/components/EngineWait'
import { BillingNote, billingBlocked } from '@renderer/components/BillingNote'
import { useEngineGate } from '@renderer/lib/engine'
import type { MigrationEstimate } from '@shared/api'
import { fmtCost, fmtNum, fmtTokens } from '@renderer/lib/format'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'

function costText(e: MigrationEstimate): string {
  if (!e.calls) return 'Nothing: no step needs an AI model.'
  if (e.subscription) return 'Uses your Claude subscription, so there is no separate charge.'
  if (e.cost_low === null || e.cost_high === null) return 'Watchdog has no price for one of the models, so it cannot estimate the whole cost.'
  if (e.cost_high < 0.01) return 'Less than one cent, at list price.'
  const low = fmtCost(e.cost_low)
  const high = fmtCost(e.cost_high)
  return low === high || e.cost_low < 0.01 ? `At most about ${high}, at list price.` : `About ${low} to ${high}, at list price.`
}

export default function MigrateModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const vault = useVault()
  const q = useRpc('migrations.estimate', open ? { vault } : null)
  const engine = useEngineGate()
  const [busy, setBusy] = useState(false)
  const e = q.data
  const blocked = !e || e.steps.length === 0 || e.busy || !e.auth.ok || !engine.ready || !!billingBlocked(e.billing)

  const start = async () => {
    setBusy(true)
    try {
      const job = await call('jobs.migrate', { vault })
      useApp.getState().upsertJob(job)
      invalidate('migrations.')
      toast({ kind: 'info', title: 'Bringing the investigation up to date', body: 'Follow it in Activity. Steps that finish are kept if it stops.' })
      onClose()
      navigate({ view: 'activity', job: job.id })
    } catch (err) {
      toast({ kind: 'error', title: 'Could not start', body: errorMessage(err) })
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
          <RefreshCcw style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Bring this investigation up to date
        </span>
      }
      sub="Applies improvements made since its documents were processed."
      footer={
        <>
          <Button autoFocus onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={RefreshCcw} loading={busy} disabled={blocked} onClick={() => void start()}>
            {e && e.calls && !e.subscription && e.cost_high !== null ? (e.cost_high < 0.01 ? 'Start, under one cent' : `Start, up to about ${fmtCost(e.cost_high)}`) : 'Start'}
          </Button>
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <p className="muted" style={{ margin: 0 }}>
          Watchdog has improved since this investigation’s documents were processed. These steps apply the same improvements to what it already holds, in order, without processing the documents again. Your marks, handled items and removed entities are kept, and any merge can be undone. If a step stops, for example at the spending limit, the steps that finished are kept and the rest wait for next time.
        </p>
        <EngineWait />
        {q.isError && <Callout tone="danger" title="Could not work out the estimate">{errorMessage(q.error)}</Callout>}
        {!e && !q.isError && (
          <div className="col" style={{ gap: 8 }}>
            <Skeleton w="60%" />
            <Skeleton w="40%" />
          </div>
        )}
        {e && e.steps.length === 0 && <Callout tone="info">This investigation is up to date.</Callout>}
        {e && e.steps.length > 0 && (
          <>
            <ol className="col" style={{ gap: 10, margin: 0, paddingLeft: 20 }}>
              {e.steps.map((s) => (
                <li key={s.id}>
                  <div className="row" style={{ gap: 8 }}>
                    <strong>{s.title}</strong>
                    {s.calls_model ? <Badge>{s.calls ? `${fmtNum(s.calls)} model ${s.calls === 1 ? 'call' : 'calls'}` : 'no call needed'}</Badge> : <Badge>no AI model</Badge>}
                  </div>
                  <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>{s.description}</div>
                  <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Found: {s.detail}.</div>
                </li>
              ))}
              <li>
                <strong>Rebuild notes and the timeline</strong>
                <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>From the updated records, with no AI model. Your own Notes sections are kept.</div>
              </li>
            </ol>
            <div className="col" style={{ gap: 4 }}>
              <div className="eyebrow">Estimated cost</div>
              <div>{costText(e)}</div>
              {e.calls > 0 && (
                <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>
                  About {fmtTokens(e.est_tokens)} tokens sent, on the post-processing models in Settings. The spending limit is checked before each step and between calls.
                </div>
              )}
            </div>
            {e.calls > 0 && <BillingNote billing={e.billing} onSettings={onClose} />}
            {e.busy && <Callout tone="warning" title="Documents are being added">Bring the investigation up to date when that has finished.</Callout>}
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
