// The sample investigation and its guided tour (D303).
//
// `takeTour()` (Help → Take the tour, first-run setup, the investigations screen) opens the sample,
// building it first when it isn't there, and starts the tour at its first step. `exploreSample()`
// opens it, offering to rebuild it fresh when it already exists. The tour itself is a small panel
// that does not block the app: each step opens a screen of the sample and rings one real element on
// it. The step is remembered on this computer (local storage), so closing the app part-way resumes
// it the next time the sample is open.

import { ArrowLeft, ArrowRight, BookOpenText, Compass, RotateCcw, X } from 'lucide-react'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { create } from 'zustand'
import type { Project, SampleStatus, TourTargets } from '@shared/api'
import { Button, Callout, Modal } from '@renderer/components/ui'
import { call, errorMessage, invalidate } from '@renderer/lib/rpc'
import { runAction, startJob, waitForJob } from '@renderer/lib/jobs'
import { toast, useApp } from '@renderer/lib/store'
import { DOCS_URL, tourSteps } from './steps'
import './tour.css'

const STORE_KEY = 'watchdog.tour'

interface Saved {
  active: boolean
  step: number
  slug: string | null
}

type Prompt =
  | { kind: 'build'; status: SampleStatus; then: 'tour' | 'open' }
  | { kind: 'exists'; status: SampleStatus }

interface TourState extends Saved {
  targets: TourTargets | null
  prompt: Prompt | null
  building: boolean
  error: string
}

function load(): Saved {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE_KEY) ?? 'null') as Partial<Saved> | null
    if (raw && typeof raw.step === 'number') return { active: !!raw.active, step: raw.step, slug: raw.slug ?? null }
  } catch {
    /* storage unavailable or unreadable: start fresh */
  }
  return { active: false, step: 0, slug: null }
}

function save(s: Saved): void {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ active: s.active, step: s.step, slug: s.slug }))
  } catch {
    /* the tour still works; it just won't resume */
  }
}

export const useTour = create<TourState>(() => ({ ...load(), targets: null, prompt: null, building: false, error: '' }))

function setSaved(patch: Partial<Saved>): void {
  useTour.setState(patch)
  const { active, step, slug } = useTour.getState()
  save({ active, step, slug })
}

const parentOf = (path: string) => path.replace(/[\\/][^\\/]+[\\/]?$/, '') || path

// ── opening and building the sample ──────────────────────────────────────────

async function openSample(slug: string): Promise<Project | null> {
  let p = await call('projects.get', { slug })
  if (p.archived) {
    await runAction('projects-archive', { slug, archived: false }, null)
    invalidate('projects.')
    p = await call('projects.get', { slug })
  }
  if (p.health) return null
  useApp.getState().setProject(p)
  return p
}

async function begin(p: Project, step: number): Promise<void> {
  try {
    const targets = await call('sample.tour', { vault: p.path })
    useTour.setState({ targets })
    setSaved({ active: true, step, slug: p.slug })
  } catch (e) {
    toast({ kind: 'error', title: 'The tour could not start', body: errorMessage(e) })
  }
}

/** Help → Take the tour: open the sample (building it if needed) and start at the first step. */
export async function takeTour(): Promise<void> {
  try {
    const status = await call('sample.status', {})
    if (status.listed && status.exists && status.slug) {
      const p = await openSample(status.slug)
      if (p) return begin(p, 0)
    }
    useTour.setState({ prompt: { kind: 'build', status, then: 'tour' }, error: '' })
  } catch (e) {
    toast({ kind: 'error', title: 'The sample investigation could not be opened', body: errorMessage(e) })
  }
}

/** The investigations screen and first-run setup: open the sample, or build it. When it already
 * exists, offer to open it or rebuild it fresh. */
export async function exploreSample(): Promise<void> {
  try {
    const status = await call('sample.status', {})
    const ready = status.listed && status.exists
    useTour.setState({ prompt: ready ? { kind: 'exists', status } : { kind: 'build', status, then: 'tour' }, error: '' })
  } catch (e) {
    toast({ kind: 'error', title: 'The sample investigation could not be opened', body: errorMessage(e) })
  }
}

/** The sample's card menu: confirm, then delete the sample and build it again. */
export async function rebuildSample(): Promise<void> {
  try {
    const status = await call('sample.status', {})
    useTour.setState({ prompt: { kind: 'build', status, then: 'open' }, error: '' })
  } catch (e) {
    toast({ kind: 'error', title: 'The sample investigation could not be found', body: errorMessage(e) })
  }
}

async function build(status: SampleStatus, then: 'tour' | 'open'): Promise<void> {
  useTour.setState({ building: true, error: '' })
  try {
    const parent = parentOf(status.path)
    const allowed = await window.watchdog.access.request(parent, 'new investigations', 'Watchdog creates the sample investigation as a folder here.')
    if (!allowed) {
      useTour.setState({ error: 'Watchdog needs your permission to create the sample in that folder.' })
      return
    }
    const rebuild = status.listed || status.exists
    // The sample is about to be replaced: don't leave it open underneath.
    if (rebuild && useApp.getState().project?.slug === status.slug) useApp.getState().setProject(null)
    const job = await startJob('sample-create', { dir: parent, rebuild }, rebuild ? 'Rebuilding the sample investigation' : 'Building the sample investigation', 'sample', null)
    const done = await waitForJob(job.id)
    if (done.state !== 'done') {
      const log = useApp.getState().jobs[job.id]?.log ?? []
      const reason = [...log].reverse().find((l) => l.stream === 'err' && l.text.trim())?.text
      useTour.setState({ error: reason ?? 'The sample was not built. Activity shows what happened.' })
      return
    }
    invalidate('projects.')
    const slug = (done.result as { slug?: string } | null)?.slug ?? (await call('sample.status', {})).slug
    useTour.setState({ prompt: null })
    if (!slug) return
    const p = await openSample(slug)
    if (p && then === 'tour') await begin(p, 0)
    else if (then === 'tour') setSaved({ active: false })
  } catch (e) {
    useTour.setState({ error: errorMessage(e) })
  } finally {
    useTour.setState({ building: false })
  }
}

export function stopTour(): void {
  setSaved({ active: false })
}

// ── the prompt: build, or open and rebuild ───────────────────────────────────

function SamplePrompt() {
  const prompt = useTour((s) => s.prompt)
  const building = useTour((s) => s.building)
  const error = useTour((s) => s.error)
  const [confirmRebuild, setConfirmRebuild] = useState(false)
  useEffect(() => setConfirmRebuild(false), [prompt])
  if (!prompt) return null
  const close = () => !building && useTour.setState({ prompt: null, error: '' })
  const status = prompt.status

  if (prompt.kind === 'exists' && !confirmRebuild) {
    const open = async (tour: boolean) => {
      const p = status.slug ? await openSample(status.slug).catch(() => null) : null
      useTour.setState({ prompt: null })
      if (p && tour) await begin(p, 0)
    }
    return (
      <Modal
        open
        onClose={close}
        title="The sample investigation"
        sub="Port Calder Waterfront is a made-up investigation for learning Watchdog. Every person and company in it is invented."
        footer={
          <>
            <Button variant="ghost" icon={RotateCcw} onClick={() => setConfirmRebuild(true)}>Rebuild it fresh…</Button>
            <span className="spacer" />
            <Button onClick={() => void open(false)}>Open the sample</Button>
            <Button variant="primary" icon={Compass} onClick={() => void open(true)}>Take the tour</Button>
          </>
        }
      >
        <p className="tour-prompt-text">It is already in your list. Open it to look around on your own, or take the guided tour, which points out one thing at a time.</p>
      </Modal>
    )
  }

  const rebuilding = prompt.kind === 'exists' || status.listed || status.exists
  return (
    <Modal
      open
      onClose={close}
      dismissable={!building}
      title={rebuilding ? 'Rebuild the sample investigation?' : 'Explore a sample investigation'}
      sub={rebuilding ? undefined : 'Port Calder Waterfront is a made-up investigation for learning Watchdog. Every person and company in it is invented.'}
      footer={
        <>
          <Button variant="ghost" disabled={building} onClick={close}>Cancel</Button>
          <Button variant="primary" loading={building} icon={rebuilding ? RotateCcw : Compass} onClick={() => void build(status, prompt.kind === 'build' ? prompt.then : 'tour')}>
            {rebuilding ? 'Rebuild the sample' : 'Build the sample'}
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        {rebuilding ? (
          <p className="tour-prompt-text">
            The sample is deleted and built again as it first was. Anything you changed in it, such as facts you checked, items you marked handled or notes,
            is lost. Your other investigations are not touched.
          </p>
        ) : (
          <>
            <p className="tour-prompt-text">
              Watchdog builds it on this computer in under a minute: fourteen public-looking records, already read, with the contradictions, leads and briefing
              a real investigation produces. It uses prepared answers instead of an AI model, so it costs nothing and needs no sign-in or internet connection.
            </p>
            <p className="tour-prompt-text faint mono truncate" title={status.path}>{status.path}</p>
          </>
        )}
        {error && <Callout tone="danger">{error}</Callout>}
      </div>
    </Modal>
  )
}

// ── the tour panel ───────────────────────────────────────────────────────────

interface Box {
  top: number
  left: number
  width: number
  height: number
}

const PAD = 6
const sameBox = (a: Box | null, b: Box | null) => !!a && !!b && a.top === b.top && a.left === b.left && a.width === b.width && a.height === b.height

/** Find `selector` once the screen has drawn it, scroll it into view and follow where it is. */
function useTargetBox(selector: string | null, key: string): Box | null {
  const [box, setBox] = useState<Box | null>(null)
  useEffect(() => {
    setBox(null)
    if (!selector) return
    let frame = 0
    let scrolled = false
    let last: Box | null = null
    const started = Date.now()
    const tick = () => {
      let el: Element | null = null
      try {
        el = document.querySelector(selector)
      } catch {
        el = null
      }
      const r = el?.getBoundingClientRect()
      if (el && r && r.width > 0 && r.height > 0) {
        if (!scrolled && Date.now() - started > 120) {
          scrolled = true
          el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
        }
        const next = { top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height) }
        if (!sameBox(next, last)) {
          last = next
          setBox(next)
        }
      } else if (last) {
        last = null
        setBox(null)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [selector, key])
  return box
}

function overlaps(a: Box, b: { top: number; left: number; width: number; height: number }): boolean {
  return a.left < b.left + b.width && a.left + a.width > b.left && a.top < b.top + b.height && a.top + a.height > b.top
}

export function TourLayer() {
  const active = useTour((s) => s.active)
  const step = useTour((s) => s.step)
  const slug = useTour((s) => s.slug)
  const targets = useTour((s) => s.targets)
  const project = useApp((s) => s.project)
  const onSample = active && !!project && project.slug === slug
  const steps = useMemo(() => tourSteps({ add: () => useApp.getState().openAdd(), closeAdd: () => useApp.getState().closeAdd() }), [])
  const current = steps[Math.min(step, steps.length - 1)]
  const panel = useRef<HTMLDivElement>(null)
  const next = useRef<HTMLButtonElement>(null)
  const entered = useRef<number | null>(null)

  // Resumed with the sample open (the app was closed part-way): fetch what the steps point at.
  useEffect(() => {
    if (!onSample || targets || !project) return
    call('sample.tour', { vault: project.path })
      .then((t) => useTour.setState({ targets: t }))
      .catch(() => setSaved({ active: false }))
  }, [onSample, targets, project])

  // Each step opens its screen once, on arriving at it.
  useEffect(() => {
    if (!onSample || !targets) {
      if (entered.current !== null) {
        steps[entered.current]?.leave?.()
        entered.current = null
      }
      return
    }
    if (entered.current === step) return
    if (entered.current !== null) steps[entered.current]?.leave?.()
    entered.current = step
    useApp.getState().navigate(current.route(targets))
    current.enter?.()
  }, [onSample, targets, step, current, steps])

  const selector = onSample && targets ? current.target(targets) : null
  const box = useTargetBox(selector, `${step}`)
  // The panel's main button takes the keyboard focus once the step's screen has drawn (a screen
  // that focuses its own field on opening would otherwise take it back), once per step.
  const found = !!box
  useEffect(() => {
    if (!found) return
    next.current?.focus({ preventScroll: true })
    // A screen can focus its own field a moment later (Search does): take it back once, unless the
    // reporter has started typing or clicking in the meantime.
    let touched = false
    const mark = () => (touched = true)
    window.addEventListener('pointerdown', mark, true)
    window.addEventListener('keydown', mark, true)
    const t = setTimeout(() => {
      if (!touched && !panel.current?.contains(document.activeElement)) next.current?.focus({ preventScroll: true })
    }, 600)
    return () => {
      clearTimeout(t)
      window.removeEventListener('pointerdown', mark, true)
      window.removeEventListener('keydown', mark, true)
    }
  }, [found, step])
  // Said only once the screen has had time to draw: the reporter has moved elsewhere.
  const [lost, setLost] = useState(false)
  useEffect(() => {
    setLost(false)
    if (box || !selector) return
    const t = setTimeout(() => setLost(true), 2500)
    return () => clearTimeout(t)
  }, [box, selector, step])
  const showAgain = () => {
    entered.current = null
    useTour.setState({ targets: { ...targets! } })
  }

  const go = useCallback(
    (n: number) => {
      if (n < 0) return
      if (n >= steps.length) {
        steps[step]?.leave?.()
        entered.current = null
        stopTour()
        toast({ kind: 'info', title: 'Tour finished', body: 'Help, then Take the tour, starts it again.' })
        return
      }
      setSaved({ step: n })
    },
    [step, steps]
  )
  const skip = () => {
    steps[step]?.leave?.()
    entered.current = null
    stopTour()
  }

  // Place the panel where it does not cover what it points at.
  const [spot, setSpot] = useState<'br' | 'bl' | 'tr' | 'side'>('br')
  useLayoutEffect(() => {
    const el = panel.current
    // A window is open (the Add documents step): keep to the narrow strip beside it.
    if (document.querySelector('.scrim .modal')) return setSpot('side')
    if (!el || !box) return setSpot('br')
    const w = el.offsetWidth
    const h = el.offsetHeight
    const W = window.innerWidth
    const H = window.innerHeight
    const ring = { top: box.top - PAD, left: box.left - PAD, width: box.width + 2 * PAD, height: box.height + 2 * PAD }
    const candidates: ['br' | 'bl' | 'tr', Box][] = [
      ['br', { top: H - h - 20, left: W - w - 20, width: w, height: h }],
      ['bl', { top: H - h - 20, left: 256, width: w, height: h }],
      ['tr', { top: 64, left: W - w - 20, width: w, height: h }]
    ]
    setSpot(candidates.find(([, c]) => !overlaps(c, ring))?.[0] ?? 'br')
  }, [box, step])

  if (!onSample || !targets) return <SamplePrompt />
  const n = steps.length
  const last = step >= n - 1
  return (
    <>
      <SamplePrompt />
      {box && (
        <div
          className="tour-ring"
          aria-hidden="true"
          style={{ top: box.top - PAD, left: box.left - PAD, width: box.width + 2 * PAD, height: box.height + 2 * PAD }}
        />
      )}
      <div
        ref={panel}
        className={`tour-panel tour-${spot}`}
        role="dialog"
        aria-modal="false"
        aria-labelledby="tour-title"
        aria-describedby="tour-body"
        data-step={current.id}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            e.stopPropagation()
            skip()
          }
        }}
      >
        <div className="tour-head">
          <span className="tour-count" aria-live="polite">Step {step + 1} of {n}</span>
          <span className="spacer" />
          <Button variant="ghost" size="sm" icon={X} tip="Skip the tour" onClick={skip} />
        </div>
        <div className="tour-progress" aria-hidden="true">
          <span style={{ width: `${((step + 1) / n) * 100}%` }} />
        </div>
        <h2 id="tour-title" className="tour-title">{current.title}</h2>
        <div id="tour-body" className="tour-body">{current.body(targets)}</div>
        {lost && <p className="tour-missing">This step points at something on another screen. Choose Show me to go back to it.</p>}
        <div className="tour-foot">
          {last ? (
            <>
              <Button variant="ghost" size="sm" icon={BookOpenText} onClick={() => void window.watchdog.shell.openExternal(DOCS_URL)}>Read the guide</Button>
              <span className="spacer" />
              <Button ref={next} variant="primary" size="sm" onClick={() => go(n)}>Finish</Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" onClick={skip}>Skip tour</Button>
              <span className="spacer" />
              {lost && <Button size="sm" onClick={showAgain}>Show me</Button>}
              <Button size="sm" icon={ArrowLeft} disabled={step === 0} onClick={() => go(step - 1)}>Back</Button>
              <Button ref={next} variant="primary" size="sm" iconRight={ArrowRight} onClick={() => go(step + 1)}>Next</Button>
            </>
          )}
        </div>
      </div>
    </>
  )
}
