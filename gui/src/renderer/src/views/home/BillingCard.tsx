// Billing on the investigation's Overview: which of this computer's labelled keys pays for each
// provider here (D290). The choice is saved in the investigation's folder, so it travels with
// it. The card stays out of the way in the common case: it appears only when a provider this
// investigation uses has more than one key, when a choice has been made, or when a chosen key is
// missing from this computer (then the next run stops before sending anything).

import { CircleAlert, Wallet } from 'lucide-react'
import { useState } from 'react'
import type { InvestigationKeys } from '@shared/api'
import { Button, Callout } from '@renderer/components/ui'
import { call, errorMessage, invalidate, queryClient, useRpc } from '@renderer/lib/rpc'
import { navigate, toast } from '@renderer/lib/store'

type Row = InvestigationKeys['providers'][number]

/** Whether the provider's row belongs on the card. Claude counts only in API-key mode. */
function relevant(r: Row, claudeMode: string | null): boolean {
  if (r.chosen || r.missing) return true
  if (r.provider === 'anthropic' && claudeMode !== 'api-key') return false
  return r.used && r.keys.length > 1
}

export function BillingCard({ vault }: { vault: string }) {
  const { data } = useRpc('auth.investigationKeys', { vault })
  const [busy, setBusy] = useState<string | null>(null)
  if (!data) return null
  const rows = data.providers.filter((r) => relevant(r, data.claude_mode))
  if (rows.length === 0) return null

  const choose = async (provider: string, id: string | null) => {
    setBusy(provider)
    try {
      queryClient.setQueryData(['auth.investigationKeys', { vault }], await call('auth.chooseKey', { vault, provider, id }))
      invalidate('ingest.preflight', 'contradictions.estimate')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change the key', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card home-panel home-billing">
      <h3>
        <Wallet />
        Billing
      </h3>
      <p className="home-billing-sub">Which account pays for model calls in this investigation.</p>
      {rows.map((r) => {
        const def = r.keys.find((k) => k.default)
        const value = r.missing ? '__missing' : r.chosen ? (r.keys.find((k) => k.id === r.chosen!.id || k.label.toLowerCase() === (r.chosen!.label ?? '').toLowerCase())?.id ?? '') : ''
        return (
          <div key={r.provider} className="home-billing-row">
            <label className="home-billing-label" htmlFor={`bill-${r.provider}`}>{r.provider_label}</label>
            <select id={`bill-${r.provider}`} className="select" value={value} disabled={busy === r.provider || r.env} onChange={(e) => void choose(r.provider, e.target.value || null)}>
              {r.missing && <option value="__missing">{r.chosen?.label ?? 'A key'} (not on this computer)</option>}
              <option value="">{def ? `Default (${def.label})` : 'Default'}</option>
              {r.keys.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.label}
                </option>
              ))}
            </select>
            {r.env && <div className="home-billing-note">A key set in your environment pays, whatever is chosen here.</div>}
            {r.provider === 'anthropic' && data.claude_mode !== 'api-key' && <div className="home-billing-note">Claude uses your subscription now; this choice applies in API-key mode.</div>}
            {r.missing && (
              <Callout tone="warning" style={{ marginTop: 6 }} action={<Button size="sm" variant="ghost" onClick={() => navigate({ view: 'settings', tab: 'auth' })}>Models & keys</Button>}>
                <span className="row" style={{ gap: 6, alignItems: 'flex-start' }}>
                  <CircleAlert style={{ width: 14, height: 14, flex: 'none', marginTop: 2 }} />
                  <span>Adding documents will stop before anything is sent. Add a {r.provider_label} key named “{r.chosen?.label}”, or choose another key.</span>
                </span>
              </Callout>
            )}
          </div>
        )
      })}
    </section>
  )
}
