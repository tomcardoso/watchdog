// Unit tests for the Add documents track (D413). Run with `npm run test:unit`.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { buildTrack, stepList, type RunProgress, type TrackInput } from './addTrack.ts'

const PLAN = ['fact-tags', 'fold', 'reconcile', 'role-targets', 'dates', 'commit', 'contradictions',
  'relationships', 'synthesis', 'timeline', 'briefing', 'leads', 'request-sources', 'requests']

const base: TrackInput = { phase: 'choose', read: null, confirm: null, run: null, runEnded: null, nothingToExtract: false }
const run = (p: Partial<RunProgress>): RunProgress => ({ stage: null, done: null, total: null, docs: {}, ...p })
const states = (t: ReturnType<typeof buildTrack>) => t.sections.map((s) => s.state).join(' ')

test('before anything starts every section waits and there is no headline', () => {
  const t = buildTrack(base)
  assert.equal(states(t), 'waiting waiting waiting waiting waiting')
  assert.equal(t.headline, null)
})

test('reading fills by files, with the current file part-way', () => {
  const t = buildTrack({ ...base, phase: 'reading', read: { done: 4, total: 10, inner: 0.5 } })
  assert.equal(t.sections[0].state, 'now')
  assert.equal(t.sections[0].fraction, 0.45)
  assert.equal(t.headline, 'Reading · 4 of 10 files')
})

test('the pause waits for the reporter, and is drawn as passed when it was not asked', () => {
  assert.equal(states(buildTrack({ ...base, phase: 'gate', read: { done: 3, total: 3, inner: 0 } })), 'done now waiting waiting waiting')
  const auto = buildTrack({ ...base, phase: 'running', confirm: 'auto', run: run({}) })
  assert.equal(auto.sections[1].state, 'skipped')
})

test('extraction fills by documents finished', () => {
  const docs = { a: { state: 'done' }, b: { state: 'failed' }, c: { state: 'extracting' }, d: { state: 'queued' } }
  const t = buildTrack({ ...base, phase: 'running', confirm: 'asked', run: run({ stage: 'dig', done: 2, total: 4, docs }) })
  assert.equal(states(t), 'done done now waiting waiting')
  assert.equal(t.sections[2].fraction, 0.5)
  assert.equal(t.headline, 'Extracting · 2 of 4 documents')
})

test('write-up counts steps from the plan, passing steps that had nothing to do', () => {
  const t = buildTrack({ ...base, phase: 'running', confirm: 'asked', run: run({ stage: 'synthesis', done: 70, total: 140, plan: PLAN, seen: ['fold', 'reconcile', 'commit', 'synthesis'] }) },
    { synthesis: 'Writing entity summaries' })
  assert.equal(states(t), 'done done done now waiting')
  // Step 9 of 10, half-way through it.
  assert.equal(t.sections[3].where, 'Step 9 of 10')
  assert.equal(t.sections[3].fraction, 0.85)
  assert.equal(t.headline, 'Writing up · step 9 of 10')
  assert.equal(t.side, 'Writing entity summaries · 70 of 140')
  const steps = stepList(run({ stage: 'synthesis', plan: PLAN, seen: ['fold', 'reconcile', 'commit', 'synthesis'] }), 'write', null)
  assert.deepEqual(steps.map((s) => s.state), ['passed', 'done', 'done', 'passed', 'passed', 'done', 'passed', 'passed', 'now', 'waiting'])
})

test('the brief section follows write-up, and a skipped briefing is not counted', () => {
  const plan = PLAN.filter((s) => s !== 'briefing')
  const t = buildTrack({ ...base, phase: 'running', confirm: 'asked', run: run({ stage: 'leads', plan, seen: ['leads'] }) })
  assert.equal(states(t), 'done done done done now')
  assert.equal(t.sections[4].where, 'Step 1 of 3')
})

test('a finished run is full, and a stopped one marks where it stopped', () => {
  const done = buildTrack({ ...base, phase: 'done', confirm: 'asked', runEnded: 'done', run: run({ stage: 'done', plan: PLAN, seen: ['fold', 'done'] }) })
  assert.equal(states(done), 'done done done done done')
  assert.equal(done.headline, 'Every stage finished')
  const atExtract = buildTrack({ ...base, phase: 'done', confirm: 'asked', runEnded: 'stopped', run: run({ stage: 'done', docs: { a: { state: 'failed' } } }) })
  assert.equal(states(atExtract), 'done done stopped waiting waiting')
  assert.equal(atExtract.headline, 'Stopped during Extract')
  // Past the plan's last event, the last step that started is where it stopped.
  const atTimeline = buildTrack({ ...base, phase: 'done', confirm: 'asked', runEnded: 'stopped', run: run({ stage: 'done', plan: PLAN, seen: ['fold', 'timeline', 'done'] }) })
  assert.equal(states(atTimeline), 'done done done stopped waiting')
  assert.equal(atTimeline.sections[3].where, 'Step 10 of 10')
})

test('a run that only finishes an earlier batch has nothing to extract', () => {
  const t = buildTrack({ ...base, phase: 'running', confirm: 'asked', nothingToExtract: true, run: run({ stage: 'reconcile', plan: PLAN, seen: ['reconcile'] }) })
  assert.equal(t.sections[2].state, 'skipped')
  assert.equal(t.sections[2].where, 'Nothing new')
})
