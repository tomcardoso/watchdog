// Settings → Version history (D286): how much history the open investigation keeps, and clearing
// it. Clearing removes every past version and keeps the current files; it cannot be undone.

import { FileClock, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { Button, Callout, Card, Empty, ErrorNote, Modal, Skeleton } from '@renderer/components/ui'
import { fmtBytes, fmtDateTime, fmtNum, plural } from '@renderer/lib/format'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp } from '@renderer/lib/store'

export function HistoryPanel() {
  const project = useApp((s) => s.project)
  const vault = project?.path ?? null
  const st = useRpc('history.stats', vault ? { vault } : null, { staleTime: 0 })
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)

  if (!project || !vault)
    return (
      <Empty icon={FileClock} title="Open an investigation">
        Each investigation keeps its own version history. Open one to see how much it holds or to clear it.
      </Empty>
    )
  const clear = async () => {
    setBusy(true)
    try {
      const r = await call('history.clear', { vault })
      invalidate('history.', 'projects.')
      toast({ kind: 'success', title: 'History cleared', body: `${plural(r.removed_versions, 'version')} removed, ${fmtBytes(r.freed_bytes)} freed. The current files were kept.` })
      setConfirm(false)
    } catch (e) {
      toast({ kind: 'error', title: 'Could not clear the history', body: errorMessage(e) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="col gap-16">
      <Callout tone="info" title="What the history keeps">
        Every time Watchdog changes a note, briefing, page or its records — when documents are added, entities merged, a fact marked, or you edit in the app — it keeps the earlier version, so you can see what changed and put your own writing back. Original documents are not part of it. Text you delete stays in the history until you clear it.
      </Callout>
      <Card title={project.name} sub="Version history" actions={<Button variant="ghost" size="sm" onClick={() => navigate({ view: 'activity', tab: 'versions' })}>Browse versions</Button>}>
        {st.isLoading ? (
          <Skeleton h={48} />
        ) : st.error ? (
          <ErrorNote error={st.error} retry={() => void st.refetch()} />
        ) : (
          <div className="col gap-12">
            <div className="muted">
              {st.data!.versions
                ? <>{plural(st.data!.versions, 'version')} of {plural(st.data!.files, 'file')}, {fmtBytes(st.data!.bytes)} on disk. The earliest is from {fmtDateTime(st.data!.since)}</>
                : 'No versions recorded yet.'}
            </div>
            <div>
              <Button variant="danger" icon={Trash2} disabled={!st.data!.versions || st.data!.too_new} onClick={() => setConfirm(true)}>
                Clear history…
              </Button>
            </div>
          </div>
        )}
      </Card>
      {confirm && st.data && (
        <Modal
          open
          onClose={() => setConfirm(false)}
          title="Clear this investigation’s history?"
          sub={project.name}
          footer={
            <>
              <Button autoFocus onClick={() => setConfirm(false)}>Cancel</Button>
              <Button variant="danger" icon={Trash2} loading={busy} onClick={() => void clear()}>Clear history</Button>
            </>
          }
        >
          <Callout tone="danger" title="This cannot be undone">
            All {fmtNum(st.data.versions)} past versions are deleted, including any text that has since been removed from the files. Your notes, briefings, pages and records stay exactly as they are now, and become the first version of a new history.
          </Callout>
        </Modal>
      )}
    </div>
  )
}
