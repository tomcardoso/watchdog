// What a screen shows in place of adding documents while the engine's background setup runs
// (D272): the plain reason, how far along it is, and a way to the details or a retry.

import { FilePlus2 } from 'lucide-react'
import { Callout, Button, Progress } from '@renderer/components/ui'
import { ENGINE_WAIT, finishSetup, useEngineGate } from '@renderer/lib/engine'
import { navigate, useApp } from '@renderer/lib/store'
import type { CSSProperties, ReactNode } from 'react'

export function EngineWait({ style, children }: { style?: CSSProperties; children?: ReactNode }) {
  const gate = useEngineGate()
  if (gate.ready) return null
  const details = () => {
    useApp.getState().closeAdd()
    navigate({ view: 'settings', tab: 'setup' })
  }
  return (
    <Callout
      tone={gate.stalled ? 'warning' : 'info'}
      title={gate.stalled ? 'Setup did not finish' : 'Still setting up'}
      style={style}
      action={
        gate.stalled ? (
          <Button size="sm" onClick={finishSetup}>Retry</Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={details}>Details</Button>
        )
      }
    >
      {gate.stalled ? 'Watchdog could not finish downloading what it needs to read documents. Try again, or open Settings, Setup for details.' : ENGINE_WAIT}
      {children}
      {!gate.stalled && <Progress value={gate.fraction} indeterminate={!gate.finishing} style={{ marginTop: 10, maxWidth: 320 }} />}
    </Callout>
  )
}

/** A disabled control's explanation on hover, without replacing the control's own label. */
export function WaitTip({ reason, children }: { reason: string | undefined; children: ReactNode }) {
  if (!reason) return <>{children}</>
  return (
    <span data-tip={reason} data-tip-pos="bottom-end" style={{ display: 'inline-flex' }}>
      {children}
    </span>
  )
}

/** "Add documents", for empty screens: disabled, with the reason on hover, until setup finishes. */
export function AddDocumentsButton() {
  const gate = useEngineGate()
  return (
    <WaitTip reason={gate.reason}>
      <Button variant="primary" icon={FilePlus2} disabled={!gate.ready} onClick={() => useApp.getState().openAdd()}>
        Add documents
      </Button>
    </WaitTip>
  )
}
