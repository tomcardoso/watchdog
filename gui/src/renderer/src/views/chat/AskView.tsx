// Ask: a Claude Code session inside the investigation, for questions. Setting its context has its
// own screen (ContextView, D342).

import { Globe } from 'lucide-react'
import { navigate, useApp } from '@renderer/lib/store'
import ChatWorkspace, { WorkspaceApi } from './ChatWorkspace'
import { SLASH } from './ChatParts'

function Empty({ api, name }: { api: WorkspaceApi; name: string }) {
  return (
    <div className="chat-empty">
      <h2>Ask about {name}</h2>
      <p>
        Claude works inside this investigation: it reads your notes, the registry and the extracted documents, answers with citations, and files substantial answers to <span className="mono">queries/</span>. Type a question below, or start from one of these.
      </p>
      <div className="chat-chips">
        {SLASH.filter((s) => s.name !== '/watchdog-context').map((s) => (
          <button
            key={s.name}
            className="chat-chip"
            onClick={() => {
              if (s.name === '/watchdog-research') navigate({ view: 'research' })
              else if (s.takesInput) api.prefill(s.name + ' ')
              else void api.begin(s.name)
            }}
          >
            <span className="chat-chip-name">{s.label}{s.name === '/watchdog-research' && <Globe />}</span>
            <span className="chat-chip-text">{s.text}</span>
            <span className="chat-chip-act">{s.name === '/watchdog-research' ? 'Opens Web research' : s.takesInput ? 'Add your question' : 'Runs now'}</span>
          </button>
        ))}
      </div>
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
      modes={['ask']}
      mode="ask"
      title="Ask Claude"
      placeholder="Ask a question about the documents…"
      session={r.session}
      prompt={r.prompt}
      empty={(api) => <Empty api={api} name={project.name} />}
      samplePlayback
    />
  )
}
