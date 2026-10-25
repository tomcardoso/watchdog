// Unit tests for the timeline's range labels (D330). Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { spanLabel } from './dateSpan.ts'

test('a range of months in one year', () => {
  assert.equal(spanLabel('2024-04', '2024-06'), 'Apr–Jun 2024')
})

test('a range of days', () => {
  assert.equal(spanLabel('2024-04-03', '2024-04-07'), '3–7 Apr 2024')
  assert.equal(spanLabel('2024-04-03', '2024-06-07'), '3 Apr – 7 Jun 2024')
  assert.equal(spanLabel('2023-12-30', '2024-01-02'), '30 Dec 2023 – 2 Jan 2024')
})

test('a range of years, and across years', () => {
  assert.equal(spanLabel('2019', '2021'), '2019–2021')
  assert.equal(spanLabel('2019-12', '2020-02'), 'Dec 2019 – Feb 2020')
})

test('a single date, and a malformed end', () => {
  assert.equal(spanLabel('2024-04-03'), '3 Apr 2024')
  assert.equal(spanLabel('2024-04', 'soon'), 'Apr 2024')
  assert.equal(spanLabel('2024'), '2024')
})
