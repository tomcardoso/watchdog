// Pieces of the conversation: tool-call rows, permission cards, the thinking indicator and the
// slash-command catalogue shared by Ask and Research.

import { AlertTriangle, ChevronRight, FileText, Globe, Pencil, ShieldQuestion, Terminal, Wrench, Search as SearchIcon } from 'lucide-react'
import { useState } from 'react'
import type { ChatMessage } from '@shared/api'
import { Button, cx } from '@renderer/components/ui'

export interface SlashCommand {
  name: string
  hint: string
  takesInput: boolean
  /** What it does, one line (docs/commands.md § Slash commands). */
  text: string
}
export const SLASH: SlashCommand[] = [
  { name: '/watchdog-query', hint: '[question]', takesInput: true, text: 'Answer a question from your vault, with sources.' },
  { name: '/watchdog-surface', hint: '', takesInput: false, text: 'Find connections and anomalies across the full vault.' },
  { name: '/watchdog-entity', hint: '[id…]', takesInput: true, text: 'Refresh an entity’s summary and timeline from all its source documents.' },
  { name: '/watchdog-wiki', hint: '', takesInput: false, text: 'Create or update investigation thread pages in wiki/.' },
  { name: '/watchdog-health', hint: '', takesInput: false, text: 'Check vault integrity: orphaned notes, broken links, registry mismatches, open contradictions.' },
  { name: '/watchdog-context', hint: '', takesInput: false, text: 'Seed context.md from the background files in context/.' },
  { name: '/watchdog-research', hint: '[question]', takesInput: true, text: 'Research open questions on the web, queuing sources for download into incoming/.' }
]

const KEYS = ['command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'description']
export function summarizeInput(input: unknown): string {
  if (typeof input === 'string') return input
  if (input && typeof input === 'object') {
    const o = input as Record<string, unknown>
    for (const k of KEYS) if (typeof o[k] === 'string' && o[k]) return String(o[k])
    try {
      return JSON.stringify(o)
    } catch {
      return ''
    }
  }
  return ''
}
const cut = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + '…' : s)
const toolName = (n: string) => n.replace(/^mcp__/, '').replace(/__/g, ' · ')

function toolIcon(name: string) {
  const n = name.toLowerCase()
  if (n.includes('bash')) return Terminal
  if (n.includes('web') || n.includes('fetch')) return Globe
  if (n.includes('grep') || n.includes('glob') || n.includes('search')) return SearchIcon
  if (n.includes('edit') || n.includes('write')) return Pencil
  if (n.includes('read')) return FileText
  return Wrench
}

export function ToolRow({ msg }: { msg: ChatMessage }) {
  const [open, setOpen] = useState(false)
  const t = msg.tool
  if (!t) return null
  const Icon = t.is_error ? AlertTriangle : toolIcon(t.name)
  const pending = t.result === null && !t.is_error
  let input = ''
  try {
    input = typeof t.input === 'string' ? t.input : JSON.stringify(t.input, null, 2)
  } catch {
    input = String(t.input)
  }
  return (
    <div className={cx('chat-tool', t.is_error && 'err', open && 'open')}>
      <button className="chat-tool-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight className="chev" />
        <Icon className="ico" />
        <span className="chat-tool-name">{toolName(t.name)}</span>
        <span className="chat-tool-sum truncate">{cut(summarizeInput(t.input).replace(/\s+/g, ' '), 140)}</span>
        {pending && <span className="spinner" style={{ width: 11, height: 11 }} />}
      </button>
      {open && (
        <div className="chat-tool-body selectable">
          <div className="eyebrow">Input</div>
          <pre>{cut(input, 4000)}</pre>
          <div className="eyebrow" style={{ marginTop: 8 }}>{t.is_error ? 'Error' : 'Result'}</div>
          <pre className={t.is_error ? 'err' : undefined}>{pending ? 'Waiting for the result…' : cut(t.result ?? '', 6000) || '(empty)'}</pre>
        </div>
      )}
    </div>
  )
}

export interface PermissionRequest {
  request_id: string
  tool: string
  input: unknown
  description: string | null
}
export function PermissionCard({ req, onAnswer }: { req: PermissionRequest; onAnswer: (allow: boolean, always?: boolean) => void }) {
  const [busy, setBusy] = useState(false)
  const answer = (a: boolean, always?: boolean) => {
    setBusy(true)
    onAnswer(a, always)
  }
  return (
    <div className="chat-perm" role="alertdialog" aria-label="Permission request">
      <div className="chat-perm-head">
        <ShieldQuestion />
        <div>
          <div style={{ fontWeight: 620 }}>Claude wants to use {toolName(req.tool)}</div>
          {req.description && <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>{req.description}</div>}
        </div>
      </div>
      <pre className="chat-perm-input selectable">{cut(typeof req.input === 'string' ? req.input : JSON.stringify(req.input, null, 2), 1200)}</pre>
      <div className="row" style={{ gap: 8 }}>
        <Button size="sm" variant="primary" disabled={busy} onClick={() => answer(true)}>Allow once</Button>
        <Button size="sm" disabled={busy} onClick={() => answer(true, true)} tip="Stops asking about this tool for the rest of the session">Always allow</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => answer(false)}>Deny</Button>
      </div>
    </div>
  )
}

export function Thinking() {
  return (
    <div className="chat-thinking" aria-live="polite">
      <span /> <span /> <span />
      <em>Claude is working</em>
    </div>
  )
}
