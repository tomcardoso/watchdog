import { CheckCircle2, Info, X, XCircle } from 'lucide-react'
import { Button } from '@renderer/components/ui'
import { useApp } from '@renderer/lib/store'

const ICON = { success: CheckCircle2, error: XCircle, info: Info }

export function Toasts() {
  const toasts = useApp((s) => s.toasts)
  const dismiss = useApp((s) => s.dismissToast)
  return (
    <div className="toasts">
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
