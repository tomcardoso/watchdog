// A generic note reader for route { view: 'note', path }: queries/, wiki/ pages, the timeline,
// requests.md and so on. Entity and document notes are sent to their own views instead.

import { FolderOpen, NotebookText } from 'lucide-react'
import { useEffect } from 'react'
import { Button, Empty, ErrorNote, Skeleton } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { NotesEditor, splitNotes } from '@renderer/components/NotesEditor'
import { HistoryButton } from '@renderer/components/FileHistory'
import { call, useRpc } from '@renderer/lib/rpc'
import { navigate, useApp, useVault } from '@renderer/lib/store'
import './briefings.css'

export function stripFrontmatter(text: string): string {
  return text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, '')
}

export function prettyKey(k: string): string {
  return k.replace(/[_-]/g, ' ')
}

export function propValue(v: unknown): string {
  if (v === null || v === undefined || v === '') return '—'
  if (Array.isArray(v)) return v.map(propValue).join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function NoteActions({ path }: { path: string }) {
  const vault = useVault()
  const file = path.endsWith('.md') ? path : `${path}.md`
  return (
    <div className="bf-actions">
      <Button size="sm" icon={NotebookText} onClick={() => void window.watchdog.shell.openInObsidian(vault, file.replace(/\.md$/, ''))}>
        Open in Obsidian
      </Button>
      <Button size="sm" variant="ghost" icon={FolderOpen} onClick={() => window.watchdog.shell.showItemInFolder(`${vault}/${file}`)}>
        Show in folder
      </Button>
      <HistoryButton vault={vault} path={file} variant="ghost" />
    </div>
  )
}

export default function NoteView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const path = route.view === 'note' ? route.path : ''
  const q = useRpc('vault.note', vault && path ? { vault, path } : null)
  const note = q.data

  useEffect(() => {
    if (!note || !note.exists) return
    const stem = note.path.replace(/\.md$/, '')
    if (note.kind === 'entity') {
      navigate({ view: 'entity', id: stem.split('/').pop()! }, { replace: true })
    } else if (note.kind === 'document') {
      call('vault.resolveLink', { vault, target: stem })
        .then((r) => r.sha && navigate({ view: 'document', sha: r.sha }, { replace: true }))
        .catch(() => {})
    }
  }, [note, vault])

  if (q.error) return <div className="page"><div className="page-inner"><ErrorNote error={q.error} retry={() => void q.refetch()} /></div></div>
  if (q.isLoading || !note)
    return (
      <div className="page"><div className="page-inner bf-note"><Skeleton w="50%" h={28} /><Skeleton h={14} style={{ marginTop: 20 }} /><Skeleton w="80%" h={14} style={{ marginTop: 8 }} /></div></div>
    )
  if (!note.exists)
    return (
      <div className="page"><div className="page-inner"><Empty icon={NotebookText} title="This note does not exist" action={<Button onClick={() => navigate({ view: 'briefings' })}>Go to briefings</Button>}>{path} is not in the vault. It may have been renamed or removed.</Empty></div></div>
    )
  if (note.kind === 'entity' || note.kind === 'document') {
    return <div className="page"><div className="page-inner"><Skeleton h={20} /></div></div>
  }

  const fm = Object.entries(note.frontmatter ?? {}).filter(([k]) => !k.startsWith('_'))
  const title = note.title ?? note.path.replace(/\.md$/, '').split('/').pop() ?? note.path
  // A saved page (queries/, wiki/) has a Notes section the reporter writes in the app (D288); the
  // rest of the page is the session's and is shown as it is.
  const saved = note.kind === 'query' || note.kind === 'wiki'
  const split = saved ? splitNotes(stripFrontmatter(note.body)) : null
  const body = split ? split.rest : stripFrontmatter(note.body)
  const hasH1 = /^\s*#\s/.test(body)
  return (
    <div className="page">
      <div className="page-inner bf-note">
        <div className="eyebrow">{note.path.replace(/\.md$/, '').split('/').slice(0, -1).join(' / ') || 'Vault'}</div>
        <div className="bf-note-head">
          <h1 className="bf-note-title">{title}</h1>
          <NoteActions path={note.path} />
        </div>
        {fm.length > 0 && (
          <dl className="bf-props">
            {fm.map(([k, v]) => (
              <div key={k} className="bf-prop">
                <dt>{prettyKey(k)}</dt>
                <dd className="selectable">{propValue(v)}</dd>
              </div>
            ))}
          </dl>
        )}
        <div className="bf-measure">
          <Markdown text={hasH1 ? body.replace(/^\s*#\s.*\n?/, '') : body} />
          {split && (
            <div className="bf-notes">
              <NotesEditor key={note.path} vault={vault} path={note.path} saved={split.notes} label={title} promise="Watchdog never writes to this section, and a Claude session that updates this page is told to leave it as it is." placeholder="Your own thoughts on this page: what to check next, who to call." />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
