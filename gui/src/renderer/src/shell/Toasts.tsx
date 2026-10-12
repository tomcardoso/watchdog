import { useLayoutEffect, useRef } from 'react'
import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { Button } from '@renderer/components/ui'
import { useApp } from '@renderer/lib/store'

const ICON = { success: CheckCircle2, error: XCircle, info: Info }

/** The stack sits bottom-right, where a dialog keeps its buttons. While a dialog is open it moves
 *  to the top centre (ui.css), and its height is published as `--toasts-h` so the dialog's scrim
 *  makes room for it: a toast never covers any part of a dialog, its actions least of all. */
export function Toasts() {
  const toasts = useApp((s) => s.toasts)
  const dismiss = useApp((s) => s.dismissToast)
  const ref = useRef<HTMLDivElement>(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const root = document.documentElement
    const publish = () => root.style.setProperty('--toasts-h', `${el.offsetHeight > 0 ? el.offsetHeight + 10 : 0}px`)
    publish()
    const ro = new ResizeObserver(publish)
    ro.observe(el)
    return () => {
      ro.disconnect()
      root.style.removeProperty('--toasts-h')
    }
  }, [])
  return (
    <div className="toasts" ref={ref}>
      {toasts.map((t) => {
        const Icon = ICON[t.kind]
        return (
          <div key={t.id} className={`toast ${t.kind}`} role="status">
            <Icon />
            <div className="grow">
              <div className="toast-title">{t.title}</div>
              {t.body && <div className="toast-body selectable">{t.body}</div>}
              {t.action && (
                <Button size="sm" variant="soft" style={{ marginTop: 8 }} onClick={() => { t.action!.run(); dismiss(t.id) }}>
                  {t.action.label}
                </Button>
              )}
            </div>
            <Button variant="ghost" size="sm" icon={X} tip="Dismiss" onClick={() => dismiss(t.id)} />
          </div>
        )
      })}
    </div>
  )
}
