// Holds the app until the Python backend is ready. A computer with no engine (or an outdated one),
// or one that has not finished first-run setup, gets the onboarding screens instead; a genuine
// failure offers Retry, Repair the engine and Choose Python.

import { FolderSearch, RotateCw, Wrench } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { Button, Spinner } from '@renderer/components/ui'
import { LogoMark } from '@renderer/components/Logo'
import { call, useEvent } from '@renderer/lib/rpc'
import OnboardingView, { OnboardingMode } from '@renderer/views/onboarding/OnboardingView'
import type { BackendStatus, EngineStatus } from '@shared/api'

export function BackendGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<BackendStatus | null>(null)
  const [engine, setEngine] = useState<EngineStatus | null>(null)
  const [pending, setPending] = useState<boolean | null>(null) // first-run setup started but not finished
  const [setupDone, setSetupDone] = useState<boolean | null>(null)
  const [busy, setBusy] = useState(false)
  const [dismissed, setDismissed] = useState(false)

  const refresh = useCallback(async () => {
    setEngine(await window.watchdog.engine.status())
  }, [])
  useEffect(() => {
    void window.watchdog.backend.status().then(setStatus)
    void refresh()
    void window.watchdog.prefs.get<{ step?: string }>('onboarding').then((p) => setPending(!!p))
  }, [refresh])
  useEvent('backend.status', setStatus)
  useEvent('engine.progress', (p) => {
    if (p.state !== 'running') void refresh()
  })
  const ready = status?.state === 'ready'
  useEffect(() => {
    if (!ready) return
    void call('app.info', {}).then((i) => setSetupDone(i.setup_complete)).catch(() => setSetupDone(true))
  }, [ready])

  const act = useCallback(async (fn: () => Promise<BackendStatus>) => {
    setBusy(true)
    try {
      setStatus(await fn())
    } finally {
      setBusy(false)
    }
  }, [])

  const forced = !!engine?.forceOnboarding && !dismissed
  const unfinished = pending === true || (ready && setupDone === false) || forced
  let mode: OnboardingMode | null = null
  if (status?.needsEngine) {
    // No usable engine. Without a settings file this is a first run; an engine that is out of date
    // updates itself; anything else is a repair.
    mode = unfinished || (engine && !engine.setupConfigExists) ? 'first-run' : engine?.engine === 'outdated' ? 'update' : 'repair'
  } else if (ready && unfinished) {
    mode = 'first-run'
  }

  const done = async (openNew?: boolean) => {
    await window.watchdog.prefs.set('onboarding', null)
    setPending(false)
    setDismissed(true)
    setSetupDone(true)
    await refresh()
    if (openNew) setTimeout(() => window.dispatchEvent(new CustomEvent('wd:command', { detail: 'new-investigation' })), 150)
  }

  // Remember that first-run setup has begun, so closing the app part-way resumes it.
  useEffect(() => {
    if (mode === 'first-run' && pending === false) {
      void window.watchdog.prefs.set('onboarding', { step: 'welcome' })
      setPending(true)
    }
  }, [mode, pending])

  if (mode && pending !== null && engine) {
    return (
      <OnboardingView
        mode={mode}
        backend={status}
        forceStep={engine.forceOnboarding && engine.forceOnboarding !== '1' ? engine.forceOnboarding : null}
        onDone={(openNew) => void done(openNew)}
        onBackendAction={act}
      />
    )
  }
  if (ready && setupDone !== null) return <>{children}</>
  const failed = status?.state === 'error' || status?.state === 'stopped'
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-logo">
          <LogoMark style={{ '--logo-glint': 'var(--brand-to)' } as React.CSSProperties} />
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
            <h1>Watchdog could not start its engine</h1>
            <p>The program that does Watchdog’s work did not start. Try again first. If it keeps failing, repair the engine, which reinstalls it.</p>
            {status?.message && (
              <details style={{ textAlign: 'left', width: '100%' }}>
                <summary className="faint" style={{ cursor: 'pointer', fontSize: 12 }}>What happened</summary>
                <pre className="log" style={{ maxHeight: 180, marginTop: 8 }}>{status.message}{'\n'}{status.stderrTail.join('\n')}</pre>
              </details>
            )}
            <div className="row" style={{ marginTop: 6 }}>
              <Button variant="primary" icon={RotateCw} loading={busy} onClick={() => act(window.watchdog.backend.restart)}>
                Try again
              </Button>
              {engine?.canInstall && (
                <Button icon={Wrench} onClick={() => void window.watchdog.engine.reinstall()}>
                  Repair the engine
                </Button>
              )}
              <Button icon={FolderSearch} onClick={() => act(window.watchdog.backend.choosePython)}>
                Choose Python…
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
