// A fact citation in AI-written or session-written text (D283): `[[documents/<slug>#^f-<id>|p. 4]]`.
// Watchdog resolves each one against the stored facts before showing it. A citation that resolves
// is a link that opens the document at the fact, and shows the fact, its document, page and the
// reporter's mark on hover; one on a disputed fact says so; one that names no stored fact is shown
// as "source not found", never as a link. Uncited sentences are left alone.

import type { ReactNode } from 'react'
import type { CitationStatus } from '@shared/api'
import { cx } from '@renderer/components/ui'
import { navigate } from '@renderer/lib/store'
import './factcite.css'

/** `[[<note>#^f-…` links in a Markdown text, as the `<note>#^<block>` keys `vault.citations` takes. */
const FACT_LINK = /\[\[([^\]|#\n]+)#\^(f-[0-9a-f]+(?:-\d+)?)/g

export function citationKeys(md: string | null | undefined): string[] {
  const keys = new Set<string>()
  for (const m of (md ?? '').matchAll(FACT_LINK)) keys.add(`${m[1].trim()}#^${m[2]}`)
  return [...keys].sort()
}

/** The citation key a decoded wikilink target names, or null when it is not a fact citation. */
export function citationKey(target: string): string | null {
  const m = /^([^#]+)#\^(f-[0-9a-f]+(?:-\d+)?)$/.exec(target.trim())
  return m ? `${m[1].trim()}#^${m[2]}` : null
}

const MARK_TEXT: Record<string, string> = {
  verified: 'You verified this fact',
  disputed: 'You marked this fact disputed',
  unverifiable: 'You could not verify this fact'
}

function plain(children: ReactNode): string {
  if (typeof children === 'string' || typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(plain).join('')
  return ''
}

export function FactCite({ label, status }: { label: ReactNode; status: CitationStatus | undefined }) {
  const text = plain(label).replace(/,\s*disputed$/i, '').trim() || 'source'
  // A short marker ("p. 4") is a pill; a document title (a contradiction's side) stays a link.
  const pill = /^(p\.|pp\.|source\b)/i.test(text)
  if (!status) return <span className={cx(pill ? 'fact-cite pill' : 'fact-cite', 'is-pending')}>{text}</span>
  if (status.status !== 'found' || !status.fact) {
    return (
      <span
        className="fact-cite-missing"
        data-tip="Watchdog could not find this fact. Its document may have been processed again, or the citation was written wrong."
        data-tip-pos="bottom-end"
      >
        {pill ? 'source not found' : `${text} (source not found)`}
      </span>
    )
  }
  const f = status.fact
  const disputed = f.mark === 'disputed'
  const open = () => navigate({ view: 'document', sha: f.sha, page: f.passage_page ?? f.page ?? undefined, fact: f.id })
  return (
    <span className="fact-cite-wrap">
      <a
        href="#"
        className={cx(pill ? 'fact-cite pill' : 'fact-cite', disputed && 'is-disputed')}
        aria-label={`Fact from ${f.title ?? 'a document'}${f.page ? `, page ${f.page}` : ''}${disputed ? ', disputed' : ''}`}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          open()
        }}
      >
        {text}
        {disputed && <span className="fact-cite-flag">disputed</span>}
      </a>
      <span className="fact-cite-card" role="tooltip">
        <span className="fact-cite-fact">{f.fact}</span>
        <span className="fact-cite-src">
          {f.title ?? 'Untitled document'}
          {f.page ? ` · p. ${f.page}` : ''}
        </span>
        <span className={cx('fact-cite-mark', f.mark && `is-${f.mark}`)}>{f.mark ? MARK_TEXT[f.mark] : 'Not checked yet'}</span>
      </span>
    </span>
  )
}
