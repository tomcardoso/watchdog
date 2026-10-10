// "All investigations": the front door. A grid of investigation cards, each with a menu covering
// every `watchdog projects` subcommand, plus register, new, and the vault health check.

import {
  Archive, ArchiveRestore, BookOpenText, FolderInput, FolderOpen, FolderPlus, History, Pencil, Plus,
  Stethoscope, TextCursorInput, Trash2, TriangleAlert, ExternalLink, MoreHorizontal, Search, Layers, Files, Clock, CircleCheck, FileClock
} from 'lucide-react'
import { useMemo, useState } from 'react'
import { Badge, Button, Callout, Dropdown, Empty, ErrorNote, Field, MenuItem, Modal, Skeleton, Switch } from '@renderer/components/ui'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { runAction } from '@renderer/lib/jobs'
import { fmtBytes, fmtRelative, fmtNum, basename, plural } from '@renderer/lib/format'
import { toast, useApp } from '@renderer/lib/store'
import { ProjectMark } from '@renderer/shell/Sidebar'
import type { DoctorIssue, Project } from '@shared/api'
import './projects.css'

type Dialog =
  | { kind: 'rename' | 'describe' | 'move' | 'log' | 'remove' | 'purge'; project: Project }
  | { kind: 'register' }
  | { kind: 'doctor' }
  | null

const newInvestigation = () => window.dispatchEvent(new CustomEvent('wd:command', { detail: 'new-investigation' }))

export default function ProjectsView() {
  const setProject = useApp((s) => s.setProject)
  const [showArchived, setShowArchived] = useState(false)
  const [filter, setFilter] = useState('')
  const [dialog, setDialog] = useState<Dialog>(null)
  const { data, error, isLoading, refetch } = useRpc('projects.list', { all: showArchived })

  const projects = useMemo(() => {
    const q = filter.trim().toLowerCase()
    return [...(data ?? [])]
      .filter((p) => !q || p.name.toLowerCase().includes(q) || (p.description ?? '').toLowerCase().includes(q))
      .sort((a, b) => Number(a.archived) - Number(b.archived) || (b.stats.last_ingest ?? b.created ?? '').localeCompare(a.stats.last_ingest ?? a.created ?? ''))
  }, [data, filter])

  const open = (p: Project) => {
    if (p.health) {
      toast({ kind: 'info', title: `${p.name} cannot be opened`, body: healthText(p) })
      return
    }
    setProject(p)
  }

  const mutate = async (run: () => Promise<unknown>, done: string, p?: Project) => {
    try {
      await run()
      invalidate('projects.')
      toast({ kind: 'success', title: done, body: p?.name })
    } catch (e) {
      toast({ kind: 'error', title: 'That did not work', body: errorMessage(e) })
    }
  }

  const menu = (p: Project): MenuItem[] => [
    { label: 'Open', icon: BookOpenText, onClick: () => open(p), disabled: !!p.health },
    { separator: true, label: '' },
    { label: 'Rename…', icon: Pencil, onClick: () => setDialog({ kind: 'rename', project: p }) },
    { label: 'Edit description…', icon: TextCursorInput, onClick: () => setDialog({ kind: 'describe', project: p }) },
    { label: 'Move to another folder…', icon: FolderInput, onClick: () => setDialog({ kind: 'move', project: p }) },
    p.archived
      ? { label: 'Unarchive', icon: ArchiveRestore, onClick: () => void mutate(() => runAction('projects-archive', { slug: p.slug, archived: false }, null), 'Restored to the list', p) }
      : { label: 'Archive', icon: Archive, onClick: () => void mutate(() => runAction('projects-archive', { slug: p.slug, archived: true }, null), 'Archived', p) },
    { separator: true, label: '' },
    { label: 'Show in folder', icon: FolderOpen, onClick: () => window.watchdog.shell.showItemInFolder(p.path) },
    { label: 'Open in Obsidian', icon: ExternalLink, onClick: () => void window.watchdog.shell.openInObsidian(p.path), disabled: !!p.health },
    { label: 'Processing history', icon: History, onClick: () => setDialog({ kind: 'log', project: p }) },
    { separator: true, label: '' },
    { label: 'Remove from Watchdog…', icon: Trash2, danger: true, onClick: () => setDialog({ kind: 'remove', project: p }) },
    { label: 'Remove and delete files…', icon: Trash2, danger: true, onClick: () => setDialog({ kind: 'purge', project: p }) }
  ]

  const empty = !isLoading && !error && (data ?? []).length === 0 && !showArchived

  return (
    <div className="page">
      <div className="page-inner proj-page">
        <header className="proj-hero">
          <div className="grow">
            <div className="eyebrow">Watchdog</div>
            <h1 className="proj-title">Investigations</h1>
            <p className="page-sub">
              {data && data.length > 0
                ? `${plural(data.length, 'investigation')}${showArchived ? ', including archived' : ''}. Each one is a folder on this computer that holds its own documents, notes and briefings.`
                : 'Each investigation is a folder on this computer that holds its own documents, notes and briefings.'}
            </p>
          </div>
          <div className="row gap-8 wrap proj-hero-actions">
            <Button icon={Stethoscope} variant="ghost" onClick={() => setDialog({ kind: 'doctor' })}>
              Check vaults
            </Button>
            <Button icon={FolderPlus} onClick={() => setDialog({ kind: 'register' })}>
              Add existing folder…
            </Button>
            <Button icon={Plus} variant="primary" onClick={newInvestigation}>
              New investigation
            </Button>
          </div>
        </header>

        {!empty && (
          <div className="proj-toolbar">
            {(data?.length ?? 0) > 5 && (
              <div className="input-group proj-filter">
                <Search />
                <input className="input" placeholder="Filter investigations" value={filter} onChange={(e) => setFilter(e.target.value)} />
              </div>
            )}
            <div className="spacer" />
            <label className="row gap-8 muted" style={{ fontSize: 'var(--fs-sm)', cursor: 'pointer' }}>
              <Switch checked={showArchived} onChange={setShowArchived} label="Show archived" />
              Show archived
            </label>
          </div>
        )}

        {error ? (
          <ErrorNote error={error} retry={() => void refetch()} />
        ) : isLoading ? (
          <div className="proj-grid">
            {[0, 1, 2].map((i) => (
              <div className="card proj-card" key={i}>
                <div className="row gap-12">
                  <Skeleton w={46} h={46} style={{ borderRadius: 12 }} />
                  <div className="grow col">
                    <Skeleton w="60%" h={16} />
                    <Skeleton w="85%" h={12} />
                  </div>
                </div>
                <Skeleton h={44} style={{ marginTop: 18 }} />
              </div>
            ))}
          </div>
        ) : empty ? (
          <Welcome onRegister={() => setDialog({ kind: 'register' })} />
        ) : projects.length === 0 ? (
          <Empty icon={Search} title="No matches">No investigation matches “{filter}”.</Empty>
        ) : (
          <div className="proj-grid">
            {projects.map((p) => (
              <ProjectCard key={p.slug} p={p} menu={menu(p)} onOpen={() => open(p)} onFix={() => setDialog({ kind: 'move', project: p })} onRemove={() => setDialog({ kind: 'remove', project: p })} />
            ))}
          </div>
        )}
      </div>

      {dialog?.kind === 'rename' && <RenameDialog p={dialog.project} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'describe' && <DescribeDialog p={dialog.project} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'move' && <MoveDialog p={dialog.project} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'log' && <LogDialog p={dialog.project} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'remove' && <RemoveDialog p={dialog.project} purge={false} onClose={() => setDialog(null)} />}
      {dialog?.kind === 'purge' && <RemoveDialog p={dialog.project} purge onClose={() => setDialog(null)} />}
      {dialog?.kind === 'register' && <RegisterDialog onClose={() => setDialog(null)} />}
      {dialog?.kind === 'doctor' && <DoctorDialog onClose={() => setDialog(null)} />}
    </div>
  )
}

function healthText(p: Project): string {
  if (p.health === 'missing') return 'The folder is no longer where Watchdog last saw it.'
  if (p.health === 'not_a_vault') return 'The folder exists but is not a Watchdog vault.'
  if (p.health === 'registry_corrupt') return 'The investigation\u2019s registry file is damaged. Run Check vaults for details.'
  return p.health ?? ''
}

// ── Card ─────────────────────────────────────────────────────────────────────
function ProjectCard({ p, menu, onOpen, onFix, onRemove }: { p: Project; menu: MenuItem[]; onOpen: () => void; onFix: () => void; onRemove: () => void }) {
  const s = p.stats
  const inProgress = s.incoming + s.awaiting
  return (
    <div
      className={'card interactive proj-card' + (p.archived ? ' archived' : '') + (p.health ? ' unhealthy' : '')}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === 'Enter' && e.target === e.currentTarget) onOpen()
      }}
    >
      <div className="proj-card-top">
        <ProjectMark project={p} size={46} />
        <div className="grow">
          <div className="proj-card-name truncate">{p.name}</div>
          <div className="proj-card-path truncate" title={p.path}>
            {basename(p.path)}
          </div>
        </div>
        <span onClick={(e) => e.stopPropagation()}>
          <Dropdown
            align="right"
            items={menu}
            trigger={(openMenu) => <Button variant="ghost" size="sm" icon={MoreHorizontal} tip="More" onClick={openMenu} />}
          />
        </span>
      </div>

      <p className={'proj-card-desc clamp-2' + (p.description ? '' : ' faint')}>{p.description || 'No description yet.'}</p>

      {p.health ? (
        <div className="proj-problem" onClick={(e) => e.stopPropagation()}>
          <TriangleAlert />
          <div className="grow">
            <div className="t">Needs attention</div>
            <div>{healthText(p)}</div>
            <div className="row gap-8" style={{ marginTop: 8 }}>
              <Button size="sm" onClick={onFix}>Locate folder…</Button>
              <Button size="sm" variant="ghost" onClick={onRemove}>Remove from list</Button>
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="proj-stats">
            <div>
              <Files />
              <b className="tnum">{fmtNum(s.documents)}</b> <span>{s.documents === 1 ? 'document' : 'documents'}</span>
            </div>
            <div>
              <Layers />
              <b className="tnum">{fmtNum(s.entities)}</b> <span>{s.entities === 1 ? 'entity' : 'entities'}</span>
            </div>
            {s.history_bytes != null && (
              <div className="proj-hist" data-tip="Space the version history of this investigation's notes, pages and records takes on this computer">
                <FileClock />
                <span className="tnum">{fmtBytes(s.history_bytes)}</span> <span>history</span>
              </div>
            )}
          </div>
          <div className="proj-card-foot">
            <span className="faint row gap-4">
              <Clock style={{ width: 13, height: 13 }} />
              {s.last_ingest ? `Last processed ${fmtRelative(s.last_ingest)}` : 'Nothing added yet'}
            </span>
            <span className="spacer" />
            {p.archived && <Badge icon={Archive}>Archived</Badge>}
            {s.failed > 0 && <Badge tone="danger" tip="Documents that failed extraction">{s.failed} failed</Badge>}
            {inProgress > 0 && <Badge tone="warning" tip="Documents waiting to be added or finished">{inProgress} in progress</Badge>}
            {!p.archived && !s.failed && !inProgress && s.documents > 0 && <Badge tone="success" icon={CircleCheck}>Up to date</Badge>}
          </div>
        </>
      )}
    </div>
  )
}

// ── Welcome ──────────────────────────────────────────────────────────────────
function Welcome({ onRegister }: { onRegister: () => void }) {
  return (
    <div className="proj-welcome card">
      <div className="proj-welcome-art">
        <BookOpenText />
      </div>
      <h2>Start your first investigation</h2>
      <p>
        An investigation is one folder on your computer that holds the documents you are working through, the people,
        organizations and events Watchdog finds in them, and the briefings it writes. Everything stays on this computer
        except the text sent to the model you choose, and you confirm that before each run.
      </p>
      <div className="row gap-8" style={{ justifyContent: 'center', marginTop: 8 }}>
        <Button variant="primary" size="lg" icon={Plus} onClick={newInvestigation}>
          New investigation
        </Button>
        <Button size="lg" icon={FolderPlus} onClick={onRegister}>
          Add existing folder…
        </Button>
      </div>
    </div>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────
function useMutation(onDone: () => void) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const run = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true)
    setError('')
    try {
      await fn()
      invalidate('projects.')
      toast({ kind: 'success', title: success })
      onDone()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }
  return { busy, error, run }
}

function RenameDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const [name, setName] = useState(p.name)
  const m = useMutation(onClose)
  const valid = name.trim().length > 0 && name.trim() !== p.name
  const go = () => valid && void m.run(() => runAction('projects-rename', { slug: p.slug, name: name.trim() }, null), 'Renamed')
  return (
    <Modal
      open
      onClose={onClose}
      title="Rename investigation"
      sub="The folder on disk is renamed to match."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!valid} loading={m.busy} onClick={go}>Rename</Button>
        </>
      }
    >
      <Field label="Name" error={m.error}>
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go()} />
      </Field>
    </Modal>
  )
}

function DescribeDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const [text, setText] = useState(p.description ?? '')
  const m = useMutation(onClose)
  const go = () => void m.run(() => runAction('projects-describe', { slug: p.slug, description: text.trim() }, null), text.trim() ? 'Description updated' : 'Description cleared')
  return (
    <Modal
      open
      onClose={onClose}
      title="Describe investigation"
      sub={p.name}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={m.busy} onClick={go}>Save</Button>
        </>
      }
    >
      <Field label="One-line description" hint="Leave empty to clear it." error={m.error}>
        <textarea className="textarea" autoFocus rows={3} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
    </Modal>
  )
}

function MoveDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const [dest, setDest] = useState('')
  const m = useMutation(onClose)
  const missing = p.health === 'missing'
  const pick = async () => {
    const d = await window.watchdog.dialog.openFolder({ grant: 'an investigation', title: missing ? 'Choose the folder that holds this investigation' : 'Move the investigation into…' })
    if (d) setDest(d)
  }
  return (
    <Modal
      open
      onClose={onClose}
      title={missing ? 'Locate the investigation folder' : 'Move investigation'}
      sub={p.name}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!dest} loading={m.busy} onClick={() => void m.run(() => runAction('projects-move', { slug: p.slug, path: dest }, null), missing ? 'Folder located' : 'Moved')}>
            {missing ? 'Use this folder' : 'Move'}
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        <dl className="kv">
          <dt>Currently at</dt>
          <dd className="mono">{p.path}</dd>
        </dl>
        {missing ? (
          <p className="muted" style={{ margin: 0 }}>If you moved the folder yourself, choose its new location. Nothing is copied or changed on disk.</p>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            The whole folder moves into the one you choose, and Watchdog keeps track of it. Close other programs that have its files open first.
          </p>
        )}
        <div className="row gap-8">
          <Button icon={FolderOpen} onClick={() => void pick()}>Choose folder…</Button>
          {dest && <span className="mono truncate faint" title={dest}>{dest}</span>}
        </div>
        {m.error && <Callout tone="danger">{m.error}</Callout>}
      </div>
    </Modal>
  )
}

function LogDialog({ p, onClose }: { p: Project; onClose: () => void }) {
  const { data, error, isLoading, refetch } = useRpc('projects.log', { slug: p.slug, lines: 400 })
  return (
    <Modal open onClose={onClose} title="Processing history" sub={p.name} width="wide" footer={<Button onClick={onClose}>Close</Button>}>
      {error ? (
        <ErrorNote error={error} retry={() => void refetch()} />
      ) : isLoading ? (
        <Skeleton h={180} />
      ) : !data?.lines.length ? (
        <Empty icon={History} title="Nothing added yet">Each run that adds documents to this investigation is recorded here.</Empty>
      ) : (
        <div className="log proj-log">
          {data.lines.map((l, i) => (
            <div key={i}>{l || ' '}</div>
          ))}
        </div>
      )}
    </Modal>
  )
}

// The confirmation (the name typed out) is this dialog's; the operation removes without asking.
async function deleteProject(p: Project, purge: boolean): Promise<void> {
  await runAction('projects-delete', { slug: p.slug, purge }, null)
  const left = await call('projects.list', { all: true })
  if (left.some((x) => x.slug === p.slug)) throw new Error('Watchdog did not remove the investigation. Nothing was deleted.')
}

function RemoveDialog({ p, purge, onClose }: { p: Project; purge: boolean; onClose: () => void }) {
  const [typed, setTyped] = useState('')
  const m = useMutation(onClose)
  const ok = !purge || typed.trim() === p.name
  return (
    <Modal
      open
      onClose={onClose}
      title={purge ? 'Delete files permanently?' : 'Remove from Watchdog?'}
      sub={p.name}
      footer={
        <>
          <Button variant="default" autoFocus onClick={onClose}>Cancel</Button>
          <Button variant="danger" disabled={!ok} loading={m.busy} onClick={() => void m.run(() => deleteProject(p, purge), purge ? 'Investigation deleted' : 'Removed from the list')}>
            {purge ? 'Delete everything' : 'Remove from list'}
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        {purge ? (
          <>
            <Callout tone="danger" title="This cannot be undone">
              The folder <span className="mono">{p.path}</span> and everything in it will be permanently deleted: your documents, extracted notes, briefings and any annotations. Watchdog’s usage and cost records for this investigation are deleted too. Nothing goes to the trash.
            </Callout>
            <Field label={<>Type <b>{p.name}</b> to confirm</>}>
              <input className="input" autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={p.name} />
            </Field>
          </>
        ) : (
          <p style={{ margin: 0 }} className="muted">
            {p.name} disappears from this list and from Obsidian’s vault list. The folder and its files stay where they are, and you can add it back later with “Add existing folder”.
          </p>
        )}
        {m.error && <Callout tone="danger">{m.error}</Callout>}
      </div>
    </Modal>
  )
}

function RegisterDialog({ onClose }: { onClose: () => void }) {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const m = useMutation(onClose)
  const pick = async () => {
    const d = await window.watchdog.dialog.openFolder({ title: 'Choose an existing investigation folder', grant: 'an investigation' })
    if (!d) return
    setPath(d)
    if (!name) setName(basename(d).replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()))
  }
  return (
    <Modal
      open
      onClose={onClose}
      title="Add an existing folder"
      sub="Use this for an investigation folder that Watchdog created earlier, on this or another computer."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          <Button
            variant="primary"
            disabled={!path || !name.trim()}
            loading={m.busy}
            onClick={() =>
              void m.run(async () => {
                await runAction('projects-register', { path, name: name.trim() }, null)
                const list = await call('projects.list', {})
                const added = list.find((x) => x.path === path)
                if (added) useApp.getState().setProject(added)
              }, 'Investigation added')
            }
          >
            Add investigation
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        <div className="row gap-8">
          <Button icon={FolderOpen} onClick={() => void pick()}>Choose folder…</Button>
          {path && <span className="mono truncate faint" title={path}>{path}</span>}
        </div>
        <Field label="Name" hint="The folder must contain a .watchdog folder, which every Watchdog investigation has.">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Investigation name" />
        </Field>
        {m.error && <Callout tone="danger">{m.error}</Callout>}
      </div>
    </Modal>
  )
}

function DoctorDialog({ onClose }: { onClose: () => void }) {
  const { data, error, isFetching, refetch } = useRpc('projects.doctor', {}, { staleTime: 0 })
  const issues: DoctorIssue[] = data?.issues ?? []
  return (
    <Modal
      open
      onClose={onClose}
      title="Check vaults"
      sub="Looks for investigations whose folders are missing, damaged or out of date."
      width="wide"
      footer={
        <>
          <Button variant="ghost" loading={isFetching} onClick={() => void refetch()}>Check again</Button>
          <Button onClick={onClose}>Close</Button>
        </>
      }
    >
      {error ? (
        <ErrorNote error={error} retry={() => void refetch()} />
      ) : !data ? (
        <Skeleton h={90} />
      ) : issues.length === 0 ? (
        <Callout tone="success" title="No problems found">Every registered investigation has a readable folder and a current registry.</Callout>
      ) : (
        <div className="col gap-12">
          {issues.map((i, k) => (
            <div className="proj-issue" key={i.slug + k}>
              <TriangleAlert />
              <div className="grow">
                <div style={{ fontWeight: 620 }}>{i.name}</div>
                <div className="mono faint truncate" title={i.path}>{i.path}</div>
                <div style={{ marginTop: 6 }}>{i.problem}</div>
                {i.suggestion && <div className="muted" style={{ marginTop: 2 }}>{i.suggestion}</div>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Modal>
  )
}
