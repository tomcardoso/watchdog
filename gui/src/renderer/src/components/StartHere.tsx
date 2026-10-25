// Start here (D327): the short ranked slice of Review items to look at first, each with the one
// line on why it is there. The ranking is done by the engine (`triage.rank`); this only shows it.

import { ArrowRight, Bell, Lightbulb, Swords } from 'lucide-react'
import type { ReviewKind, StartHereItem } from '@shared/api'
import { fmtNum } from '@renderer/lib/format'
import './starthere.css'

const ICON: Partial<Record<ReviewKind, typeof Swords>> = { contradictions: Swords, leads: Lightbulb, alerts: Bell }
const KIND_LABEL: Partial<Record<ReviewKind, string>> = { contradictions: 'Contradiction', leads: 'Lead', alerts: 'Alert' }

const WORDS: Record<ReviewKind, [string, string]> = {
  contradictions: ['contradiction', 'contradictions'],
  leads: ['lead', 'leads'],
  alerts: ['watch-list alert', 'watch-list alerts'],
  duplicates: ['possible duplicate document', 'possible duplicate documents'],
  merges: ['possible same entity', 'possible same entities']
}

function joinList(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? ''
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/** "6 contradictions and 4 leads, plus 2 tidy-up items" — or '' when nothing was added. */
export function describeAdded(added: Partial<Record<ReviewKind, number>>): string {
  const judgement = (['contradictions', 'leads', 'alerts'] as ReviewKind[])
    .filter((k) => (added[k] ?? 0) > 0)
    .map((k) => `${fmtNum(added[k]!)} ${added[k] === 1 ? WORDS[k][0] : WORDS[k][1]}`)
  const tidy = (added.duplicates ?? 0) + (added.merges ?? 0)
  const tidyText = tidy ? `${fmtNum(tidy)} tidy-up ${tidy === 1 ? 'item' : 'items'}` : ''
  if (!judgement.length) return tidyText
  return joinList(judgement) + (tidyText ? `, plus ${tidyText}` : '')
}

export function StartHereList({ items, onPick, compact }: { items: StartHereItem[]; onPick: (item: StartHereItem) => void; compact?: boolean }) {
  return (
    <ol className={'sh-list' + (compact ? ' compact' : '')}>
      {items.map((it) => {
        const Icon = ICON[it.kind] ?? Lightbulb
        const dash = it.title.indexOf(' — ')
        const head = (dash > 0 ? it.title.slice(0, dash) : it.title).replace(/`/g, '')
        const tail = dash > 0 ? it.title.slice(dash + 3) : ''
        return (
          <li key={it.rid}>
            <button type="button" className="sh-item" onClick={() => onPick(it)}>
              <span className="ico" data-tip={KIND_LABEL[it.kind]} aria-label={KIND_LABEL[it.kind]}>
                <Icon />
              </span>
              <span className="grow col sh-text">
                <span className="t">
                  <b>{head}</b>
                  {tail && <span className="muted"> — {tail.replace(/`/g, '')}</span>}
                </span>
                <span className="why">{it.reason}</span>
              </span>
              <ArrowRight className="go" />
            </button>
          </li>
        )
      })}
    </ol>
  )
}
