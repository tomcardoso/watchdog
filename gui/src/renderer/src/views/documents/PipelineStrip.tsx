// "Not yet in the vault": everything vault.pipeline reports that hasn't become a document note —
// files waiting, queued or staged work, failures, set-aside files, and stuck locks.

import { AlertTriangle, ChevronRight, Copy, FileWarning, Hourglass, Inbox, Lock, RotateCw, Workflow } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Badge, Button } from '@renderer/components/ui'
import { fmtBytes, plural } from '@renderer/lib/format'
import { runAction } from '@renderer/lib/jobs'
import { errorMessage, invalidate } from '@renderer/lib/rpc'
import { toast, useApp } from '@renderer/lib/store'
import type { PipelineState } from '@shared/api'

const SHOW = 40

function FileList({ rows }: { rows: { key: string; name: string; why?: string | null; err?: boolean }[] }) {
  return (
    <div className="pipe-files selectable">
      {rows.slice(0, SHOW).map((r) => (
        <div className="pipe-file" key={r.key}>
          <span className="name truncate" title={r.name}>{r.name}</span>
          {r.why && <span className={'why truncate' + (r.err ? ' err' : '')} title={r.why}>{r.why}</span>}
        </div>
      ))}
      {rows.length > SHOW && <div className="pipe-more">and {rows.length - SHOW} more</div>}
    </div>
  )
}

export function PipelineStrip({ pipeline, vaultName }: { pipeline: PipelineState; vaultName: string }) {
  const openAdd = useApp((s) => s.openAdd)
  const [open, setOpen] = useState<boolean | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const p = pipeline
  const waiting = p.queued.filter((q) => !q.staged)
  const staged = p.queued.filter((q) => q.staged)
  const finalizing = p.pending_finalization
  const locked = p.locks.chew || p.locks.ingest
  const groups =
    (p.incoming.length ? 1 : 0) + (waiting.length ? 1 : 0) + (staged.length || finalizing ? 1 : 0) +
    (p.failed.length ? 1 : 0) + (p.chew_failed.length ? 1 : 0) + (p.skipped.length ? 1 : 0) + (locked ? 1 : 0)
  const attention = p.failed.length > 0 || p.chew_failed.length > 0 || locked

  useEffect(() => {
    window.watchdog.prefs.get<boolean>('docsPipelineOpen').then((v) => setOpen(v === null || v === undefined ? attention : !!v)).catch(() => setOpen(attention))
  }, [attention])

  if (!groups) return null
  const expanded = open ?? false
  const toggle = () => {
    setOpen(!expanded)
    void window.watchdog.prefs.set('docsPipelineOpen', !expanded)
  }

  const requeue = async () => {
    setBusy('requeue')
    try {
      await runAction(['requeue'])
      toast({ kind: 'success', title: 'Moved back into the queue', body: 'Run Add documents to extract them again.' })
      invalidate('vault.', 'projects.', 'ingest.')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not requeue', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  const unlock = async (force: boolean) => {
    const ok = await window.watchdog.dialog.confirm({
      title: force ? 'Force-remove the lock?' : 'Remove the lock?',
      message: force
        ? `Remove the lock on ${vaultName} even if it is recent?`
        : `Remove the stale lock on ${vaultName}?`,
      detail: force
        ? 'A lock is only safe to remove once you are sure nothing is still running. If a chew or ingest is still working on this investigation, removing its lock lets a second run start and the two can overwrite each other.'
        : 'Do this only if an earlier run was interrupted. A lock under 30 minutes old is left in place unless you force it, because the run that holds it may still be working.',
      confirm: force ? 'Force remove' : 'Remove lock',
      destructive: force
    })
    if (!ok) return
    setBusy(force ? 'force' : 'unlock')
    try {
      const out = await runAction(force ? ['unlock', '--force'] : ['unlock'])
      toast({ kind: 'success', title: 'Lock removed', body: out.trim().split('\n').pop() || undefined })
      invalidate('vault.', 'projects.', 'ingest.')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not remove the lock', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="pipe" aria-label="Documents not yet in the vault">
      <button className="pipe-head" aria-expanded={expanded} onClick={toggle}>
        <ChevronRight />
        <Workflow style={{ color: 'var(--text-2)' }} />
        <span className="pipe-title">Pipeline</span>
        <span className="pipe-summary">
          {p.incoming.length > 0 && <Badge icon={Inbox}>{p.incoming.length} waiting</Badge>}
          {waiting.length > 0 && <Badge icon={Hourglass}>{waiting.length} to extract</Badge>}
          {(staged.length > 0 || finalizing) && <Badge icon={Hourglass}>{finalizing?.docs ?? staged.length} to finish</Badge>}
          {p.failed.length > 0 && <Badge tone="danger" icon={AlertTriangle}>{p.failed.length} failed</Badge>}
          {p.chew_failed.length > 0 && <Badge tone="warning" icon={FileWarning}>{p.chew_failed.length} unreadable</Badge>}
          {p.skipped.length > 0 && <Badge icon={Copy}>{p.skipped.length} set aside</Badge>}
          {locked && <Badge tone="warning" icon={Lock}>locked</Badge>}
        </span>
        <span className="spacer" />
        <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{expanded ? 'Hide' : 'Show'}</span>
      </button>

      {expanded && (
        <div className="pipe-body">
          {locked && (
            <div className="pipe-group wide">
              <div className="pipe-group-head"><Lock style={{ color: 'var(--warning)' }} />Lock present</div>
              <p>
                {p.locks.chew && p.locks.ingest ? 'A chew lock and an ingest lock are present.' : p.locks.chew ? 'A chew lock is present.' : 'An ingest lock is present.'}{' '}
                While a lock exists, Watchdog refuses to start another run on this investigation. If a run is still going, wait for it. If one was interrupted, the lock is left behind and can be removed. A lock under 30 minutes old is kept unless you force it.
              </p>
              <div className="pipe-actions">
                <Button size="sm" icon={Lock} loading={busy === 'unlock'} onClick={() => unlock(false)}>Unlock…</Button>
                <Button size="sm" variant="danger" loading={busy === 'force'} onClick={() => unlock(true)}>Force unlock…</Button>
              </div>
            </div>
          )}

          {p.incoming.length > 0 && (
            <div className="pipe-group">
              <div className="pipe-group-head"><Inbox style={{ color: 'var(--info)' }} />Waiting in _INCOMING <span className="n">{p.incoming.length}</span></div>
              <p>Files in the investigation's _INCOMING folder that haven't been read yet.</p>
              <FileList rows={p.incoming.map((f) => ({ key: f.path, name: f.name, why: [fmtBytes(f.size), f.sidecar ? 'with sidecar' : ''].filter(Boolean).join(' · ') }))} />
              <div className="pipe-actions"><Button size="sm" variant="primary" onClick={() => openAdd()}>Add them</Button></div>
            </div>
          )}

          {waiting.length > 0 && (
            <div className="pipe-group">
              <div className="pipe-group-head"><Hourglass style={{ color: 'var(--text-2)' }} />Read, awaiting extraction <span className="n">{waiting.length}</span></div>
              <p>Chewed locally into text and queued. The model hasn't extracted facts from them yet.</p>
              <FileList rows={waiting.map((q) => ({ key: q.sha, name: q.filename, why: q.page_count ? plural(q.page_count, 'page') : null }))} />
              <div className="pipe-actions"><Button size="sm" variant="primary" onClick={() => openAdd()}>Extract them</Button></div>
            </div>
          )}

          {(staged.length > 0 || finalizing) && (
            <div className="pipe-group">
              <div className="pipe-group-head"><Hourglass style={{ color: 'var(--text-2)' }} />Staged, awaiting finishing <span className="n">{finalizing?.docs ?? staged.length}</span></div>
              <p>
                Extracted, but not yet written to the vault. The wrap-up reconciles duplicate entities, writes the notes and produces the briefing
                {finalizing ? ` (${plural(finalizing.docs, 'document')}, ${plural(finalizing.entities, 'entity', 'entities')})` : ''}. It is safe to run more than once, and nothing staged is ever discarded.
              </p>
              {staged.length > 0 && <FileList rows={staged.map((q) => ({ key: q.sha, name: q.filename, why: q.page_count ? plural(q.page_count, 'page') : null }))} />}
              <div className="pipe-actions"><Button size="sm" variant="primary" onClick={() => openAdd()}>Finish them</Button></div>
            </div>
          )}

          {p.failed.length > 0 && (
            <div className="pipe-group">
              <div className="pipe-group-head"><AlertTriangle style={{ color: 'var(--danger)' }} />Failed extraction <span className="n">{p.failed.length}</span></div>
              <p>
                These were set aside so the rest of the batch could finish. Requeue moves them back into the queue without running them. Retry puts them back and runs them again, the same as watchdog add --retry.
              </p>
              <FileList rows={p.failed.map((f) => ({ key: f.sha, name: f.filename, why: f.reason ?? 'No reason recorded. See Activity for the run output.', err: true }))} />
              <div className="pipe-actions">
                <Button size="sm" variant="primary" icon={RotateCw} onClick={() => window.dispatchEvent(new CustomEvent('wd:add', { detail: { retry: true } }))}>Retry</Button>
                <Button size="sm" loading={busy === 'requeue'} onClick={requeue}>Requeue</Button>
              </div>
            </div>
          )}

          {p.chew_failed.length > 0 && (
            <div className="pipe-group">
              <div className="pipe-group-head"><FileWarning style={{ color: 'var(--warning)' }} />Couldn't be read <span className="n">{p.chew_failed.length}</span></div>
              <p>
                These landed in _INCOMING/_FAILED when chewing failed. Common causes are a password-protected PDF (remove the password), a corrupted file (download or export it again), or an unsupported format. To retry, move the file back into _INCOMING.
              </p>
              <FileList rows={p.chew_failed.map((f) => ({ key: f.path, name: f.name, why: fmtBytes(f.size) }))} />
            </div>
          )}

          {p.skipped.length > 0 && (
            <div className="pipe-group">
              <div className="pipe-group-head"><Copy style={{ color: 'var(--text-2)' }} />Set aside <span className="n">{p.skipped.length}</span></div>
              <p>
                Neither is an error. A file byte-identical to one already ingested is set aside rather than processed twice. A file with no readable text, even after OCR, is set aside too. Open the original to check a very poor scan.
              </p>
              <FileList rows={p.skipped.map((f) => ({ key: f.path, name: f.name, why: f.reason ?? undefined }))} />
            </div>
          )}
        </div>
      )}
    </section>
  )
}
