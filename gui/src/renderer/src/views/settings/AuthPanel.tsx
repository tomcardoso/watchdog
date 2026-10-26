// Models & keys: how Watchdog signs in to Claude and to each model provider.

import { CheckCircle2, Gauge, KeyRound, MoreHorizontal, Pencil, Plus, RotateCcw, Trash2, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { AuthStatus, KeyStorage, LabelledKey } from '@shared/api'
import { Badge, Button, Callout, Dropdown, ErrorNote, Skeleton } from '@renderer/components/ui'
import { plural } from '@renderer/lib/format'
import { ModelPicker, modelLabel, providerName } from '@renderer/components/ModelPicker'
import { ClaudeSignIn } from '@renderer/components/ClaudeSignIn'
import { call, errorMessage, queryClient, useRpc } from '@renderer/lib/rpc'
import { toast } from '@renderer/lib/store'

type Extended = AuthStatus & {
  claude: AuthStatus['claude'] & { env_key_set?: boolean; key_masked?: string | null; key_label?: string | null; key_source?: 'env' | 'stored' | null }
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
function KeyRow({ provider, label, stored, keys, pays, opening, onClosed, onStatus }: { provider: string; label: string; stored?: Extended['keys'][number]; keys: LabelledKey[]; pays: (k: LabelledKey) => string[]; opening: number; onClosed: () => void; onStatus: (s: AuthStatus) => void }) {
  const [form, setForm] = useState<KeyForm | null>(null)
  const [key, setKey] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const row = useRef<HTMLDivElement>(null)
  // "Add a key" (under Keys, or in a Paid with menu) lands here, with the add form open.
  useEffect(() => {
    if (!opening) return
    open({ kind: 'add' })
    row.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opening])
  const open = (f: KeyForm | null) => {
    if (!f) onClosed()
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
    <div className="set-keyrow" ref={row}>
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
        {keys.length > 0 && <Button size="sm" variant={keys.length ? 'ghost' : undefined} icon={keys.length ? Plus : KeyRound} tip={keys.length ? 'Add another key for this provider, with a name, to bill a different account' : undefined} onClick={() => open(form?.kind === 'add' ? null : { kind: 'add' })}>
          {keys.length ? 'Add key' : 'Add'}
        </Button>}
      </div>
      {keys.length > 0 && (
        <div className="set-keylist">
          {keys.map((k) => {
            const lim = k.limit
            return (
              <div key={k.id} className="set-keyitem">
                <span className="set-keyitem-name">{k.label}</span>
                <span className="mono muted">{k.masked}</span>
                {!env && pays(k).map((w) => <Badge key={w} tone="success" tip="Chosen under Paid with, below">Pays for {w}</Badge>)}
                {(k.users?.length ?? 0) > 0 && <Badge tip={`Chosen on the Overview of ${k.users!.join(', ')}`}>{plural(k.users!.length, 'investigation')}</Badge>}
                {!env && !pays(k).length && !(k.users?.length) && <Badge>Not used</Badge>}
                {k.locked ? (
                  <Badge tone="warning" tip="Encrypted under another user account or computer; replace or delete it">can’t be read</Badge>
                ) : (
                  <Badge tip={k.encrypted ? 'Stored encrypted with a key kept in your system’s secure storage' : 'Stored in a file only your user account can read'}>{k.encrypted ? 'encrypted' : 'not encrypted'}</Badge>
                )}
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
              <input className="input set-keyname" autoFocus maxLength={40} placeholder={keys.length || form.kind === 'rename' ? 'Name, such as Work' : 'Name (optional)'} value={name} onChange={(e) => { setName(e.target.value); setErr(null) }} onKeyDown={(e) => e.key === 'Enter' && ready && submit()} />
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
  housekeeping: ['Finish: housekeeping', 'Matching, contradictions, the timeline, dates'],
  writing: ['Finish: writing', 'Entity summaries and the briefing']
}

// A step that can't run yet, as a badge: neutral while nothing has been chosen to pay for its
// provider (no Claude billing chosen, or no key for another provider), red once a choice was made
// that can't work (a key that can't be read, a subscription that isn't signed in).
function NotReady({ s, provider }: { s: Extended; provider: string }) {
  const unchosen = provider === 'anthropic' ? !s.claude.mode : !(s.key_sets?.[provider]?.length) && !s.keys.some((k) => k.provider === provider)
  if (unchosen) return <Badge tip="Choose what pays for it under Paid with">{provider === 'anthropic' ? 'Not set up' : 'Needs a key'}</Badge>
  return <Badge tone="danger" icon={XCircle} tip="What pays for it can’t be used: see Paid with">Not ready</Badge>
}

// Each step's model, chosen here (D347): the picker writes the step's own setting, and "Use the
// default" clears it. What pays for each step is under Paid with, below.
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
          <div className="card-sub">The model for each step of adding documents. Each step can use any provider.</div>
        </div>
      </div>
      <table className="table">
        <thead>
          <tr><th>Step</th><th>Model</th><th>Ready</th></tr>
        </thead>
        <tbody>
          {s.stages.map((st) => {
            const [title, sub] = STEP_NAMES[st.stage] ?? [st.stage, '']
            return (
              <tr key={st.stage}>
                <td><div style={{ fontWeight: 560 }}>{title}</div><div className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{sub}</div></td>
                <td style={{ minWidth: 260 }}>
                  <ModelPicker compact value={st.value} models={models} emptyLabel="Use the default" onChange={(v) => void pick(st.config_key, v)} disabled={busy === st.config_key} />
                  {/* A catalogued model's name already says whose it is; a typed-in id or the default may not. */}
                  {!(st.value && modelLabel(st.value, models)) && <div className="faint" style={{ fontSize: 'var(--fs-sm)', marginTop: 3 }}>{providerName(st.provider)}</div>}
                </td>
                <td>{st.ready ? <Badge tone="success" icon={CheckCircle2}>Ready</Badge> : <NotReady s={s} provider={st.provider} />}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

// A key's name with its provider, so a menu or a line never leaves the account in doubt.
const keyName = (k: { label: string }, provider: string) => `${k.label} (${provider === 'anthropic' ? 'Anthropic' : providerName(provider)} key)`

// Keys are only added under Keys: "Add a key…" scrolls there and opens that provider's form.
const goAddKey = (provider: string) => window.dispatchEvent(new CustomEvent('wd-add-key', { detail: provider }))

// What pays for adding documents, one line per provider its steps use, and for Ask Claude and
// Research (D348). A provider's line picks its default key; Claude's lines also offer the
// subscription. A provider with one key shows it as text: there is nothing to choose.
function PaidWithCard({ s, onStatus }: { s: Extended; onStatus: (s: AuthStatus) => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const run = async (what: string, fn: () => Promise<AuthStatus>) => {
    setBusy(what)
    try {
      onStatus(await fn())
    } catch (e) {
      toast({ kind: 'error', title: 'Could not change what pays', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  const anthKeys = s.key_sets?.anthropic ?? []
  const envKey = (p: string) => s.keys.find((k) => k.provider === p)?.source === 'env'
  const providers = [...new Set(s.stages.map((st) => st.provider))]
  const covers = (p: string) => s.stages.filter((st) => st.provider === p).map((st) => (STEP_NAMES[st.stage] ?? [st.stage])[0]).join(', ')
  const ready = (p: string) => s.stages.filter((st) => st.provider === p).every((st) => st.ready)

  // Claude, for the steps: the subscription, or an Anthropic key (the default one).
  const docClaude = s.claude.mode === 'subscription' ? 'sub' : s.claude.mode === 'api-key' ? (envKey('anthropic') ? 'env' : anthKeys.find((k) => k.default)?.id ?? '') : ''
  const setDocClaude = (v: string) => {
    if (v === '+') return goAddKey('anthropic')
    void run('doc-anthropic', async () => {
      if (v !== 'sub' && v !== 'env') await call('auth.setDefaultKey', { provider: 'anthropic', id: v })
      return call('auth.setAnthropicMode', { mode: v === 'sub' ? 'subscription' : 'api-key' })
    })
  }
  // Claude, for Ask Claude and Research: the same choices, kept on their own.
  const sessionMode = s.claude.session_mode ?? s.claude.mode
  const askClaude = sessionMode === 'subscription' ? 'sub' : sessionMode === 'api-key' ? (envKey('anthropic') ? 'env' : s.claude.session_key_id ?? '') : ''
  const setAskClaude = (v: string) => {
    if (v === '+') return goAddKey('anthropic')
    void run('ask', () => call('auth.setSessionMode', v === 'sub' ? { mode: 'subscription' } : v === 'env' ? { mode: 'api-key' } : { mode: 'api-key', id: v }))
  }
  const claudeMenu = (value: string, onChange: (v: string) => void, what: string, label: string) => (
    <select className="select set-paysel" value={value} disabled={busy === what} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      {!value && <option value="" disabled>Choose…</option>}
      <option value="sub">Claude subscription</option>
      {envKey('anthropic')
        ? <option value="env">Environment key (Anthropic)</option>
        : anthKeys.map((k) => <option key={k.id} value={k.id}>{keyName(k, 'anthropic')}</option>)}
      <option value="+">Add a key…</option>
    </select>
  )
  const askReady = sessionMode === 'subscription' ? !!s.claude.signed_in && !s.claude.session_reason : sessionMode === 'api-key' && (envKey('anthropic') || anthKeys.length > 0)
  const signIn = !s.claude.signed_in && ((providers.includes('anthropic') && s.claude.mode === 'subscription') || sessionMode === 'subscription')

  const line = (p: string) => {
    const keys = s.key_sets?.[p] ?? []
    let control
    if (p === 'anthropic') control = claudeMenu(docClaude, setDocClaude, 'doc-anthropic', 'What pays for the steps on Claude')
    else if (envKey(p)) control = <span>Environment key <span className="faint">({providerName(p)} key)</span></span>
    else if (keys.length > 1) control = (
      <select className="select set-paysel" value={keys.find((k) => k.default)?.id ?? keys[0].id} disabled={busy === `doc-${p}`} aria-label={`Which ${providerName(p)} key pays`}
        onChange={(e) => e.target.value === '+' ? goAddKey(p) : void run(`doc-${p}`, () => call('auth.setDefaultKey', { provider: p, id: e.target.value }))}>
        {keys.map((k) => <option key={k.id} value={k.id}>{keyName(k, p)}</option>)}
        <option value="+">Add a key…</option>
      </select>
    )
    else if (keys.length === 1) control = <span>{keyName(keys[0], p)} <span className="faint">· your only one</span></span>
    else control = <Button size="sm" icon={KeyRound} onClick={() => goAddKey(p)}>Add a {providerName(p)} key</Button>
    return (
      <div key={p} className="set-payline">
        <span className="set-payprov">{providerName(p).replace(/ \(Anthropic\)$/, '')}</span>
        <span className="faint">{covers(p)}</span>
        <span>{control}</span>
        {ready(p) ? <Badge tone="success" icon={CheckCircle2}>Ready</Badge> : <NotReady s={s} provider={p} />}
      </div>
    )
  }
  return (
    <section className="card set-card">
      <div className="set-card-head">
        <div>
          <div className="card-title">Paid with</div>
          <div className="card-sub">Which subscription or key pays for each kind of work. Keys are added, named and limited under Keys, above.</div>
        </div>
      </div>
      <div className="set-paygroup">
        <div className="set-field-label">Adding documents</div>
        {providers.map(line)}
      </div>
      <div className="set-paygroup">
        <div className="set-field-label">Ask Claude and Research</div>
        <div className="set-payline">
          <span className="set-payprov">Claude</span>
          <span className="faint">Questions and web research, which always run on Claude</span>
          <span>{claudeMenu(askClaude, setAskClaude, 'ask', 'What pays for Ask Claude and Research')}</span>
          {askReady ? <Badge tone="success" icon={CheckCircle2}>Ready</Badge> : askClaude ? <Badge tone="danger" icon={XCircle}>Not ready</Badge> : <Badge>Not set up</Badge>}
        </div>
      </div>
      {signIn && (
        <div style={{ margin: '0 18px 16px' }}>
          <ClaudeSignIn onSignedIn={() => void queryClient.invalidateQueries({ queryKey: ['auth.status', {}] })} />
        </div>
      )}
      {providers.includes('anthropic') && s.claude.mode && s.claude.reason && !signIn && <Callout tone="warning" style={{ margin: '0 18px 16px' }}>{s.claude.reason}</Callout>}
      {s.claude.session_reason && !signIn && <Callout tone="warning" style={{ margin: '0 18px 16px' }}>{s.claude.session_reason}</Callout>}
      <div className="set-foot">An investigation can bill a different key from its Overview. A Claude subscription is not metered, but a large batch can reach your plan’s limit; an API key bills per token, with no usage limit.</div>
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
  // The provider whose add form is open, counted so asking again reopens it. Keys lists only the
  // providers with a key, so this one is shown too until the form closes.
  const [adding, setAdding] = useState<{ provider: string; n: number } | null>(null)
  useEffect(() => {
    const onAdd = (e: Event) => setAdding((a) => ({ provider: (e as CustomEvent<string>).detail, n: (a?.n ?? 0) + 1 }))
    window.addEventListener('wd-add-key', onAdd)
    return () => window.removeEventListener('wd-add-key', onAdd)
  }, [])
  const setStatus = (s: AuthStatus) => {
    queryClient.setQueryData(['auth.status', {}], s)
    void queryClient.invalidateQueries({ queryKey: ['settings.schema'] })
  }
  if (q.isLoading) return <Skeleton h={400} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const s = q.data as Extended
  const providers = (s.providers?.map((p) => ({ provider: p.provider, label: p.label })) ?? FALLBACK.map((p) => ({ provider: p, label: providerName(p) })))
  const urls = Object.fromEntries((s.base_urls ?? []).map((b) => [b.provider, b.url]))
  // Keys lists the providers with a key (stored or in the environment), and the one being added.
  const hasKey = (p: string) => (s.key_sets?.[p]?.length ?? 0) > 0 || s.keys.some((k) => k.provider === p)
  const listed = providers.filter((p) => hasKey(p.provider) || adding?.provider === p.provider)
  const unlisted = providers.filter((p) => !listed.includes(p))
  // What a stored key pays for, from the choices under Paid with: a provider's default key pays
  // for its steps (Claude's only on an API key), and Ask Claude's own key for questions.
  const stepProviders = new Set(s.stages.map((st) => st.provider))
  const sessionMode = s.claude.session_mode ?? s.claude.mode
  const pays = (p: string, k: LabelledKey) => [
    ...(k.default && stepProviders.has(p) && (p !== 'anthropic' || s.claude.mode === 'api-key') ? ['adding documents'] : []),
    ...(p === 'anthropic' && sessionMode === 'api-key' && s.claude.session_key_id === k.id ? ['Ask Claude'] : [])
  ]

  return (
    <div className="col" style={{ gap: 22 }}>
      <StepsCard s={s} onStatus={setStatus} />
      {info.data && !info.data.claude_code.installed && (
        <Callout tone="warning" title="Claude is missing from the engine">
          Ask, Research and context seeding need it. It comes with the engine: repair the engine under Settings → Setup, then sign in.
        </Callout>
      )}

      <section className="card set-card">
        <div className="set-card-head">
          <div>
            <div className="card-title">Keys</div>
            <div className="card-sub set-keystore" data-store={s.storage?.store ?? 'unknown'}>{storageLine(s.storage)}</div>
          </div>
          <span className="spacer" />
          {unlisted.length > 0 && (
            <Dropdown
              align="right"
              trigger={(o) => <Button size="sm" icon={Plus} onClick={o}>Add a key</Button>}
              items={unlisted.map((p) => ({ label: p.label, onClick: () => goAddKey(p.provider) }))}
            />
          )}
        </div>
        {/* The storage warning is about keys, so it waits until there is one. */}
        {listed.length > 0 && <StorageNotes st={s.storage} />}
        <div style={{ padding: '0 18px' }}>
          {listed.length === 0 && <div className="faint" style={{ padding: '6px 0 14px' }}>No keys yet. Add one for each provider your models use.</div>}
          {listed.map((p) => (
            <KeyRow key={p.provider} provider={p.provider} label={p.label} stored={s.keys.find((k) => k.provider === p.provider)} keys={s.key_sets?.[p.provider] ?? []} pays={(k) => pays(p.provider, k)}
              opening={adding?.provider === p.provider ? adding.n : 0} onClosed={() => setAdding((a) => (a?.provider === p.provider ? null : a))} onStatus={setStatus} />
          ))}
        </div>
        <div className="set-foot">
          To bill different accounts with the same provider, add another key and give each a name; each investigation can choose which one it bills on its Overview, and otherwise uses the one chosen under Paid with. A key set in your environment (for example <span className="mono">OPENAI_API_KEY</span>) always takes precedence over every stored one, and cannot be removed here. Each key can have a spending limit, from its menu: Watchdog stops before sending anything more on a key that reaches it, counting what it paid for in every investigation on this computer. Use a key made for Watchdog, with a limit set with the provider too, which is the one it enforces.
        </div>
      </section>

      <PaidWithCard s={s} onStatus={setStatus} />

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
