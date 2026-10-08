// First-run setup. A calm, full-window sequence that replaces `pipx install watchdog-intel` and
// `watchdog setup`: install the engine, choose where investigations live, connect a model provider,
// answer the auto-approve question, then start. Progress is remembered (preferences 'onboarding'),
// so closing the app part-way and reopening returns to the right step.

import { ArrowLeft, Check, CheckCircle2, FolderOpen, KeyRound, ShieldCheck, Sparkles } from 'lucide-react'
import { ReactNode, useEffect, useMemo, useState } from 'react'
import type { AuthStatus, BackendStatus, ModelChoice } from '@shared/api'
import { Button, Callout, Field, Spinner, cx } from '@renderer/components/ui'
import { LogoMark } from '@renderer/components/Logo'
import { ModelPicker, providerName } from '@renderer/components/ModelPicker'
import { call, errorMessage, useEvent, useRpc } from '@renderer/lib/rpc'
import { EngineActions, EngineProgressView, ENGINE_DISK_TEXT, ENGINE_DOWNLOAD_TEXT, ENGINE_FIRST_TEXT, useEngine } from './EngineInstall'
import './onboarding.css'

type Step = 'welcome' | 'engine' | 'folder' | 'provider' | 'approve' | 'done'
const FIRST_RUN: Step[] = ['welcome', 'engine', 'folder', 'provider', 'approve', 'done']
const TITLES: Record<Step, string> = {
  welcome: 'Welcome',
  engine: 'Install',
  folder: 'Investigations',
  provider: 'Model',
  approve: 'Approval',
  done: 'Ready'
}

export type OnboardingMode = 'first-run' | 'repair' | 'update'

interface Props {
  backend: BackendStatus | null
  mode: OnboardingMode
  /** WATCHDOG_FORCE_ONBOARDING: a step id to start on. */
  forceStep?: string | null
  onDone: (openNewInvestigation?: boolean) => void
  onBackendAction: (fn: () => Promise<BackendStatus>) => Promise<void>
}

function Frame({ step, steps, children, footer, wide }: { step: Step; steps: Step[]; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  const i = steps.indexOf(step)
  return (
    <div className="onb">
      <header className="onb-top">
        <div className="onb-brand">
          <LogoMark />
          <span>Watchdog</span>
        </div>
        {steps.length > 1 && (
          <ol className="onb-dots" aria-label={`Step ${i + 1} of ${steps.length}: ${TITLES[step]}`}>
            {steps.map((s, n) => (
              <li key={s} className={cx(n === i && 'on', n < i && 'past')} aria-current={n === i ? 'step' : undefined}>
                <span />
                {TITLES[s]}
              </li>
            ))}
          </ol>
        )}
        <div className="onb-top-end" />
      </header>
      <main className="onb-main">
        <section className={cx('onb-card', wide && 'wide')}>
          {children}
          {footer && <div className="onb-footer">{footer}</div>}
        </section>
      </main>
    </div>
  )
}

function Choice({ on, onClick, title, children, badge }: { on: boolean; onClick: () => void; title: string; children: ReactNode; badge?: string }) {
  return (
    <button type="button" className="onb-option" aria-pressed={on} onClick={onClick}>
      <span className="onb-radio">{on && <Check />}</span>
      <span className="grow">
        <span className="onb-option-title">
          {title}
          {badge && <span className="onb-badge">{badge}</span>}
        </span>
        <span className="onb-option-body">{children}</span>
      </span>
    </button>
  )
}

// ── a. welcome ───────────────────────────────────────────────────────────────

function Welcome({ next }: { next: () => void }) {
  return (
    <>
      <h1>Welcome to Watchdog</h1>
      <p className="onb-lead">
        Watchdog reads the public records you give it, such as court filings, contracts and reports, and builds a linked, sourced
        investigation from them: who is named, what each document says, and where records agree or conflict. It runs on your computer.
      </p>
      <Callout tone="info" title="Public records only">
        Documents you add are sent to the model provider you choose so that they can be read. Add only records that are public. Watchdog
        asks you to confirm this before each run.
      </Callout>
      <p className="onb-note">Setup takes a few minutes, most of it a one-time download. You will need an internet connection.</p>
      <div className="onb-footer">
        <Button variant="primary" size="lg" onClick={next}>Continue</Button>
      </div>
    </>
  )
}

// ── b. engine ────────────────────────────────────────────────────────────────

function EngineStep({ backend, mode, next, back, onBackendAction }: { backend: BackendStatus | null; mode: OnboardingMode; next: () => void; back?: () => void; onBackendAction: Props['onBackendAction'] }) {
  const eng = useEngine()
  const { status, run } = eng
  const running = run?.state === 'running'
  const external = status?.usingExternal ?? null
  // Phase 1 is all setup waits for (D272): once the backend runs on it, the rest carries on in the
  // background while the person continues.
  const inBackground = running && run?.phase === 2
  const coreReady = backend?.state === 'ready' && (inBackground || (!running && (run?.state === 'done' || status?.engine === 'ready' || !!external)))
  const broken = status?.engine === 'ready' && backend?.state === 'error' && !running && run?.state !== 'done'
  const title = mode === 'update' ? 'Updating the Watchdog engine' : mode === 'repair' ? 'Repair the Watchdog engine' : 'Install the Watchdog engine'

  useEffect(() => {
    // An update after a new version of the app needs no decision from the person.
    if (mode === 'update' && status && !status.run.steps.some((s) => s.state !== 'pending') && !running && status.canInstall) void eng.start(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, status?.engine])

  // Move on by itself the moment phase 1 is in place during this visit.
  const [advanced, setAdvanced] = useState(false)
  useEffect(() => {
    if (advanced || !coreReady || !run || run.state === 'idle' || mode === 'update') return
    setAdvanced(true)
    next()
  }, [coreReady, run, advanced, mode, next])

  // With a Python the app did not install, only the models remain: start them and carry on.
  const continueExternal = () => {
    if (status && !status.complete && status.canInstall) void window.watchdog.engine.install()
    next()
  }

  return (
    <>
      <h1>{title}</h1>
      {!run || run.state === 'idle' ? (
        <div className="col gap-16">
          {external ? (
            <p className="onb-lead">
              Watchdog is already installed on this computer, at <span className="mono selectable">{external}</span>. It still needs its local models for document
              conversion, name detection and search, a one-time download of about 4 GB. They download in the background while you finish setting up.
            </p>
          ) : (
            <p className="onb-lead">
              Watchdog needs Python, a set of libraries and several models that run on your computer. It keeps them in the app’s own folder, so nothing else on
              your computer changes. The first part, <strong>{ENGINE_FIRST_TEXT}</strong>, takes a minute or two; then you can carry on while the rest,{' '}
              {ENGINE_DOWNLOAD_TEXT}, downloads in the background. It needs an internet connection and {ENGINE_DISK_TEXT} of free space.
            </p>
          )}
          <ul className="onb-list">
            <li><strong>Python and Watchdog</strong>, the program that does the work. Setup waits for this part.</li>
            <li><strong>Document conversion (Docling)</strong>, which reads PDFs and scans, including text in images.</li>
            <li><strong>Name detection (GLiNER)</strong>, which finds people, organizations and places on your computer before any model is asked.</li>
            <li><strong>Search</strong>, an embedding model and a reranker that rank passages by meaning.</li>
          </ul>
          <p className="onb-note">You can add documents once the background download has finished. A small bar at the bottom of the sidebar shows how far along it is.</p>
          {broken && (
            <Callout tone="danger" title="The engine is installed but did not start">
              <span className="selectable" style={{ whiteSpace: 'pre-wrap' }}>{backend?.message}</span>
            </Callout>
          )}
          {status && !status.canInstall && !external && (
            <Callout tone="danger" title="This copy of the app cannot install the engine">The installer files are missing from the app. Download the app again from the Watchdog releases page.</Callout>
          )}
        </div>
      ) : (
        <EngineProgressView eng={eng} backend={backend} focus={1} />
      )}
      <div className="onb-footer">
        {back && !running && <Button variant="ghost" icon={ArrowLeft} onClick={back}>Back</Button>}
        <span className="spacer" />
        {broken && (
          <>
            <Button onClick={() => void onBackendAction(window.watchdog.backend.restart)}>Try again</Button>
            <Button onClick={() => void eng.start(true)}>Repair the engine</Button>
            <Button variant="ghost" onClick={() => void onBackendAction(window.watchdog.backend.choosePython)}>Choose Python…</Button>
          </>
        )}
        {!coreReady && !broken && <EngineActions eng={eng} primary="Install" onFresh={() => void eng.start(true)} />}
        {coreReady && external && (!run || run.state === 'idle') && <Button variant="primary" size="lg" onClick={continueExternal}>Continue</Button>}
        {coreReady && !(external && (!run || run.state === 'idle')) && mode !== 'update' && (
          <Button variant="primary" size="lg" onClick={next}>{mode === 'repair' ? 'Open Watchdog' : 'Continue'}</Button>
        )}
        {coreReady && mode === 'update' && <Spinner />}
      </div>
    </>
  )
}

// ── c. folder ────────────────────────────────────────────────────────────────

function parentOf(path: string): string {
  const sep = path.includes('\\') && !path.includes('/') ? '\\' : '/'
  const parts = path.split(sep)
  parts.pop()
  return parts.join(sep) || sep
}

function FolderStep({ next, back }: { next: (dir: string) => void; back: () => void }) {
  const info = useRpc('app.info', {})
  const [dir, setDir] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fallback = info.data ? (info.data.projects_dir ?? `${parentOf(info.data.watchdog_home)}${info.data.platform === 'win32' ? '\\' : '/'}Investigations`) : ''
  const shown = dir || fallback
  const save = async () => {
    setBusy(true)
    setError('')
    try {
      // Watchdog may only create and change files in folders the user has allowed; this asks,
      // unless the folder was just chosen in the folder dialog (which already allowed it).
      const allowed = await window.watchdog.access.request(shown, 'new investigations', 'Watchdog creates each new investigation as a folder here.')
      if (!allowed) {
        setError('Watchdog needs your permission to keep investigations in this folder. Choose another folder, or allow this one.')
        return
      }
      const r = await call('settings.set', { key: 'projects_dir', value: shown })
      next(String(r.value ?? shown))
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <h1>Where your investigations live</h1>
      <p className="onb-lead">
        Each investigation is a folder on this computer with its documents, notes and findings inside. Watchdog creates them in the folder below, and
        you can change it later in Settings.
      </p>
      <Field label="Investigations folder">
        <div className="row">
          <span className="mono truncate grow onb-path" title={shown}>{shown || '…'}</span>
          <Button icon={FolderOpen} onClick={() => void window.watchdog.dialog.openFolder({ title: 'Choose a folder for your investigations', grant: 'new investigations' }).then((d) => d && setDir(d))}>Choose…</Button>
        </div>
      </Field>
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="onb-footer">
        <Button variant="ghost" icon={ArrowLeft} onClick={back}>Back</Button>
        <span className="spacer" />
        <Button variant="primary" size="lg" loading={busy} disabled={!shown} onClick={() => void save()}>Continue</Button>
      </div>
    </>
  )
}

// ── d. provider ──────────────────────────────────────────────────────────────

function ClaudeSignIn({ onSignedIn, label = 'Sign in with Claude' }: { onSignedIn: () => void; label?: string }) {
  const [phase, setPhase] = useState<'idle' | 'waiting' | 'done'>('idle')
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState('')
  useEvent('claude.signin', (d) => setUrl(d.url))
  useEffect(() => {
    void window.watchdog.claude.status().then((s) => {
      if (s.loggedIn) {
        setPhase('done')
        onSignedIn()
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const go = async () => {
    setError('')
    setPhase('waiting')
    const r = await window.watchdog.claude.signIn()
    if (r.ok) {
      setPhase('done')
      onSignedIn()
    } else {
      setPhase('idle')
      setError(r.message ?? 'Sign-in did not complete.')
    }
  }
  if (phase === 'done') return <div className="onb-ok"><CheckCircle2 /> Signed in to Claude</div>
  return (
    <div className="col gap-12">
      <div className="row">
        {phase === 'waiting' ? (
          <>
            <Spinner />
            <span className="muted">Waiting for you to finish signing in in your browser…</span>
            <Button size="sm" variant="ghost" onClick={() => void window.watchdog.claude.cancel()}>Cancel</Button>
          </>
        ) : (
          <Button icon={KeyRound} onClick={() => void go()}>{label}</Button>
        )}
      </div>
      {phase === 'waiting' && url && (
        <div className="onb-note">
          If the browser did not open, <a href={url} onClick={(e) => { e.preventDefault(); void window.watchdog.shell.openExternal(url) }}>open the sign-in page</a>.
        </div>
      )}
      {error && <Callout tone="danger">{error}</Callout>}
    </div>
  )
}

type Choose = 'subscription' | 'api-key' | 'other'

function ProviderStep({ next, back }: { next: () => void; back: () => void }) {
  const status = useRpc('auth.status', {}, { staleTime: 0 })
  const models = useRpc('settings.models', {})
  const [choice, setChoice] = useState<Choose | null>(null)
  const [connected, setConnected] = useState(false)
  const [key, setKey] = useState('')
  const [url, setUrl] = useState('')
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [claudeIn, setClaudeIn] = useState(false)

  const providers = (status.data?.providers ?? []).filter((p: { provider: string }) => p.provider !== 'anthropic') as { provider: string; label: string; requires_key: boolean; base_url_setting: string | null }[]
  const meta = providers.find((p) => p.provider === provider)
  const choices: ModelChoice[] = useMemo(() => (models.data?.models ?? []).filter((m) => m.provider === provider), [models.data, provider])
  useEffect(() => {
    setModel(choices[0]?.value ?? '')
    setKey('')
    setUrl('')
    setError('')
  }, [provider, choices.length === 0]) // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (c: Choose) => {
    setChoice(c)
    setError('')
    setConnected(false)
  }
  const wrap = async (fn: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      setConnected(true)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  const saveSubscription = () => wrap(async () => void (await call('auth.setAnthropicMode', { mode: 'subscription' })))
  const saveKey = () => wrap(async () => void (await call('auth.setAnthropicMode', { mode: 'api-key', key: key.trim() })))
  const saveOther = () =>
    wrap(async () => {
      let s: AuthStatus | null = null
      if (meta?.base_url_setting && url.trim()) s = await call('auth.setBaseUrl', { provider: provider as 'local' | 'openrouter', url: url.trim() })
      if (key.trim()) s = await call('auth.setKey', { provider, key: key.trim() })
      void s
      const value = model.includes(':') ? model : `${provider}:${model.trim()}`
      await call('auth.routeIngestion', { provider, model: value })
    })

  const needsUrl = provider === 'local'
  const canSaveOther = !!model.trim() && (!needsUrl || !!url.trim()) && (!meta?.requires_key || !!key.trim() || !!(status.data?.keys ?? []).find((k) => k.provider === provider))

  return (
    <>
      <h1>Connect a model</h1>
      <p className="onb-lead">Watchdog sends each document to an AI model to be read. Choose how it should sign in. You can change this later in Settings.</p>
      <div className="col gap-12" role="radiogroup" aria-label="Model provider">
        <Choice on={choice === 'subscription'} onClick={() => pick('subscription')} title="Claude subscription" badge="No charge per run">
          Sign in with your Claude account. Runs use your plan’s allowance, so there is no per-run charge. A Pro plan is enough for most work; very large
          batches can reach its session limits, and a Max plan raises them.
        </Choice>
        {choice === 'subscription' && (
          <div className="onb-panel">
            <ClaudeSignIn onSignedIn={() => void saveSubscription()} />
            {connected && <div className="onb-ok"><CheckCircle2 /> Watchdog will use your subscription.</div>}
            {error && <Callout tone="danger">{error}</Callout>}
          </div>
        )}
        <Choice on={choice === 'api-key'} onClick={() => pick('api-key')} title="Anthropic API key" badge="Billed per use">
          Pay Anthropic for what each run uses. Create a key in the Anthropic Console, under API keys. Cost depends on the documents and the models you choose.
        </Choice>
        {choice === 'api-key' && (
          <div className="onb-panel">
            <Field label="Anthropic API key">
              <input className="input" type="password" autoComplete="off" placeholder="sk-ant-…" value={key} onChange={(e) => { setKey(e.target.value); setConnected(false) }} />
            </Field>
            <div className="row">
              <Button variant="primary" loading={busy} disabled={!key.trim()} onClick={() => void saveKey()}>Save key</Button>
              <Button variant="ghost" onClick={() => void window.watchdog.shell.openExternal('https://platform.claude.com/')}>Open the Console</Button>
              {connected && <span className="onb-ok"><CheckCircle2 /> Key saved</span>}
            </div>
            {error && <Callout tone="danger">{error}</Callout>}
          </div>
        )}
        <Choice on={choice === 'other'} onClick={() => pick('other')} title="Another provider" badge="Billed per use">
          Use OpenAI, Google Gemini, DeepSeek, OpenRouter, or a model running on your own computer for the document work. Most are billed per use by the provider.
        </Choice>
        {choice === 'other' && (
          <div className="onb-panel">
            <Field label="Provider">
              <select className="input" value={provider} onChange={(e) => { setProvider(e.target.value); setConnected(false) }}>
                {providers.map((p) => (
                  <option key={p.provider} value={p.provider}>{providerName(p.provider)}</option>
                ))}
              </select>
            </Field>
            {meta?.base_url_setting && (
              <Field label={provider === 'local' ? 'Server address' : 'Address (optional)'} hint={provider === 'local' ? 'The OpenAI-compatible server, for example http://localhost:8080/v1.' : 'Leave blank to use OpenRouter’s own.'}>
                <input className="input" value={url} placeholder={provider === 'local' ? 'http://localhost:8080/v1' : 'https://openrouter.ai/api/v1'} onChange={(e) => setUrl(e.target.value)} />
              </Field>
            )}
            {meta?.requires_key !== false && (
              <Field label={`${providerName(provider)} key`}>
                <input className="input" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} />
              </Field>
            )}
            <Field label="Model" hint="Used to classify, read and summarize documents. You can set each step separately later in Settings.">
              {choices.length > 0 ? (
                <ModelPicker value={model} onChange={setModel} models={choices} />
              ) : (
                <input className="input" value={model} placeholder="model name" onChange={(e) => setModel(e.target.value)} />
              )}
            </Field>
            <div className="row">
              <Button variant="primary" loading={busy} disabled={!canSaveOther} onClick={() => void saveOther()}>Save</Button>
              {connected && <span className="onb-ok"><CheckCircle2 /> {providerName(provider)} is set up</span>}
            </div>
            {error && <Callout tone="danger">{error}</Callout>}
            <Callout tone="info" title="Ask Claude needs a Claude sign-in">
              The question-and-answer screens run in Claude Code, which always uses Claude. Sign in with your Claude account to use them. This is optional now and
              can be done later in Settings.
              <div style={{ marginTop: 8 }}>
                {claudeIn ? <div className="onb-ok"><CheckCircle2 /> Signed in to Claude</div> : <ClaudeSignIn label="Sign in to Claude (optional)" onSignedIn={() => setClaudeIn(true)} />}
              </div>
            </Callout>
          </div>
        )}
      </div>
      <div className="onb-footer">
        <Button variant="ghost" icon={ArrowLeft} onClick={back}>Back</Button>
        <span className="spacer" />
        {!connected && <Button variant="ghost" onClick={next}>Set up later</Button>}
        <Button variant="primary" size="lg" disabled={!connected} onClick={next}>Continue</Button>
      </div>
    </>
  )
}

// ── e. auto-approve ──────────────────────────────────────────────────────────

function ApproveStep({ next, back, busy, error }: { next: (autoApprove: boolean) => void; back: () => void; busy: boolean; error: string }) {
  const [skip, setSkip] = useState(false)
  return (
    <>
      <h1>Confirm what you send</h1>
      <p className="onb-lead">
        Before sending documents to a model, Watchdog pauses and asks you to confirm they are public records. You can skip that pause for runs where every
        step uses your Claude subscription. Do this only if you already check that what you add is public record. A run that uses a paid API key for any
        step always asks.
      </p>
      <div className="col gap-12" role="radiogroup" aria-label="Public-records pause">
        <Choice on={!skip} onClick={() => setSkip(false)} title="Ask before every run" badge="Recommended">
          Watchdog shows the public-records confirmation each time documents are about to be sent.
        </Choice>
        <Choice on={skip} onClick={() => setSkip(true)} title="Skip the pause for subscription runs">
          Runs that use only your Claude subscription go ahead without asking, with a one-line notice.
        </Choice>
      </div>
      {error && <Callout tone="danger">{error}</Callout>}
      <div className="onb-footer">
        <Button variant="ghost" icon={ArrowLeft} onClick={back}>Back</Button>
        <span className="spacer" />
        <Button variant="primary" size="lg" loading={busy} onClick={() => next(skip)}>Finish setup</Button>
      </div>
    </>
  )
}

// ── f. done ──────────────────────────────────────────────────────────────────

function DoneStep({ onDone }: { onDone: (openNew?: boolean) => void }) {
  const info = useRpc('app.info', {})
  return (
    <>
      <div className="onb-seal"><ShieldCheck /></div>
      <h1>Watchdog is ready</h1>
      <p className="onb-lead">
        {info.data?.projects_dir ? <>Investigations will be kept in <span className="mono selectable">{info.data.projects_dir}</span>. </> : null}
        Start your first investigation now, or open Watchdog and begin later.
      </p>
      <div className="onb-footer">
        <span className="spacer" />
        <Button size="lg" onClick={() => onDone(false)}>Open Watchdog</Button>
        <Button variant="primary" size="lg" icon={Sparkles} onClick={() => onDone(true)}>Create my first investigation</Button>
      </div>
    </>
  )
}

// ── the sequence ─────────────────────────────────────────────────────────────

export default function OnboardingView({ backend, mode, forceStep, onDone, onBackendAction }: Props) {
  const steps: Step[] = mode === 'first-run' ? FIRST_RUN : ['engine']
  const [step, setStep] = useState<Step | null>(null)
  const [finishing, setFinishing] = useState(false)
  const [finishError, setFinishError] = useState('')
  const [folder, setFolder] = useState<string | null>(null)

  useEffect(() => {
    void (async () => {
      if (mode !== 'first-run') return setStep('engine')
      const forced = FIRST_RUN.find((s) => s === forceStep)
      const saved = await window.watchdog.prefs.get<{ step?: Step }>('onboarding')
      setStep(forced ?? (saved?.step && FIRST_RUN.includes(saved.step) ? saved.step : 'welcome'))
    })()
  }, [mode, forceStep])

  const go = (s: Step) => {
    setStep(s)
    if (mode === 'first-run') void window.watchdog.prefs.set('onboarding', { step: s })
  }
  const move = (delta: number) => step && go(steps[Math.max(0, Math.min(steps.length - 1, steps.indexOf(step) + delta))])

  const finish = async (autoApprove: boolean) => {
    setFinishing(true)
    setFinishError('')
    try {
      await call('setup.complete', { projects_dir: folder ?? undefined, auto_approve: autoApprove })
      go('done')
    } catch (e) {
      setFinishError(errorMessage(e))
    } finally {
      setFinishing(false)
    }
  }

  if (!step) return <Frame step="welcome" steps={[]}><div className="row"><Spinner /> Starting…</div></Frame>
  return (
    <Frame step={step} steps={steps} wide={step === 'provider' || step === 'engine'}>
      {step === 'welcome' && <Welcome next={() => move(1)} />}
      {step === 'engine' && (
        <EngineStep backend={backend} mode={mode} onBackendAction={onBackendAction} back={mode === 'first-run' ? () => move(-1) : undefined} next={() => (mode === 'first-run' ? move(1) : onDone())} />
      )}
      {step === 'folder' && <FolderStep back={() => move(-1)} next={(d) => { setFolder(d); move(1) }} />}
      {step === 'provider' && <ProviderStep back={() => move(-1)} next={() => move(1)} />}
      {step === 'approve' && <ApproveStep back={() => move(-1)} next={(a) => void finish(a)} busy={finishing} error={finishError} />}
      {step === 'done' && <DoneStep onDone={onDone} />}
    </Frame>
  )
}
