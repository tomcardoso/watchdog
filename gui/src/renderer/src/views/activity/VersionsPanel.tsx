// Activity → Version history (D286): every version Watchdog recorded for this investigation,
// newest first, with the files each one changed. A file opens its own history at that version. A
// whole version can be removed for good (D288), except for the files it left as they are now.

import { ChevronDown, ChevronRight, FileClock, Trash2 } from 'lucide-react'
import { useState } from 'react'
import type { HistoryVersion } from '@shared/api'
import { FileHistoryModal, RemovalPlan, removalSummary } from '@renderer/components/FileHistory'
import { Button, Callout, Empty, ErrorNote, Modal, Skeleton } from '@renderer/components/ui'
import { fmtBytes, fmtDateTime, plural } from '@renderer/lib/format'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useVault } from '@renderer/lib/store'
import '@renderer/components/history.css'

const SHOWN_FILES = 40

function fileLabel(path: string): string {
  if (path.startsWith('.watchdog/registry/')) return `${path.slice('.watchdog/registry/'.length)} (data)`
  return path
}

export default function VersionsPanel() {
  const vault = useVault()
  const [limit, setLimit] = useState(50)
  const q = useRpc('history.versions', { vault, limit }, { staleTime: 0 })
  const st = useRpc('history.stats', { vault }, { staleTime: 0 })
  const [open, setOpen] = useState<Record<number, boolean>>({})
  const [file, setFile] = useState<{ path: string; version: number } | null>(null)
  const [removing, setRemoving] = useState<HistoryVersion | null>(null)

  if (q.isLoading) return <Skeleton h={300} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const rows = q.data?.versions ?? []
  if (!rows.length)
    return (
      <Empty icon={FileClock} title="No versions recorded yet">
        Watchdog records a version of its notes, briefings, pages and data each time they change, so you can see what changed and put back your own writing.
      </Empty>
    )
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row">
        <span className="muted">
          {plural(q.data!.total, 'version')}
          {st.data && <> · {plural(st.data.files, 'file')} · {fmtBytes(st.data.bytes)} on disk</>}
        </span>
        <span className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => navigate({ view: 'settings', tab: 'history' })}>Clear history…</Button>
      </div>
      <div className="card card-pad" style={{ paddingTop: 4, paddingBottom: 4 }}>
        {rows.map((v) => {
          const expanded = !!open[v.version]
          return (
            <div key={v.version} className="vh-row">
              <button className="vh-head" onClick={() => setOpen((o) => ({ ...o, [v.version]: !expanded }))} aria-expanded={expanded}>
                {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                <span className="t">{v.label}</span>
                <span className="m">{fmtDateTime(v.at)}</span>
                <span className="spacer" />
                <span className="m">
                  {plural(v.files, 'file')}
                  {v.removed > 0 && <> · {v.removed} removed</>}
                </span>
              </button>
              {expanded && (
                <div className="vh-files">
                  {v.changes.slice(0, SHOWN_FILES).map((c) => (
                    <button key={c.path} className={'vh-file' + (c.deleted ? ' gone' : '')} onClick={() => setFile({ path: c.path, version: v.version })} title={c.deleted ? 'Deleted in this version' : 'Show this file’s history'}>
                      {fileLabel(c.path)}
                    </button>
                  ))}
                  {v.changes.length > SHOWN_FILES && <span className="faint">and {plural(v.changes.length - SHOWN_FILES, 'more file')}</span>}
                  <div className="vh-actions">
                    <Button size="sm" variant="ghost" icon={Trash2} disabled={q.data!.too_new} onClick={() => setRemoving(v)}>
                      Remove this version…
                    </Button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
      {q.data!.more && (
        <div className="row" style={{ justifyContent: 'center' }}>
          <Button size="sm" onClick={() => setLimit(limit + 100)}>Show older versions</Button>
        </div>
      )}
      {removing && <RemoveVersion vault={vault} v={removing} onClose={() => setRemoving(null)} />}
      {file && <FileHistoryModal vault={vault} path={file.path} initialVersion={file.version} onClose={() => setFile(null)} />}
    </div>
  )
}

function RemoveVersion({ vault, v, onClose }: { vault: string; v: HistoryVersion; onClose: () => void }) {
  const plan = useRpc('history.remove', { vault, version: v.version, dry_run: true }, { staleTime: 0 })
  const [busy, setBusy] = useState(false)
  const p = plan.data
  const run = async () => {
    setBusy(true)
    try {
      const r = await call('history.remove', { vault, version: v.version })
      invalidate('history.', 'projects.')
      toast({ kind: 'success', title: 'Version removed', body: removalSummary(r) })
      onClose()
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
      title="Remove this version from the history?"
      sub={`${v.label} · ${fmtDateTime(v.at)}`}
      footer={
        <>
          <Button autoFocus onClick={onClose}>Cancel</Button>
          <Button variant="danger" icon={Trash2} loading={busy} disabled={!p || !p.removed.length} onClick={() => void run()}>
            Remove this version
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        {plan.isLoading ? (
          <Skeleton h={60} />
        ) : plan.error ? (
          <ErrorNote error={plan.error} />
        ) : p && !p.removed.length ? (
          <Callout tone="info" title="Nothing to remove">
            Every file in this version is still as this version left it, so its text is in the files now. Change a file first; the version it replaces can then be removed.
          </Callout>
        ) : (
          p && (
            <>
              <Callout tone="danger" title="This cannot be undone">
                What this version recorded for {plural(p.removed.length, 'file')} is deleted from this computer. The files as they are now do not change.
              </Callout>
              <RemovalPlan r={p} />
              {p.kept_current.length > 0 && (
                <div className="hist-explain faint">
                  {plural(p.kept_current.length, 'file')} {p.kept_current.length === 1 ? 'is' : 'are'} still as this version left {p.kept_current.length === 1 ? 'it' : 'them'} and {p.kept_current.length === 1 ? 'stays' : 'stay'} in the history, along with this version’s description: {p.kept_current.slice(0, 5).map(fileLabel).join(', ')}
                  {p.kept_current.length > 5 && ` and ${p.kept_current.length - 5} more`}.
                </div>
              )}
            </>
          )
        )}
      </div>
    </Modal>
  )
}
