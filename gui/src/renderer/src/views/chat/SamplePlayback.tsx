// Ask Claude in the sample investigation (D309): prepared conversations, played back in the real
// chat screen. The backend builds each one from the sample's own records (`sample.conversation`)
// as the events a live session streams (messages, text deltas, tool calls, a permission card,
// status), and this plays them through the same handlers, paced so it reads as live and skippable.
// Nothing is sent to Claude: a real session is refused in the sample (D307).

import { MessagesSquare, ShieldQuestion } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { SamplePlayback, SampleQuestion } from '@shared/api'
import { call, errorMessage } from '@renderer/lib/rpc'
import { cx } from '@renderer/components/ui'

export const SAMPLE_LABEL =
  'These answers were prepared in advance, so trying them costs nothing and nothing is sent to Claude. In your own investigations, Ask Claude answers any question, using your Claude account.'

const KIND: Record<SamplePlayback['event'], 'message' | 'delta' | 'status' | 'permission'> = {
  'chat.message': 'message',
  'chat.delta': 'delta',
  'chat.status': 'status',
  'chat.permission': 'permission'
}

export interface Playback {
  /** Play a prepared conversation from the start. */
  play: (id: string) => Promise<void>
  /** The reporter's answer to the conversation's permission card; plays what follows. */
  answer: (requestId: string, allow: boolean) => Promise<void>
  /** Show the rest of the turn at once. */
  skip: () => void
  /** Stop and forget the conversation (a new conversation, or leaving the screen). */
  stop: () => void
  playing: boolean
  /** The conversation on screen, if any. */
  current: string | null
}

export function useSamplePlayback(
  vault: string,
  apply: (kind: 'message' | 'delta' | 'status' | 'permission', data: never) => void,
  hooks: { onStart: (session: string) => void; onError: (message: string) => void }
): Playback {
  const queue = useRef<SamplePlayback[]>([])
  const timer = useRef<number | null>(null)
  const run = useRef(0)
  const [playing, setPlaying] = useState(false)
  const [current, setCurrent] = useState<string | null>(null)

  const clear = () => {
    if (timer.current !== null) window.clearTimeout(timer.current)
    timer.current = null
  }
  const fire = (ev: SamplePlayback) => apply(KIND[ev.event], ev.data as never)
  const next = useCallback(() => {
    const ev = queue.current[0]
    if (!ev) {
      timer.current = null
      setPlaying(false)
      return
    }
    timer.current = window.setTimeout(() => {
      queue.current.shift()
      fire(ev)
      next()
    }, ev.after_ms)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const start = (events: SamplePlayback[]) => {
    clear()
    queue.current = events.slice()
    setPlaying(true)
    next()
  }
  const stop = useCallback(() => {
    run.current++
    clear()
    queue.current = []
    setPlaying(false)
    setCurrent(null)
  }, [])
  useEffect(() => () => clear(), [])

  const play = async (id: string) => {
    stop()
    const mine = ++run.current
    try {
      const r = await call('sample.conversation', { vault, id })
      if (mine !== run.current) return
      setCurrent(id)
      hooks.onStart(r.session)
      start(r.events)
    } catch (e) {
      hooks.onError(errorMessage(e))
    }
  }
  const answer = async (requestId: string, allow: boolean) => {
    if (!current) return
    const mine = run.current
    try {
      const r = await call('sample.conversationAnswer', { vault, id: current, request_id: requestId, allow })
      if (mine === run.current) start(r.events)
    } catch (e) {
      hooks.onError(errorMessage(e))
    }
  }
  const skip = () => {
    clear()
    const rest = queue.current
    queue.current = []
    rest.forEach(fire)
    setPlaying(false)
  }
  return { play, answer, skip, stop, playing, current }
}

/** The prepared questions, as large cards (the empty screen) or a compact row (after an answer). */
export function SampleQuestions({ questions, onPick, compact, exclude }: { questions: SampleQuestion[]; onPick: (id: string) => void; compact?: boolean; exclude?: string | null }) {
  const list = questions.filter((q) => q.id !== exclude)
  if (!list.length) return null
  if (compact)
    return (
      <div className="chat-sample-more">
        <div className="eyebrow">Try another prepared question</div>
        <div className="chat-sample-row">
          {list.map((q) => (
            <button key={q.id} className="chat-sample-pill" onClick={() => onPick(q.id)}>
              {q.question}
            </button>
          ))}
        </div>
      </div>
    )
  return (
    <div className="chat-chips chat-sample-qs">
      {list.map((q) => (
        <button key={q.id} className={cx('chat-chip', 'chat-sample-q')} onClick={() => onPick(q.id)}>
          <span className="chat-chip-name">
            {q.writes ? <ShieldQuestion /> : <MessagesSquare />}
            {q.question}
          </span>
          <span className="chat-chip-text">{q.blurb}</span>
        </button>
      ))}
    </div>
  )
}

export function SampleEmpty({ name, questions, onPick }: { name: string; questions: SampleQuestion[]; onPick: (id: string) => void }) {
  return (
    <div className="chat-empty">
      <h2>Ask about {name}</h2>
      <p>
        Ask Claude works inside an investigation: it searches the documents, reads the notes and answers with a citation for each claim, linked to the page it came from. Before it changes anything, it asks you. In the sample, choose one of these questions to see a conversation prepared in advance.
      </p>
      <SampleQuestions questions={questions} onPick={onPick} />
    </div>
  )
}
