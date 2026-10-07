// The conversation screen shared by Ask and Research: past sessions on the left, the live
// conversation on the right, a composer, permissions, tool calls and cost.

import { ArrowUp, Plus, Square, Terminal, Trash2 } from 'lucide-react'
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChatMessage, ChatMode, ChatSessionRow } from '@shared/api'
import { Badge, Button, Callout, Segmented, Skeleton, cx } from '@renderer/components/ui'
import { Markdown } from '@renderer/components/Markdown'
import { call, errorMessage, invalidate, useEvent, useRpc } from '@renderer/lib/rpc'
import { fmtCost, fmtRelative } from '@renderer/lib/format'
import { navigate, toast, useApp } from '@renderer/lib/store'
import { PermissionCard, PermissionRequest, SLASH, Thinking, ToolRow } from './ChatParts'
import './chat.css'

type ModelChoice = 'default' | 'sonnet' | 'opus' | 'haiku'

export interface WorkspaceApi {
  /** Start a session (or send into the open one). `startMode` overrides the workspace's mode. */
  begin: (text: string, startMode?: ChatMode) => Promise<void>
  /** Put text in the composer and focus it. */
  prefill: (text: string) => void
  active: string | null
  live: boolean
  busy: boolean
  /** Close the live session; resolves with the research sources it queued. */
  end: () => Promise<number>
}

interface Props {
  /** Modes whose past sessions are listed here. */
  modes: ChatMode[]
  /** The mode a new session starts in. */
  mode: ChatMode
  title: string
  placeholder: string
  terminalArgs: (firstText: string) => string[]
  /** Shown when there is no conversation yet. */
  empty: (api: WorkspaceApi) => ReactNode
  /** Rendered between the header and the messages (research sources, for example). */
  strip?: (api: WorkspaceApi) => ReactNode
  /** Route to open sessions under. */
  view: 'ask' | 'research'
  session?: string
  prompt?: string
  /** Start a session of this mode as soon as the screen opens (the Overview's “Read it”). */
  autoStart?: ChatMode
  onSessionEnded?: (queued: number) => void
  onMessage?: () => void
}

const MODEL_ARG: Record<ModelChoice, string | null> = { default: null, sonnet: 'sonnet', opus: 'opus', haiku: 'haiku' }
let localId = 1

export default function ChatWorkspace(p: Props) {
  const project = useApp((s) => s.project)!
  const vault = project.path
  const info = useRpc('app.info', {})
  const sessions = useRpc('chat.list', { vault })

  const [active, setActive] = useState<string | null>(null)
  const [live, setLive] = useState(false)
  const [msgs, setMsgs] = useState<ChatMessage[]>([])
  const [perms, setPerms] = useState<PermissionRequest[]>([])
  const [state, setState] = useState<'idle' | 'thinking' | 'closed' | 'error'>('idle')
  const [, setDetail] = useState<string | null>(null)
  const [cost, setCost] = useState<number | null>(null)
  const [model, setModel] = useState<ModelChoice>('default')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const sessionRef = useRef<string | null>(null)
  const liveRef = useRef(false)
  const startingRef = useRef(false)
  const buf = useRef<{ kind: 'delta' | 'message' | 'permission' | 'status'; data: never }[]>([])
  const scroller = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const composer = useRef<HTMLTextAreaElement>(null)
  const handledPrompt = useRef(false)
  const firstText = useRef('')

  // ── event application ──────────────────────────────────────────────────────
  const applyDelta = useCallback((d: { message_id: string; text: string }) => {
    setMsgs((ms) => {
      const i = ms.findIndex((m) => m.id === d.message_id)
      if (i < 0) return [...ms, { id: d.message_id, role: 'assistant', text: d.text, ts: new Date().toISOString() }]
      const next = ms.slice()
      next[i] = { ...next[i], text: next[i].text + d.text }
      return next
    })
  }, [])
  const applyMessage = useCallback((m: ChatMessage) => {
    setMsgs((ms) => {
      let list = ms
      if (m.role === 'user') {
        const k = list.findIndex((x) => x.id.startsWith('local-'))
        if (k >= 0) list = list.filter((_, j) => j !== k)
      }
      const i = list.findIndex((x) => x.id === m.id)
      if (i >= 0) {
        const next = list.slice()
        next[i] = m
        return next
      }
      return [...list, m]
    })
    p.onMessage?.()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const applyStatus = useCallback((s: { state: 'thinking' | 'idle' | 'closed' | 'error'; detail: string | null; cost_usd: number | null }) => {
    setState(s.state)
    setDetail(s.detail)
    if (s.cost_usd !== null && s.cost_usd !== undefined) setCost(s.cost_usd)
    if (s.state === 'closed' || s.state === 'error') {
      setLive(false)
      liveRef.current = false
    }
    if (s.state === 'error') setFailure(s.detail ?? 'The session stopped with an error.')
    if (s.state === 'idle' || s.state === 'closed') invalidate('chat.list')
  }, [])
  const apply = useCallback(
    (kind: string, data: never) => {
      if (kind === 'delta') applyDelta(data)
      else if (kind === 'message') applyMessage((data as { message: ChatMessage }).message)
      else if (kind === 'permission') setPerms((ps) => (ps.some((x) => x.request_id === (data as PermissionRequest).request_id) ? ps : [...ps, data as PermissionRequest]))
      else applyStatus(data)
    },
    [applyDelta, applyMessage, applyStatus]
  )
  const route = <K extends 'delta' | 'message' | 'permission' | 'status'>(kind: K, d: { session: string }) => {
    if (sessionRef.current === null && startingRef.current) buf.current.push({ kind, data: d as never })
    else if (d.session === sessionRef.current) apply(kind, d as never)
  }
  useEvent('chat.delta', (d) => route('delta', d))
  useEvent('chat.message', (d) => route('message', d))
  useEvent('chat.permission', (d) => route('permission', d))
  useEvent('chat.status', (d) => route('status', d))

  // ── session control ────────────────────────────────────────────────────────
  const reset = () => {
    setMsgs([])
    setPerms([])
    setState('idle')
    setDetail(null)
    setCost(null)
    setFailure(null)
    stick.current = true
  }
  const close = useCallback(async (): Promise<number> => {
    const id = sessionRef.current
    if (!id || !liveRef.current) return 0
    liveRef.current = false
    setLive(false)
    try {
      const r = await call('chat.close', { session: id })
      invalidate('chat.list', 'research.')
      return r.research_queued ?? 0
    } catch {
      return 0
    }
  }, [])

  const attach = (id: string, isLive: boolean) => {
    sessionRef.current = id
    setActive(id)
    liveRef.current = isLive
    setLive(isLive)
  }

  const begin = useCallback(
    async (input: string, startMode?: ChatMode) => {
      const t = input.trim()
      if (!t && (startMode ?? p.mode) !== 'context') return
      setFailure(null)
      setBusy(true)
      try {
        let id = sessionRef.current
        if (id && !liveRef.current) {
          // A saved session: reopen it, then send.
          const r = await call('chat.resume', { session: id })
          attach(r.session, true)
          id = r.session
        }
        const optimistic = (txt: string) => setMsgs((ms) => [...ms, { id: `local-${localId++}`, role: 'user', text: txt, ts: new Date().toISOString() }])
        if (id) {
          optimistic(t)
          stick.current = true
          setState('thinking')
          await call('chat.send', { session: id, text: t })
        } else {
          reset()
          startingRef.current = true
          buf.current = []
          firstText.current = t
          const mode = startMode ?? p.mode
          // A plain first question goes in as the prompt, which the server wraps for the query
          // skill as `watchdog ask` does. A prompt that is already a slash command is sent as a
          // message once the session is open, so it is never wrapped.
          const asPrompt = !!t && !t.startsWith('/') && mode !== 'context'
          const r = await call('chat.start', { vault, mode, model: MODEL_ARG[model], prompt: asPrompt ? t : undefined })
          attach(r.session, true)
          if (t) optimistic(t)
          setState('thinking')
          const pending = buf.current
          buf.current = []
          startingRef.current = false
          for (const ev of pending) if ((ev.data as { session: string }).session === r.session) apply(ev.kind, ev.data)
          if (t && !asPrompt) await call('chat.send', { session: r.session, text: t })
          navigate({ view: p.view, session: r.session }, { replace: true })
          invalidate('chat.list')
        }
      } catch (e) {
        startingRef.current = false
        setFailure(errorMessage(e))
        setState('idle')
      } finally {
        setBusy(false)
      }
    },
    [vault, model, p.mode, p.view, apply] // eslint-disable-line react-hooks/exhaustive-deps
  )

  const openSaved = useCallback(
    async (id: string) => {
      if (id === sessionRef.current) return
      void close()
      reset()
      sessionRef.current = id
      setActive(id)
      setLive(false)
      liveRef.current = false
      setLoading(true)
      try {
        const r = await call('chat.get', { session: id })
        if (sessionRef.current !== id) return
        setMsgs(r.messages)
      } catch (e) {
        setFailure(errorMessage(e))
      } finally {
        setLoading(false)
      }
    },
    [close]
  )

  const fresh = () => {
    void close()
    sessionRef.current = null
    setActive(null)
    setLive(false)
    reset()
    setText('')
    navigate({ view: p.view }, { replace: true })
    setTimeout(() => composer.current?.focus(), 0)
  }

  // Route-driven: open a given session, or start one with a prompt.
  useEffect(() => {
    if (p.session && p.session !== sessionRef.current) void openSaved(p.session)
  }, [p.session]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (p.prompt && !handledPrompt.current && !sessionRef.current) {
      handledPrompt.current = true
      void begin(p.prompt)
    }
  }, [p.prompt]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (p.autoStart && !handledPrompt.current && !sessionRef.current && !p.session) {
      handledPrompt.current = true
      void begin('', p.autoStart)
    }
  }, [p.autoStart]) // eslint-disable-line react-hooks/exhaustive-deps
  // Leaving the screen ends the live session, which costs money while it is open.
  useEffect(
    () => () => {
      const id = sessionRef.current
      if (id && liveRef.current) {
        void call('chat.close', { session: id })
          .then((r) => {
            invalidate('research.', 'chat.list')
            if (r.research_queued) toast({ kind: 'info', title: `${r.research_queued} ${r.research_queued === 1 ? 'source' : 'sources'} queued`, body: 'Open Web research to download them into incoming.' })
          })
          .catch(() => undefined)
      }
    },
    []
  )

  const answer = async (req: PermissionRequest, allow: boolean, always?: boolean) => {
    try {
      await call('chat.permission', { session: sessionRef.current!, request_id: req.request_id, allow, always })
      setPerms((ps) => ps.filter((x) => x.request_id !== req.request_id))
    } catch (e) {
      toast({ kind: 'error', title: 'Could not send your answer', body: errorMessage(e) })
    }
  }
  const stop = () => {
    if (sessionRef.current) void call('chat.interrupt', { session: sessionRef.current })
  }
  const remove = async (row: ChatSessionRow) => {
    const ok = await window.watchdog.dialog.confirm({
      title: 'Delete this conversation?',
      message: `“${row.title || 'Untitled'}” will be removed from this computer.`,
      detail: 'Notes Claude already saved to the vault, such as answers in queries/, are not affected.',
      confirm: 'Delete',
      destructive: true
    })
    if (!ok) return
    try {
      await call('chat.delete', { session: row.session })
      if (row.session === sessionRef.current) fresh()
      invalidate('chat.list')
    } catch (e) {
      toast({ kind: 'error', title: 'Could not delete', body: errorMessage(e) })
    }
  }

  // ── scrolling & composer ───────────────────────────────────────────────────
  useEffect(() => {
    const el = scroller.current
    if (el && stick.current && (msgs.length || perms.length)) el.scrollTop = el.scrollHeight
  }, [msgs, perms, state])
  const resize = () => {
    const el = composer.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 220) + 'px'
  }
  useEffect(resize, [text])
  const send = () => {
    const t = text.trim()
    if (!t || busy || state === 'thinking') return
    setText('')
    void begin(t)
  }
  const sugg = useMemo(() => (/^\/[^\s]*$/.test(text) ? SLASH.filter((s) => s.name.startsWith(text.toLowerCase())) : []), [text])

  const api: WorkspaceApi = {
    begin,
    prefill: (t) => {
      setText(t)
      setTimeout(() => {
        composer.current?.focus()
        composer.current?.setSelectionRange(t.length, t.length)
      }, 0)
    },
    active,
    live,
    busy,
    end: async () => {
      const n = await close()
      setState('closed')
      p.onSessionEnded?.(n)
      return n
    }
  }

  const rows = (sessions.data ?? []).filter((r) => p.modes.includes(r.mode))
  const cc = info.data?.claude_code
  // A hint, not a gate: the sign-in check is best-effort (an API key in the environment, or the
  // Agent SDK's bundled Claude Code, can work where it says no), and a session that really can't
  // start reports why through chat.status.
  const signinWarning = cc && (!cc.installed || !cc.logged_in)
  const conversation = msgs.length > 0 || !!active
  const thinking = state === 'thinking'

  return (
    <div className="chat-root">
      <aside className="chat-side">
        <div className="chat-side-head">
          <Button icon={Plus} onClick={fresh} style={{ width: '100%' }}>
            New conversation
          </Button>
        </div>
        <div className="chat-side-list">
          {sessions.isLoading && Array.from({ length: 4 }, (_, i) => <Skeleton key={i} h={44} style={{ margin: '6px 0' }} />)}
          {!sessions.isLoading && rows.length === 0 && <div className="chat-side-empty">Past conversations for this investigation appear here, so you can pick one up again.</div>}
          {rows.map((r) => (
            <div key={r.session} className={cx('chat-sess', r.session === active && 'on')}>
              <button className="chat-sess-main" onClick={() => navigate({ view: p.view, session: r.session })}>
                <span className="chat-sess-title clamp-2">{r.title || 'Untitled conversation'}</span>
                <span className="chat-sess-sub">
                  {r.mode === 'context' && <Badge>Context</Badge>}
                  {fmtRelative(r.updated || r.started)}
                  {r.model ? ` · ${r.model}` : ''}
                </span>
              </button>
              <Button className="chat-sess-del" size="sm" variant="ghost" icon={Trash2} tip="Delete" onClick={() => void remove(r)} />
            </div>
          ))}
        </div>
      </aside>

      <section className="chat-main">
        <header className="chat-head">
          <div className="grow">
            <div className="chat-head-title truncate">{p.title}</div>
            <div className="chat-head-sub">
              {live ? (thinking ? 'Working…' : 'Open — ask a follow-up any time') : active ? 'Saved conversation — send a message to reopen it' : 'Runs on the Claude you are signed in with'}
              {cost !== null && <span className="tnum" data-tip="Cost so far. On a subscription this is what it would cost at published rates, not what is billed."> · {fmtCost(cost)}</span>}
            </div>
          </div>
          <Segmented<ModelChoice>
            value={model}
            onChange={setModel}
            options={[
              { value: 'default', label: 'Default', tip: 'Claude Code’s own setting' },
              { value: 'sonnet', label: 'Sonnet' },
              { value: 'opus', label: 'Opus' },
              { value: 'haiku', label: 'Haiku' }
            ]}
          />
          <Button
            size="sm"
            icon={Terminal}
            tip="Open the same session in your terminal instead"
            onClick={() => void window.watchdog.shell.openTerminal(vault, p.terminalArgs(firstText.current || text))}
          >
            Open in Terminal
          </Button>
        </header>
        {model !== 'default' && active && <div className="chat-model-note">The model applies to new conversations. This one keeps the model it started with.</div>}

        {signinWarning && (
          <Callout tone="warning" title={!cc!.installed ? 'Claude Code may not be installed' : 'Claude Code may not be signed in'} style={{ margin: '12px 20px 0' }} action={<Button size="sm" onClick={() => navigate({ view: 'settings', tab: 'auth' })}>Open settings</Button>}>
            This screen runs Claude Code in your investigation, so it needs to be {!cc!.installed ? 'installed (claude.ai/download)' : 'signed in (run claude in a terminal once)'}. If a question fails to start, that is why; you can also open the same session in a terminal.
          </Callout>
        )}
        {p.strip?.(api)}

        <div
          className="chat-scroll"
          ref={scroller}
          onScroll={(e) => {
            const el = e.currentTarget
            stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60
          }}
        >
          <div className="chat-col">
            {!conversation && !loading && p.empty(api)}
            {loading && <Skeleton h={80} />}
            {msgs.map((m) => {
              if (m.role === 'tool') return <ToolRow key={m.id} msg={m} />
              if (m.role === 'system') return <div key={m.id} className="chat-system">{m.text}</div>
              if (m.role === 'user')
                return (
                  <div key={m.id} className="chat-user">
                    <div className="chat-user-bubble selectable">{m.text}</div>
                  </div>
                )
              return m.text.trim() ? (
                <div key={m.id} className="chat-assistant">
                  <Markdown text={m.text} compact />
                </div>
              ) : null
            })}
            {perms.map((r) => (
              <PermissionCard key={r.request_id} req={r} onAnswer={(a, always) => void answer(r, a, always)} />
            ))}
            {thinking && perms.length === 0 && <Thinking />}
            {failure && (
              <Callout
                tone="danger"
                title="The session hit a problem"
                action={
                  <Button size="sm" icon={Terminal} onClick={() => void window.watchdog.shell.openTerminal(vault, p.terminalArgs(firstText.current || text))}>
                    Open in Terminal
                  </Button>
                }
              >
                <span className="selectable">{failure}</span> If it keeps happening, check that Claude Code is installed and signed in, or use the terminal.
              </Callout>
            )}
          </div>
        </div>

        <footer className="chat-composer-wrap">
          <div className="chat-col">
            {sugg.length > 0 && (
              <div className="chat-sugg">
                {sugg.map((s) => (
                  <button key={s.name} onClick={() => api.prefill(s.name + (s.takesInput ? ' ' : ''))}>
                    <span className="mono">{s.name}</span>
                    <span className="faint">{s.hint}</span>
                    <span className="muted grow truncate">{s.text}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="chat-composer">
              <textarea
                ref={composer}
                rows={1}
                value={text}
                placeholder={p.placeholder}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault()
                    send()
                  }
                }}
              />
              {thinking ? (
                <Button variant="danger" icon={Square} onClick={stop} tip="Stop Claude's current turn">
                  Stop
                </Button>
              ) : (
                <Button variant="primary" icon={ArrowUp} disabled={!text.trim() || busy} loading={busy} onClick={send} tip="Send (Enter)" />
              )}
            </div>
            <div className="chat-hint">
              <span><kbd className="kbd">Enter</kbd> to send · <kbd className="kbd">Shift</kbd> <kbd className="kbd">Enter</kbd> for a new line · type <span className="mono">/</span> for commands</span>
              {live && (
                <button className="chat-end" onClick={() => void api.end()}>
                  End session
                </button>
              )}
            </div>
          </div>
        </footer>
      </section>
    </div>
  )
}

