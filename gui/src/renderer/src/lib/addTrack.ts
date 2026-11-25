// The Add documents flow as one track (D413): Read, the pause to confirm, Extract, Write up and
// Brief, each a section of the bar that fills by its own count. The sections are not sized by
// time: nothing predicts how long extraction or write-up will take, so each says how far through
// itself the run is, and the headline says which one is under way.
//
// Write up and Brief count steps from the run's announced plan (`orchestrate.POST_STAGES`, the
// `plan` progress event): a step with nothing to do sends no event and is passed, so every step
// before the current one counts as done. Pure, so it is unit-tested (addTrack.test.ts).

export type SectionKey = 'read' | 'confirm' | 'extract' | 'write' | 'brief'
export type SectionState = 'waiting' | 'now' | 'done' | 'skipped' | 'stopped'

export interface TrackSection {
  key: SectionKey
  label: string
  state: SectionState
  /** 0 to 1: how full the section is drawn. */
  fraction: number
  /** No count to show: the section pulses instead of filling. */
  pulse: boolean
  /** The count under the section ("4 of 9 documents"), or ''. */
  where: string
  /** Runs on this computer (true) or calls a model (false); null for the pause. */
  local: boolean | null
}

export interface Track {
  sections: TrackSection[]
  /** "Extracting · 4 of 9 documents"; null before anything has started. */
  headline: string | null
  /** The step's own name and count beside the headline, or null. */
  side: string | null
}

export type FlowPhase = 'choose' | 'reading' | 'gate' | 'running' | 'done'

export interface TrackInput {
  phase: FlowPhase
  /** Files read so far in this flow; `inner` is how far through the current one (0 to 1). */
  read: { done: number; total: number; inner: number } | null
  /** How the pause went: shown to the reporter, or passed without asking (automatic approval or
   * every stage on this computer). */
  confirm: 'asked' | 'auto' | null
  /** The run's progress once it has started. */
  run: RunProgress | null
  /** The run finished ('done'), ended otherwise, or is still going (null). */
  runEnded: 'done' | 'stopped' | null
  /** The run only finishes an earlier batch: there is nothing to extract. */
  nothingToExtract: boolean
}

export interface RunProgress {
  stage: string | null
  done: number | null
  total: number | null
  docs: Record<string, { state: string }>
  plan?: string[] | null
  seen?: string[]
}

// Which section each post-processing stage is drawn in; any other stage of the plan is Write up.
const BRIEF_STAGES = new Set(['briefing', 'leads', 'request-sources', 'requests'])

const LABELS: Record<SectionKey, string> = {
  read: 'Read', confirm: 'Confirm', extract: 'Extract', write: 'Write up', brief: 'Brief'
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

function section(key: SectionKey, state: SectionState, fraction = state === 'done' ? 1 : 0, where = '', pulse = false): TrackSection {
  return { key, label: LABELS[key], state, fraction: Math.max(0, Math.min(1, fraction)), pulse, where, local: key === 'read' ? true : key === 'confirm' ? null : false }
}

/** Where the run is in a post-processing section: index of the current step and the count. */
function stepsOf(plan: string[], key: 'write' | 'brief'): string[] {
  return plan.filter((s) => (key === 'brief') === BRIEF_STAGES.has(s))
}

function currentStep(run: RunProgress | null): string | null {
  const plan = run?.plan
  if (!plan) return null
  if (run.stage && plan.includes(run.stage)) return run.stage
  return [...(run.seen ?? [])].reverse().find((s) => plan.includes(s)) ?? null
}

/** The track for one moment of the flow. `labels` names a stage ("Reconciling entities"). */
export function buildTrack(input: TrackInput, labels: Record<string, string> = {}): Track {
  const { phase, read, confirm, run, runEnded } = input
  const started = phase !== 'choose'
  const atRun = phase === 'running' || phase === 'done'

  // ── Read ──
  let readSec: TrackSection
  if (phase === 'reading' && read) {
    readSec = section('read', 'now', read.total ? (read.done + read.inner) / read.total : 0, `${read.done} of ${plural(read.total, 'file')}`)
  } else if (phase === 'reading') {
    readSec = section('read', 'now', 0, '', true)
  } else if (started) {
    readSec = read && read.total ? section('read', 'done', 1, `${read.done} of ${plural(read.total, 'file')}`) : section('read', 'done', 1)
  } else {
    readSec = section('read', 'waiting')
  }

  // ── Confirm ──
  const confirmSec = phase === 'gate' ? section('confirm', 'now')
    : atRun ? section('confirm', confirm === 'auto' ? 'skipped' : 'done')
    : section('confirm', 'waiting')

  // ── Extract, Write up, Brief ──
  const plan = run?.plan ?? null
  const stage = run?.stage ?? null
  const inPlan = !!plan && !!stage && plan.includes(stage)
  // The run has reached post-processing once it announces the plan (before its first step).
  const reachedPost = !!plan
  // Where post-processing is: the current stage, or once it has moved past the plan (the run's
  // closing 'done'), the last step that started.
  const cur = currentStep(run)
  const currentPost: 'write' | 'brief' | null = cur ? (BRIEF_STAGES.has(cur) ? 'brief' : 'write') : reachedPost ? 'write' : null

  let extractSec: TrackSection
  const docs = Object.values(run?.docs ?? {})
  const docTotal = (stage === 'dig' && run?.total) || docs.length
  const docDone = docs.filter((d) => d.state === 'done' || d.state === 'failed').length
  const docWhere = docTotal ? `${docDone} of ${plural(docTotal, 'document')}` : ''
  if (!atRun) extractSec = section('extract', 'waiting')
  else if (input.nothingToExtract) extractSec = section('extract', 'skipped', 0, 'Nothing new')
  else if (reachedPost || runEnded === 'done') extractSec = section('extract', 'done', 1, docWhere)
  else if (runEnded) extractSec = section('extract', 'stopped', docTotal ? docDone / docTotal : 0, docWhere)
  else extractSec = section('extract', 'now', docTotal ? docDone / docTotal : 0, docWhere, !docTotal)

  const postSec = (key: 'write' | 'brief'): TrackSection => {
    const steps = plan ? stepsOf(plan, key) : []
    const n = steps.length
    const count = n ? `${plural(n, 'step')}` : ''
    if (runEnded === 'done') return section(key, steps.length || !plan ? 'done' : 'skipped', 1, n ? `${n} of ${count}` : '')
    const order: ('write' | 'brief')[] = ['write', 'brief']
    if (!currentPost || order.indexOf(key) > order.indexOf(currentPost)) return section(key, 'waiting')
    if (order.indexOf(key) < order.indexOf(currentPost)) return section(key, 'done', 1, n ? `${n} of ${count}` : '')
    // The current section.
    const i = cur ? Math.max(0, steps.indexOf(cur)) : 0
    const inner = inPlan && run?.total ? (run.done ?? 0) / run.total : 0
    const fraction = n ? (i + inner) / n : 0
    const where = n ? `Step ${i + 1} of ${n}` : ''
    return section(key, runEnded ? 'stopped' : 'now', fraction, where)
  }
  const writeSec = postSec('write')
  const briefSec = postSec('brief')
  const sections = [readSec, confirmSec, extractSec, writeSec, briefSec]

  // ── Headline ──
  let headline: string | null = null
  let side: string | null = null
  if (phase === 'reading') headline = readSec.where ? `Reading · ${readSec.where}` : 'Reading'
  else if (phase === 'gate') headline = 'Waiting for you to confirm'
  else if (atRun && runEnded === 'done') headline = 'Every stage finished'
  else if (atRun) {
    const stopped = sections.find((s) => s.state === 'stopped')
    const now = sections.find((s) => s.state === 'now')
    const verb: Record<string, string> = { extract: 'Extracting', write: 'Writing up', brief: 'Briefing' }
    if (stopped) headline = `Stopped during ${stopped.label}`
    else if (now) headline = now.where ? `${verb[now.key]} · ${now.where.charAt(0).toLowerCase()}${now.where.slice(1)}` : verb[now.key]
    if (inPlan) {
      side = labels[stage!] ?? stage
      if (run?.total) side += ` · ${run.done ?? 0} of ${run.total}`
    } else if (!reachedPost && stage && stage !== 'dig' && labels[stage]) side = labels[stage]
  }
  return { sections, headline, side }
}

/** The steps of a post-processing section, for the list under the track: each done (ran),
 * passed (had nothing to do), under way or waiting. */
export function stepList(run: RunProgress | null, key: 'write' | 'brief', runEnded: 'done' | 'stopped' | null): { stage: string; state: 'done' | 'passed' | 'now' | 'waiting' | 'stopped' }[] {
  const plan = run?.plan
  if (!plan) return []
  const steps = stepsOf(plan, key)
  const seen = new Set(run?.seen ?? [])
  const cur = currentStep(run)
  const at = cur ? plan.indexOf(cur) : -1
  return steps.map((s) => {
    const p = plan.indexOf(s)
    if (runEnded === 'done' || p < at) return { stage: s, state: seen.has(s) ? 'done' : 'passed' }
    if (p === at) return { stage: s, state: runEnded ? 'stopped' : 'now' }
    return { stage: s, state: 'waiting' }
  })
}
