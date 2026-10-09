// Activity → Version history (D286): every version Watchdog recorded for this investigation,
// newest first, with the files each one changed. A file opens its own history at that version.

import { ChevronDown, ChevronRight, FileClock } from 'lucide-react'
import { useState } from 'react'
import { FileHistoryModal } from '@renderer/components/FileHistory'
import { Button, Empty, ErrorNote, Skeleton } from '@renderer/components/ui'
import { fmtBytes, fmtDateTime, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, useVault } from '@renderer/lib/store'
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
                <span className="m">{plural(v.files, 'file')}</span>
              </button>
              {expanded && (
                <div className="vh-files">
                  {v.changes.slice(0, SHOWN_FILES).map((c) => (
                    <button key={c.path} className={'vh-file' + (c.deleted ? ' gone' : '')} onClick={() => setFile({ path: c.path, version: v.version })} title={c.deleted ? 'Deleted in this version' : 'Show this file’s history'}>
                      {fileLabel(c.path)}
                    </button>
                  ))}
                  {v.changes.length > SHOWN_FILES && <span className="faint">and {plural(v.changes.length - SHOWN_FILES, 'more file')}</span>}
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
      {file && <FileHistoryModal vault={vault} path={file.path} initialVersion={file.version} onClose={() => setFile(null)} />}
    </div>
  )
}
