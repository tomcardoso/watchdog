// Models & keys: how Watchdog signs in to Claude and to each model provider (`watchdog settings auth`).

import { CheckCircle2, KeyRound, MoreHorizontal, Pencil, Plus, Star, Trash2, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { AuthStatus, LabelledKey } from '@shared/api'
import { Badge, Button, Callout, Dropdown, ErrorNote, Segmented, Skeleton } from '@renderer/components/ui'
import { plural } from '@renderer/lib/format'
import { providerName } from '@renderer/components/ModelPicker'
import { call, errorMessage, queryClient, useRpc } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'

type Extended = AuthStatus & {
  claude: AuthStatus['claude'] & { env_key_set?: boolean; key_masked?: string | null }
  providers?: { provider: string; label: string; env: string; requires_key: boolean; base_url_setting: string | null; base_url: string | null; ready: boolean }[]
  base_urls?: { provider: string; url: string }[]
}
const FALLBACK = ['anthropic', 'openai', 'deepseek', 'gemini', 'openrouter', 'local']
const ENV: Record<string, string> = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', deepseek: 'DEEPSEEK_API_KEY', gemini: 'GEMINI_API_KEY', openrouter: 'OPENROUTER_API_KEY', local: 'LOCAL_API_KEY' }

type KeyForm = { kind: 'replace'; id?: string } | { kind: 'add' } | { kind: 'rename'; id: string; label: string }

// One provider's keys. The common case, a single key, reads as it always has: the masked key,
// Replace and Delete. "Add another" opens a named second key (D290); once a provider has
// more than one, each key shows on its own line with its name, and one is the default.
function KeyRow({ provider, label, stored, keys, onStatus }: { provider: string; label: string; stored?: Extended['keys'][number]; keys: LabelledKey[]; onStatus: (s: AuthStatus) => void }) {
  const [form, setForm] = useState<KeyForm | null>(null)
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const open = (f: KeyForm | null) => {
    setForm(f)
    setKey('')
    setName(f?.kind === 'rename' ? f.label : '')
    setErr(null)
  }
  const act = async (fn: () => Promise<AuthStatus>, ok: string) => {
    setBusy(true)
    try {
      const s = await fn()
      onStatus(s)
      open(null)
      const w = (s as unknown as { warning?: string | null }).warning
      toast({ kind: w ? 'info' : 'success', title: ok, body: w ?? undefined })
    } catch (e) {
      setErr(errorMessage(e))
      if (!form) toast({ kind: 'error', title: 'Could not change the key', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const remove = async (k: LabelledKey | null) => {
    const users = k?.users ?? []
    const named = keys.length > 1 && k ? `the ${k.label} key` : `the ${label} key`
    const message = users.length
      ? `${users.join(', ')} ${users.length === 1 ? 'is' : 'are'} set to bill this key. ${users.length === 1 ? 'Its' : 'Their'} next run will stop, without sending anything, until you add a key with the same name or choose another key in ${users.length === 1 ? 'that investigation' : 'those investigations'}.`
      : 'The stored key is removed from this computer. You can add it again later.'
    const ok = await window.watchdog.dialog.confirm({ title: `Delete ${named}?`, message, confirm: 'Delete key', destructive: true })
    if (ok) void act(() => call('auth.deleteKey', k ? { provider, id: k.id } : { provider }), 'Key deleted')
  }
  const submit = () => {
    if (!form) return
    if (form.kind === 'add') void act(() => call('auth.addKey', { provider, label: name, key }), 'Key added')
    else if (form.kind === 'rename') void act(() => call('auth.renameKey', { provider, id: form.id, label: name }), 'Key renamed')
    else void act(() => call('auth.setKey', form.id ? { provider, key, id: form.id } : { provider, key }), 'Key saved')
  }
  const ready = form?.kind === 'add' ? !!key.trim() && !!name.trim() : form?.kind === 'rename' ? !!name.trim() : !!key.trim()
  const tone = stored?.in_use === 'in use' ? 'success' : undefined
  const env = stored?.source === 'env'
  const several = keys.length > 1 || (keys.length === 1 && keys[0].label !== 'Default')
  return (
    <div className="set-keyrow">
      <div className="set-keyrow-main">
        <div className="grow">
          <div style={{ fontWeight: 580 }}>{label}</div>
          <div className="faint mono" style={{ fontSize: 11.5 }}>{ENV[provider]}</div>
        </div>
        {stored ? (
          <>
            {!several && <span className="mono muted">{stored.masked}</span>}
            <Badge tone={tone}>{stored.in_use}</Badge>
            {(env || !several) && <Badge tip={env ? 'Set in your environment; it takes precedence over every stored key' : 'Stored in Watchdog’s credentials file'}>{env ? 'environment' : 'stored'}</Badge>}
          </>
        ) : (
          <span className="faint">No key</span>
        )}
        {!several && (
          <Button size="sm" icon={KeyRound} onClick={() => open(form?.kind === 'replace' ? null : { kind: 'replace' })}>
            {keys.length ? 'Replace' : 'Add'}
          </Button>
        )}
        {keys.length > 0 && (
          <Button size="sm" variant="ghost" icon={Plus} tip="Add another key for this provider, with a name, to bill a different account" onClick={() => open(form?.kind === 'add' ? null : { kind: 'add' })}>
            Add another
          </Button>
        )}
        {!several && keys.length > 0 && <Button size="sm" variant="ghost" icon={Trash2} tip="Delete the stored key" onClick={() => void remove(null)} />}
      </div>
      {several && (
        <div className="set-keylist">
          {keys.map((k) => (
            <div key={k.id} className="set-keyitem">
              <span className="set-keyitem-name">{k.label}</span>
              <span className="mono muted">{k.masked}</span>
              {k.default && <Badge tone="accent" tip="Used by every investigation that hasn’t chosen another key">Default</Badge>}
              {(k.users?.length ?? 0) > 0 && <span className="faint set-keyitem-users" title={k.users!.join(', ')}>{plural(k.users!.length, 'investigation')}</span>}
              <span className="spacer" />
              <Dropdown
                align="right"
                trigger={(o) => <Button size="sm" variant="ghost" icon={MoreHorizontal} tip={`Change the ${k.label} key`} onClick={o} />}
                items={[
                  { label: 'Make default', icon: Star, disabled: k.default, onClick: () => void act(() => call('auth.setDefaultKey', { provider, id: k.id }), `${k.label} is now the default`) },
                  { label: 'Rename', icon: Pencil, onClick: () => open({ kind: 'rename', id: k.id, label: k.label }) },
                  { label: 'Replace the key', icon: KeyRound, onClick: () => open({ kind: 'replace', id: k.id }) },
                  { separator: true, label: '' },
                  { label: 'Delete', icon: Trash2, danger: true, onClick: () => void remove(k) },
                ]}
              />
            </div>
          ))}
        </div>
      )}
      {form && (
        <div className="col" style={{ gap: 6, marginTop: 8 }}>
          <div className="set-input-row">
            {(form.kind === 'add' || form.kind === 'rename') && (
              <input className="input set-keyname" autoFocus maxLength={40} placeholder="Name, such as Work" value={name} onChange={(e) => { setName(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && ready && submit()} />
            )}
            {form.kind !== 'rename' && (
              <input className="input" type="password" autoComplete="off" autoFocus={form.kind === 'replace'} placeholder={form.kind === 'replace' && form.id ? `Paste the new ${keys.find((k) => k.id === form.id)?.label ?? ''} key` : `Paste the ${label} key`} value={key} onChange={(e) => { setKey(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && ready && submit()} />
            )}
            <Button variant="primary" loading={busy} disabled={!ready} onClick={submit}>
              {form.kind === 'rename' ? 'Rename' : 'Save key'}
            </Button>
            <Button variant="ghost" onClick={() => open(null)}>Cancel</Button>
          </div>
          {form.kind === 'add' && <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{keys.length === 1 ? `Your current key keeps working as the default. Each investigation can choose which key it bills, on its Overview.` : 'Each investigation can choose which key it bills, on its Overview.'}</div>}
          {err && <div className="field-error">{err}</div>}
        </div>
      )}
    </div>
  )
}

function BaseUrl({ provider, url, onStatus }: { provider: 'local' | 'openrouter'; url: string; onStatus: (s: AuthStatus) => void }) {
  const [v, setV] = useState(url)
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <div className="set-field" style={{ padding: '10px 0' }}>
      <div className="set-field-text">
        <div className="set-field-label">{providerName(provider)} base URL</div>
        <div className="set-field-short">{provider === 'local' ? 'An OpenAI-compatible server you run yourself.' : 'Change only to point at an OpenRouter-compatible proxy.'}</div>
      </div>
      <div className="set-field-control">
        <div className="set-input-row">
          <input className="input" value={v} placeholder={provider === 'local' ? 'http://localhost:8080/v1' : 'https://openrouter.ai/api/v1'} onChange={(e) => { setV(e.target.value); setErr(null) }} />
          {v !== url && (
            <Button
              variant="primary"
              loading={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  onStatus(await call('auth.setBaseUrl', { provider, url: v }))
                } catch (e) {
                  setErr(errorMessage(e))
                } finally {
                  setBusy(false)
                }
              }}
            >
              Save
            </Button>
          )}
        </div>
        {err && <div className="field-error">{err}</div>}
      </div>
    </div>
  )
}

export default function AuthPanel() {
  const q = useRpc('auth.status', {})
  const info = useRpc('app.info', {})
  const [apiKey, setApiKey] = useState('')
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const setStatus = (s: AuthStatus) => {
    queryClient.setQueryData(['auth.status', {}], s)
    void queryClient.invalidateQueries({ queryKey: ['settings.schema'] })
  }
  if (q.isLoading) return <Skeleton h={400} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const s = q.data as Extended
  const mode = s.claude.mode === 'api-key' ? 'api-key' : 'subscription'
  const providers = (s.providers?.map((p) => ({ provider: p.provider, label: p.label })) ?? FALLBACK.map((p) => ({ provider: p, label: providerName(p) })))
  const urls = Object.fromEntries((s.base_urls ?? []).map((b) => [b.provider, b.url]))

  const switchMode = async (m: 'subscription' | 'api-key', key?: string) => {
    setBusy(true)
    try {
      setStatus(await call('auth.setAnthropicMode', { mode: m, key }))
      setAsking(false)
      setApiKey('')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not switch', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="col" style={{ gap: 22 }}>
      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Claude</div>
            <div className="card-sub">Required for Ask and Research, and the default for adding documents.</div>
          </div>
          <span className="spacer" />
          {s.claude.logged_in ? <Badge tone="success" icon={CheckCircle2}>{mode === 'subscription' ? 'Signed in' : 'Key stored'}</Badge> : <Badge tone="warning" icon={XCircle}>{mode === 'subscription' ? 'Not signed in' : 'No key'}</Badge>}
        </div>
        <div className="set-field" style={{ borderTop: 'none' }}>
          <div className="set-field-text">
            <div className="set-field-label">How Claude is billed</div>
            <div className="set-field-short">A subscription is not metered. An API key bills per token, to your Anthropic account.</div>
          </div>
          <div className="set-field-control">
            <Segmented
              value={asking ? 'api-key' : mode}
              onChange={(m) => {
                if (m === 'subscription') void switchMode('subscription')
                else if (s.claude.key_masked) void switchMode('api-key')
                else setAsking(true)
              }}
              options={[{ value: 'subscription', label: 'Subscription' }, { value: 'api-key', label: 'API key' }]}
            />
            {asking && (
              <div className="set-input-row">
                <input className="input" type="password" autoFocus autoComplete="off" placeholder="Paste your Anthropic API key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                <Button variant="primary" loading={busy} disabled={!apiKey.trim()} onClick={() => void switchMode('api-key', apiKey)}>
                  Use this key
                </Button>
              </div>
            )}
            {s.claude.key_masked && mode === 'api-key' && <div className="set-meta"><span className="mono">{s.claude.key_masked}</span></div>}
          </div>
        </div>
        {s.claude.reason && <Callout tone="warning" style={{ margin: '0 18px 16px' }}>{s.claude.reason}</Callout>}
        {info.data && !info.data.claude_code.installed && (
          <Callout tone="warning" title="Claude is missing from the engine" style={{ margin: '0 18px 16px' }}>
            Ask, Research and context seeding need it. It comes with the engine: repair the engine under Settings → Setup, then sign in.
          </Callout>
        )}
      </section>

      <section className="card" style={{ overflow: 'hidden' }}>
        <div className="set-card-head">
          <div>
            <div className="card-title">Processing stages</div>
            <div className="card-sub">Which provider each step runs on, and whether it is ready. Change models under Models in the sections list.</div>
          </div>
        </div>
        <table className="table">
          <thead>
            <tr><th>Stage</th><th>Model</th><th>Provider</th><th>Ready</th><th>Billing</th></tr>
          </thead>
          <tbody>
            {s.stages.map((st) => (
              <tr key={st.stage}>
                <td style={{ textTransform: 'capitalize', fontWeight: 560 }}>{st.stage}</td>
                <td className="mono">{st.value}</td>
                <td>{providerName(st.provider)}</td>
                <td>{st.ready ? <Badge tone="success" icon={CheckCircle2}>Ready</Badge> : <Badge tone="danger" icon={XCircle}>Not ready</Badge>}</td>
                <td className="muted">{st.billing === 'subscription' ? 'Subscription' : st.billing === 'api-key' ? 'Metered' : st.provider === 'anthropic' ? '—' : 'Metered'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Provider keys</div>
            <div className="card-sub">Stored on this computer in a file only you can read. Never shown in full.</div>
          </div>
        </div>
        <div style={{ padding: '0 18px' }}>
          {providers.map((p) => (
            <KeyRow key={p.provider} provider={p.provider} label={p.label} stored={s.keys.find((k) => k.provider === p.provider)} keys={s.key_sets?.[p.provider] ?? []} onStatus={setStatus} />
          ))}
        </div>
        <div className="set-foot">
          To bill different accounts with the same provider, add another key and give each a name; each investigation then chooses which one it bills on its Overview, and uses the default otherwise. A key set in your environment (for example <span className="mono">OPENAI_API_KEY</span>) always takes precedence over every stored one, and cannot be removed here. A stored Anthropic key is only used while Claude is in API-key mode.
        </div>
      </section>

      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Custom endpoints</div>
            <div className="card-sub">For a self-hosted model or an OpenRouter-compatible proxy.</div>
          </div>
        </div>
        <div style={{ padding: '0 18px 8px' }}>
          <BaseUrl key={`l-${urls.local ?? ''}`} provider="local" url={urls.local ?? ''} onStatus={setStatus} />
          <BaseUrl key={`o-${urls.openrouter ?? ''}`} provider="openrouter" url={urls.openrouter ?? ''} onStatus={setStatus} />
        </div>
      </section>
    </div>
  )
}
