// Watchlist: the terms every new batch of documents is scanned for (D343). Its own screen under
// Work: a standing instruction, not part of the brief (Context) or a finding (Review). The
// matches stay in Review's Alerts tab, where they are handled like every other item; each term
// here links to its open ones.

import { Plus, ScanSearch, Trash2, Zap } from 'lucide-react'
import { useState } from 'react'
import { Button, Callout, Empty, ErrorNote, Skeleton } from '@renderer/components/ui'
import { HistoryButton } from '@renderer/components/FileHistory'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { startJob } from '@renderer/lib/jobs'
import { navigate, toast, useVault } from '@renderer/lib/store'
import { plural } from '@renderer/lib/format'
import '../review/review.css'

function WatchlistPanel({ vault }: { vault: string }) {
  const q = useRpc('review.watchlist', vault ? { vault } : null, { staleTime: 0 })
  const [draft, setDraft] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const terms = q.data?.terms ?? []

  const add = async () => {
    const term = draft.trim()
    if (!term) return setProblem('Type a name or term to add.')
    if (terms.some((t) => t.term.toLowerCase() === term.toLowerCase())) return setProblem('That term is already on the list.')
    setBusy('add')
    try {
      await call('review.watchlistAdd', { vault, term })
      setDraft('')
      setProblem(null)
      await q.refetch()
      invalidate('history.')
    } catch (e) {
      setProblem(errorMessage(e))
    } finally {
      setBusy(null)
    }
  }
  const remove = async (term: string) => {
    setBusy(term)
    try {
      await call('review.watchlistRemove', { vault, term })
      await q.refetch()
      invalidate('history.')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not remove it', body: errorMessage(e) })
    } finally {
      setBusy(null)
    }
  }
  const sweep = async () => {
    setRunning(true)
    try {
      await startJob('watchlist-check', {}, 'Check every document against the watchlist')
      toast({ kind: 'info', title: 'Checking every document', body: 'Matches appear under Alerts when it finishes. Follow progress in Activity.' })
    } catch (e) {
      toast({ kind: 'error', title: 'Could not start the check', body: errorMessage(e) })
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="rv-queue">
      <div className="rv-blurb">
        <ScanSearch />
        <p>
          Terms you want flagged whenever they appear in a document: a name, a company, an address, a phrase. Matching ignores capitals and respects whole words, so Ana does not match banana. Wrap a term in slashes, like <code>/Acme\s+(Ltd|Inc)/</code>, to use a regular expression, a pattern-matching syntax. Each new batch of documents is scanned automatically.
        </p>
      </div>
      <form
        className="rv-watch-add"
        onSubmit={(e) => {
          e.preventDefault()
          void add()
        }}
      >
        <input className="input" aria-label="Add a term" value={draft} placeholder="Add a term, such as a name or company" onChange={(e) => (setDraft(e.target.value), setProblem(null))} spellCheck={false} />
        <Button type="submit" variant="primary" icon={Plus} loading={busy === 'add'}>
          Add term
        </Button>
      </form>
      {problem && (
        <div className="rv-watch-problem" role="alert">
          {problem}
        </div>
      )}
      {q.error ? (
        <ErrorNote error={q.error} retry={() => void q.refetch()} />
      ) : q.isLoading ? (
        <Skeleton h={160} />
      ) : !terms.length ? (
        <Empty icon={ScanSearch} title="No terms yet">
          An empty list scans for nothing. Add a name or company above.
        </Empty>
      ) : (
        <ul className="rv-watch-list" aria-label="Watchlist terms">
          {terms.map((t) => (
            <li key={t.term} className="rv-watch-row">
              <span className="rv-watch-term truncate">{t.term}</span>
              {t.regex && <span className="faint rv-watch-kind">pattern</span>}
              <span className="spacer" />
              {t.hits > 0 ? (
                <button type="button" className="rv-watch-hits" onClick={() => navigate({ view: 'review', kind: 'alerts', filter: t.term })}>
                  {plural(t.hits, 'open hit')}
                </button>
              ) : (
                <span className="faint">No open hits</span>
              )}
              <Button size="sm" variant="ghost" icon={Trash2} loading={busy === t.term} aria-label={`Remove ${t.term}`} onClick={() => void remove(t.term)}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div className="rv-toolbar">
        <span className="faint">{terms.length ? plural(terms.length, 'term') : 'No terms yet'}</span>
        <span className="spacer" />
        <HistoryButton vault={vault} path="watchlist.md" size="md" variant="ghost" />
      </div>
      <Callout tone="info" title="Check every document now" action={<Button icon={Zap} loading={running} disabled={!terms.length} onClick={() => void sweep()}>Check every document now</Button>}>
        The scan at the end of each run only sees that run&apos;s new documents, so a term added now is never compared with the documents already in the investigation. This sweeps the whole library against the current list. No model is called. Matches go to Alerts.
      </Callout>
    </div>
  )
}

export default function WatchlistView() {
  const vault = useVault()
  if (!vault) return null
  return (
    <div className="page">
      <div className="page-inner rv">
        <div className="page-header" style={{ marginBottom: 14 }}>
          <div>
            <h1 className="page-title">Watchlist</h1>
            <div className="page-sub">Names and terms every new batch of documents is checked for. Matches appear in Review, under Alerts.</div>
          </div>
        </div>
        <WatchlistPanel vault={vault} />
      </div>
    </div>
  )
}
