// Skills, appearance, vault check, setup and about.

import { BookOpen, CheckCircle2, ExternalLink, FolderOpen, Monitor, Moon, RefreshCw, Search, Sun, Terminal, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { BackendStatus } from '@shared/api'
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
          <dt>Found through</dt><dd>{be?.source ? { env: 'WATCHDOG_PYTHON', settings: 'your choice in this app', pipx: 'pipx install', path: 'the system path', dev: 'development checkout' }[be.source] ?? be.source : '—'}</dd>
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

export function SetupPanel() {
  const q = useRpc('setup.check', {})
  const info = useRpc('app.info', {})
  if (q.isLoading) return <Skeleton h={300} />
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  const c = q.data!
  const Item = ({ ok, label, hint }: { ok: boolean; label: string; hint?: string | null }) => (
    <div className="set-check">
      {ok ? <CheckCircle2 style={{ color: 'var(--success)' }} /> : <XCircle style={{ color: 'var(--danger)' }} />}
      <div className="grow">
        <div style={{ fontWeight: 560 }}>{label}</div>
        {!ok && hint && <div className="muted" style={{ fontSize: 'var(--fs-sm)', marginTop: 2 }}>{hint}</div>}
      </div>
    </div>
  )
  return (
    <div className="col" style={{ gap: 18 }}>
      <section className="card set-card">
        <div className="set-card-head"><div className="card-title">Required tools</div></div>
        <div style={{ padding: '0 18px 10px' }}>{c.deps.map((d) => <Item key={d.label} ok={d.ok} label={d.label} hint={d.hint} />)}</div>
      </section>
      <section className="card set-card">
        <div className="set-card-head"><div className="card-title">Optional and downloaded pieces</div></div>
        <div style={{ padding: '0 18px 10px' }}>
          <Item ok={c.playwright} label="Capture browser" hint="Optional. Lets web pages be saved as full snapshots, with images and client-rendered content. Without it, pages are saved as sanitized text." />
          <Item ok={c.gliner_model} label="Name-detection model (GLiNER)" hint="Downloaded on first use of chew, or by running full setup. Detects people and organizations locally." />
          <Item ok={!!c.projects_dir} label={c.projects_dir ? `Investigations folder: ${c.projects_dir}` : 'Investigations folder'} hint="Not chosen yet. Set it in Vaults under Settings." />
          <Item ok={c.config_exists} label="Settings file" hint="Created the first time you save a setting or run setup." />
        </div>
      </section>
      <div className="row" style={{ gap: 10 }}>
        <Button icon={Terminal} disabled={!info.data} onClick={() => info.data && void window.watchdog.shell.openTerminal(info.data.watchdog_home, ['setup', '--force'])}>Run full setup in Terminal</Button>
        <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Opens a terminal and runs <span className="mono">watchdog setup --force</span>, which can install the pieces above.</span>
      </div>
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
