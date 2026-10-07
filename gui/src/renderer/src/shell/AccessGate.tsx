// Shown in place of an investigation's screens when the user hasn't yet allowed Watchdog to work
// in its folder (main/access.ts, src/watchdog/access.py). Existing investigations meet this once,
// the first time they're opened after folder access arrived.

import { FolderLock } from 'lucide-react'
import { useState } from 'react'
import { Button, Callout } from '@renderer/components/ui'
import { call, invalidate } from '@renderer/lib/rpc'
import { useApp } from '@renderer/lib/store'
import type { Project } from '@shared/api'

export function AccessGate({ project }: { project: Project }) {
  const [busy, setBusy] = useState(false)
  const [declined, setDeclined] = useState(false)
  const allow = async () => {
    setBusy(true)
    try {
      const ok = await window.watchdog.access.request(
        project.path,
        project.name,
        'Watchdog reads this investigation’s documents and writes its notes, briefings and indexes here.'
      )
      if (!ok) {
        setDeclined(true)
        return
      }
      const updated = await call('projects.get', { slug: project.slug })
      useApp.setState({ project: updated })
      invalidate()
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="page">
      <div className="page-inner" style={{ maxWidth: 620, paddingTop: 72 }}>
        <div className="empty" style={{ paddingTop: 0 }}>
          <div className="empty-icon">
            <FolderLock />
          </div>
          <div className="empty-title">Allow Watchdog to work in this investigation</div>
          <div className="empty-body">
            Watchdog only reads and changes files in folders you have allowed. <strong>{project.name}</strong> is in{' '}
            <span className="mono selectable">{project.path}</span>.
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <Button variant="primary" icon={FolderLock} loading={busy} onClick={() => void allow()}>
              Allow access…
            </Button>
            <Button variant="ghost" onClick={() => useApp.getState().setProject(null)}>
              All investigations
            </Button>
          </div>
          {declined && (
            <Callout tone="info" style={{ marginTop: 16, textAlign: 'left' }}>
              Nothing was changed. You can allow access later from here or from Settings → Folder access.
            </Callout>
          )}
        </div>
      </div>
    </div>
  )
}
