// The language a document's pages were read in, for the Documents screen (D382): what
// pre-processing detected (or the document's sidecar said), and which scanned pages were read
// again in their language. Documents pre-processed before D382 have nothing recorded.

import type { DocumentLanguage } from '@shared/api'

const names = (() => {
  try {
    return new Intl.DisplayNames(['en'], { type: 'language' })
  } catch {
    return null
  }
})()

export function languageName(code: string): string {
  try {
    return names?.of(code) ?? code
  } catch {
    return code
  }
}

/** "page 3", "pages 3–5", "pages 1, 4 and 7–9". */
export function pageRanges(pages: number[]): string {
  const sorted = [...new Set(pages)].sort((a, b) => a - b)
  const runs: string[] = []
  for (let i = 0; i < sorted.length; i++) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j++
    runs.push(j > i ? `${sorted[i]}–${sorted[j]}` : `${sorted[i]}`)
    i = j
  }
  const list = runs.length > 1 ? `${runs.slice(0, -1).join(', ')} and ${runs[runs.length - 1]}` : runs[0]
  return `${sorted.length === 1 ? 'page' : 'pages'} ${list}`
}

/** One line saying what language the document is in and how its scans were read, or null when
 * nothing was recorded. */
export function readIn(lang: DocumentLanguage | null | undefined): string | null {
  if (!lang) return null
  if (!lang.code) return 'Not detected: too little text to tell'
  const main = languageName(lang.code)
  const parts: string[] = []
  const others = Object.entries(lang.pages ?? {}).filter(([code]) => code !== lang.code)
  parts.push(others.length ? `Mostly ${main}, with ${others.map(([code, pages]) => `${languageName(code)} on ${pageRanges(pages)}`).join('; ')}` : main)
  if (lang.source === 'sidecar') parts[0] += ' (from its sidecar file)'
  const reread = Object.entries(lang.reread ?? {})
  if (reread.length) parts.push(`Scanned ${reread.map(([code, pages]) => `${pageRanges(pages)} read in ${languageName(code)}`).join('; ')}`)
  const asked = Object.entries(lang.ask ?? {})
  if (asked.length) parts.push(`Scanned ${asked.map(([code, pages]) => `${languageName(code)} ${pageRanges(pages)}`).join('; ')} read as English`)
  return parts.join('. ')
}
