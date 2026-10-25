// Web research: a Claude Code session that queues sources, then a deterministic download into
// incoming/.

import { AlertTriangle, Download, ExternalLink, Globe, Inbox, RotateCw, X } from 'lucide-react'
import { useState } from 'react'
import { Badge, Button, Callout } from '@renderer/components/ui'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { startJob, waitForJob } from '@renderer/lib/jobs'
import { investigationName, isElsewhere, switchTo } from '@renderer/lib/investigation'
import { plural } from '@renderer/lib/format'
import { navigate, toast, useApp } from '@renderer/lib/store'
import ChatWorkspace, { WorkspaceApi } from './ChatWorkspace'

const STEPS = [
  'Proposes a research mission from the vault’s open gaps and leads.',
  'Confirms the question, and how wide to cast the net: quick, standard or deep.',
  'Researches in rounds, checking in with you between each.',
  'Queues every source it keeps, with a reliability tag and why it matters.',
  'Writes a research memo to briefings/.'
]

function useDownload() {
  const [busy, setBusy] = useState(false)
  const go = async (n: number) => {
    setBusy(true)
    try {
      const vault = useApp.getState().project?.path ?? null
      const job = await startJob('research-fetch', {}, `Download ${plural(n, 'source')}`, 'research-fetch', vault)
      toast({ kind: 'info', title: 'Downloading sources', body: 'Each is checked and saved with a note of where it came from, ready to add.', action: { label: 'Show output', run: () => navigate({ view: 'activity', job: job.id }) } })
      void waitForJob(job.id).then((j) => {
        invalidate('research.status')
        if (j.state !== 'done') return
        // The download may finish after the reporter has opened another investigation: say
        // which one the sources went to, and add them there.
        const elsewhere = isElsewhere(vault) ? investigationName(vault) : null
        toast({
          kind: 'success',
          title: elsewhere ? `Sources downloaded to ${elsewhere}` : 'Sources downloaded',
          body: elsewhere ? `Switch to ${elsewhere} to add them.` : 'Add them to read and extract them.',
          action: {
            label: elsewhere ? `Switch to ${elsewhere} and add them` : 'Add them',
            run: () => void (elsewhere && vault ? switchTo(vault) : Promise.resolve(true)).then((ok) => ok && useApp.getState().openAdd())
          }
        })
      })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the download', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  return { busy, go }
}

/** A download error in plain words: the site refused, it didn't answer in time, or something else. */
function why(error: string): string {
  if (/HTTP 40[13]/.test(error)) return 'the site refused the download'
  if (/HTTP 404|HTTP 410/.test(error)) return 'the page no longer exists'
  if (/HTTP \d+/.test(error)) return 'the site returned an error'
  if (/timed out/i.test(error)) return 'the site took too long to answer'
  if (/only http|public|private|refused: /i.test(error) && !/fetch/i.test(error)) return 'the address can’t be downloaded'
  return 'it couldn’t be fetched'
}

function Strip({ api }: { api: WorkspaceApi }) {
  const vault = useApp((s) => s.project!.path)
  const q = useRpc('research.status', { vault }, { refetchInterval: api.live ? 4000 : false })
  const dl = useDownload()
  const [open, setOpen] = useState(false)
  const [removing, setRemoving] = useState(false)
  const all = q.data?.queued ?? []
  const fresh = all.filter((s) => !s.failed)
  const failed = all.filter((s) => s.failed)
  if (!all.length) return null
  // Sources that failed stay queued so they can be tried again (D346); they are shown as failed,
  // with the reason, never as still waiting to download.
  const remove = async () => {
    setRemoving(true)
    try {
      await call('research.dismiss', { vault, urls: failed.map((s) => s.url) })
      invalidate('research.status')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not remove them', body: errorMessage(e) })
    } finally {
      setRemoving(false)
    }
  }
  return (
    <div className="chat-strip">
      {fresh.length > 0 && (
        <div className="row" style={{ gap: 10 }}>
          <Inbox style={{ width: 16, height: 16, color: 'var(--text-3)' }} />
          <button className="chat-strip-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {plural(fresh.length, 'source')} queued
          </button>
          <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Nothing is downloaded until you say so.</span>
          <span className="spacer" />
          <Button size="sm" variant="primary" icon={Download} loading={dl.busy} onClick={() => void dl.go(all.length)}>
            Download {plural(fresh.length, 'source')}
          </Button>
        </div>
      )}
      {failed.length > 0 && (
        <div className="row" style={{ gap: 10, marginTop: fresh.length ? 8 : 0 }}>
          <AlertTriangle style={{ width: 16, height: 16, color: 'var(--warning)' }} />
          <button className="chat-strip-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {failed.length === 1 ? '1 source couldn’t be downloaded' : `${failed.length} sources couldn’t be downloaded`}
          </button>
          <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>{failed.length === 1 ? why(failed[0].failed!.error) : 'Show them for the reasons.'}</span>
          <span className="spacer" />
          {fresh.length === 0 && (
            <Button size="sm" icon={RotateCw} loading={dl.busy} onClick={() => void dl.go(all.length)}>Try again</Button>
          )}
          <Button size="sm" variant="ghost" icon={X} loading={removing} onClick={() => void remove()}>Remove</Button>
        </div>
      )}
      {open && (
        <ul className="chat-strip-list">
          {all.map((s) => (
            <li key={s.url}>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="truncate" style={{ fontWeight: 560 }}>{s.title || s.url}</div>
                <button className="chat-url truncate" onClick={() => void window.watchdog.shell.openExternal(s.url)}>{s.url}<ExternalLink /></button>
                {s.failed ? (
                  <div style={{ fontSize: 'var(--fs-sm)', color: 'var(--warning)' }} title={s.failed.error}>
                    Not downloaded: {why(s.failed.error)}{s.failed.attempts > 1 ? ` (tried ${s.failed.attempts} times)` : ''}. You can open it in your browser and save it yourself.
                  </div>
                ) : (
                  s.relevance && <div className="muted clamp-2" style={{ fontSize: 'var(--fs-sm)' }}>{s.relevance}</div>
                )}
              </div>
              {s.source_type && <Badge>{s.source_type}</Badge>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Empty({ api }: { api: WorkspaceApi }) {
  const vault = useApp((s) => s.project!.path)
  const status = useRpc('research.status', { vault })
  const [q, setQ] = useState('')
  const dl = useDownload()
  const stale = status.data?.queued.filter((s) => !s.failed).length ?? 0
  return (
    <div className="chat-empty">
      <h2>Research the open questions on the web</h2>
      <p>
        Seeded by your vault, Claude does bounded web research and queues the sources it finds. When you are ready, Watchdog downloads them, and you add them like any other document, so findings go through the same reading and extraction. Claude never writes vault notes directly. In the session it will:
      </p>
      <ol className="chat-steps">
        {STEPS.map((s, i) => (
          <li key={i}><span>{i + 1}</span>{s}</li>
        ))}
      </ol>
      {stale > 0 && (
        <Callout tone="warning" title={`${plural(stale, 'source')} from an earlier session ${stale === 1 ? 'is' : 'are'} queued and not downloaded`} action={<Button size="sm" loading={dl.busy} onClick={() => void dl.go(stale)}>Download now</Button>}>
          Leaving them queued is fine; they are never discarded.
        </Callout>
      )}
      {status.data && !status.data.wayback_configured && (
        <Callout tone="info" title="Optional: archive each source on the Wayback Machine" action={<Button size="sm" onClick={() => navigate({ view: 'settings', tab: 'web-archiving' })}>Open Settings</Button>}>
          With free archive.org keys, every source you find is also saved to the Internet Archive and its snapshot link recorded.
        </Callout>
      )}
      <div className="chat-seed">
        <label className="field-label" htmlFor="seed">Research question (optional)</label>
        <textarea id="seed" className="textarea" rows={2} value={q} placeholder="Leave blank to let Claude propose one from the vault" onChange={(e) => setQ(e.target.value)} />
        <div className="row" style={{ gap: 8 }}>
          <Button variant="primary" icon={Globe} loading={api.busy} onClick={() => void api.begin(q.trim() ? `/watchdog-research ${q.trim()}` : '/watchdog-research')}>
            Start research
          </Button>
          <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Sessions run on the Claude you are signed in with. Exit with End session; queued sources stay queued.</span>
        </div>
      </div>
    </div>
  )
}

export default function ResearchView() {
  const project = useApp((s) => s.project)
  const route = useApp((s) => s.route)
  if (!project) return null
  const r = route.view === 'research' ? route : { view: 'research' as const }
  return (
    <ChatWorkspace
      key={project.slug}
      view="research"
      modes={['research']}
      mode="research"
      title="Web research"
      placeholder="Reply to Claude, or add a direction for the research…"
      session={r.session}
      empty={(api) => <Empty api={api} />}
      strip={(api) => <Strip api={api} />}
    />
  )
}
