// Context (D342): the investigation's brief, context.md, and the background material behind it,
// in one place under Work. The brief is sent to the AI model with every document added, so it is
// asked for first (the Overview says so until it exists). Claude can build it from the files in
// context/ and a short interview; that conversation runs here, as a session in `context` mode.

import { FilePlus2, FileText, FolderOpen, MessageCircle, Pencil, Save, Target, X } from 'lucide-react'
import { useState } from 'react'
import { Button, Callout } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { fmtBytes, plural } from '@renderer/lib/format'
import { toast, useApp } from '@renderer/lib/store'
import { NoteActions, stripFrontmatter } from '../briefings/NoteView'
import ChatWorkspace, { WorkspaceApi } from './ChatWorkspace'
import '../briefings/briefings.css'

function Brief({ api }: { api: WorkspaceApi }) {
  const vault = useApp((s) => s.project!.path)
  const file = useRpc('vault.readFile', { vault, path: 'context.md' }, { staleTime: 5_000 })
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const text = file.data?.text ?? ''
  const body = stripFrontmatter(text).trim()

  const save = async () => {
    setSaving(true)
    try {
      await call('vault.writeFile', { vault, path: 'context.md', text: draft })
      invalidate('vault.readFile', 'vault.summary')
      setEditing(false)
      toast({ kind: 'success', title: 'Context saved', body: 'The next run and briefing will use it.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not save', body: errorMessage(e) })
    } finally {
      setSaving(false)
    }
  }
  const edit = () => {
    setDraft(text)
    setEditing(true)
  }

  return (
    <section className="ctx-section">
      <div className="ctx-section-head">
        <h3>Your brief</h3>
        <span className="spacer" />
        {!editing && body && (
          <>
            <Button size="sm" icon={MessageCircle} onClick={() => void api.begin('', 'context')} loading={api.busy}>Update with Claude</Button>
            <Button size="sm" icon={Pencil} onClick={edit}>Edit</Button>
            <NoteActions path="context.md" />
          </>
        )}
      </div>
      {editing ? (
        <>
          <textarea className="textarea bf-editor" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck autoFocus />
          <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
            <Button icon={X} variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
            <Button icon={Save} variant="primary" loading={saving} onClick={() => void save()}>Save</Button>
          </div>
        </>
      ) : body ? (
        <div className="bf-article ctx-brief"><Markdown text={stripFrontmatter(text)} /></div>
      ) : (
        <div className="ctx-start">
          <p>No brief yet. Claude can build one with you: it reads the background material below, then asks you what you are looking for. Or write it yourself.</p>
          <div className="row" style={{ gap: 8 }}>
            <Button variant="primary" icon={MessageCircle} onClick={() => void api.begin('', 'context')} loading={api.busy}>Build it with Claude</Button>
            <Button icon={Pencil} onClick={edit}>Write it yourself</Button>
          </div>
        </div>
      )}
    </section>
  )
}

/** Copy files the reporter picks into context/. Shared by the panel and the strip above a
 * conversation, so the interview can ask for background and the reporter can add it there. */
function useAddBackground() {
  const project = useApp((s) => s.project!)
  const [busy, setBusy] = useState(false)
  const add = async () => {
    const picked = await window.watchdog.dialog.openFiles({ title: 'Add background files', multi: true })
    if (!picked.length) return
    setBusy(true)
    try {
      const r = await call('vault.addContextFiles', { vault: project.path, paths: picked })
      invalidate('vault.contextFiles', 'vault.summary')
      if (r.added.length) toast({ kind: 'success', title: `${plural(r.added.length, 'file')} added to the background`, body: 'Claude reads them the next time it builds or updates the brief.' })
      if (r.skipped.length) toast({ kind: 'info', title: `${plural(r.skipped.length, 'item')} skipped`, body: 'Only files can be added, not folders.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not add the files', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  return { busy, add }
}

/** Above a context conversation: how many background files there are, and the way to add more. */
function BackgroundStrip() {
  const vault = useApp((s) => s.project!.path)
  const files = useRpc('vault.contextFiles', { vault })
  const { busy, add } = useAddBackground()
  const n = files.data?.length ?? 0
  return (
    <div className="chat-strip">
      <div className="row" style={{ gap: 10 }}>
        <FileText style={{ width: 16, height: 16, color: 'var(--text-3)' }} />
        <span style={{ fontWeight: 600 }}>{n ? `${plural(n, 'background file')}` : 'No background files yet'}</span>
        <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Claude reads them when it builds the brief.</span>
        <span className="spacer" />
        <Button size="sm" icon={FilePlus2} loading={busy} onClick={() => void add()}>Add background files…</Button>
      </div>
    </div>
  )
}

function Background() {
  const project = useApp((s) => s.project!)
  const files = useRpc('vault.contextFiles', { vault: project.path })
  const { busy, add } = useAddBackground()
  const list = files.data ?? []
  return (
    <section className="ctx-section">
      <div className="ctx-section-head">
        <h3>Background material</h3>
        <span className="spacer" />
        <Button size="sm" variant="ghost" icon={FolderOpen} onClick={() => void window.watchdog.shell.openPath(`${project.path}/context`)}>Show folder</Button>
        <Button size="sm" variant="primary" icon={FilePlus2} loading={busy} onClick={() => void add()}>Add background files…</Button>
      </div>
      <p className="muted ctx-note">
        Published stories, earlier reporting, notes and screenshots that describe what you are looking into: PDFs, Word files, text and images. They are copied into the
        investigation’s <span className="mono">context/</span> folder for Claude to read when it builds the brief; they are not added as documents. Claude reads them in full, so leave out anything confidential, such as source names or unpublished tips.
      </p>
      {list.length === 0 ? (
        <p className="faint ctx-note">Nothing here yet.</p>
      ) : (
        <ul className="chat-ctx-files">
          {list.map((f) => (
            <li key={f.name}><FileText /> <span className="truncate">{f.name}</span><span className="faint tnum">{fmtBytes(f.size)}</span></li>
          ))}
        </ul>
      )}
    </section>
  )
}

function ContextHome({ api }: { api: WorkspaceApi }) {
  const name = useApp((s) => s.project!.name)
  return (
    <div className="chat-empty ctx-home">
      <h2><Target /> Context for {name}</h2>
      <Callout tone="info">
        The brief tells Watchdog what you are investigating. It is sent to the AI model with every document you add, and shapes the briefings, so set it before your first
        batch. Write it as open questions, such as “I want to understand how the contract was awarded”, rather than conclusions: a brief that states what you expect to
        find can lead the model to read the documents that way. Leave out source names, contact details and unpublished tips.
      </Callout>
      <Brief api={api} />
      <Background />
    </div>
  )
}

export default function ContextView() {
  const project = useApp((s) => s.project)
  const route = useApp((s) => s.route)
  if (!project) return null
  const r = route.view === 'context' ? route : { view: 'context' as const }
  return (
    <ChatWorkspace
      key={project.slug}
      view="context"
      modes={['context']}
      mode="context"
      title="Context"
      placeholder="Answer Claude’s questions, or tell it what you are investigating…"
      session={r.session}
      autoStart={r.start ? 'context' : undefined}
      empty={(api) => <ContextHome api={api} />}
      strip={(api) => (api.live || r.session ? <BackgroundStrip /> : null)}
    />
  )
}
