// Audio and video documents (D273): a page of a transcript is a fixed block of time, so a page
// citation reads as a time range and opens the recording at that point.

import type { MediaInfo } from '@shared/api'

export const MEDIA_EXTS = new Set(['mp3', 'm4a', 'aac', 'wav', 'flac', 'ogg', 'oga', 'opus', 'mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi'])
export const VIDEO_EXTS = new Set(['mp4', 'm4v', 'mov', 'webm', 'mkv', 'avi'])

/** 4:05, or 1:02:05 past an hour. */
export function fmtClock(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.floor(seconds ?? 0))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** A recording's length in words: "42 min", "1 h 05 min", "35 s". */
export function fmtDuration(seconds: number | null | undefined): string {
  const s = Math.round(seconds ?? 0)
  if (s < 60) return `${s} s`
  const m = Math.round(s / 60)
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')} min`
}

/** Page n's time range, from the stored map or, failing that, the fixed block size. */
export function pageSpan(media: MediaInfo, page: number): { start: number; end: number } {
  const found = media.pages.find((p) => p.page === page)
  if (found) return { start: found.start, end: found.end }
  const size = media.page_seconds ?? 300
  const start = (page - 1) * size
  return { start, end: Math.min(page * size, Math.max(media.duration_seconds ?? start + size, start)) }
}

/** How a citation of `page` reads: "10:00–15:00" for a recording, "p. 3" otherwise. */
export function pageLabel(media: MediaInfo | null | undefined, page: number): string {
  if (!media) return `p. ${page}`
  const { start, end } = pageSpan(media, page)
  return `${fmtClock(start)}–${fmtClock(end)}`
}

export interface TranscriptLine { at: number | null; text: string }

const STAMP = /^\[(\d{1,2}):(\d{2}):(\d{2})\]\s*/

/** A transcript page split into its timestamped lines. */
export function transcriptLines(text: string): TranscriptLine[] {
  return text
    .split(/\n\s*\n/)
    .map((raw) => raw.trim())
    .filter(Boolean)
    .map((raw) => {
      const m = STAMP.exec(raw)
      return m ? { at: Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]), text: raw.slice(m[0].length) } : { at: null, text: raw }
    })
}

const squash = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, ' ').trim()

/** The time to play a citation from: the line on `page` holding `find`, else the page's start. */
export function seekTime(media: MediaInfo, pageText: string | undefined, page: number, find?: string): number {
  const start = pageSpan(media, page).start
  if (!find || !pageText) return start
  const needle = squash(find)
  const hit = transcriptLines(pageText).find((l) => squash(l.text).includes(needle))
  return hit?.at ?? start
}
