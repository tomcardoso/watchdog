// An audio or video document (D273): the recording, played from the original in the vault, above
// its transcript. Each transcript line starts at a timestamp; clicking it plays from there, and a
// fact's citation jumps to its page (a block of time) or to the line its passage is on.

import { ExternalLink, Play } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Callout, cx } from '@renderer/components/ui'
import { fmtClock, fmtDuration, pageSpan, seekTime, transcriptLines, VIDEO_EXTS } from '@renderer/lib/media'
import type { DocumentDetail, MediaInfo } from '@shared/api'
import type { JumpTarget } from './PdfViewer'

interface Props {
  d: DocumentDetail
  media: MediaInfo
  abs: string | null
  target: JumpTarget | null
  onOpen: () => void
}

export function MediaViewer({ d, media, abs, target, onOpen }: Props) {
  const player = useRef<HTMLMediaElement | null>(null)
  const scroller = useRef<HTMLDivElement>(null)
  const [now, setNow] = useState(0)
  const [failed, setFailed] = useState(false)
  const [flash, setFlash] = useState<number | null>(null)
  const isVideo = media.kind === 'video' && VIDEO_EXTS.has(d.ext)
  const pages = useMemo(() => d.pages.map((p) => ({ ...p, span: pageSpan(media, p.page), lines: transcriptLines(p.text) })), [d.pages, media])

  useEffect(() => setFailed(false), [d.sha])

  const play = (at: number, autoplay = true) => {
    const el = player.current
    if (!el) return
    el.currentTime = at
    setNow(at)
    if (autoplay) void el.play().catch(() => undefined)
  }

  // A citation: seek to the cited line (or the page's start), scroll it into view and mark it.
  useEffect(() => {
    if (!target) return
    const page = d.pages.find((p) => p.page === target.page)
    const at = seekTime(media, page?.text, target.page, target.find)
    play(at, false)
    // The line to mark: the one the seek lands on, or the page's first line when it starts in a pause.
    const stamps = transcriptLines(page?.text ?? '').map((l) => l.at).filter((x): x is number => x !== null)
    const mark = stamps.filter((x) => x <= at).pop() ?? stamps[0] ?? at
    setFlash(mark)
    const line = scroller.current?.querySelector(`[data-at="${Math.floor(mark)}"]`) ?? scroller.current?.querySelector(`[data-p="${target.page}"]`)
    line?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    const t = setTimeout(() => setFlash(null), 1700)
    return () => clearTimeout(t)
  }, [target]) // eslint-disable-line react-hooks/exhaustive-deps

  // The line being spoken: the last one that starts at or before the playhead.
  const current = useMemo(() => {
    let best: number | null = null
    for (const p of pages) for (const l of p.lines) if (l.at !== null && l.at <= now + 0.25) best = l.at
    return best
  }, [pages, now])

  const src = abs ? window.watchdog.files.url(abs) : null
  const common = {
    src: src ?? undefined,
    controls: true,
    preload: 'metadata' as const,
    onTimeUpdate: (e: React.SyntheticEvent<HTMLMediaElement>) => setNow(e.currentTarget.currentTime),
    onError: () => setFailed(true)
  }

  return (
    <div className="pdf-viewer media-viewer">
      <div className="pdf-toolbar">
        <span className="pdf-pagelabel">
          {isVideo ? 'Video' : 'Audio'} · {fmtDuration(media.duration_seconds)} · transcribed on this computer
        </span>
        <span className="spacer" />
        {abs && <Button size="sm" icon={ExternalLink} onClick={onOpen}>Open original</Button>}
      </div>
      <div className={cx('media-stage', isVideo && 'is-video')}>
        {!src ? (
          <Callout tone="info">The original recording is not in the investigation folder, so it can’t be played here. The transcript is below.</Callout>
        ) : failed ? (
          <Callout tone="info">This format can’t be played inside Watchdog. The transcript is below; use Open original to play the recording in another app.</Callout>
        ) : isVideo ? (
          <video ref={(el) => { player.current = el }} className="media-player" {...common} />
        ) : (
          <audio ref={(el) => { player.current = el }} className="media-player" {...common} />
        )}
      </div>
      <div className="text-reader media-transcript" ref={scroller}>
        <div className="text-reader-note">
          <Callout tone="info">
            A machine transcript: it has no speaker names, and names and figures can be misheard. Click a timestamp to hear that passage before relying on it.
          </Callout>
        </div>
        {pages.length === 0 && <div className="text-reader-note faint">No speech was found in this recording.</div>}
        {pages.map((p) => (
          <section key={p.page} data-p={p.page} className="media-page">
            <h3 className="media-page-head">
              Page {p.page} <span className="faint">({fmtClock(p.span.start)}–{fmtClock(p.span.end)})</span>
            </h3>
            {p.lines.map((l, i) => (
              <p
                key={i}
                data-at={l.at === null ? undefined : Math.floor(l.at)}
                className={cx('media-line', l.at !== null && l.at === current && 'is-now', l.at !== null && flash !== null && Math.floor(l.at) === Math.floor(flash) && 'flash')}
              >
                {l.at !== null && (
                  <button className="media-stamp" disabled={!src || failed} onClick={() => play(l.at!)} data-tip={`Play from ${fmtClock(l.at)}`} aria-label={`Play from ${fmtClock(l.at)}`}>
                    <Play />{fmtClock(l.at)}
                  </button>
                )}
                <span className="selectable">{l.text}</span>
              </p>
            ))}
          </section>
        ))}
      </div>
    </div>
  )
}
