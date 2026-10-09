// Which labelled key will pay for a run (D290), shown wherever a run's cost is confirmed:
// Add documents, the public-records pause and the re-check estimate. With one key per provider
// there is nothing to choose, so nothing is shown. A chosen key that isn't on this computer is
// shown as a stop: the run would end before sending anything rather than bill another account.

import { Wallet } from 'lucide-react'
import type { Billing } from '@shared/api'
import { Button, Callout } from '@renderer/components/ui'
import { navigate } from '@renderer/lib/store'

/** A chosen key this computer lacks, if any: the run can't start until it is fixed. */
export const billingBlocked = (billing?: Billing[]) => (billing ?? []).find((b) => b.missing) ?? null

const shown = (b: Billing) => b.several || b.source === 'chosen' || b.source === 'env'

const name = (b: Billing) => (b.source === 'env' ? 'the key in your environment' : b.label ?? '—')

export function BillingNote({ billing, onSettings }: { billing?: Billing[]; onSettings?: () => void }) {
  const blocked = billingBlocked(billing)
  if (blocked) {
    return (
      <Callout
        tone="danger"
        title="The key this investigation bills isn’t on this computer"
        action={<Button size="sm" onClick={() => { onSettings?.(); navigate({ view: 'settings', tab: 'auth' }) }}>Models & keys</Button>}
      >
        {blocked.message}
      </Callout>
    )
  }
  const rows = (billing ?? []).filter(shown)
  if (rows.length === 0) return null
  return (
    <div className="billing-note">
      <Wallet />
      <span>
        Billed to{' '}
        {rows.map((b, i) => (
          <span key={b.provider}>
            {i > 0 && (i === rows.length - 1 ? ' and ' : ', ')}
            <b>{name(b)}</b>
            <span className="muted"> ({b.provider_label})</span>
          </span>
        ))}
        .
      </span>
    </div>
  )
}
