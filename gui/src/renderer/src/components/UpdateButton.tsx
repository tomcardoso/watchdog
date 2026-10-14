// The top bar's update control: "Update available" → download progress → "Restart to update".
// Hidden while there is nothing to do. State comes from the main process (main/updater.ts), which
// replays it on request so a window opened after the check still shows it.

import { Download, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button } from '@renderer/components/ui'
import { useEvent } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'
import type { UpdateState } from '@shared/api'

export function UpdateButton() {
  const [u, setU] = useState<UpdateState | null>(null)
  useEffect(() => {
    void window.watchdog.updates.get().then(setU)
  }, [])
  useEvent('update.state', (s) => {
    setU(s)
    if (s.error) toast({ kind: 'error', title: 'Update', body: s.error })
  })
  if (!u || u.state === 'idle') return null
  if (u.state === 'downloading') {
    return (
      <span className="update-progress" role="status" aria-live="polite">
        Downloading update{u.percent !== null ? ` ${u.percent}%` : '…'}
      </span>
    )
  }
  if (u.state === 'ready') {
    return (
      <Button size="sm" variant="primary" icon={RefreshCw} onClick={() => void window.watchdog.updates.install()}>
        Restart to update{u.version ? ` (${u.version})` : ''}
      </Button>
    )
  }
  return (
    <Button size="sm" variant="soft" icon={Download} tip="Download it now; you choose when to restart" onClick={() => void window.watchdog.updates.download()}>
      Update available{u.version ? ` (${u.version})` : ''}
    </Button>
  )
}
