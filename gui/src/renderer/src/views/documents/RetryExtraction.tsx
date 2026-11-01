// Retry extraction (D367): a document whose extraction kept implausibly few facts for its length
// is sent to the extraction model again, alone. The same checks as Add documents come first: the
// cost estimate, the key that pays, the spending limit and the public-records acknowledgement.
// The run is the `add` operation limited to this document (`reextract`); the new extraction
// replaces the earlier one only once it passes post-flight and is committed.

import { RotateCw, ShieldAlert, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { Button, Callout, Modal, Skeleton } from '@renderer/components/ui'
import { BillingNote, billingBlocked } from '@renderer/components/BillingNote'
import { EngineWait } from '@renderer/components/EngineWait'
import { useEngineGate } from '@renderer/lib/engine'
import { plural } from '@renderer/lib/format'
import { startJob } from '@renderer/lib/jobs'
import { errorMessage, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useVault } from '@renderer/lib/store'
import type { ThinExtraction } from '@shared/api'

/** The warning line a thin extraction shows, wherever it is shown. */
export function thinText(t: ThinExtraction): string {
  const facts = t.facts !== null ? plural(t.facts, 'fact') : 'Very few facts'
  const words = t.words !== null ? ` from about ${t.words.toLocaleString('en-CA')} words of text` : ''
  const expected = t.expected_min !== null ? `, where at least ${t.expected_min} would be usual` : ''
  return `${facts} extracted${words}${expected}. The model may have stopped early.`
}

export function ThinExtractionNote({ sha, filename, thin }: { sha: string; filename: string; thin: ThinExtraction }) {
  const [open, setOpen] = useState(false)
  const engine = useEngineGate()
  return (
    <>
      <Callout
        tone="warning"
        title="Few facts for a document this long"
        action={thin.retry ? <Button size="sm" icon={RotateCw} disabled={!engine.ready} onClick={() => setOpen(true)}>Retry extraction</Button> : undefined}
      >
        {thinText(thin)}{' '}
        {thin.retry
          ? 'Retrying sends this document to the extraction model again; if the new extraction is better, it replaces this one.'
          : 'Its text is no longer kept for a retry. To extract it again, add the file again.'}
      </Callout>
      {thin.retry && <RetryExtractionModal open={open} onClose={() => setOpen(false)} sha={sha} filename={filename} />}
    </>
  )
}

export function RetryExtractionModal({ open, onClose, sha, filename }: { open: boolean; onClose: () => void; sha: string; filename: string }) {
  const vault = useVault()
  const engine = useEngineGate()
  const options = { reextract: [sha] }
  const pf = useRpc('ingest.preflight', open ? { vault, options } : null, { staleTime: 0 })
  const est = useRpc('ingest.estimate', open ? { vault, stage: 'dig' as const, options } : null, { staleTime: 0 })
  const [busy, setBusy] = useState(false)
  const p = pf.data
  const auto = !!p && p.auto_approve.enabled && p.auto_approve.approve
  const ack = !!p && p.ack_required && !auto
  const blocked = !p || p.documents_to_send === 0 || !p.auth.ok || !engine.ready || !!billingBlocked(p.billing) || !!p.sample?.unprepared.length

  const start = async () => {
    setBusy(true)
    try {
      // The acknowledgement was given here (or is not needed); the worker checks auto-approve and
      // a run on this computer for itself, as for Add documents.
      await startJob('add', { reextract: [sha], skip_warning: ack || undefined }, `Retrying extraction of ${filename}`, 'add', vault)
      toast({ kind: 'info', title: 'Retrying the extraction', body: 'Follow it in Activity. The document updates when the run finishes.' })
      onClose()
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the retry', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="wide"
      title={
        <span className="row" style={{ gap: 8 }}>
          <RotateCw style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Retry extraction
        </span>
      }
      sub={filename}
      footer={
        <>
          <Button autoFocus onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={RotateCw} loading={busy} disabled={blocked} onClick={() => void start()}>
            {ack ? 'Acknowledge and retry' : 'Retry extraction'}
          </Button>
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <p className="muted" style={{ margin: 0 }}>
          This sends the document’s text to the extraction model again, with the models and effort in Settings, then runs the finishing step. Nothing else waiting is extracted. The earlier facts stay until the new extraction has been checked and saved; if it is still thin, the warning stays.
        </p>
        <EngineWait />
        {(pf.isError || est.isError) && <Callout tone="danger" title="Could not prepare the retry">{errorMessage(pf.error ?? est.error)}</Callout>}
        {!p && !pf.isError && <Skeleton w="60%" />}
        {p && p.documents_to_send === 0 && (
          <Callout tone="warning" title="Nothing to retry">This document’s text is no longer kept. Add the file again to read it from the start.</Callout>
        )}
        {est.data && p && p.documents_to_send > 0 && (
          <div>
            <div className="eyebrow">Estimated cost</div>
            <pre className="log add-estimate">{est.data.text.trim()}</pre>
            {est.data.spending_warning && <Callout tone="warning" title="Spending limit" style={{ marginTop: 10 }}>{est.data.spending_warning}</Callout>}
          </div>
        )}
        {p && p.documents_to_send > 0 && (
          <>
            {p.spending_limit?.warning && <Callout tone="warning" title="Spending limit reached">{p.spending_limit.warning}</Callout>}
            <BillingNote billing={p.billing} onSettings={onClose} />
            {ack ? (
              <div className="add-warning" role="alert">
                <ShieldAlert />
                <pre>{p.warning_text.trim()}</pre>
              </div>
            ) : (
              <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>
                {auto ? 'Auto-approve is on for this investigation, so the public-records notice is not repeated.' : p.ack_note}
              </div>
            )}
          </>
        )}
        {p && !p.auth.ok && (
          <Callout tone="danger" title="Sign-in needed before this can run" action={<Button size="sm" icon={TriangleAlert} onClick={() => { onClose(); navigate({ view: 'settings', tab: 'auth' }) }}>Open Settings</Button>}>
            {p.auth.reason ?? 'No usable sign-in or API key was found for the extraction model.'}
          </Callout>
        )}
      </div>
    </Modal>
  )
}
