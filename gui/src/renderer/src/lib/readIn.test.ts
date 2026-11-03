// Unit tests for the Documents screen's "Language" line (D382). Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pageRanges, readIn } from './readIn.ts'

test('says nothing for a document pre-processed before the language was recorded', () => {
  assert.equal(readIn(null), null)
  assert.equal(readIn(undefined), null)
})

test('says when no page had enough text to judge', () => {
  assert.equal(readIn({ code: null, source: 'undetermined', mixed: false, pages: {} }), 'Not detected: too little text to tell')
})

test('names a one-language document, and a sidecar that declared it', () => {
  assert.equal(readIn({ code: 'en', source: 'detected', mixed: false, pages: { en: [1, 2] } }), 'English')
  assert.equal(readIn({ code: 'fr', source: 'sidecar', mixed: false, pages: { fr: [1] } }), 'French (from its sidecar file)')
})

test('says which scanned pages were read again in their language', () => {
  assert.equal(
    readIn({ code: 'fr', source: 'detected', mixed: false, pages: { fr: [1, 2, 3] }, reread: { fr: [2, 3] } }),
    'French. Scanned pages 2–3 read in French'
  )
})

test('names the other language of a mixed document, and pages left as English', () => {
  assert.equal(
    readIn({ code: 'en', source: 'detected', mixed: true, pages: { en: [1, 2, 3], fr: [4, 5] }, ask: { fr: [5] } }),
    'Mostly English, with French on pages 4–5. Scanned French page 5 read as English'
  )
})

test('compacts page numbers into ranges', () => {
  assert.equal(pageRanges([3]), 'page 3')
  assert.equal(pageRanges([7, 1, 8, 4, 9]), 'pages 1, 4 and 7–9')
})
