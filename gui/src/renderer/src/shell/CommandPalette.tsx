// ⌘K: jump to any entity, document or view, start a search or a question, run common actions.

import { Command } from 'cmdk'
import {
  Activity, BookOpenText, CalendarRange, FilePlus2, Files, FolderOpen, Globe2, House, Link2, ListChecks,
  MessageSquareText, Moon, Network, Plus, Search, Settings, Shapes, Sun
} from 'lucide-react'
import { useState } from 'react'
import { typeMeta } from '@renderer/lib/entityTypes'
import { useRpc } from '@renderer/lib/rpc'
import { Route, useApp } from '@renderer/lib/store'

export function CommandPalette() {
  const { paletteOpen, setPalette, project, navigate, openAdd, setProject, setTheme } = useApp()
  const [q, setQ] = useState('')
  const vault = project?.path
  const { data: entities } = useRpc('vault.entities', vault && paletteOpen ? { vault } : null)
  const { data: documents } = useRpc('vault.documents', vault && paletteOpen ? { vault } : null)
  const { data: projects } = useRpc('projects.list', paletteOpen ? {} : null)
  if (!paletteOpen) return null

  const close = () => {
    setPalette(false)
    setQ('')
  }
  const go = (r: Route) => {
    close()
    navigate(r)
  }
  const cmd = (name: string) => {
    close()
    window.dispatchEvent(new CustomEvent('wd:command', { detail: name }))
  }
  const views: { label: string; icon: typeof House; route: Route }[] = project
    ? [
        { label: 'Overview', icon: House, route: { view: 'home' } },
        { label: 'Documents', icon: Files, route: { view: 'documents' } },
        { label: 'Entities', icon: Shapes, route: { view: 'entities' } },
        { label: 'Timeline', icon: CalendarRange, route: { view: 'timeline' } },
        { label: 'Network', icon: Network, route: { view: 'graph' } },
        { label: 'Review', icon: ListChecks, route: { view: 'review' } },
        { label: 'Briefings', icon: BookOpenText, route: { view: 'briefings' } },
        { label: 'Activity', icon: Activity, route: { view: 'activity' } },
        { label: 'Settings', icon: Settings, route: { view: 'settings' } }
      ]
    : [
        { label: 'All investigations', icon: FolderOpen, route: { view: 'projects' } },
        { label: 'Settings', icon: Settings, route: { view: 'settings' } }
      ]
  const query = q.trim()

  return (
    <div className="palette-scrim" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <Command className="palette" label="Command palette" loop onKeyDown={(e) => e.key === 'Escape' && close()}>
        <div className="palette-search">
          <Search aria-hidden="true" />
          <Command.Input autoFocus value={q} onValueChange={setQ} placeholder={project ? 'Search the investigation, jump to an entity or document…' : 'Jump to…'} />
          <kbd className="kbd">Esc</kbd>
        </div>
        <Command.List>
          <Command.Empty>No matches.</Command.Empty>
          {project && query && (
            <Command.Group heading="Ask">
              <Command.Item value={`search ${query}`} onSelect={() => go({ view: 'search', query })}>
                <Search />
                Search documents for “{query}”
              </Command.Item>
              <Command.Item value={`ask ${query}`} onSelect={() => go({ view: 'ask', prompt: query })}>
                <MessageSquareText />
                Ask Claude: “{query}”
              </Command.Item>
            </Command.Group>
          )}
          {project && (
            <Command.Group heading="Actions">
              <Command.Item onSelect={() => { close(); openAdd() }}>
                <FilePlus2 />
                Add documents…
              </Command.Item>
              <Command.Item onSelect={() => cmd('fetch-links')}>
                <Link2 />
                Fetch links into the investigation…
              </Command.Item>
              <Command.Item onSelect={() => go({ view: 'research' })}>
                <Globe2 />
                Start web research
              </Command.Item>
            </Command.Group>
          )}
          <Command.Group heading="Go to">
            {views.map((v) => (
              <Command.Item key={v.label} value={`go ${v.label}`} onSelect={() => go(v.route)}>
                <v.icon />
                {v.label}
              </Command.Item>
            ))}
          </Command.Group>
          {query.length > 0 && entities && (
            <Command.Group heading="Entities">
              {entities.slice(0, 400).map((e) => {
                const m = typeMeta(e.type)
                return (
                  <Command.Item key={e.id} value={`entity ${e.name} ${e.aliases.join(' ')} ${e.id}`} onSelect={() => go({ view: 'entity', id: e.id })}>
                    <m.icon style={{ color: m.color }} />
                    {e.name}
                    <span className="hint">{m.label} · {e.doc_count} doc{e.doc_count === 1 ? '' : 's'}</span>
                  </Command.Item>
                )
              })}
            </Command.Group>
          )}
          {query.length > 0 && documents && (
            <Command.Group heading="Documents">
              {documents.slice(0, 400).map((d) => (
                <Command.Item key={d.sha} value={`doc ${d.title ?? ''} ${d.filename} ${d.document_type ?? ''}`} onSelect={() => go({ view: 'document', sha: d.sha })}>
                  <Files />
                  <span className="truncate">{d.title || d.filename}</span>
                  <span className="hint">{d.document_type}</span>
                </Command.Item>
              ))}
            </Command.Group>
          )}
          {projects && projects.length > 1 && (
            <Command.Group heading="Investigations">
              {projects.filter((p) => !p.archived).map((p) => (
                <Command.Item key={p.slug} value={`investigation ${p.name}`} onSelect={() => { close(); setProject(p) }}>
                  <FolderOpen />
                  {p.name}
                </Command.Item>
              ))}
              <Command.Item onSelect={() => cmd('new-investigation')}>
                <Plus />
                New investigation…
              </Command.Item>
            </Command.Group>
          )}
          <Command.Group heading="Appearance">
            <Command.Item onSelect={() => { setTheme('light'); close() }}><Sun />Light appearance</Command.Item>
            <Command.Item onSelect={() => { setTheme('dark'); close() }}><Moon />Dark appearance</Command.Item>
            <Command.Item onSelect={() => { setTheme('system'); close() }}><Settings />Match system appearance</Command.Item>
          </Command.Group>
        </Command.List>
        <div className="palette-foot" aria-hidden="true">
          <span><kbd className="kbd">↑</kbd><kbd className="kbd">↓</kbd> to move</span>
          <span><kbd className="kbd">↵</kbd> to open</span>
          <span><kbd className="kbd">Esc</kbd> to close</span>
        </div>
      </Command>
    </div>
  )
}
