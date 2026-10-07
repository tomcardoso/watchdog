// A model chooser built from `settings.models`: grouped by provider, with price per million tokens
// and context window, a search box, and a free-text entry for any `backend:model` value. Used by
// Settings (rich) and by the maintenance cards in Activity (compact).

import { Check, ChevronDown, Search } from 'lucide-react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ModelChoice } from '@shared/api'
import { cx } from '@renderer/components/ui'
import './modelpicker.css'

const PROVIDER_NAMES: Record<string, string> = {
  anthropic: 'Claude (Anthropic)',
  openai: 'OpenAI',
  deepseek: 'DeepSeek',
  gemini: 'Google Gemini',
  openrouter: 'OpenRouter',
  local: 'Local model'
}
export const providerName = (p: string) => PROVIDER_NAMES[p] ?? p

function price(n: number | null): string {
  if (n === null || n === undefined) return '—'
  return `$${n >= 10 ? n.toFixed(0) : n.toFixed(2).replace(/\.?0+$/, '')}`
}
function ctx(n: number | null): string {
  if (!n) return ''
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M context` : `${Math.round(n / 1000)}K context`
}

export function modelLabel(value: string, models: ModelChoice[]): string | null {
  return models.find((m) => m.value === value)?.label ?? null
}

interface Props {
  value: string
  onChange: (v: string) => void
  models: ModelChoice[]
  /** Text shown when the value is empty (the setting falls back to a default). */
  emptyLabel?: string
  compact?: boolean
  disabled?: boolean
}

export function ModelPicker({ value, onChange, models, emptyLabel = 'Use the default', compact, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const pop = useRef<HTMLDivElement>(null)

  useLayoutEffect(() => {
    if (!open || !btn.current) return
    const r = btn.current.getBoundingClientRect()
    const width = Math.max(r.width, 380)
    const left = Math.min(r.left, window.innerWidth - width - 12)
    const below = window.innerHeight - r.bottom
    const top = below < 360 && r.top > below ? Math.max(12, r.top - 372) : r.bottom + 6
    setPos({ left: Math.max(12, left), top, width })
  }, [open])

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (!pop.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', esc)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [open])

  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase()
    const out = new Map<string, ModelChoice[]>()
    for (const m of models) {
      if (needle && !`${m.label} ${m.value} ${m.provider}`.toLowerCase().includes(needle)) continue
      out.set(m.provider, [...(out.get(m.provider) ?? []), m])
    }
    return [...out.entries()]
  }, [models, q])

  const label = value ? modelLabel(value, models) : null
  const typed = q.trim()
  const canUseTyped = typed && !models.some((m) => m.value === typed)

  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
    setQ('')
  }

  return (
    <>
      <button ref={btn} type="button" className={cx('mp-trigger', compact && 'compact')} disabled={disabled} onClick={() => setOpen((o) => !o)} aria-haspopup="listbox" aria-expanded={open}>
        <span className="mp-trigger-text">
          {value ? (
            <>
              <span>{label ?? value}</span>
              {label && label !== value && <span className="mp-code">{value}</span>}
            </>
          ) : (
            <span className="faint">{emptyLabel}</span>
          )}
        </span>
        <ChevronDown />
      </button>
      {open &&
        pos &&
        createPortal(
          <div ref={pop} className="mp-pop" style={{ left: pos.left, top: pos.top, width: pos.width }} role="listbox">
            <div className="mp-search">
              <Search />
              <input
                autoFocus
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search models, or type backend:model"
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && canUseTyped) pick(typed)
                }}
              />
            </div>
            <div className="mp-list">
              <button type="button" className={cx('mp-row', !value && 'on')} onClick={() => pick('')}>
                <span className="mp-row-main">{emptyLabel}</span>
                {!value && <Check />}
              </button>
              {groups.map(([provider, rows]) => (
                <div key={provider}>
                  <div className="mp-group">{providerName(provider)}</div>
                  {rows.map((m) => (
                    <button type="button" key={m.value} className={cx('mp-row', m.value === value && 'on')} onClick={() => pick(m.value)}>
                      <span className="mp-row-main">
                        <span className="mp-name">{m.label}</span>
                        <span className="mp-code">{m.value}</span>
                      </span>
                      <span className="mp-meta tnum">
                        {m.input_per_mtok !== null ? `${price(m.input_per_mtok)} in · ${price(m.output_per_mtok)} out per Mtok` : 'Price not listed'}
                        {m.context_window ? ` · ${ctx(m.context_window)}` : ''}
                      </span>
                      {m.value === value && <Check />}
                    </button>
                  ))}
                </div>
              ))}
              {!groups.length && !canUseTyped && <div className="mp-empty">No model matches.</div>}
              {canUseTyped && (
                <button type="button" className="mp-row custom" onClick={() => pick(typed)}>
                  <span className="mp-row-main">
                    Use <span className="mp-code">{typed}</span> as typed
                  </span>
                </button>
              )}
            </div>
            <div className="mp-foot">Any model works as <span className="mp-code">backend:model</span>, for example <span className="mp-code">openrouter:vendor/model</span>.</div>
          </div>,
          document.body
        )}
    </>
  )
}
