// Settings → Folder access: the folders Watchdog may change, with the way to add and remove them.
// Grants are made in the main process (main/access.ts); this panel only lists, asks and revokes.

import { FolderLock, FolderPlus, Trash2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Callout, Empty } from '@renderer/components/ui'
import { fmtDate } from '@renderer/lib/format'
import { invalidate } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'
import type { FolderGrant } from '@shared/api'

export function FolderAccessPanel() {
  const [grants, setGrants] = useState<FolderGrant[] | null>(null)
  const reload = () => void window.watchdog.access.list().then(setGrants)
  useEffect(reload, [])

  const add = async () => {
    const picked = await window.watchdog.dialog.openFolder({ title: 'Allow Watchdog to work in a folder', grant: 'added in Settings' })
    if (picked) {
      reload()
      invalidate('projects.')
    }
  }
  const remove = async (g: FolderGrant) => {
    const ok = await window.watchdog.dialog.confirm({
      title: 'Remove access',
      message: `Stop Watchdog changing files in “${g.path}”?`,
      detail: 'Nothing in the folder is deleted. Investigations inside it will ask for access again before Watchdog works in them.',
      confirm: 'Remove access',
      destructive: true
    })
    if (!ok) return
    setGrants(await window.watchdog.access.revoke(g.path))
    invalidate()
    toast({ kind: 'info', title: 'Access removed', body: g.path })
  }

  return (
    <div className="col gap-16">
      <Callout tone="info" title="Watchdog only changes files in folders you have allowed">
        Choosing a folder for Watchdog to use — a home for new investigations, an existing investigation, an export destination — allows it. Anything
        else, including a Claude session following instructions found in a document, is refused. Watchdog’s own settings, the system’s temporary folder
        and its downloaded models are always available to it.
      </Callout>
      <div className="row">
        <Button icon={FolderPlus} onClick={() => void add()}>
          Allow a folder…
        </Button>
      </div>
      {grants && grants.length === 0 && (
        <Empty icon={FolderLock} title="No folders allowed yet">
          Allow your investigations folder, or open an investigation and allow it when asked.
        </Empty>
      )}
      {grants && grants.length > 0 && (
        <div className="card" style={{ overflow: 'hidden' }}>
          <table className="table">
            <thead>
              <tr>
                <th>Folder</th>
                <th>Allowed for</th>
                <th>Since</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {grants.map((g) => (
                <tr key={g.path}>
                  <td className="mono selectable" style={{ fontSize: 'var(--fs-sm)', wordBreak: 'break-all' }}>{g.path}</td>
                  <td className="muted">{g.label}</td>
                  <td className="faint tnum">{fmtDate(g.granted)}</td>
                  <td style={{ textAlign: 'right' }}>
                    <Button size="sm" variant="ghost" icon={Trash2} tip="Remove access" onClick={() => void remove(g)} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
