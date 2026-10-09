// The public-records acknowledgement for anything that sends document text to a model. It mirrors
// the CLI: `ingest.preflight` supplies the warning text, the model plan and the auto-approve
// verdict; when auto-approve clears the run it goes ahead with a notice, otherwise the person
// acknowledges here. The job then runs with --skip-warning, because the pause has been done.

import { ShieldAlert } from 'lucide-react'
import { useCallback, useState } from 'react'
import type { Preflight, RunOptions } from '@shared/api'
import { Button, Callout, Modal } from '@renderer/components/ui'
import { BillingNote, billingBlocked } from '@renderer/components/BillingNote'
import { call, errorMessage } from '@renderer/lib/rpc'
import { startJob } from '@renderer/lib/jobs'
import { navigate, toast, useApp } from '@renderer/lib/store'
import { fmtNum } from '@renderer/lib/format'
import { plural } from '@renderer/lib/format'

export interface GatedRun {
  args: string[]
  label: string
  kind?: string
  options?: RunOptions
}

const withSkip = (args: string[]) => (args.includes('--skip-warning') ? args : [...args, '--skip-warning'])

/** Starts a model-sending job behind the acknowledgement. Render `modal` once in the view. */
export function usePublicRecordsGate() {
  const [pending, setPending] = useState<{ run: GatedRun; pf: Preflight } | null>(null)
  const [busy, setBusy] = useState(false)

  const launch = useCallback(async (run: GatedRun) => {
    try {
      const job = await startJob(withSkip(run.args), run.label, run.kind)
      navigate({ view: 'activity', job: job.id })
    } catch (e) {
      toast({ kind: 'error', title: `Could not start ${run.label.toLowerCase()}`, body: errorMessage(e) })
    }
  }, [])

  const request = useCallback(
    async (run: GatedRun) => {
      const vault = useApp.getState().project?.path
      if (!vault) return
      setBusy(true)
      try {
        const pf = await call('ingest.preflight', { vault, options: run.options })
        if (pf.documents_to_send === 0) {
          // Nothing will reach a model, so there is nothing to acknowledge.
          const job = await startJob(run.args, run.label, run.kind)
          navigate({ view: 'activity', job: job.id })
        } else if (pf.auto_approve.approve && !billingBlocked(pf.billing)) {
          toast({ kind: 'info', title: `Sending ${plural(pf.documents_to_send, 'document')} to the model`, body: 'Auto-approve is on and every step uses your Claude subscription.' })
          await launch(run)
        } else {
          setPending({ run, pf })
        }
      } catch (e) {
        toast({ kind: 'error', title: 'Could not check the run', body: errorMessage(e) })
      } finally {
        setBusy(false)
      }
    },
    [launch]
  )

  const pf = pending?.pf
  const modal = (
    <Modal
      open={!!pending}
      onClose={() => setPending(null)}
      title={
        <span className="row" style={{ gap: 8 }}>
          <ShieldAlert style={{ width: 18, height: 18, color: 'var(--warning)' }} />
          Public records only
        </span>
      }
      footer={
        <>
          <Button autoFocus onClick={() => setPending(null)}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={!pf?.auth.ok || !!billingBlocked(pf?.billing)}
            onClick={() => {
              const run = pending!.run
              setPending(null)
              void launch(run)
            }}
          >
            Acknowledge and run
          </Button>
        </>
      }
    >
      {pf && (
        <div className="col" style={{ gap: 14 }}>
          <pre className="act-warning selectable">{pf.warning_text}</pre>
          {pf.models.length > 0 && (
            <div className="col" style={{ gap: 4 }}>
              <div className="eyebrow">Models for this run</div>
              {pf.models.map((m) => (
                <div key={m.stage} className="row act-model-row">
                  <span className="muted" style={{ width: 150 }}>{m.stage}</span>
                  <span className="grow truncate">{m.label}</span>
                  {m.effort && <span className="faint">{m.effort} effort</span>}
                </div>
              ))}
            </div>
          )}
          <BillingNote billing={pf.billing} onSettings={() => setPending(null)} />
          {pf.auto_approve.enabled && pf.auto_approve.blocker && (
            <Callout tone="info" title="Auto-approve is on, but this run still asks">
              {pf.auto_approve.blocker}
            </Callout>
          )}
          {!pf.auth.ok && (
            <Callout tone="danger" title="This run cannot start">
              {pf.auth.reason ?? 'The provider for one of the steps is not set up.'} Fix it under Settings, then try again.
            </Callout>
          )}
          <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>
            {fmtNum(pf.documents_to_send)} {pf.documents_to_send === 1 ? 'document is' : 'documents are'} waiting to be sent. Acknowledging applies to this run only.
          </div>
        </div>
      )}
    </Modal>
  )
  return { request, modal, busy }
}
