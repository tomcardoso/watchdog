// Version history of one file (D286): the versions Watchdog recorded, a before/after diff of the
// selected one, and "Restore this version" where restoring means something that lasts. Entity and
// document notes are rebuilt from data at every run, so only their Notes section can be put back;
// generated files and registry data can be viewed or copied, not restored. A past version can be
// removed for good (D288): its stored text is deleted from disk unless another version holds the
// same text; the file's newest version is the file as it is now and cannot be removed.

import { Check, Copy, History, RotateCcw, Trash2 } from 'lucide-react'
import { Fragment, useEffect, useMemo, useState } from 'react'
import type { DiffLine, FileDiff, FileHistory as FileHistoryT, HistoryRemoval, RestoreKind } from '@shared/api'
import { fmtBytes, fmtDateTime, plural } from '@renderer/lib/format'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'
import { Button, Callout, Empty, ErrorNote, Modal, Segmented, Skeleton, cx } from './ui'
import './history.css'

type Against = 'previous' | 'current'

export function HistoryButton({ vault, path, title, size = 'sm', variant }: { vault: string; path: string | null; title?: string; size?: 'sm' | 'md'; variant?: 'ghost' | 'default' }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size={size} variant={variant} icon={History} disabled={!path} onClick={() => setOpen(true)}>
        History
      </Button>
      {open && path && <FileHistoryModal vault={vault} path={path} title={title} onClose={() => setOpen(false)} />}
    </>
  )
}

const RESTORE_NOTE: Record<RestoreKind, string> = {
  page: 'Restoring writes this version back over the file. The version it replaces stays in the history.',
  notes:
    'This note is rebuilt from the investigation’s facts every time documents are added, so only your Notes section can be restored. The rest of an older version can be read and copied here.',
  none: 'This file is generated from the investigation’s data and rewritten after every change, so an older version can be read and copied but not restored.'
}

export function FileHistoryModal({ vault, path, title, initialVersion, onClose }: { vault: string; path: string; title?: string; initialVersion?: number; onClose: () => void }) {
  const q = useRpc('history.file', { vault, path }, { staleTime: 0 })
  const [selected, setSelected] = useState<number | null>(initialVersion ?? null)
  const [against, setAgainst] = useState<Against>('previous')
  const [confirm, setConfirm] = useState(false)
  const [removing, setRemoving] = useState(false)
  const h = q.data
  useEffect(() => {
    if (h && h.versions.length && (selected === null || !h.versions.some((v) => v.version === selected))) setSelected(h.versions[0].version)
  }, [h, selected])
  const row = h?.versions.find((v) => v.version === selected) ?? null

  return (
    <Modal open onClose={onClose} dismissable={!confirm && !removing} width="xwide" title="Version history" sub={<span className="mono">{title ?? h?.path ?? path}</span>}>
      {q.isLoading ? (
        <Skeleton h={320} />
      ) : q.error ? (
        <ErrorNote error={q.error} retry={() => void q.refetch()} />
      ) : !h || h.versions.length === 0 ? (
        <Empty icon={History} title="No versions recorded yet">
          Watchdog records a version each time this file changes: when documents are added, when you merge entities, mark a fact or edit in the app.
        </Empty>
      ) : (
        <div className="hist">
          <ol className="hist-list" aria-label="Versions, newest first">
            {h.versions.map((v) => (
              <Fragment key={v.version}>
                <li>
                  <button className={cx('hist-item', v.version === selected && 'on')} onClick={() => setSelected(v.version)}>
                    <span className="hist-item-label" title={v.label}>{v.label}</span>
                    <span className="hist-item-meta">
                      {fmtDateTime(v.at)}
                      {v.current && <span className="hist-tag">Current</span>}
                      {v.deleted && <span className="hist-tag warn">Deleted</span>}
                    </span>
                  </button>
                </li>
                {v.removed_before > 0 && (
                  <li className="hist-removed">
                    {plural(v.removed_before, 'version')} removed
                  </li>
                )}
              </Fragment>
            ))}
          </ol>
          <div className="hist-main">
            {h.too_new && <Callout tone="warning" title="Read only">This history was written by a newer version of Watchdog. Update Watchdog to restore versions.</Callout>}
            {row && (
              <>
                <div className="hist-bar">
                  <Segmented
                    value={against}
                    onChange={setAgainst}
                    options={[
                      { value: 'previous', label: 'Changes in this version' },
                      { value: 'current', label: 'Compared with now' }
                    ]}
                  />
                  <span className="spacer" />
                  <CopyVersion vault={vault} path={h.path} version={row.version} disabled={row.deleted} />
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={Trash2}
                    disabled={row.latest || h.too_new}
                    tip={row.latest ? 'This is the file as it is now, so it cannot be removed from the history. Change the file itself first; the version it replaces can then be removed.' : 'Delete this version from the history for good'}
                    onClick={() => setRemoving(true)}
                  >
                    Remove…
                  </Button>
                  {h.restore !== 'none' && (
                    <Button size="sm" variant="primary" icon={RotateCcw} disabled={row.deleted || row.current || h.too_new} onClick={() => setConfirm(true)}>
                      {h.restore === 'notes' ? 'Restore my notes' : 'Restore this version'}
                    </Button>
                  )}
                </div>
                <p className="hist-explain faint">{RESTORE_NOTE[h.restore]}</p>
                <DiffPane vault={vault} path={h.path} version={row.version} against={against} />
              </>
            )}
          </div>
        </div>
      )}
      {removing && h && row && (
        <RemoveConfirm
          vault={vault}
          history={h}
          version={row.version}
          at={row.at}
          onClose={() => setRemoving(false)}
          onDone={() => {
            setRemoving(false)
            setSelected(null)
            void q.refetch()
          }}
        />
      )}
      {confirm && h && row && <RestoreConfirm vault={vault} history={h} version={row.version} at={row.at} onClose={() => setConfirm(false)} onDone={() => { setConfirm(false); void q.refetch() }} />}
    </Modal>
  )
}

function CopyVersion({ vault, path, version, disabled }: { vault: string; path: string; version: number; disabled?: boolean }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try {
      const r = await call('history.read', { vault, path, version })
      await navigator.clipboard.writeText(r.text)
      setDone(true)
      setTimeout(() => setDone(false), 1500)
    } catch (e) {
      toast({ kind: 'error', title: 'Could not copy', body: errorMessage(e) })
    }
  }
  return (
    <Button size="sm" icon={done ? Check : Copy} disabled={disabled} onClick={() => void copy()}>
      {done ? 'Copied' : 'Copy this version'}
    </Button>
  )
}

function RestoreConfirm({ vault, history, version, at, onClose, onDone }: { vault: string; history: FileHistoryT; version: number; at: string | null; onClose: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false)
  const notes = history.restore === 'notes'
  const run = async () => {
    setBusy(true)
    try {
      await call('history.restore', { vault, path: history.path, version })
      invalidate('vault.', 'history.')
      toast({ kind: 'success', title: notes ? 'Notes restored' : 'Version restored', body: 'Recorded in the history as “Restored by you”.' })
      onDone()
    } catch (e) {
      toast({ kind: 'error', title: 'Could not restore', body: errorMessage(e) })
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={notes ? 'Restore your notes from this version?' : 'Restore this version?'}
      sub={`The version of ${fmtDateTime(at)}`}
      footer={
        <>
          <Button autoFocus onClick={onClose}>Cancel</Button>
          <Button variant="primary" icon={RotateCcw} loading={busy} onClick={() => void run()}>
            {notes ? 'Restore my notes' : 'Restore'}
          </Button>
        </>
      }
    >
      <p className="muted" style={{ margin: 0 }}>
        {notes
          ? 'The Notes section of this note will be replaced with the notes you had in this version. The facts, summary and everything else Watchdog wrote stay as they are now, because they are rebuilt from the investigation’s data.'
          : 'The file will be replaced with this version.'}{' '}
        Nothing is lost: what you have now stays in the history, and you can restore it the same way.
      </p>
    </Modal>
  )
}

type Scope = 'one' | 'older'

/** Remove one version of a file, or it and every older one, after an irreversible-action
 * confirmation that says exactly what leaves the disk (D288). */
function RemoveConfirm({ vault, history, version, at, onClose, onDone }: { vault: string; history: FileHistoryT; version: number; at: string | null; onClose: () => void; onDone: () => void }) {
  const older = history.versions.filter((v) => v.version < version).length
  const [scope, setScope] = useState<Scope>('one')
  const [busy, setBusy] = useState(false)
  const params = { vault, path: history.path, version, older: scope === 'older' }
  const plan = useRpc('history.remove', { ...params, dry_run: true }, { staleTime: 0 })
  const run = async () => {
    setBusy(true)
    try {
      const r = await call('history.remove', params)
      invalidate('history.', 'projects.')
      toast({ kind: 'success', title: r.removed.length === 1 ? 'Version removed' : `${plural(r.removed.length, 'version')} removed`, body: removalSummary(r) })
      onDone()
    } catch (e) {
      toast({ kind: 'error', title: 'Could not remove', body: errorMessage(e) })
      setBusy(false)
    }
  }
  return (
    <Modal
      open
      onClose={onClose}
      dismissable={!busy}
      title="Remove from the history?"
      sub={<span className="mono">{history.path}</span>}
      footer={
        <>
          <Button autoFocus onClick={onClose}>Cancel</Button>
          <Button variant="danger" icon={Trash2} loading={busy} disabled={!plan.data} onClick={() => void run()}>
            {scope === 'older' ? `Remove ${plural(older + 1, 'version')}` : 'Remove this version'}
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        {older > 0 && (
          <Segmented
            value={scope}
            onChange={setScope}
            options={[
              { value: 'one', label: 'Only this version' },
              { value: 'older', label: `This and ${plural(older, 'older version')}` }
            ]}
          />
        )}
        <Callout tone="danger" title="This cannot be undone">
          {scope === 'older'
            ? `The version of ${fmtDateTime(at)} and every earlier version of this file are deleted from this computer.`
            : `The version of ${fmtDateTime(at)} is deleted from this computer.`}{' '}
          The file as it is now does not change: text still in the file stays there, so remove it from the file first.
        </Callout>
        {plan.isLoading ? <Skeleton h={40} /> : plan.error ? <ErrorNote error={plan.error} /> : plan.data && <RemovalPlan r={plan.data} />}
        <p className="hist-explain faint">
          The versions either side are then compared with each other directly, and the list shows where versions were removed. A copy of this folder made by a backup or a sync service is not affected.
        </p>
      </div>
    </Modal>
  )
}

export function removalSummary(r: HistoryRemoval): string {
  const parts = [r.purged ? `${plural(r.purged, 'stored text')} deleted from this computer (${fmtBytes(r.freed_bytes)}).` : 'No stored text needs deleting.']
  if (r.shared.length) parts.push(`${plural(r.shared.length, 'version')} had the same text as a version that stays, so that text is still in the history.`)
  return parts.join(' ')
}

export function RemovalPlan({ r }: { r: HistoryRemoval }) {
  return (
    <div className="hist-plan">
      <div>{removalSummary(r)}</div>
      <div className="faint">Other versions can hold the same words. If you are removing a name or a detail, look through the versions that stay as well.</div>
    </div>
  )
}

function DiffPane({ vault, path, version, against }: { vault: string; path: string; version: number; against: Against }) {
  const q = useRpc('history.diff', { vault, path, version, against }, { staleTime: 60_000 })
  if (q.isLoading) return <Skeleton h={260} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const d = q.data!
  return <DiffView d={d} against={against} />
}

export function DiffView({ d, against }: { d: FileDiff; against: Against }) {
  const caption = useMemo(() => {
    const before = against === 'current' ? 'this version' : d.before.version ? 'the version before' : null
    const after = against === 'current' ? 'the file now' : 'this version'
    const gap = against === 'previous' ? d.removed_between ?? 0 : 0
    const removed = gap ? ` ${plural(gap, 'version')} between them ${gap === 1 ? 'was' : 'were'} removed from the history, so the changes shown include ${gap === 1 ? 'its' : 'theirs'}.` : ''
    if (!before)
      return gap
        ? `The earliest version still in the history; ${plural(gap, 'earlier version')} ${gap === 1 ? 'was' : 'were'} removed. Everything in it is shown as added.`
        : 'The first recorded version: everything in it is shown as added.'
    if (against === 'current' && !d.after.exists) return 'The file no longer exists.'
    if (against === 'previous' && !d.after.exists) return 'This version records the file being deleted.' + removed
    return `From ${before} to ${after}.` + removed
  }, [d, against])
  return (
    <div className="hist-diff">
      <div className="hist-diff-head">
        <span>{caption}</span>
        <span className="spacer" />
        {!d.identical && (
          <span className="hist-counts tnum">
            {d.added > 0 && <span className="ins">+{plural(d.added, 'line')}</span>}
            {d.removed > 0 && <span className="del">−{plural(d.removed, 'line')}</span>}
          </span>
        )}
      </div>
      {d.identical ? (
        <div className="hist-same">No difference.</div>
      ) : (
        <div className="hist-lines selectable" role="table" aria-label="Differences">
          {d.hunks.map((h, i) => (
            <div key={i} className="hist-hunk">
              {i > 0 && <div className="hist-gap" aria-hidden>⋯</div>}
              {h.lines.map((l, j) => (
                <Line key={j} l={l} />
              ))}
            </div>
          ))}
          {d.truncated && <div className="hist-gap">The rest of the changes are not shown. Copy the version to read it in full.</div>}
        </div>
      )}
    </div>
  )
}

function Line({ l }: { l: DiffLine }) {
  const cls = l.op === '+' ? 'ins' : l.op === '-' ? 'del' : 'eq'
  // A whole added or removed line is tinted as a line; only words inside a changed line get the
  // stronger word tint.
  const whole = l.segments.length === 1
  return (
    <div className={cx('hist-line', cls)} role="row">
      <span className="hist-num" aria-hidden>{l.old ?? ''}</span>
      <span className="hist-num" aria-hidden>{l.new ?? ''}</span>
      <span className="hist-mark" aria-label={l.op === '+' ? 'added' : l.op === '-' ? 'removed' : undefined}>{l.op === ' ' ? '' : l.op === '-' ? '−' : '+'}</span>
      <span className="hist-text">
        {l.segments.map((s, k) => (
          <span key={k} className={s.t === 'eq' || whole ? undefined : `w-${s.t}`}>
            {s.s}
          </span>
        ))}
        {l.segments.every((s) => !s.s) && ' '}
      </span>
    </div>
  )
}
