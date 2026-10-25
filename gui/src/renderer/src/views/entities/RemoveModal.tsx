// Remove entities from the investigation (the `hide-entities` operation, D326), and bring them back
// (`unhide-entities`). Hiding is a view overlay: nothing a document says is changed, and the
// entity's facts still reach the AI-written summaries, briefings and contradiction checks.

import { EyeOff } from 'lucide-react'
import { useState } from 'react'
import { Button, Modal } from '@renderer/components/ui'
import { plural } from '@renderer/lib/format'
import { runAction } from '@renderer/lib/jobs'
import { errorMessage, invalidate } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'

type Target = { id: string; name: string }

function names(list: Target[]): string {
  if (list.length === 1) return list[0].name
  if (list.length <= 3) return `${list.slice(0, -1).map((e) => e.name).join(', ')} and ${list[list.length - 1].name}`
  return `${list.slice(0, 2).map((e) => e.name).join(', ')} and ${list.length - 2} more`
}

function refresh() {
  invalidate('vault.', 'review.', 'search.')
}

export default function RemoveModal({ open, onClose, entities, onDone }: { open: boolean; onClose: () => void; entities: Target[]; onDone?: () => void }) {
  const [busy, setBusy] = useState(false)
  const subject = entities.length === 1 ? entities[0].name : plural(entities.length, 'entity', 'entities')
  const go = async () => {
    setBusy(true)
    try {
      await runAction('hide-entities', { ids: entities.map((e) => e.id) })
      refresh()
      toast({ kind: 'success', title: `Removed ${names(entities)}`, body: 'Bring it back from Entities → Removed at any time.' })
      onClose()
      onDone?.()
    } catch (err) {
      toast({ kind: 'error', title: 'Could not remove it', body: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        <span className="row" style={{ gap: 8 }}>
          <EyeOff style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Remove from investigation
        </span>
      }
      sub={`${subject.replace(/\.$/, '')}.`}
      footer={
        <>
          <Button autoFocus onClick={onClose}>
            Cancel
          </Button>
          <Button variant="danger" icon={EyeOff} loading={busy} disabled={entities.length === 0} onClick={() => void go()}>
            Remove {entities.length === 1 ? '' : plural(entities.length, 'entity', 'entities')}
          </Button>
        </>
      }
    >
      <div className="col" style={{ gap: 10 }}>
        <p style={{ margin: 0 }}>
          Use this for something the documents name that is not part of your story, such as an office on a letterhead or your own organization. {entities.length === 1 ? 'It' : 'They'} will no longer appear in the Network, the Entities list, Search, Review or the Overview counts, and lines to {entities.length === 1 ? 'it' : 'them'} are not drawn. Documents added later that name {entities.length === 1 ? 'it' : 'them'} do not bring {entities.length === 1 ? 'it' : 'them'} back.
        </p>
        <p className="muted" style={{ margin: 0 }}>
          Nothing is deleted. The facts stay in the document notes, other entities keep their relationships to {entities.length === 1 ? 'it' : 'them'}, and the AI model still reads those facts when it writes summaries and briefings and looks for contradictions. Bring {entities.length === 1 ? 'it' : 'them'} back at any time from Entities → Removed.
        </p>
      </div>
    </Modal>
  )
}

/** Bring removed entities back into the views (`unhide-entities`). Resolves true when it worked. */
export async function bringBack(list: Target[]): Promise<boolean> {
  try {
    await runAction('unhide-entities', { ids: list.map((e) => e.id) })
    refresh()
    toast({ kind: 'success', title: `Brought back ${names(list)}` })
    return true
  } catch (err) {
    toast({ kind: 'error', title: 'Could not bring it back', body: errorMessage(err) })
    return false
  }
}
