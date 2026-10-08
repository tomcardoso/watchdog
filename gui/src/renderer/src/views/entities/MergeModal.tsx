// The merge flow: choose the entity to keep and the duplicate to fold into it, see both, read
// exactly what `watchdog review merge-entities` will do, confirm, then follow the job.

import { ENGINE_WAIT_OTHER, useEngineGate } from '@renderer/lib/engine'
import { ArrowLeftRight, ArrowRight, GitMerge, RefreshCw, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { EntityAvatar } from '@renderer/components/EntityChip'
import { Button, Callout, ErrorNote, Modal, Skeleton, Spinner, cx } from '@renderer/components/ui'
import type { EntityRow } from '@shared/api'
import { typeMeta } from '@renderer/lib/entityTypes'
import { fmtNum, plural } from '@renderer/lib/format'
import { startJob, waitForJob } from '@renderer/lib/jobs'
import { errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'

type Stage = 'form' | 'running' | 'done' | 'failed'

export default function MergeModal({ open, onClose, initialKeep, initialMerge }: { open: boolean; onClose: () => void; initialKeep?: string; initialMerge?: string }) {
  const vault = useVault()
  const [keep, setKeep] = useState<string | null>(null)
  const [merge, setMerge] = useState<string | null>(null)
  const [ack, setAck] = useState(false)
  const [stage, setStage] = useState<Stage>('form')
  const [jobId, setJobId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const engine = useEngineGate()
  const [reindexing, setReindexing] = useState(false)
  const [bothSummaries, setBothSummaries] = useState(false)

  useEffect(() => {
    if (!open) return
    setKeep(initialKeep ?? null)
    setMerge(initialMerge ?? null)
    setAck(false)
    setStage('form')
    setJobId(null)
    setError(null)
    setReindexing(false)
  }, [open, initialKeep, initialMerge])

  const preview = useRpc('review.mergePreview', open && keep && merge && keep !== merge ? { vault, keep, merge } : null)

  const run = async () => {
    if (!keep || !merge) return
    setError(null)
    try {
      setBothSummaries(!!preview.data?.both_have_summary)
      const job = await startJob(['review', 'merge-entities', keep, merge, '--force'], 'Merging entities')
      setJobId(job.id)
      setStage('running')
      const done = await waitForJob(job.id)
      if (done.state === 'done') {
        invalidate('vault.', 'review.')
        setStage('done')
      } else {
        setError(done.state === 'cancelled' ? 'The merge was stopped before it finished.' : 'The merge did not finish. Open Activity to see what the run reported.')
        setStage('failed')
      }
    } catch (e) {
      setError(errorMessage(e))
      setStage('failed')
    }
  }

  const reindex = async () => {
    setReindexing(true)
    try {
      await startJob(['reindex'], 'Rebuilding search index')
    } finally {
      setReindexing(false)
    }
  }

  const survivor = keep
  const close = () => {
    if (stage === 'running') return
    onClose()
  }
  const survivorName = preview.data?.keep.name

  return (
    <Modal
      open={open}
      onClose={close}
      width="xwide"
      dismissable={stage !== 'running'}
      title={stage === 'done' ? 'Entities merged' : 'Merge duplicate entities'}
      sub={stage === 'form' ? 'Use this when the same real-world person, company or place was extracted under two ids.' : undefined}
      footer={
        stage === 'form' ? (
          <>
            <label className="checkbox" style={{ marginRight: 'auto', color: 'var(--text-2)' }}>
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
              <span>{engine.ready ? 'I understand this cannot be undone from the app' : ENGINE_WAIT_OTHER}</span>
            </label>
            <Button onClick={close}>Cancel</Button>
            <Button variant="danger" icon={GitMerge} disabled={!keep || !merge || keep === merge || !ack || preview.isLoading || preview.isError || !engine.ready} onClick={() => void run()}>
              Merge into {survivorName ? truncate(survivorName, 22) : 'survivor'}
            </Button>
          </>
        ) : stage === 'done' ? (
          <>
            <Button icon={RefreshCw} loading={reindexing} disabled={!engine.ready} onClick={() => void reindex()}>
              Rebuild search index
            </Button>
            <Button variant="primary" iconRight={ArrowRight} onClick={() => { onClose(); if (survivor) navigate({ view: 'entity', id: survivor }) }}>
              Open {survivorName ? truncate(survivorName, 24) : 'the survivor'}
            </Button>
          </>
        ) : stage === 'failed' ? (
          <>
            <Button onClick={close}>Close</Button>
            <Button onClick={() => setStage('form')}>Back</Button>
          </>
        ) : undefined
      }
    >
      {stage === 'form' && (
        <FormBody keep={keep} merge={merge} setKeep={setKeep} setMerge={setMerge} preview={preview} />
      )}
      {stage === 'running' && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '28px 4px' }}>
          <Spinner size="lg" />
          <div>
            <div style={{ fontWeight: 620 }}>Merging…</div>
            <div className="muted">A snapshot is taken first, then the registry and both notes are rewritten. This usually takes a few seconds.</div>
            {jobId && <JobTail id={jobId} />}
          </div>
        </div>
      )}
      {stage === 'done' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <Callout tone="success" title="The merge is complete">
            The duplicate's aliases, documents, roles and timeline now live on {survivorName ?? 'the surviving entity'}. The old note is a redirect stub.
          </Callout>
          <Callout tone="info" title="Next steps">
            Rebuild the search index so the merged entity's stale entries drop out.{bothSummaries ? ' Both entities had a written summary, so refresh the survivor’s summary from all sources: open it and choose “Refresh summary from all sources”.' : ''} A snapshot of the pre-merge files is kept inside the vault if you need to inspect it.
          </Callout>
        </div>
      )}
      {stage === 'failed' && <ErrorNote error={error ?? 'The merge failed.'} />}
    </Modal>
  )
}

function truncate(s: string, n: number) {
  return s.length > n ? s.slice(0, n - 1) + '…' : s
}

function JobTail({ id }: { id: string }) {
  const job = useApp((s) => s.jobs[id])
  const last = job?.log?.length ? job.log[job.log.length - 1].text : ''
  return last ? <div className="faint mono" style={{ marginTop: 6, fontSize: 'var(--fs-xs)' }}>{last.slice(0, 140)}</div> : null
}

function FormBody({ keep, merge, setKeep, setMerge, preview }: { keep: string | null; merge: string | null; setKeep: (id: string | null) => void; setMerge: (id: string | null) => void; preview: ReturnType<typeof useRpc<'review.mergePreview'>> }) {
  const sameId = keep && merge && keep === merge
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div className="ent-merge-grid">
        <Side tone="keep" title="Keep (survivor)" id={keep} other={merge} onPick={setKeep} row={preview.data?.keep} loading={preview.isLoading && !!keep && !!merge} />
        <div className="ent-merge-mid">
          <Button variant="ghost" size="sm" icon={ArrowLeftRight} tip="Swap" disabled={!keep && !merge} onClick={() => { setKeep(merge); setMerge(keep) }} />
          <ArrowRight size={16} />
        </div>
        <Side tone="lose" title="Merge away (duplicate)" id={merge} other={keep} onPick={setMerge} row={preview.data?.merge} loading={preview.isLoading && !!keep && !!merge} />
      </div>

      {sameId && <Callout tone="warning">Choose two different entities.</Callout>}
      {preview.isError && <ErrorNote error={preview.error} retry={() => void preview.refetch()} />}
      {preview.data && ((preview.data as { type_mismatch?: boolean }).type_mismatch ?? preview.data.keep.type !== preview.data.merge.type) && (
        <Callout tone="warning" title="These entities have different types">
          {typeMeta(preview.data.keep.type).label} and {typeMeta(preview.data.merge.type).label}. Merging is usually right only when both are the same kind of thing. Check before you go on.
        </Callout>
      )}
      {preview.data?.both_have_summary && (
        <Callout tone="info" title="Both entities have a written summary">
          The survivor keeps its own summary. Afterwards, refresh it from all sources with “Refresh summary from all sources” on the entity page so it reflects both.
        </Callout>
      )}

      <div className="panel">
        <div style={{ fontWeight: 620 }}>What merging does</div>
        <ul className="ent-merge-list">
          <li>Unions aliases, document appearances, roles and timeline events onto the survivor.</li>
          <li>Remaps every relationship anywhere in the registry that pointed at the duplicate, so other entities now point at the survivor.</li>
          <li>Carries the duplicate’s Analysis section over, with its sources intact.</li>
          <li>Replaces the duplicate’s note with a stub that redirects to the survivor. Handled-contradiction marks follow the entity.</li>
          <li>Takes a snapshot of the affected files first. Restoring from it means copying files back by hand; the app has no undo button.</li>
          <li>Makes no model call and costs nothing. Rebuilding the search index afterwards is advisable.</li>
        </ul>
      </div>
    </div>
  )
}

function Side({ tone, title, id, other, onPick, row, loading }: { tone: 'keep' | 'lose'; title: string; id: string | null; other: string | null; onPick: (id: string | null) => void; row?: EntityRow; loading: boolean }) {
  const vault = useVault()
  const ents = useRpc('vault.entities', { vault })
  const [q, setQ] = useState('')
  const picked = ents.data?.find((e) => e.id === id) ?? row
  const matches = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = (ents.data ?? []).filter((e) => !s || e.name.toLowerCase().includes(s) || e.aliases.some((a) => a.toLowerCase().includes(s)))
    return list.slice(0, 40)
  }, [ents.data, q])

  return (
    <div className={cx('ent-merge-col', tone)}>
      <div className="ent-merge-role">{title}</div>
      {picked && id ? (
        <>
          <div className="ent-picked">
            <EntityAvatar type={picked.type} size={40} />
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="nm truncate">{picked.name}</div>
              <div className="sub">{typeMeta(picked.type).label} · <span className="mono">{picked.id}</span></div>
            </div>
            <Button variant="ghost" size="sm" onClick={() => onPick(null)}>
              Change
            </Button>
          </div>
          {loading ? (
            <Skeleton h={48} />
          ) : (
            <div className="ent-mstats">
              <div className="ent-mstat"><div className="v">{fmtNum(picked.doc_count)}</div><div className="l">documents</div></div>
              <div className="ent-mstat"><div className="v">{fmtNum(picked.role_count)}</div><div className="l">relationships</div></div>
              <div className="ent-mstat"><div className="v">{fmtNum(picked.aliases.length)}</div><div className="l">aliases</div></div>
            </div>
          )}
          {picked.aliases.length > 0 && <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>Also known as: {picked.aliases.slice(0, 6).join(', ')}</div>}
          {picked.summary && <div className="muted" style={{ fontSize: 'var(--fs-sm)', lineHeight: 1.45, display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{picked.summary}</div>}
        </>
      ) : (
        <>
          <div className="input-group">
            <Search />
            <input className="input" placeholder="Find an entity" value={q} onChange={(e) => setQ(e.target.value)} autoFocus={tone === 'keep'} />
          </div>
          <div className="ent-pick-list">
            {ents.isLoading && <div style={{ padding: 10 }}><Skeleton h={14} /></div>}
            {matches.map((e) => (
              <button key={e.id} className="ent-pick-item" disabled={e.id === other} onClick={() => { onPick(e.id); setQ('') }}>
                <EntityAvatar type={e.type} size={24} />
                <span className="nm truncate">{e.name}</span>
                <span className="n">{plural(e.doc_count, 'doc')}</span>
              </button>
            ))}
            {!ents.isLoading && matches.length === 0 && <div style={{ padding: 12 }} className="faint">No entity matches.</div>}
          </div>
        </>
      )}
    </div>
  )
}

