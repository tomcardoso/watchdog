// One tooltip for the whole app. Any element with `data-tip` (Button's `tip`, Badge, Segmented…)
// shows it on hover or keyboard focus. It is drawn in a fixed layer above everything, placed against
// the window rather than inside its own panel, so a panel that hides its overflow can't cut it off:
// it sits above the element (or where `data-tip-pos` asks), flips to the other side when there is no
// room, and is kept inside the window's edges.

import { CSSProperties, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

type Pos = 'top' | 'bottom' | 'right' | 'bottom-end'
interface Tip { el: HTMLElement; text: string; pos: Pos }

const GAP = 6
const EDGE = 8
const DELAY = 300

export function placeTip(anchor: DOMRect, tip: { width: number; height: number }, pos: Pos, view: { width: number; height: number }): { left: number; top: number } {
  const clampX = (x: number) => Math.min(Math.max(x, EDGE), Math.max(EDGE, view.width - tip.width - EDGE))
  const clampY = (y: number) => Math.min(Math.max(y, EDGE), Math.max(EDGE, view.height - tip.height - EDGE))
  if (pos === 'right') {
    const left = anchor.right + GAP + 2
    if (left + tip.width <= view.width - EDGE) return { left, top: clampY(anchor.top + anchor.height / 2 - tip.height / 2) }
    pos = 'top' // no room to the right: fall back to above or below
  }
  const above = anchor.top - GAP - tip.height
  const below = anchor.bottom + GAP
  const wantBelow = pos === 'bottom' || pos === 'bottom-end'
  const fitsAbove = above >= EDGE
  const fitsBelow = below + tip.height <= view.height - EDGE
  const top = wantBelow ? (fitsBelow || !fitsAbove ? below : above) : fitsAbove || !fitsBelow ? above : below
  const left = pos === 'bottom-end' ? anchor.right - tip.width : anchor.left + anchor.width / 2 - tip.width / 2
  return { left: clampX(left), top: clampY(top) }
}

function tipFor(target: EventTarget | null): Tip | null {
  const el = target instanceof Element ? (target.closest('[data-tip]') as HTMLElement | null) : null
  const text = el?.getAttribute('data-tip')?.trim()
  if (!el || !text) return null
  const p = el.getAttribute('data-tip-pos')
  return { el, text, pos: p === 'bottom' || p === 'right' || p === 'bottom-end' ? p : 'top' }
}

export function TooltipLayer() {
  const [tip, setTip] = useState<Tip | null>(null)
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden' })
  const box = useRef<HTMLDivElement>(null)
  const timer = useRef<number | undefined>(undefined)

  // The element whose tooltip is showing or about to.
  const active = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const show = (t: Tip | null) => {
      if ((t?.el ?? null) === active.current) return
      active.current = t?.el ?? null
      window.clearTimeout(timer.current)
      setTip(null)
      if (t) timer.current = window.setTimeout(() => setTip(t), DELAY)
    }
    const over = (e: PointerEvent) => show(tipFor(e.target))
    const focus = (e: FocusEvent) => {
      const t = tipFor(e.target)
      if (t && (e.target as HTMLElement).matches?.(':focus-visible')) show(t)
    }
    const hide = () => {
      active.current = null
      window.clearTimeout(timer.current)
      setTip(null)
    }
    document.addEventListener('pointerover', over)
    document.addEventListener('focusin', focus)
    document.addEventListener('focusout', hide)
    document.addEventListener('pointerdown', hide, true)
    document.addEventListener('keydown', hide, true)
    window.addEventListener('scroll', hide, true)
    window.addEventListener('blur', hide)
    return () => {
      window.clearTimeout(timer.current)
      document.removeEventListener('pointerover', over)
      document.removeEventListener('focusin', focus)
      document.removeEventListener('focusout', hide)
      document.removeEventListener('pointerdown', hide, true)
      document.removeEventListener('keydown', hide, true)
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('blur', hide)
    }
  }, [])

  // The element can disappear (a dialog closes) or change its tip while the tooltip shows.
  useEffect(() => {
    if (!tip) return
    const check = window.setInterval(() => {
      if (!tip.el.isConnected || tip.el.getAttribute('data-tip')?.trim() !== tip.text) {
        active.current = null
        setTip(null)
      }
    }, 250)
    return () => window.clearInterval(check)
  }, [tip])

  useLayoutEffect(() => {
    if (!tip || !box.current) return setStyle({ visibility: 'hidden' })
    const b = box.current.getBoundingClientRect()
    const { left, top } = placeTip(tip.el.getBoundingClientRect(), b, tip.pos, { width: window.innerWidth, height: window.innerHeight })
    setStyle({ left, top })
  }, [tip])

  if (!tip) return null
  return createPortal(
    <div ref={box} role="tooltip" className={tip.pos === 'bottom-end' || tip.text.length > 60 ? 'tooltip tooltip-wrap' : 'tooltip'} style={style}>
      {tip.text}
    </div>,
    document.body
  )
}
