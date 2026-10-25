// The guided tour's steps (D303). Each step opens one screen of the sample investigation, points at
// one real thing on it and says what it is. What a step opens comes from `sample.tour`, which reads
// the sample's own registries, so no step names something the sample does not hold; the Python
// test `tests/test_sample.py` and the end-to-end test `gui/tests/tour.spec.ts` guard both halves.

import type { ReactNode } from 'react'
import type { TourTargets } from '@shared/api'
import type { Route } from '@renderer/lib/store'

export interface TourStep {
  id: string
  title: string
  body: (t: TourTargets) => ReactNode
  /** Where the step takes the app. */
  route: (t: TourTargets) => Route
  /** The element the step points at: a CSS selector, looked for once the screen has drawn. */
  target: (t: TourTargets) => string
  /** Opens or closes something the route alone can't (the Add documents window). */
  enter?: () => void
  leave?: () => void
}

const q = (s: string) => `“${s}”`
const rid = (r: string) => `.rv-card[data-rid="${CSS.escape(r)}"], .rv-tt-row[data-rid="${CSS.escape(r)}"]`
const subject = (title: string) => title.split(' — ').slice(1).join(' — ') || title
const fact = (id: string) => `[data-fact-id="${CSS.escape(id)}"]`

export const DOCS_URL = 'https://tomcardoso.github.io/watchdog/getting-started.html'

export function tourSteps(open: { add: () => void; closeAdd: () => void }): TourStep[] {
  return [
    {
      id: 'overview',
      title: 'The Overview',
      body: () => (
        <>
          <p>Port Calder Waterfront is a made-up investigation into a city’s land purchase and a contract awarded without a tender. Every person and company in it is invented, and building it cost nothing.</p>
          <p>Each investigation opens here. The highlighted briefing is the short report Watchdog writes after every batch of documents: what was added, where records disagree and what to check next.</p>
        </>
      ),
      route: () => ({ view: 'home' }),
      target: () => '.home-briefing'
    },
    {
      id: 'scanned',
      title: 'A scanned page you can search',
      body: (t) => (
        <>
          <p>Each document opens beside the facts taken from it, and every fact names its page. This page of the appraisal was a scan with no text, so Watchdog read it on this computer with optical character recognition (OCR).</p>
          <p>Type {q(t.scanned.find)} in Find in document: the match is marked on the scanned image itself.</p>
        </>
      ),
      route: (t) => ({ view: 'document', sha: t.scanned.sha, page: t.scanned.page }),
      target: () => '.find-box'
    },
    {
      id: 'entity',
      title: 'An entity and its facts',
      body: (t) => (
        <>
          <p>An entity is a person, organization, public body, place, asset or court case named in the documents. This page gathers every fact about {t.entity.name}, each with the document and page it came from.</p>
          <p>Under a fact, Show the matched passage opens the sentence it was drawn from. The summary above is written from these facts and cites them.</p>
        </>
      ),
      route: (t) => ({ view: 'entity', id: t.entity.id }),
      target: () => '.ent-main-col [data-fact-id]'
    },
    {
      id: 'verify',
      title: 'Checking a fact',
      body: () => (
        <>
          <p>Watchdog never decides that a fact is true. You do: read the source, then mark the fact Verified, Disputed or Can’t verify. Your mark and your name are kept with it.</p>
          <p>The sample’s fictional reporter has verified this one. Review, under Verification, lists every fact and whether anyone has checked it.</p>
        </>
      ),
      route: (t) => ({ view: 'entity', id: t.entity.id }),
      target: (t) => `.ent-main-col ${fact(t.entity.fact)}`
    },
    {
      id: 'contradiction',
      title: 'A contradiction',
      body: (t) => (
        <>
          <p>A contradiction is two documents, or two places in one, that say different things about the same thing. Here the records disagree on {q(subject(t.contradiction.title))}, and each claim links to its page.</p>
          <p>Read both at the source. Once you have decided which to trust, or noted the conflict, choose Mark handled; Undo brings it back.</p>
        </>
      ),
      route: () => ({ view: 'review', kind: 'contradictions' }),
      target: (t) => rid(t.contradiction.rid)
    },
    {
      id: 'duplicate',
      title: 'A near-duplicate',
      body: () => (
        <>
          <p>When documents are added, each one is compared with those already here. This revised news release is almost the same as the original, so it is flagged as a possible duplicate.</p>
          <p>Compare the two, then say whether they are copies of the same record or different documents. Neither answer deletes anything.</p>
        </>
      ),
      route: () => ({ view: 'review', kind: 'duplicates' }),
      target: (t) => rid(t.duplicate.rid)
    },
    {
      id: 'lead',
      title: 'A lead',
      body: () => (
        <>
          <p>Leads are places the record points that the investigation has not followed yet: a body named but never profiled, an entity with no known connections, a relationship the model inferred.</p>
          <p>They are suggestions for reporting, not findings.</p>
        </>
      ),
      route: () => ({ view: 'review', kind: 'leads' }),
      target: (t) => rid(t.lead.rid)
    },
    {
      id: 'search',
      title: 'Search',
      body: (t) => (
        <>
          <p>Search finds your exact wording in every document and note, with the page it is on. Here it finds each use of {q(t.search)}, the reason City staff gave for skipping a tender.</p>
          <p>Once an investigation’s search index is built, Search also finds passages close in meaning, worded differently from your query.</p>
        </>
      ),
      route: (t) => ({ view: 'search', query: t.search }),
      target: () => '.srch-box'
    },
    {
      id: 'timeline',
      title: 'The timeline',
      body: () => (
        <>
          <p>Every dated fact lands on the timeline, linked to its source page. Filter it by entity, type or year to see the order in which things happened.</p>
          <p>The bars show how many events fall in each year; select one to jump to it.</p>
        </>
      ),
      route: () => ({ view: 'timeline' }),
      target: () => '.tl-density'
    },
    {
      id: 'network',
      title: 'The network',
      body: () => (
        <>
          <p>The network shows who is connected to whom. Each dot is an entity and each line a relationship stated in a document, such as a director of a company or a party to a contract.</p>
          <p>Choose which kinds of entity to show here. Click a dot to see its connections; double-click to open its page.</p>
        </>
      ),
      route: () => ({ view: 'graph' }),
      target: () => '.gr-legend'
    },
    {
      id: 'usage',
      title: 'What a run costs',
      body: () => (
        <>
          <p>Activity records every run: what ran, how long it took and what it cost. Your own documents are read by the AI model you chose, billed by its provider or counted against your Claude plan.</p>
          <p>The sample was built with prepared answers instead of a model, so its runs show $0.</p>
        </>
      ),
      route: () => ({ view: 'activity', tab: 'usage' }),
      target: () => '.act-runs'
    },
    {
      id: 'add',
      title: 'Adding documents',
      body: (t) =>
        t.incoming.length > 0 ? (
          <>
            <p>{t.incoming.length === 1 ? 'One file is' : `${t.incoming.length} files are`} waiting in this investigation’s incoming folder. Choose Read documents to add {t.incoming.length === 1 ? 'it' : 'them'} and watch the run: Watchdog converts each file on this computer, extracts its facts, then updates the entities, the timeline and the briefing.</p>
            <p>In the sample, prepared answers stand in for the AI model, so nothing is sent anywhere and it costs nothing. When the run finishes, the Overview has a new briefing, with a new contradiction to look at. With your own documents, Watchdog first shows what processing will cost and asks you to confirm they are public records.</p>
          </>
        ) : (
          <>
            <p>The sample’s example files have been added. The newest briefing on the Overview is from that run.</p>
            <p>With your own documents, Watchdog shows what processing will cost and asks you to confirm they are public records before anything goes to a model.</p>
          </>
        ),
      route: () => ({ view: 'home' }),
      target: (t) => (t.incoming.length > 0 ? '.add-waiting' : '.modal .add-label'),
      enter: open.add,
      leave: open.closeAdd
    },
    {
      id: 'ask',
      title: 'Ask Claude',
      body: () => (
        <>
          <p>Ask Claude answers questions about the investigation, citing the documents and pages it relied on, and can look for connections across the whole record. It asks you before it changes anything.</p>
          <p>Try one of these questions. In the sample, the conversations were prepared in advance, so nothing is sent to Claude and it costs nothing. In your own investigations you can ask anything; it needs a Claude sign-in, which you can add in Settings under Models &amp; keys.</p>
        </>
      ),
      route: () => ({ view: 'ask' }),
      target: () => '[data-tour="sample-ask"]'
    },
    {
      id: 'next',
      title: 'Your own investigation',
      body: () => (
        <>
          <p>That is the tour. When you are ready, create an investigation for your own reporting and add the public records you hold.</p>
          <p>The sample stays in your list until you delete it, and Take the Tour, in the Help menu, starts this tour again.</p>
        </>
      ),
      route: () => ({ view: 'projects' }),
      target: () => '[data-tour="new-investigation"]'
    }
  ]
}
