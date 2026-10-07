// Ask: a Claude Code session inside the investigation — replaces `watchdog ask` and
// `watchdog ask --context`.

import { FileText, Globe, Sprout } from 'lucide-react'
import { Button } from '@renderer/components/ui'
import { useRpc } from '@renderer/lib/rpc'
import { fmtBytes } from '@renderer/lib/format'
import { navigate, useApp } from '@renderer/lib/store'
import ChatWorkspace, { WorkspaceApi } from './ChatWorkspace'
import { SLASH } from './ChatParts'

function ContextCard({ api }: { api: WorkspaceApi }) {
  const vault = useApp((s) => s.project!.path)
  const files = useRpc('vault.contextFiles', { vault })
  const list = files.data ?? []
  return (
    <div className="chat-ctx">
      <div className="row" style={{ gap: 10 }}>
        <div className="chat-ctx-icon"><Sprout /></div>
        <div className="grow">
          <div style={{ fontWeight: 640 }}>Seed investigation context</div>
          <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>Tell Watchdog what the story is, so extraction and briefings are framed by it.</div>
        </div>
        <Button variant="primary" onClick={() => void api.begin('', 'context')} loading={api.busy}>Start</Button>
      </div>
      <p className="chat-ctx-text">
        Claude reads the background files in <span className="mono">_CONTEXT/</span> (briefs, earlier reporting, notes you already hold), interviews you where they fall short, and writes <span className="mono">context.md</span> at the top of the vault.
        An existing <span className="mono">context.md</span> is updated, not replaced. {list.length === 0 ? 'The folder is empty, so Claude will interview you instead.' : 'Files it will read:'}
      </p>
      {list.length > 0 && (
        <ul className="chat-ctx-files">
          {list.slice(0, 8).map((f) => (
            <li key={f.name}><FileText /> <span className="truncate">{f.name}</span><span className="faint tnum">{fmtBytes(f.size)}</span></li>
          ))}
          {list.length > 8 && <li className="faint">and {list.length - 8} more</li>}
        </ul>
      )}
    </div>
  )
}

function Empty({ api, name }: { api: WorkspaceApi; name: string }) {
  return (
    <div className="chat-empty">
      <h2>Ask about {name}</h2>
      <p>
        Claude works inside this investigation: it reads your notes, the registry and the extracted documents, answers with citations, and files substantial answers to <span className="mono">queries/</span>. Type a question below, or start from a command.
      </p>
      <div className="chat-chips">
        {SLASH.map((s) => (
          <button
            key={s.name}
            className="chat-chip"
            onClick={() => {
              if (s.name === '/watchdog-research') navigate({ view: 'research' })
              else if (s.name === '/watchdog-context') void api.begin('', 'context')
              else if (s.takesInput) api.prefill(s.name + ' ')
              else void api.begin(s.name)
            }}
          >
            <span className="mono chat-chip-name">{s.name}{s.name === '/watchdog-research' && <Globe />}</span>
            <span className="chat-chip-text">{s.text}</span>
            <span className="chat-chip-act">{s.name === '/watchdog-research' ? 'Opens Web research' : s.takesInput ? 'Add your question' : 'Runs now'}</span>
          </button>
        ))}
      </div>
      <ContextCard api={api} />
    </div>
  )
}

export default function AskView() {
  const project = useApp((s) => s.project)
  const route = useApp((s) => s.route)
  if (!project) return null
  const r = route.view === 'ask' ? route : { view: 'ask' as const }
  return (
    <ChatWorkspace
      key={project.slug}
      view="ask"
      modes={['ask', 'context']}
      mode="ask"
      title="Ask Claude"
      placeholder="Ask a question about the documents…"
      session={r.session}
      prompt={r.prompt}
      autoStart={'mode' in r && r.mode === 'context' ? 'context' : undefined}
      terminalArgs={(t) => (t.trim() && !t.startsWith('/') ? ['ask', t.trim()] : ['ask'])}
      empty={(api) => <Empty api={api} name={project.name} />}
    />
  )
}
