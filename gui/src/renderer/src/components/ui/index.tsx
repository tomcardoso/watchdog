// The shared component kit. Styles live in styles/ui.css.

import { AlertTriangle, CheckCircle2, Info, LucideIcon, X, XCircle } from 'lucide-react'
import {
  ButtonHTMLAttributes,
  CSSProperties,
  ReactNode,
  forwardRef,
  useEffect,
  useLayoutEffect,
  useRef,
  useState
} from 'react'
import { createPortal } from 'react-dom'

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(' ')
export { cx }

// ── Button ───────────────────────────────────────────────────────────────────
type BtnVariant = 'default' | 'primary' | 'ghost' | 'danger' | 'soft'
interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: BtnVariant
  size?: 'sm' | 'md' | 'lg'
  icon?: LucideIcon
  iconRight?: LucideIcon
  loading?: boolean
  tip?: string
}
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'default', size = 'md', icon: Icon, iconRight: IconRight, loading, tip, className, children, disabled, ...rest },
  ref
) {
  const iconOnly = !children && (Icon || loading)
  return (
    <button
      ref={ref}
      type="button"
      className={cx(
        'btn',
        variant !== 'default' && `btn-${variant}`,
        size !== 'md' && `btn-${size}`,
        iconOnly && 'btn-icon',
        className
      )}
      disabled={disabled || loading}
      data-tip={tip}
      aria-label={tip}
      {...rest}
    >
      {loading ? <span className="spinner" style={{ width: 13, height: 13 }} /> : Icon ? <Icon /> : null}
      {children}
      {IconRight && <IconRight />}
    </button>
  )
})

// ── Inputs ───────────────────────────────────────────────────────────────────
export function Field({ label, hint, error, children, style }: { label?: ReactNode; hint?: ReactNode; error?: ReactNode; children: ReactNode; style?: CSSProperties }) {
  return (
    <label className="field" style={style}>
      {label && <span className="field-label">{label}</span>}
      {children}
      {error ? <span className="field-error">{error}</span> : hint ? <span className="field-hint">{hint}</span> : null}
    </label>
  )
}

export function Switch({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="switch"
      disabled={disabled}
      onClick={(e) => {
        e.preventDefault()
        onChange(!checked)
      }}
    />
  )
}

export function Segmented<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { value: T; label?: ReactNode; icon?: LucideIcon; tip?: string }[] }) {
  return (
    <div className="segmented" role="group">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)} data-tip={o.tip} data-tip-pos="bottom">
          {o.icon && <o.icon />}
          {o.label}
        </button>
      ))}
    </div>
  )
}

// ── Tabs ─────────────────────────────────────────────────────────────────────
export function Tabs<T extends string>({ value, onChange, tabs, style }: { value: T; onChange: (v: T) => void; tabs: { value: T; label: ReactNode; icon?: LucideIcon; count?: number }[]; style?: CSSProperties }) {
  return (
    <div className="tabs" role="tablist" style={style}>
      {tabs.map((t) => (
        <button key={t.value} role="tab" className="tab" aria-selected={t.value === value} onClick={() => onChange(t.value)}>
          {t.icon && <t.icon />}
          {t.label}
          {t.count !== undefined && t.count > 0 && <span className={cx('count-pill', t.value !== value && 'quiet')}>{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

// ── Cards ────────────────────────────────────────────────────────────────────
export function Card({ title, sub, actions, children, className, onClick, style, pad = true }: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children?: ReactNode; className?: string; onClick?: () => void; style?: CSSProperties; pad?: boolean }) {
  return (
    <div className={cx('card', onClick && 'interactive', className)} onClick={onClick} style={style}>
      {(title || actions) && (
        <div className="card-header">
          <div className="grow">
            {title && <div className="card-title">{title}</div>}
            {sub && <div className="card-sub">{sub}</div>}
          </div>
          {actions}
        </div>
      )}
      {children !== undefined && <div className={title || actions ? 'card-body' : pad ? 'card-pad' : undefined}>{children}</div>}
    </div>
  )
}

export function Badge({ children, tone, icon: Icon, tip }: { children: ReactNode; tone?: 'accent' | 'success' | 'warning' | 'danger' | 'info'; icon?: LucideIcon; tip?: string }) {
  return (
    <span className={cx('badge', tone && `badge-${tone}`)} data-tip={tip}>
      {Icon && <Icon />}
      {children}
    </span>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

// ── Feedback ─────────────────────────────────────────────────────────────────
export function Spinner({ size }: { size?: 'lg' }) {
  return <span className={cx('spinner', size)} role="status" aria-label="Loading" />
}

export function Progress({ value, max = 1, indeterminate, style }: { value?: number | null; max?: number; indeterminate?: boolean; style?: CSSProperties }) {
  const pct = !indeterminate && value !== null && value !== undefined && max > 0 ? Math.min(100, (value / max) * 100) : 0
  return (
    <div className={cx('progress', (indeterminate || value === null || value === undefined) && 'indeterminate')} style={style}>
      <div style={{ width: `${pct}%` }} />
    </div>
  )
}

export function Skeleton({ w, h = 14, style }: { w?: number | string; h?: number | string; style?: CSSProperties }) {
  return <div className="skeleton" style={{ width: w ?? '100%', height: h, ...style }} />
}

export function Empty({ icon: Icon, title, children, action }: { icon?: LucideIcon; title: ReactNode; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {Icon && (
        <div className="empty-icon">
          <Icon />
        </div>
      )}
      <div className="empty-title">{title}</div>
      {children && <div className="empty-body">{children}</div>}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  )
}

const CALLOUT_ICON = { info: Info, warning: AlertTriangle, danger: XCircle, success: CheckCircle2 }
export function Callout({ tone = 'info', title, children, style, action }: { tone?: 'info' | 'warning' | 'danger' | 'success'; title?: ReactNode; children?: ReactNode; style?: CSSProperties; action?: ReactNode }) {
  const Icon = CALLOUT_ICON[tone]
  return (
    <div className={cx('callout', tone)} style={style}>
      <Icon />
      <div className="grow">
        {title && <div style={{ fontWeight: 620, marginBottom: children ? 2 : 0 }}>{title}</div>}
        {children && <div className="muted" style={{ color: 'var(--text-2)' }}>{children}</div>}
      </div>
      {action}
    </div>
  )
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error)
  return (
    <Callout tone="danger" title="Something went wrong" action={retry && <Button size="sm" onClick={retry}>Try again</Button>}>
      <span className="selectable">{msg}</span>
    </Callout>
  )
}

// ── Modal ────────────────────────────────────────────────────────────────────
export function Modal({ open, onClose, title, sub, children, footer, width, dismissable = true }: { open: boolean; onClose: () => void; title?: ReactNode; sub?: ReactNode; children?: ReactNode; footer?: ReactNode; width?: 'wide' | 'xwide'; dismissable?: boolean }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && dismissable) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose, dismissable])
  if (!open) return null
  return createPortal(
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && dismissable && onClose()}>
      <div className={cx('modal', width)} role="dialog" aria-modal="true">
        {title && (
          <div className="modal-header">
            <div className="grow">
              <div className="modal-title">{title}</div>
              {sub && <div className="modal-sub">{sub}</div>}
            </div>
            {dismissable && <Button variant="ghost" size="sm" icon={X} tip="Close" onClick={onClose} />}
          </div>
        )}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>,
    document.body
  )
}

// ── Dropdown menu ────────────────────────────────────────────────────────────
export interface MenuItem {
  label: ReactNode
  icon?: LucideIcon
  onClick?: () => void
  danger?: boolean
  separator?: boolean
  disabled?: boolean
}
export function Dropdown({ trigger, items, align = 'left' }: { trigger: (open: () => void) => ReactNode; items: MenuItem[]; align?: 'left' | 'right' }) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null)
  const anchor = useRef<HTMLSpanElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const open = () => {
    const r = anchor.current?.getBoundingClientRect()
    if (r) setPos({ x: align === 'right' ? r.right : r.left, y: r.bottom + 4 })
  }
  useLayoutEffect(() => {
    if (!pos || !menu.current) return
    const m = menu.current.getBoundingClientRect()
    let { x, y } = pos
    if (align === 'right') x -= m.width
    if (x + m.width > window.innerWidth - 8) x = window.innerWidth - m.width - 8
    if (y + m.height > window.innerHeight - 8) y = Math.max(8, y - m.height - 40)
    menu.current.style.left = `${Math.max(8, x)}px`
    menu.current.style.top = `${y}px`
  }, [pos, align])
  useEffect(() => {
    if (!pos) return
    const close = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node)) setPos(null)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setPos(null)
    // Added after this click has finished; cleared with the rest, so a quick re-render cannot
    // leave a listener behind.
    const t = setTimeout(() => window.addEventListener('mousedown', close))
    window.addEventListener('keydown', esc)
    return () => {
      clearTimeout(t)
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', esc)
    }
  }, [pos])
  return (
    <>
      <span ref={anchor} style={{ display: 'inline-flex' }}>
        {trigger(open)}
      </span>
      {pos &&
        createPortal(
          <div ref={menu} className="menu" style={{ left: -9999, top: -9999 }}>
            {items.map((it, i) =>
              it.separator ? (
                <div key={i} className="menu-sep" />
              ) : (
                <button
                  key={i}
                  className={cx('menu-item', it.danger && 'danger')}
                  disabled={it.disabled}
                  style={it.disabled ? { opacity: 0.45, pointerEvents: 'none' } : undefined}
                  onClick={() => {
                    setPos(null)
                    it.onClick?.()
                  }}
                >
                  {it.icon && <it.icon />}
                  {it.label}
                </button>
              )
            )}
          </div>,
          document.body
        )}
    </>
  )
}

// ── Stat ─────────────────────────────────────────────────────────────────────
export function Stat({ label, value, sub, icon: Icon, tone, onClick }: { label: ReactNode; value: ReactNode; sub?: ReactNode; icon?: LucideIcon; tone?: string; onClick?: () => void }) {
  return (
    <div className={cx('stat', onClick && 'interactive')} onClick={onClick} style={tone ? ({ '--stat-color': tone } as CSSProperties) : undefined}>
      <div className="stat-label">
        {Icon && <Icon />}
        {label}
      </div>
      <div className="stat-value tnum">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

/** Debounced value for search boxes. */
export function useDebounced<T>(value: T, ms = 200): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}
