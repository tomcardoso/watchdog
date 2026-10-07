// Holds the app until the Python backend is ready; explains how to fix it when it can't start.

import { FolderSearch, RotateCw, Terminal } from 'lucide-react'
import { ReactNode, useEffect, useState } from 'react'
import { Button, Callout, Spinner } from '@renderer/components/ui'
import { LogoMark } from '@renderer/components/Logo'
import { useEvent } from '@renderer/lib/rpc'
import type { BackendStatus } from '@shared/api'

export function BackendGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<BackendStatus | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    void window.watchdog.backend.status().then(setStatus)
  }, [])
  useEvent('backend.status', setStatus)

  if (status?.state === 'ready') return <>{children}</>
  const failed = status?.state === 'error' || status?.state === 'stopped'
  const act = async (fn: () => Promise<BackendStatus>) => {
    setBusy(true)
    try {
      setStatus(await fn())
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-logo">
          <LogoMark style={{ '--logo-glint': '#c4471a' } as React.CSSProperties} />
        </div>
        {!failed ? (
          <>
            <h1>Watchdog</h1>
            <div className="row" style={{ color: 'var(--text-2)' }}>
              <Spinner />
              {status?.message ?? 'Starting…'}
            </div>
          </>
        ) : (
          <>
            <h1>Watchdog needs its engine</h1>
            <p>
              The app runs on the same Python program as the <code>watchdog</code> command. It couldn’t find a Python
              installation with Watchdog in it.
            </p>
            <div style={{ textAlign: 'left', width: '100%' }} className="col gap-12">
              <Callout tone="info" title="Install Watchdog, then try again">
                In a terminal, run <code>pipx install watchdog-intel</code> followed by <code>watchdog setup</code>. The{' '}
                <a href="#" onClick={(e) => { e.preventDefault(); void window.watchdog.shell.openExternal('https://github.com/tomcardoso/watchdog/blob/main/docs/install.md') }}>install guide</a>{' '}
                walks through it step by step.
              </Callout>
              {status?.message && (
                <details>
                  <summary className="faint" style={{ cursor: 'pointer', fontSize: 12 }}>What the app tried</summary>
                  <pre className="log" style={{ maxHeight: 180, marginTop: 8 }}>{status.message}{'\n'}{status.stderrTail.join('\n')}</pre>
                </details>
              )}
            </div>
            <div className="row" style={{ marginTop: 6 }}>
              <Button variant="primary" icon={RotateCw} loading={busy} onClick={() => act(window.watchdog.backend.restart)}>
                Try again
              </Button>
              <Button icon={FolderSearch} onClick={() => act(window.watchdog.backend.choosePython)}>
                Choose Python…
              </Button>
              <Button variant="ghost" icon={Terminal} onClick={() => void window.watchdog.shell.openExternal('https://github.com/tomcardoso/watchdog/blob/main/docs/app.md')}>
                About the app
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
