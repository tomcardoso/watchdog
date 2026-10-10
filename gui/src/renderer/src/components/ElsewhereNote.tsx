// A prominent note on a dialog raised by a background run whose investigation is not the one
// open: it names the run's investigation and offers to switch to it. Renders nothing when the
// run's investigation is the open one.

import { FolderOpen } from 'lucide-react'
import { Button, Callout } from '@renderer/components/ui'
import { investigationName, switchTo } from '@renderer/lib/investigation'
import { useApp } from '@renderer/lib/store'

export function ElsewhereNote({ vault, what }: { vault: string | null | undefined; what: string }) {
  const open = useApp((s) => s.project)
  if (!vault || open?.path === vault) return null
  const name = investigationName(vault)
  return (
    <Callout
      tone="warning"
      title={`This is for ${name}`}
      action={
        <Button size="sm" icon={FolderOpen} onClick={() => void switchTo(vault)}>
          Switch to {name}
        </Button>
      }
    >
      {what} belongs to {name}, not to {open?.name ?? 'the investigation that is open'}. It stays with {name} whichever investigation you have open.
    </Callout>
  )
}
