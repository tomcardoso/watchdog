// Models & keys: how Watchdog signs in to Claude and to each model provider.

import { CheckCircle2, Gauge, KeyRound, MoreHorizontal, Pencil, Plus, RotateCcw, Star, Trash2, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { AuthStatus, KeyStorage, LabelledKey } from '@shared/api'
import { Badge, Button, Callout, Dropdown, ErrorNote, Segmented, Skeleton } from '@renderer/components/ui'
import { plural } from '@renderer/lib/format'
import { ModelPicker, providerName } from '@renderer/components/ModelPicker'
import { ClaudeSignIn } from '@renderer/components/ClaudeSignIn'
import { call, errorMessage, queryClient, useRpc } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'

type Extended = AuthStatus & {
  claude: AuthStatus['claude'] & { env_key_set?: boolean; key_masked?: string | null; key_label?: string | null }
  providers?: { provider: string; label: string; env: string; requires_key: boolean; base_url_setting: string | null; base_url: string | null; ready: boolean }[]
  base_urls?: { provider: string; url: string }[]
}
const FALLBACK = ['anthropic', 'openai', 'deepseek', 'gemini', 'openrouter', 'local']
const ENV: Record<string, string> = { anthropic: 'ANTHROPIC_API_KEY', openai: 'OPENAI_API_KEY', deepseek: 'DEEPSEEK_API_KEY', gemini: 'GEMINI_API_KEY', openrouter: 'OPENROUTER_API_KEY', local: 'LOCAL_API_KEY' }

// Where keys are kept (D295), in the words the Provider keys card uses.
function storageLine(st: KeyStorage | undefined): string {
  if (st?.store === 'encrypted') {
    if (st.backend === 'keychain') return 'Encrypted on this computer with a key kept in your macOS Keychain. Never shown in full.'
    if (st.backend === 'dpapi') return 'Encrypted on this computer with Windows’ protection for your user account. Never shown in full.'
    return 'Encrypted on this computer with a key kept in your system keyring. Never shown in full.'
  }
  if (st?.store === 'plaintext') return 'Not encrypted: kept in a file on this computer that only your user account can read. Never shown in full.'
  return 'Stored on this computer in a file only you can read. Never shown in full.'
}

function StorageNotes({ st }: { st: KeyStorage | undefined }) {
  if (!st) return null
  const linux = st.backend !== 'keychain' && st.backend !== 'dpapi'
  return (
    <>
      {st.store === 'plaintext' && (
        <Callout tone="warning" title="Your keys are not encrypted on this computer" style={{ margin: '0 18px 12px' }}>
          Watchdog found no secure storage it can use here, so your keys are kept unencrypted in a file only your user account can read. Anyone who can sign in as you, or who has a copy of your home folder, such as a backup, could read them.
          {linux ? ' On Linux, installing and unlocking a keyring such as GNOME Keyring or KWallet, then restarting Watchdog, lets it encrypt them.' : ''} Use keys with a monthly spending limit.
        </Callout>
      )}
      {st.locked > 0 && (
        <Callout tone="warning" title={`${st.locked === 1 ? 'One stored key' : `${st.locked} stored keys`} can’t be read on this computer`} style={{ margin: '0 18px 12px' }}>
          {st.locked === 1 ? 'It was' : 'They were'} encrypted by Watchdog under another user account or computer, or the system’s secure storage was reset. Replace or delete {st.locked === 1 ? 'it' : 'them'} below. Until then, anything that needs {st.locked === 1 ? 'it' : 'them'} stops without sending anything.
        </Callout>
      )}
    </>
  )
}

type KeyForm = { kind: 'replace'; id?: string } | { kind: 'add' } | { kind: 'rename'; id: string; label: string } | { kind: 'limit'; id: string; label: string; usd: number | null }

const usd = (n: number) => `$${n.toFixed(2)}`

// One provider's keys (D290, D347). Every stored key has its own line, whether it is the only one
// or not: its name, the masked key, whether it is in use and encrypted, its spending limit and
// what it has spent, and a menu to make it the default, rename it, set or reset its limit, replace
// or delete it. "Add key" adds another, with a name.
function KeyRow({ provider, label, stored, keys, onStatus }: { provider: string; label: string; stored?: Extended['keys'][number]; keys: LabelledKey[]; onStatus: (s: AuthStatus) => void }) {
  const [form, setForm] = useState<KeyForm | null>(null)
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const open = (f: KeyForm | null) => {
    setForm(f)
    setKey('')
    setName(f?.kind === 'rename' ? f.label : f?.kind === 'limit' && f.usd != null ? String(f.usd) : '')
    setErr(null)
  }
  const act = async (fn: () => Promise<AuthStatus>, ok: string) => {
    setBusy(true)
    try {
      const s = await fn()
      onStatus(s)
      open(null)
      const r = s as unknown as { warning?: string | null; adopted?: string[] }
      const moved = r.adopted?.length ? `Steps that had no way to run now use ${label}'s recommended model; change them under Models above.` : null
      toast({ kind: r.warning ? 'info' : 'success', title: ok, body: [r.warning, moved].filter(Boolean).join(' ') || undefined })
    } catch (e) {
      setErr(errorMessage(e))
      if (!form) toast({ kind: 'error', title: 'Could not change the key', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const remove = async (k: LabelledKey) => {
    const users = k.users ?? []
    const message = users.length
      ? `${users.join(', ')} ${users.length === 1 ? 'is' : 'are'} set to bill this key. ${users.length === 1 ? 'Its' : 'Their'} next run will stop, without sending anything, until you add a key with the same name or choose another key in ${users.length === 1 ? 'that investigation' : 'those investigations'}.`
      : 'The stored key is removed from this computer. You can add it again later.'
    const ok = await window.watchdog.dialog.confirm({ title: `Delete the ${k.label} key?`, message, confirm: 'Delete key', destructive: true })
    if (ok) void act(() => call('auth.deleteKey', { provider, id: k.id }), 'Key deleted')
  }
  const submit = () => {
    if (!form) return
    if (form.kind === 'add') void act(() => call('auth.addKey', { provider, label: name || (keys.length ? '' : 'Default'), key }), 'Key added')
    else if (form.kind === 'rename') void act(() => call('auth.renameKey', { provider, id: form.id, label: name }), 'Key renamed')
    else if (form.kind === 'limit') void act(() => call('auth.setKeyLimit', { provider, id: form.id, usd: name.trim() || null }), name.trim() ? 'Spending limit set' : 'Spending limit removed')
    else void act(() => call('auth.setKey', form.id ? { provider, key, id: form.id } : { provider, key }), 'Key saved')
  }
  const ready = form?.kind === 'add' ? !!key.trim() && (!!name.trim() || keys.length === 0) : form?.kind === 'rename' ? !!name.trim() : form?.kind === 'limit' ? true : !!key.trim()
  const env = stored?.source === 'env'
  return (
    <div className="set-keyrow">
      <div className="set-keyrow-main">
        <div className="grow">
          <div style={{ fontWeight: 580 }}>{label}</div>
          <div className="faint mono" style={{ fontSize: 11.5 }}>{ENV[provider]}</div>
        </div>
        {env && (
          <>
            <span className="mono muted">{stored!.masked}</span>
            <Badge tone={stored!.in_use === 'in use' ? 'success' : undefined}>{stored!.in_use}</Badge>
            <Badge tip="Set in your environment; it takes precedence over every stored key">environment</Badge>
          </>
        )}
        {!env && keys.length === 0 && <span className="faint">No key</span>}
        <Button size="sm" variant={keys.length ? 'ghost' : undefined} icon={keys.length ? Plus : KeyRound} tip={keys.length ? 'Add another key for this provider, with a name, to bill a different account' : undefined} onClick={() => open(form?.kind === 'add' ? null : { kind: 'add' })}>
          {keys.length ? 'Add key' : 'Add'}
        </Button>
      </div>
      {keys.length > 0 && (
        <div className="set-keylist">
          {keys.map((k) => {
            const lim = k.limit
            return (
              <div key={k.id} className="set-keyitem">
                <span className="set-keyitem-name">{k.label}</span>
                <span className="mono muted">{k.masked}</span>
                {k.default && !env && stored && <Badge tone={stored.in_use === 'in use' ? 'success' : undefined} tip="Whether a step, or Ask Claude, uses this provider now">{stored.in_use}</Badge>}
                {!k.default && <Badge tip="Used only by investigations that chose it on their Overview">{(k.users?.length ?? 0) > 0 ? plural(k.users!.length, 'investigation') : 'unused'}</Badge>}
                {k.locked ? (
                  <Badge tone="warning" tip="Encrypted under another user account or computer; replace or delete it">can’t be read</Badge>
                ) : (
                  <Badge tip={k.encrypted ? 'Stored encrypted with a key kept in your system’s secure storage' : 'Stored in a file only your user account can read'}>{k.encrypted ? 'encrypted' : 'not encrypted'}</Badge>
                )}
                {k.default && <Badge tone="accent" tip="Used by every investigation that hasn’t chosen another key">Default</Badge>}
                <span className="spacer" />
                {lim?.usd != null ? (
                  <span className={lim.reached ? 'set-keylimit reached' : 'set-keylimit'} title="Counted across every investigation on this computer since the limit was set or reset">
                    {usd(lim.spent)} of {usd(lim.usd)}{lim.reached ? ' · limit reached' : ''}
                  </span>
                ) : (
                  <span className="faint set-keylimit">No limit</span>
                )}
                <Dropdown
                  align="right"
                  trigger={(o) => <Button size="sm" variant="ghost" icon={MoreHorizontal} tip={`Change the ${k.label} key`} onClick={o} />}
                  items={[
                    { label: 'Make default', icon: Star, disabled: k.default, onClick: () => void act(() => call('auth.setDefaultKey', { provider, id: k.id }), `${k.label} is now the default`) },
                    { label: 'Rename', icon: Pencil, onClick: () => open({ kind: 'rename', id: k.id, label: k.label }) },
                    { label: lim?.usd != null ? 'Change spending limit' : 'Set a spending limit', icon: Gauge, onClick: () => open({ kind: 'limit', id: k.id, label: k.label, usd: lim?.usd ?? null }) },
                    ...(lim?.usd != null ? [{ label: 'Reset the count', icon: RotateCcw, onClick: () => void act(() => call('auth.resetKeyLimit', { provider, id: k.id }), 'Count reset') }] : []),
                    { label: 'Replace the key', icon: KeyRound, onClick: () => open({ kind: 'replace', id: k.id }) },
                    { separator: true, label: '' },
                    { label: 'Delete', icon: Trash2, danger: true, onClick: () => void remove(k) },
                  ]}
                />
              </div>
            )
          })}
        </div>
      )}
      {form && (
        <div className="col" style={{ gap: 6, marginTop: 8 }}>
          <div className="set-input-row">
            {(form.kind === 'add' || form.kind === 'rename') && (
              <input className="input set-keyname" autoFocus maxLength={40} placeholder={keys.length || form.kind === 'rename' ? 'Name, such as Work' : 'Name (optional), such as Work'} value={name} onChange={(e) => { setName(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && ready && submit()} />
            )}
            {form.kind === 'limit' && (
              <input className="input set-keyname" autoFocus inputMode="decimal" placeholder="Limit in US$, such as 50" value={name} onChange={(e) => { setName(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && submit()} />
            )}
            {(form.kind === 'add' || form.kind === 'replace') && (
              <input className="input" type="password" autoComplete="off" autoFocus={form.kind === 'replace'} placeholder={form.kind === 'replace' && form.id ? `Paste the new ${keys.find((k) => k.id === form.id)?.label ?? ''} key` : `Paste the ${label} key`} value={key} onChange={(e) => { setKey(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && ready && submit()} />
            )}
            <Button variant="primary" loading={busy} disabled={!ready} onClick={submit}>
              {form.kind === 'rename' ? 'Rename' : form.kind === 'limit' ? 'Save limit' : 'Save key'}
            </Button>
            <Button variant="ghost" onClick={() => open(null)}>Cancel</Button>
          </div>
          {form.kind === 'add' && keys.length > 0 && <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Your current default keeps working. Each investigation can choose which key it bills, on its Overview.</div>}
          {form.kind === 'limit' && <div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>What {form.label} pays for in Watchdog on this computer, across every investigation, from now. Once it is reached, processing and Ask Claude stop before sending anything more on this key. Leave it blank for no limit. A limit set with {label} itself is still the one it enforces.</div>}
          {err && <div className="field-error">{err}</div>}
        </div>
      )}
    </div>
  )
}

const STEP_NAMES: Record<string, [string, string]> = {
  classifier: ['Classify', 'What kind of record each document is'],
  extractor: ['Extract', 'Reads each document and lists the facts'],
  writing: ['Finish: writing', 'Entity summaries and the briefing'],
  housekeeping: ['Finish: housekeeping', 'Matching, contradictions, the timeline, dates']
}

// Each step's model, chosen here, and what pays for it (D347). The model picker writes the step's
// own setting; clearing it falls back to the default shown. "Paid with" names the provider's
// default key (or the Claude subscription); an investigation can bill another on its Overview.
function StepsCard({ s, onStatus }: { s: Extended; onStatus: (s: AuthStatus) => void }) {
  const models = useRpc('settings.models', {}).data?.models ?? []
  const [busy, setBusy] = useState<string | null>(null)
  const pick = async (key: string, value: string) => {
    setBusy(key)
    try {
      await call('settings.set', { key, value })
      onStatus(await call('auth.status', {}))
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change the model', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  return (
    <section className="card" style={{ overflow: 'hidden' }}>
      <div className="set-card-head">
        <div>
          <div className="card-title">Models</div>
          <div className="card-sub">Each step can use any provider. Pick its model here; it is paid for with that provider’s default key, or your Claude subscription. An investigation can bill another key on its Overview.</div>
        </div>
      </div>
      <table className="table">
        <thead>
          <tr><th>Step</th><th>Model</th><th>Paid with</th><th>Ready</th></tr>
        </thead>
        <tbody>
          {s.stages.map((st) => {
            const [title, sub] = STEP_NAMES[st.stage] ?? [st.stage, '']
            return (
              <tr key={st.stage}>
                <td><div style={{ fontWeight: 560 }}>{title}</div><div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{sub}</div></td>
                <td style={{ minWidth: 220 }}>
                  <ModelPicker compact value={st.value} models={models} emptyLabel="Use the default" onChange={(v) => void pick(st.config_key, v)} disabled={busy === st.config_key} />
                  <div className="faint" style={{ fontSize: 'var(--fs-sm)', marginTop: 3 }}>{providerName(st.provider)}</div>
                </td>
                <td>{st.paid_with ? <span>{st.paid_with}{st.paid_with !== 'Claude subscription' && <span className="faint"> · {providerName(st.provider)}</span>}</span> : <span className="faint">No {providerName(st.provider)} key yet</span>}</td>
                <td>{st.ready ? <Badge tone="success" icon={CheckCircle2}>Ready</Badge> : <Badge tone="danger" icon={XCircle}>Not ready</Badge>}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
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
  const [askingSession, setAskingSession] = useState(false)
  const [sessionKey, setSessionKey] = useState('')
  const [busy, setBusy] = useState(false)
  const setStatus = (s: AuthStatus) => {
    queryClient.setQueryData(['auth.status', {}], s)
    void queryClient.invalidateQueries({ queryKey: ['settings.schema'] })
  }
  if (q.isLoading) return <Skeleton h={400} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const s = q.data as Extended
  // Neither toggle is pressed until a choice is made: an unset mode is not a subscription.
  const asMode = (m: string | null | undefined) => (m === 'api-key' || m === 'subscription' ? m : '') as 'subscription' | 'api-key'
  const mode = asMode(s.claude.mode)
  const sessionMode = asMode(s.claude.session_mode ?? s.claude.mode)
  // Whether a processing step runs on Claude now. With none, Claude matters only to Ask Claude and
  // Research, so an unset Claude is a note rather than a warning.
  const claudeNeeded = s.stages.some((st) => st.provider === 'anthropic')
  const needsSignIn = (mode === 'subscription' || sessionMode === 'subscription' || (!mode && claudeNeeded)) && !s.claude.signed_in
  const ready = s.claude.logged_in && !s.claude.session_reason
  const unset = !s.claude.mode && !s.claude.session_mode
  // With no step on Claude, processing is paid with each provider's API key, so the processing
  // toggle shows API key until a choice is saved. Only shown: saving it would also move Ask Claude,
  // which follows processing until set on its own (D318).
  const processingShown = mode || (claudeNeeded ? mode : 'api-key')
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
  // Ask Claude and Research have their own billing (D318); an API key for them is stored as the
  // default Anthropic key, the same one processing uses on a key.
  const switchSessionMode = async (m: 'subscription' | 'api-key', key?: string) => {
    setBusy(true)
    try {
      if (key?.trim()) await call('auth.setKey', { provider: 'anthropic', key: key.trim() })
      setStatus(await call('auth.setSessionMode', { mode: m }))
      setAskingSession(false)
      setSessionKey('')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not switch', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="col" style={{ gap: 22 }}>
      <StepsCard s={s} onStatus={setStatus} />
      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Billing</div>
            <div className="card-sub">How adding documents and Ask Claude pay for their models. A step on OpenAI, Gemini, DeepSeek or another provider is billed per token to that provider’s key, under Provider keys below.</div>
          </div>
          <span className="spacer" />
          {/* Claude's readiness: Ask Claude always needs it, and so does any stage set to a Claude model. */}
          {ready ? <Badge tone="success" icon={CheckCircle2}>Claude ready</Badge> : claudeNeeded || !unset ? <Badge tone="warning" icon={XCircle}>Claude not ready</Badge> : <Badge>Claude not set up</Badge>}
        </div>
        <div className="set-field" style={{ borderTop: 'none' }}>
          <div className="set-field-text">
            <div className="set-field-label">Adding documents</div>
            <div className="set-field-short">
              {s.stages.every((st) => st.provider === 'anthropic')
                ? 'Every processing stage runs on Claude, which can be paid for two ways.'
                : s.stages.some((st) => st.provider === 'anthropic')
                  ? 'Stages on other providers are billed to their own keys. The stages on Claude can be paid for two ways.'
                  : 'No step runs on Claude at the moment: each is paid with its provider’s API key, under Models above. This choice applies once you set a step to a Claude model.'}{' '}
              An API key bills per token, to your Anthropic account, with no usage limit; recommended. A Claude subscription is not metered, but a large batch can reach your plan’s limit.
            </div>
          </div>
          <div className="set-field-control">
            <Segmented
              value={asking ? 'api-key' : processingShown}
              onChange={(m) => {
                if (m === processingShown) return
                if (m === 'subscription') void switchMode('subscription')
                else if (s.claude.key_masked) void switchMode('api-key')
                else setAsking(true)
              }}
              options={[{ value: 'subscription', label: 'Claude subscription' }, { value: 'api-key', label: 'API key' }]}
            />
            {asking && (
              <div className="set-input-row">
                <input className="input" type="password" autoFocus autoComplete="off" placeholder="No Anthropic key is stored yet: paste one" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
                <Button variant="primary" loading={busy} disabled={!apiKey.trim()} onClick={() => void switchMode('api-key', apiKey)}>
                  Use this key
                </Button>
              </div>
            )}
            {s.claude.key_masked && mode === 'api-key' && <div className="set-meta">{s.claude.key_label && <span>{s.claude.key_label}, the default key</span>}<span className="mono">{s.claude.key_masked}</span></div>}
          </div>
        </div>
        <div className="set-field">
          <div className="set-field-text">
            <div className="set-field-label">Ask Claude and Research</div>
            <div className="set-field-short">Questions and web research always run on Claude. A Claude subscription covers them with no charge per question; recommended if you have one.</div>
          </div>
          <div className="set-field-control">
            <Segmented
              value={askingSession ? 'api-key' : sessionMode}
              onChange={(m) => {
                if (m === 'subscription') void switchSessionMode('subscription')
                else if (s.claude.key_masked) void switchSessionMode('api-key')
                else setAskingSession(true)
              }}
              options={[{ value: 'subscription', label: 'Claude subscription' }, { value: 'api-key', label: 'API key' }]}
            />
            {askingSession && (
              <div className="set-input-row">
                <input className="input" type="password" autoFocus autoComplete="off" placeholder="No Anthropic key is stored yet: paste one" value={sessionKey} onChange={(e) => setSessionKey(e.target.value)} />
                <Button variant="primary" loading={busy} disabled={!sessionKey.trim()} onClick={() => void switchSessionMode('api-key', sessionKey)}>
                  Use this key
                </Button>
              </div>
            )}
          </div>
        </div>
        {needsSignIn && (
          <div style={{ margin: '0 18px 16px' }}>
            <ClaudeSignIn onSignedIn={() => void queryClient.invalidateQueries({ queryKey: ['auth.status', {}] })} />
          </div>
        )}
        {unset && !claudeNeeded ? (
          <div className="faint" style={{ margin: '0 18px 16px', fontSize: 'var(--fs-sm)' }}>Ask Claude and Research run on Claude. To use them, choose how to pay above.</div>
        ) : s.claude.reason && <Callout tone="warning" style={{ margin: '0 18px 16px' }}>{s.claude.reason}</Callout>}
        {s.claude.session_reason && <Callout tone="warning" style={{ margin: '0 18px 16px' }}>{s.claude.session_reason}</Callout>}
        {info.data && !info.data.claude_code.installed && (
          <Callout tone="warning" title="Claude is missing from the engine" style={{ margin: '0 18px 16px' }}>
            Ask, Research and context seeding need it. It comes with the engine: repair the engine under Settings → Setup, then sign in.
          </Callout>
        )}
      </section>


      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Provider keys</div>
            <div className="card-sub set-keystore" data-store={s.storage?.store ?? 'unknown'}>{storageLine(s.storage)}</div>
          </div>
        </div>
        <StorageNotes st={s.storage} />
        <div style={{ padding: '0 18px' }}>
          {providers.map((p) => (
            <KeyRow key={p.provider} provider={p.provider} label={p.label} stored={s.keys.find((k) => k.provider === p.provider)} keys={s.key_sets?.[p.provider] ?? []} onStatus={setStatus} />
          ))}
        </div>
        <div className="set-foot">
          To bill different accounts with the same provider, add another key and give each a name; each investigation then chooses which one it bills on its Overview, and uses the default otherwise. A key set in your environment (for example <span className="mono">OPENAI_API_KEY</span>) always takes precedence over every stored one, and cannot be removed here. A stored Anthropic key is only used for what is set to API key above. Each key can have a spending limit, from its menu: Watchdog stops before sending anything more on a key that reaches it, counting what it paid for in every investigation on this computer. Use a key made for Watchdog, with a limit set with the provider too, which is the one it enforces.
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
