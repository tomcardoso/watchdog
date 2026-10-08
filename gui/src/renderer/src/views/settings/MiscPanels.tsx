// Skills, appearance, vault check, setup and about.

import { BookOpen, CheckCircle2, ExternalLink, FolderOpen, MinusCircle, Monitor, Moon, RefreshCw, Search, Sun, Wrench, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { BackendStatus, SetupModels } from '@shared/api'
import { EngineActions, EngineProgressView, useEngine } from '@renderer/views/onboarding/EngineInstall'
import { Badge, Button, Callout, Empty, ErrorNote, Modal, Segmented, Skeleton, Spinner } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { toast, useApp, Theme } from '@renderer/lib/store'
import { errorMessage, useRpc } from '@renderer/lib/rpc'
import { plural } from '@renderer/lib/format'

export function SkillsPanel() {
  const q = useRpc('skills.list', {})
  const [open, setOpen] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const text = useRpc('skills.read', open ? { name: open } : null)
  if (q.isLoading) return <Skeleton h={300} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const { skills, user_dir } = q.data!
  const shown = skills.filter((s) => `${s.name} ${s.description}`.toLowerCase().includes(filter.toLowerCase()))
  return (
    <div className="col" style={{ gap: 18 }}>
      <Callout tone="info" title="Record skills tell the extractor how to read a kind of document">
        Each skill describes what matters in, say, a court filing or a procurement contract. Watchdog picks one per document, or you can pin one. Add your own by placing a markdown file in{' '}
        <span className="mono selectable">{user_dir}</span>.
        <div style={{ marginTop: 8 }}>
          <Button size="sm" icon={FolderOpen} onClick={() => window.watchdog.shell.openPath(user_dir)}>Show in folder</Button>
        </div>
      </Callout>
      <div className="input-group" style={{ maxWidth: 360 }}>
        <Search />
        <input className="input" placeholder="Filter skills" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="set-skills">
        {shown.map((s) => (
          <button key={s.name} className="set-skill" onClick={() => setOpen(s.name)}>
            <div className="row">
              <span style={{ fontWeight: 600 }} className="grow truncate">{s.name}</span>
              {s.source === 'user' && <Badge tone="accent">Yours</Badge>}
            </div>
            <div className="muted clamp-2" style={{ fontSize: 'var(--fs-sm)' }}>{s.description}</div>
          </button>
        ))}
        {!shown.length && <Empty icon={BookOpen} title="No skill matches">Try a different word.</Empty>}
      </div>
      <Modal open={!!open} onClose={() => setOpen(null)} title={open ?? ''} sub="Record skill" width="xwide">
        {text.isLoading ? <Spinner size="lg" /> : text.error ? <ErrorNote error={text.error} /> : <Markdown text={text.data?.text ?? ''} />}
      </Modal>
    </div>
  )
}

export function AppearancePanel() {
  const theme = useApp((s) => s.theme)
  const setTheme = useApp((s) => s.setTheme)
  const info = useRpc('app.info', {})
  const [be, setBe] = useState<BackendStatus | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    void window.watchdog.backend.status().then(setBe).catch(() => undefined)
  }, [])
  const run = async (fn: () => Promise<BackendStatus>) => {
    setBusy(true)
    try {
      setBe(await fn())
    } catch (e) {
      toast({ kind: 'error', title: 'Backend', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  const i = info.data
  return (
    <div className="col" style={{ gap: 22 }}>
      <section className="card set-card">
        <div className="set-card-head"><div><div className="card-title">Theme</div><div className="card-sub">System follows your computer’s light or dark setting.</div></div></div>
        <div style={{ padding: '4px 18px 18px' }}>
          <Segmented<Theme> value={theme} onChange={setTheme} options={[{ value: 'system', label: 'System', icon: Monitor }, { value: 'light', label: 'Light', icon: Sun }, { value: 'dark', label: 'Dark', icon: Moon }]} />
        </div>
      </section>
      <section className="card set-card">
        <div className="set-card-head">
          <div><div className="card-title">Python backend</div><div className="card-sub">The Watchdog program the app runs behind the scenes.</div></div>
          <span className="spacer" />
          {be && <Badge tone={be.state === 'ready' ? 'success' : be.state === 'error' ? 'danger' : undefined}>{be.state === 'ready' ? 'Running' : be.state}</Badge>}
        </div>
        <dl className="kv" style={{ padding: '4px 18px 8px' }}>
          <dt>Python</dt><dd className="mono selectable">{i?.python ?? be?.python ?? '—'}</dd>
          <dt>Version</dt><dd>{i?.python_version ?? '—'}</dd>
          <dt>Found through</dt><dd>{be?.source ? { env: 'WATCHDOG_PYTHON', settings: 'your choice in this app', managed: 'Watchdog’s own engine', pipx: 'a separate Watchdog installation', path: 'the system path', dev: 'development checkout' }[be.source] ?? be.source : '—'}</dd>
          <dt>Platform</dt><dd>{i?.platform ?? '—'}</dd>
          <dt>Settings file</dt><dd className="mono selectable">{i?.config_file ?? '—'}</dd>
          <dt>Data folder</dt><dd className="mono selectable">{i?.watchdog_home ?? '—'}</dd>
        </dl>
        {be?.message && be.state === 'error' && <Callout tone="danger" style={{ margin: '0 18px 12px' }}>{be.message}</Callout>}
        <div className="row" style={{ padding: '4px 18px 18px', gap: 8 }}>
          <Button icon={RefreshCw} loading={busy} onClick={() => void run(() => window.watchdog.backend.restart())}>Restart backend</Button>
          <Button onClick={() => void run(() => window.watchdog.backend.choosePython())}>Choose Python…</Button>
        </div>
      </section>
    </div>
  )
}

export function DoctorPanel() {
  const q = useRpc('projects.doctor', {})
  const [checked, setChecked] = useState(0)
  return (
    <div className="col" style={{ gap: 16 }}>
      <div className="muted" style={{ maxWidth: '64ch', lineHeight: 1.55 }}>
        Checks every registered investigation for a folder that has moved or been deleted, or one that is no longer a Watchdog vault. Nothing is changed; each problem comes with the fix.
      </div>
      <div><Button icon={RefreshCw} loading={q.isFetching} onClick={() => { setChecked(checked + 1); void q.refetch() }}>Check again</Button></div>
      {q.isLoading ? <Skeleton h={120} /> : q.error ? <ErrorNote error={q.error} retry={() => void q.refetch()} /> : q.data!.issues.length === 0 ? (
        <Callout tone="success" title="Every investigation looks healthy" />
      ) : (
        <div className="col" style={{ gap: 10 }}>
          <div className="faint">{plural(q.data!.issues.length, 'problem')} found</div>
          {q.data!.issues.map((it, n) => (
            <div key={n} className="card card-pad">
              <div style={{ fontWeight: 620 }}>{it.name}</div>
              <div className="mono faint selectable" style={{ fontSize: 11.5, margin: '2px 0 8px' }}>{it.path}</div>
              <div style={{ color: 'var(--danger)' }}>{it.problem}</div>
              <div className="muted" style={{ marginTop: 4 }}>{it.suggestion}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const HELPER_NOTES: Record<string, string> = {
  qpdf: 'Optional. Used only to repair damaged or protected PDFs. Without it, Watchdog skips that fallback.',
  ghostscript: 'Optional. Used only to re-render problem PDFs. Without it, Watchdog skips that fallback.',
  'Tesseract OCR': 'Optional. Watchdog reads scanned pages with its own built-in engine.'
}
const MODEL_LABELS: [keyof SetupModels, string, string][] = [
  ['docling', 'Document conversion models (Docling)', 'Reads PDFs and scans. Downloaded the first time a document is converted if missing.'],
  ['gliner', 'Name-detection model (GLiNER)', 'Finds people, organizations and places on your computer. Downloaded on first use if missing.'],
  ['embedding', 'Search embedding model', 'Ranks passages by meaning. Downloaded on first use if missing.'],
  ['reranker', 'Search reranker', 'Orders the best matches. Downloaded on first use if missing.']
]

export function SetupPanel() {
  const q = useRpc('setup.check', {})
  const models = useRpc('setup.models', {})
  const install = useEngine()
  const eng = install.status
  const run = install.run
  // The background phase (D272), or a run that stopped: shown first, with its steps and log.
  const unfinished = !!eng && (!eng.complete || run?.state === 'running' || run?.state === 'failed' || run?.state === 'cancelled')
  useEffect(() => {
    if (run?.state === 'done') void models.refetch()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.state])
  if (q.isLoading) return <Skeleton h={300} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const c = q.data!
  const m = models.data
  const Item = ({ ok, label, hint, optional }: { ok: boolean; label: string; hint?: string | null; optional?: boolean }) => (
    <div className="set-check">
      {ok ? <CheckCircle2 style={{ color: 'var(--success)' }} /> : optional ? <MinusCircle style={{ color: 'var(--text-3)' }} /> : <XCircle style={{ color: 'var(--danger)' }} />}
      <div className="grow">
        <div style={{ fontWeight: 560 }}>{label}</div>
        {!ok && hint && <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 2 }}>{hint}</div>}
      </div>
    </div>
  )
  const repair = async () => {
    const yes = await window.watchdog.dialog.confirm({
      title: 'Repair or reinstall the engine?',
      message: 'Watchdog will remove its private Python environment and install it again.',
      detail: 'Your investigations, settings and downloaded models are not touched. This needs an internet connection and can take several minutes.',
      confirm: 'Reinstall'
    })
    if (yes) void window.watchdog.engine.reinstall()
  }
  const fetchModels = async () => {
    await window.watchdog.engine.install()
    void models.refetch()
  }
  return (
    <div className="col" style={{ gap: 18 }}>
      {unfinished && (
        <section className="card set-card">
          <div className="set-card-head">
            <div>
              <div className="card-title">{run?.state === 'running' ? 'Finishing setup' : run?.state === 'failed' || run?.state === 'cancelled' ? 'Setup did not finish' : 'Setup is not finished'}</div>
              <div className="card-sub">
                Watchdog downloads its document and search libraries and its local models in the background. Adding documents waits until they are in place; everything
                else works now. If the app is closed or the connection drops, setup continues the next time Watchdog opens.
              </div>
            </div>
          </div>
          <div style={{ padding: '4px 18px 18px' }} className="col gap-16">
            {run && run.state !== 'idle' && <EngineProgressView eng={install} />}
            <div className="row" style={{ gap: 8 }}>
              <EngineActions eng={install} primary="Finish setup" />
            </div>
          </div>
        </section>
      )}
      <section className="card set-card">
        <div className="set-card-head">
          <div><div className="card-title">Engine</div><div className="card-sub">The private Python environment Watchdog runs in, installed and kept up to date by the app.</div></div>
        </div>
        <dl className="kv" style={{ padding: '4px 18px 8px' }}>
          <dt>Status</dt><dd>{eng?.engine === 'ready' ? (eng.complete ? 'Installed' : 'Installed; finishing setup in the background') : eng?.usingExternal ? 'Using another Watchdog installation' : eng?.engine === 'outdated' ? 'Needs an update' : 'Not installed'}</dd>
          <dt>Version</dt><dd>{eng?.installedVersion ?? '—'}</dd>
          <dt>Location</dt><dd className="mono selectable">{eng?.usingExternal ?? eng?.dir ?? '—'}</dd>
        </dl>
        <div className="row wrap" style={{ padding: '4px 18px 18px', gap: 8 }}>
          <Button icon={Wrench} disabled={!eng?.canInstall} onClick={() => void repair()}>Repair or reinstall the engine</Button>
          <Button icon={RefreshCw} disabled={!eng?.canInstall} onClick={() => void fetchModels()}>Download missing models</Button>
        </div>
      </section>
      <section className="card set-card">
        <div className="set-card-head"><div className="card-title">Local models</div></div>
        <div style={{ padding: '0 18px 10px' }}>
          {MODEL_LABELS.map(([key, label, hint]) => (m ? <Item key={key} ok={m[key] !== false} label={label} hint={hint} optional /> : null))}
          {m && <Item ok={!!m.ocr} label={m.ocr ? `Text recognition for scans (${m.ocr})` : 'Text recognition for scans'} hint="No OCR engine is installed. Repair the engine to add one." />}
          {m && <Item ok={!!m.claude_cli} label="Claude Code (for Ask Claude and Research)" hint="Comes with the engine. Repair the engine if it is missing." />}
        </div>
      </section>
      <section className="card set-card">
        <div className="set-card-head"><div className="card-title">Helper tools</div></div>
        <div style={{ padding: '0 18px 10px' }}>
          {c.deps.filter((d) => d.label !== 'Claude Code').map((d) => <Item key={d.label} ok={d.ok} label={d.ok ? d.label : `${d.label} is not installed`} hint={HELPER_NOTES[d.label] ?? d.hint} optional />)}
        </div>
      </section>
      <section className="card set-card">
        <div className="set-card-head"><div className="card-title">Optional and downloaded pieces</div></div>
        <div style={{ padding: '0 18px 10px' }}>
          <Item ok={c.playwright} optional label="Capture browser" hint="Optional. Lets web pages be saved as full snapshots, with images and client-rendered content. Without it, pages are saved as sanitized text." />
          <Item ok={!!c.projects_dir} label={c.projects_dir ? `Investigations folder: ${c.projects_dir}` : 'Investigations folder'} hint="Not chosen yet. Set it under General in Settings." />
          <Item ok={c.config_exists} label="Settings file" hint="Created the first time you save a setting." />
        </div>
      </section>
    </div>
  )
}

export function AboutPanel() {
  const info = useRpc('app.info', {})
  const link = (label: string, url: string) => (
    <Button icon={ExternalLink} onClick={() => void window.watchdog.shell.openExternal(url)}>{label}</Button>
  )
  return (
    <div className="col" style={{ gap: 18 }}>
      <section className="card card-pad">
        <div style={{ fontSize: 'var(--fs-xl)', fontWeight: 660 }}>Watchdog</div>
        <div className="muted" style={{ margin: '4px 0 14px' }}>Version <span className="tnum">{info.data?.version ?? '—'}</span></div>
        <div className="muted" style={{ maxWidth: '60ch', lineHeight: 1.55, marginBottom: 16 }}>
          Turns public-records documents into a linked, sourced investigation vault. Open source, and it runs on your computer.
        </div>
        <div className="row wrap" style={{ gap: 8 }}>
          {link('GitHub', 'https://github.com/tomcardoso/watchdog')}
          {link('Report an issue', 'https://github.com/tomcardoso/watchdog/issues')}
          {link('Install guide', 'https://github.com/tomcardoso/watchdog/blob/main/docs/install.md')}
        </div>
      </section>
    </div>
  )
}
