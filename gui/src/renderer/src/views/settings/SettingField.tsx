// One setting as a form row: label, short description, the right control for its kind, a "More"
// disclosure for the full help, and current / default with a reset.

import { Check, ChevronRight, FolderOpen, RotateCcw } from 'lucide-react'
import { useState } from 'react'
import type { ModelChoice, SettingKey } from '@shared/api'
import { Button, Switch } from '@renderer/components/ui'
import { ModelPicker } from '@renderer/components/ModelPicker'
import { call, errorMessage, invalidate } from '@renderer/lib/rpc'

export const humanize = (key: string) => {
  const s = key.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}
const asText = (v: unknown) => (v === null || v === undefined ? '' : String(v))

export function SettingField({ setting, models, efforts }: { setting: SettingKey & { is_set?: boolean }; models: ModelChoice[]; efforts: string[] }) {
  const { key, kind } = setting
  const saved = kind === 'secret' ? '' : asText(setting.current)
  const [draft, setDraft] = useState(saved)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const [more, setMore] = useState(false)

  const save = async (value: string) => {
    setBusy(true)
    setError(null)
    try {
      await call('settings.set', { key, value })
      invalidate('settings.')
      setDone(true)
      setTimeout(() => setDone(false), 1800)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const isSet = setting.is_set ?? (setting.current !== null && setting.current !== undefined && setting.current !== '')
  const defText = setting.default === null || setting.default === undefined || setting.default === '' ? 'not set' : String(setting.default)
  const dirty = draft !== saved

  const control = () => {
    switch (kind) {
      case 'bool': {
        const on = setting.current === null || setting.current === undefined ? !!setting.default : !!setting.current
        return <Switch checked={on} disabled={busy} onChange={(v) => void save(String(v))} label={humanize(key)} />
      }
      case 'choice':
      case 'effort': {
        const opts = setting.choices ?? (kind === 'effort' ? efforts : [])
        const cur = saved || asText(setting.default)
        return (
          <select className="select" value={cur} disabled={busy} onChange={(e) => void save(e.target.value)}>
            {opts.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
            {cur && !opts.includes(cur) && <option value={cur}>{cur}</option>}
          </select>
        )
      }
      case 'model':
        return <ModelPicker value={saved} models={models} emptyLabel={`Default (${defText})`} onChange={(v) => void save(v)} disabled={busy} />
      default: {
        const inputType = kind === 'secret' ? 'password' : 'text'
        return (
          <div className="set-input-row">
            <input
              className="input"
              type={inputType}
              autoComplete="off"
              value={draft}
              inputMode={kind === 'int' || kind === 'float' ? 'decimal' : undefined}
              placeholder={kind === 'secret' ? (isSet ? `${setting.display} — type to replace` : 'Not set') : `Default: ${defText}`}
              onChange={(e) => {
                setDraft(e.target.value)
                setError(null)
              }}
              onKeyDown={(e) => e.key === 'Enter' && (dirty || kind === 'secret') && draft !== '' && void save(draft)}
            />
            {kind === 'path' && (
              <Button
                icon={FolderOpen}
                tip="Choose a folder"
                onClick={async () => {
                  const d = await window.watchdog.dialog.openFolder({ title: humanize(key) })
                  if (d) {
                    setDraft(d)
                    void save(d)
                  }
                }}
              />
            )}
            {(dirty || (kind === 'secret' && draft)) && (
              <Button variant="primary" loading={busy} onClick={() => void save(draft)}>
                Save
              </Button>
            )}
          </div>
        )
      }
    }
  }

  return (
    <div className="set-field">
      <div className="set-field-text">
        <div className="set-field-label">
          {humanize(key)}
          {done && (
            <span className="set-saved">
              <Check /> Saved
            </span>
          )}
        </div>
        <div className="set-field-short">{setting.short}</div>
        {setting.help && (
          <>
            <button className="act-disclose" aria-expanded={more} onClick={() => setMore(!more)} style={{ marginTop: 2 }}>
              <ChevronRight /> More
            </button>
            {more && <div className="set-help selectable">{setting.help}</div>}
          </>
        )}
      </div>
      <div className="set-field-control">
        {control()}
        {error && <div className="field-error">{error}</div>}
        <div className="set-meta">
          <span>
            {kind === 'secret' ? (isSet ? 'A value is stored' : 'Not set') : isSet ? `Current: ${setting.display}` : `Using the default: ${defText}`}
          </span>
          {kind !== 'secret' && isSet && (
            <button className="set-reset" disabled={busy} onClick={() => void save(setting.default === null || setting.default === undefined ? '' : String(setting.default))}>
              <RotateCcw /> Reset to default{defText !== 'not set' ? ` (${defText})` : ''}
            </button>
          )}
          {kind === 'secret' && isSet && (
            <button className="set-reset" disabled={busy} onClick={() => void save('')}>
              Remove
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
