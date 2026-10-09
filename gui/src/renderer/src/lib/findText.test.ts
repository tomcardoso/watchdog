// Unit tests for the shared find matcher (D289). Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { countMatches, findRanges, fold, foldQuery, matchSegments, splitByRanges } from './findText.ts'

const hits = (hay: string, q: string) => findRanges(hay, q).map((r) => hay.slice(r.start, r.end))

test('ignores case and accents', () => {
  assert.deepEqual(hits('Councillor Renée DUFRESNE', 'renee dufresne'), ['Renée DUFRESNE'])
  assert.deepEqual(hits('Councillor Renee Dufresne', 'Renée'), ['Renee'])
  assert.deepEqual(hits('ÉCOLE Saint-Jérôme', 'ecole saint jerome'), ['ÉCOLE Saint-Jérôme'])
})

test('treats curly and straight quotation marks alike', () => {
  assert.deepEqual(hits('The “north pier” works', '"north pier"'), ['“north pier”'])
  assert.deepEqual(hits("the Clerk's office", 'clerk’s'), ["Clerk's"])
  assert.deepEqual(hits('« le quai »', '"le quai"'), ['« le quai »'])
})

test('ignores dashes, hyphens and white space', () => {
  assert.deepEqual(hits('2021–2022 budget', '2021-2022'), ['2021–2022'])
  assert.deepEqual(hits('a term of twenty-five years', 'twenty five'), ['twenty-five'])
  assert.deepEqual(hits('at $1,250,000 peryear.', 'per year'), ['peryear'])
  assert.deepEqual(hits('Calder   Marine\n Ltd.', 'calder marine ltd'), ['Calder   Marine\n Ltd'])
})

test('joins a word hyphenated across a line break', () => {
  assert.deepEqual(hits('the water-\nfront lease', 'waterfront'), ['water-\nfront'])
  assert.deepEqual(hits('the water­\nfront lease', 'waterfront'), ['water­\nfront'])
})

test('maps matches back to the original characters', () => {
  const hay = 'ﬁnal ﬁgures: Ŝ 12'
  const r = findRanges(hay, 'figures')
  assert.equal(r.length, 1)
  assert.equal(hay.slice(r[0].start, r[0].end), 'ﬁgures')
  // Characters outside the basic plane keep their full width.
  assert.deepEqual(hits('📄 Pier 9 📄', 'pier 9'), ['Pier 9'])
})

test('finds every non-overlapping match, left to right', () => {
  assert.equal(countMatches('pier, Pier, PIER', 'pier'), 3)
  assert.equal(countMatches('aaaa', 'aa'), 2)
  assert.equal(countMatches('anything', ''), 0)
  assert.equal(countMatches('a - b', ' - '), 0)
  assert.equal(foldQuery('  —  '), '')
})

test('a match can run across segments and is split into their parts', () => {
  const { count, bySegment } = matchSegments(['The commission approved the water-', 'front lease to Calder'], 'waterfront lease')
  assert.equal(count, 1)
  assert.deepEqual(bySegment.get(0), [{ from: 28, to: 34, k: 0 }])
  assert.deepEqual(bySegment.get(1), [{ from: 0, to: 11, k: 0 }])
  const two = matchSegments(['Pier 9', 'and pier 9'], 'pier 9')
  assert.equal(two.count, 2)
  assert.deepEqual(two.bySegment.get(1), [{ from: 4, to: 10, k: 1 }])
})

test('splitByRanges cuts text into plain and matched pieces', () => {
  const text = 'Pier 9 and pier 9.'
  assert.deepEqual(splitByRanges(text, findRanges(text, 'pier')), [
    { text: 'Pier', hit: 0 },
    { text: ' 9 and ', hit: null },
    { text: 'pier', hit: 1 },
    { text: ' 9.', hit: null }
  ])
  assert.equal(fold('A').text, 'a')
})
