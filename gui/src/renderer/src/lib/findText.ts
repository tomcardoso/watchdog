// Finding words in a document's text, the way a reader means them rather than character for
// character (D289). One matcher for every find box: the PDF viewer (its own text layer and the
// saved OCR lines of a scanned page), the image and text readers, the transcript and the Text tab.
//
// A match ignores case, accents, the style of a quotation mark (curly or straight), every dash
// and hyphen, and all white space. Ignoring white space and dashes entirely is what joins a word
// hyphenated across a line break ("water-\nfront" matches "waterfront") and what finds words an
// OCR engine ran together ("peryear" matches "per year"); the cost is a rare extra match where
// two words happen to spell a third. Every match maps back to the original characters, so a
// highlight covers exactly the text the reader sees.
//
// No imports: the unit test runs this file directly under Node.

/** Text reduced for matching: `text[i]` came from original characters `starts[i]` to `ends[i]`. */
export interface Folded {
  text: string
  starts: number[]
  ends: number[]
}

/** A match, in the original string's UTF-16 indices: [start, end). */
export interface Range {
  start: number
  end: number
}

const IGNORED = /[\-\s­‐-―−⸺⸻﹘﹣－​-‍⁠﻿]/
const SINGLE_QUOTES = /[‘’‚‛′`´ʼ]/
const DOUBLE_QUOTES = /[“”„‟″«»]/
const MARKS = /\p{M}/gu

function foldChar(ch: string): string {
  if (IGNORED.test(ch)) return ''
  if (SINGLE_QUOTES.test(ch)) return "'"
  if (DOUBLE_QUOTES.test(ch)) return '"'
  return ch.normalize('NFKD').toLowerCase().normalize('NFKD').replace(MARKS, '')
}

export function fold(s: string): Folded {
  let text = ''
  const starts: number[] = []
  const ends: number[] = []
  for (let i = 0; i < s.length; ) {
    const cp = s.codePointAt(i) ?? 0
    const len = cp > 0xffff ? 2 : 1
    const out = foldChar(s.slice(i, i + len))
    for (let k = 0; k < out.length; k++) {
      starts.push(i)
      ends.push(i + len)
    }
    text += out
    i += len
  }
  return { text, starts, ends }
}

/** The query as it is matched; empty when nothing in it can be matched (only spaces or dashes). */
export function foldQuery(q: string): string {
  return fold(q).text
}

/** Every non-overlapping match of `query` in `hay`, left to right, in original indices. */
export function findRanges(hay: string | Folded, query: string): Range[] {
  const f = typeof hay === 'string' ? fold(hay) : hay
  const q = foldQuery(query)
  const out: Range[] = []
  if (!q) return out
  for (let at = f.text.indexOf(q); at !== -1; at = f.text.indexOf(q, at + q.length)) {
    out.push({ start: f.starts[at], end: f.ends[at + q.length - 1] })
  }
  return out
}

export function countMatches(hay: string, query: string): number {
  return findRanges(hay, query).length
}

/** A part of one match that falls in one segment: characters [from, to) of that segment, and `k`,
 * the match's number on the page. */
export interface SegmentHit {
  from: number
  to: number
  k: number
}

/** Matches over a page held as separate pieces (a PDF's text items, a scan's OCR lines), joined
 * as lines so a match can run from one piece into the next. Each match is split into the parts
 * that fall in each piece. */
export function matchSegments(segments: string[], query: string): { count: number; bySegment: Map<number, SegmentHit[]> } {
  const bySegment = new Map<number, SegmentHit[]>()
  let text = ''
  const starts: number[] = []
  segments.forEach((s, i) => {
    if (i) text += '\n'
    starts[i] = text.length
    text += s
  })
  const ranges = findRanges(text, query)
  ranges.forEach((r, k) => {
    for (let j = 0; j < segments.length; j++) {
      const s = starts[j]
      const e = s + segments[j].length
      if (e <= r.start) continue
      if (s >= r.end) break
      const hit = { from: Math.max(r.start, s) - s, to: Math.min(r.end, e) - s, k }
      if (hit.to <= hit.from) continue
      const list = bySegment.get(j)
      if (list) list.push(hit)
      else bySegment.set(j, [hit])
    }
  })
  return { count: ranges.length, bySegment }
}

/** `text` cut into plain and matched pieces, for rendering highlights. */
export function splitByRanges(text: string, ranges: Range[]): { text: string; hit: number | null }[] {
  const out: { text: string; hit: number | null }[] = []
  let pos = 0
  ranges.forEach((r, i) => {
    if (r.start > pos) out.push({ text: text.slice(pos, r.start), hit: null })
    out.push({ text: text.slice(r.start, r.end), hit: i })
    pos = r.end
  })
  if (pos < text.length) out.push({ text: text.slice(pos), hit: null })
  return out
}
