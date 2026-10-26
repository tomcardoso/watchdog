// Pieces of the conversation: tool-call rows, permission cards, the thinking indicator and the
// slash-command catalogue shared by Ask and Research.

import { AlertTriangle, ChevronRight, FileText, Globe, Pencil, ShieldQuestion, Terminal, Wrench, Search as SearchIcon } from 'lucide-react'
import { useState } from 'react'
import type { ChatMessage } from '@shared/api'
import { Button, cx } from '@renderer/components/ui'

export interface SlashCommand {
  name: string
  /** What the reporter sees: the command's job in plain words. `name` is what Claude is sent. */
  label: string
  hint: string
  takesInput: boolean
  /** What it does, one line. */
  text: string
}
export const SLASH: SlashCommand[] = [
  { name: '/watchdog-query', label: 'Answer a question', hint: '[question]', takesInput: true, text: 'Answer a question from the investigation, with a source for every claim.' },
  { name: '/watchdog-surface', label: 'Find connections', hint: '', takesInput: false, text: 'Look across the whole investigation for connections and anomalies.' },
  { name: '/watchdog-entity', label: 'Refresh an entity', hint: '[id…]', takesInput: true, text: 'Rewrite an entity’s summary and timeline from all its source documents.' },
  { name: '/watchdog-wiki', label: 'Write thread pages', hint: '', takesInput: false, text: 'Create or update pages that follow a line of inquiry, saved to Briefings.' },
  { name: '/watchdog-health', label: 'Check the investigation', hint: '', takesInput: false, text: 'Look for orphaned notes, broken links, record mismatches and open contradictions.' },
  { name: '/watchdog-context', label: 'Set the context', hint: '', takesInput: false, text: 'Write context.md, your brief, from the files in context/ and a short interview.' },
  { name: '/watchdog-request', label: 'Draft a records request', hint: '[request]', takesInput: true, text: 'Draft a request for a document the investigation needs, saved to Briefings.' },
  { name: '/watchdog-research', label: 'Research on the web', hint: '[question]', takesInput: true, text: 'Research open questions on the web, queuing sources for you to download.' }
]

/** A message as the reporter sees it: a leading command shown by its label. */
export function splitCommand(text: string): { command: SlashCommand | null; rest: string } {
  const m = /^(\/[\w-]+)(?:\s+([\s\S]*))?$/.exec(text.trim())
  const command = m ? SLASH.find((s) => s.name === m[1].toLowerCase()) ?? null : null
  return command ? { command, rest: (m![2] ?? '').trim() } : { command: null, rest: text }
}

const KEYS = ['command', 'file_path', 'path', 'pattern', 'query', 'url', 'prompt', 'description']
/** A path inside the investigation, shown relative to it (`entities/…md`), so a tool row never
 *  spells out the folder's full location on this computer. */
export function relativeToVault(text: string, vault?: string): string {
  if (!vault) return text
  const root = vault.replace(/[\\/]+$/, '')
  if (text === root) return '.'
  for (const sep of ['/', '\\']) if (text.startsWith(root + sep)) return text.slice(root.length + 1)
  return text
}
export function summarizeInput(input: unknown, vault?: string): string {
  if (typeof input === 'string') return input
  if (input && typeof input === 'object') {
    const o = input as Record<string, unknown>
    for (const k of KEYS) if (typeof o[k] === 'string' && o[k]) return relativeToVault(String(o[k]), vault)
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

export function ToolRow({ msg, vault }: { msg: ChatMessage; vault?: string }) {
  const [open, setOpen] = useState(false)
  const t = msg.tool
  if (!t) return null
  // Reading a file that isn't there yet (a new investigation's context.md) is not a failure.
  const missing = t.is_error && t.name === 'Read' && /^File does not exist/.test(t.result ?? '')
  const failed = t.is_error && !missing
  const Icon = failed ? AlertTriangle : toolIcon(t.name)
  const pending = t.result === null && !t.is_error
  let input = ''
  try {
    input = typeof t.input === 'string' ? t.input : JSON.stringify(t.input, null, 2)
  } catch {
    input = String(t.input)
  }
  return (
    <div className={cx('chat-tool', failed && 'err', open && 'open')}>
      <button className="chat-tool-head" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight className="chev" />
        <Icon className="ico" />
        <span className="chat-tool-name">{toolName(t.name)}</span>
        <span className="chat-tool-sum truncate">{cut(summarizeInput(t.input, vault).replace(/\s+/g, ' '), 140)}{missing && ' — not there yet'}</span>
        {pending && <span className="spinner" style={{ width: 11, height: 11 }} />}
      </button>
      {open && (
        <div className="chat-tool-body selectable">
          <div className="eyebrow">Input</div>
          <pre>{cut(input, 4000)}</pre>
          <div className="eyebrow" style={{ marginTop: 8 }}>{failed ? 'Error' : 'Result'}</div>
          <pre className={failed ? 'err' : undefined}>{pending ? 'Waiting for the result…' : missing ? 'This file doesn’t exist yet, so Claude carries on without it.' : cut(t.result ?? '', 6000) || '(empty)'}</pre>
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
  /** The tool in plain words (Watchdog's own tools), else its name. */
  title?: string | null
  /** What "Allow for this conversation" covers, in plain words. Null: the whole tool. */
  scope?: string | null
  /** Asked about every time: no "Allow for this conversation" (recording a contradiction, D308). */
  once_only?: boolean
  /** What "for this conversation" covers, or why only once, and whether version history keeps the change. */
  note?: string | null
}
export function PermissionCard({ req, onAnswer }: { req: PermissionRequest; onAnswer: (allow: boolean, forConversation?: boolean) => void }) {
  const [busy, setBusy] = useState(false)
  const answer = (a: boolean, forConversation?: boolean) => {
    setBusy(true)
    onAnswer(a, forConversation)
  }
  return (
    <div className="chat-perm" role="alertdialog" aria-label="Permission request">
      <div className="chat-perm-head">
        <ShieldQuestion />
        <div>
          <div style={{ fontWeight: 620 }}>{req.tool.startsWith('mcp__watchdog__') && req.title ? `Claude wants to: ${req.title.charAt(0).toLowerCase()}${req.title.slice(1)}` : `Claude wants to use ${toolName(req.tool)}`}</div>
          {req.description && <div className="muted" style={{ fontSize: 'var(--fs-sm)' }}>{req.description}</div>}
        </div>
      </div>
      <pre className="chat-perm-input selectable">{cut(typeof req.input === 'string' ? req.input : JSON.stringify(req.input, null, 2), 1200)}</pre>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <Button size="sm" variant="primary" disabled={busy} onClick={() => answer(true)}>Allow once</Button>
        {!req.once_only && (
          <Button size="sm" disabled={busy} onClick={() => answer(true, true)}>Allow for this conversation</Button>
        )}
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => answer(false)}>Deny</Button>
      </div>
      {req.note && <div className="muted chat-perm-note">{req.note}</div>}
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
