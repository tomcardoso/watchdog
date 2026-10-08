// Models & keys: how Watchdog signs in to Claude and to each model provider (`watchdog settings auth`).

import { CheckCircle2, KeyRound, Trash2, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { AuthStatus } from '@shared/api'
import { Badge, Button, Callout, ErrorNote, Segmented, Skeleton } from '@renderer/components/ui'
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

function KeyRow({ provider, label, stored, onStatus }: { provider: string; label: string; stored?: Extended['keys'][number]; onStatus: (s: AuthStatus) => void }) {
  const [editing, setEditing] = useState(false)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const act = async (fn: () => Promise<AuthStatus>, ok: string) => {
    setBusy(true)
    try {
      const s = await fn()
      onStatus(s)
      setEditing(false)
      setKey('')
      const w = (s as unknown as { warning?: string | null }).warning
      toast({ kind: w ? 'info' : 'success', title: ok, body: w ?? undefined })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change the key', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const tone = stored?.in_use === 'in use' ? 'success' : undefined
  return (
    <div className="set-keyrow">
      <div className="set-keyrow-main">
        <div className="grow">
          <div style={{ fontWeight: 580 }}>{label}</div>
          <div className="faint mono" style={{ fontSize: 11.5 }}>{ENV[provider]}</div>
        </div>
        {stored ? (
          <>
            <span className="mono muted">{stored.masked}</span>
            <Badge tone={tone}>{stored.in_use}</Badge>
            <Badge tip={stored.source === 'env' ? 'Set in your environment; it takes precedence over a stored key' : 'Stored in Watchdog’s credentials file'}>{stored.source === 'env' ? 'environment' : 'stored'}</Badge>
          </>
        ) : (
          <span className="faint">No key</span>
        )}
        <Button size="sm" icon={KeyRound} onClick={() => setEditing(!editing)}>
          {stored ? 'Replace' : 'Add'}
        </Button>
        {stored?.source === 'stored' && (
          <Button
            size="sm"
            variant="ghost"
            icon={Trash2}
            tip="Delete the stored key"
            onClick={async () => {
              const ok = await window.watchdog.dialog.confirm({ title: `Delete the ${label} key?`, message: 'The stored key is removed from this computer. You can add it again later.', confirm: 'Delete key', destructive: true })
              if (ok) void act(() => call('auth.deleteKey', { provider }), 'Key deleted')
            }}
          />
        )}
      </div>
      {editing && (
        <div className="set-input-row" style={{ marginTop: 8 }}>
          <input className="input" type="password" autoComplete="off" autoFocus placeholder={`Paste the ${label} key`} value={key} onChange={(e) => setKey(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && key.trim() && void act(() => call('auth.setKey', { provider, key }), 'Key saved')} />
          <Button variant="primary" loading={busy} disabled={!key.trim()} onClick={() => void act(() => call('auth.setKey', { provider, key }), 'Key saved')}>
            Save key
          </Button>
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
            <div className="card-title">Claude Code</div>
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
          <Callout tone="warning" title="Claude Code is not installed" style={{ margin: '0 18px 16px' }}>
            Ask, Research and context seeding need it. Install it from claude.ai/download, then sign in.
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
            <KeyRow key={p.provider} provider={p.provider} label={p.label} stored={s.keys.find((k) => k.provider === p.provider)} onStatus={setStatus} />
          ))}
        </div>
        <div className="set-foot">
          A key set in your environment (for example <span className="mono">OPENAI_API_KEY</span>) always takes precedence over a stored one, and cannot be removed here. A stored Anthropic key is only used while Claude is in API-key mode.
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
