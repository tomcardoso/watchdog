// Edit details (D381): a document's source, the date it was obtained, the reporter's notes and its
// record skill, the fields of its `.yml` sidecar, set on a file waiting to be added or on a
// document afterwards. Every field is optional. The engine writes the sidecar and every copy of it
// Watchdog keeps (`set-details`); nothing is billed, except that a new record skill on a document
// already extracted means extracting it again, which goes through Retry extraction's estimate and
// acknowledgement and is saved only once the reporter agrees there.

import { PencilLine } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Callout, Field, Modal, Skeleton } from '@renderer/components/ui'
import { useEngineGate } from '@renderer/lib/engine'
import { runAction } from '@renderer/lib/jobs'
import { errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { toast, useVault } from '@renderer/lib/store'
import type { DetailKey, DetailValues } from '@shared/api'
import { RetryExtractionModal } from './RetryExtraction'

/** The form's fields, in order. A new sidecar field is added here and to the engine's
 * `sidecar.EDITABLE_KEYS`. */
const FIELDS: { key: DetailKey; label: string; hint: string; kind: 'text' | 'date' | 'textarea' | 'skill' }[] = [
  { key: 'source', label: 'Source', hint: 'Where the document came from: a records request, a court registry, a person.', kind: 'text' },
  { key: 'obtained', label: 'Date obtained', hint: 'When you got it.', kind: 'date' },
  { key: 'notes', label: 'Notes', hint: 'Context the extraction model reads with the document, such as what it is part of.', kind: 'textarea' },
  { key: 'skill', label: 'Record skill', hint: 'The kind of record this is, which decides what is looked for. Left on detect, Watchdog chooses.', kind: 'skill' }
]

const MAX = 2000
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const bare = (skill: string) => skill.replace(/\.md$/, '')

export type DetailsTarget = { sha: string } | { path: string }

export function EditDetailsButton({ target, label, small }: { target: DetailsTarget; label: string; small?: boolean }) {
  const [open, setOpen] = useState(false)
  const engine = useEngineGate()
  return (
    <>
      {small
        ? <Button size="sm" variant="ghost" icon={PencilLine} tip="Edit details" aria-label={`Edit the details of ${label}`} disabled={!engine.ready} onClick={() => setOpen(true)} />
        : <Button size="sm" icon={PencilLine} disabled={!engine.ready} onClick={() => setOpen(true)}>Edit details</Button>}
      {open && <EditDetailsModal onClose={() => setOpen(false)} target={target} label={label} />}
    </>
  )
}

function EditDetailsModal({ onClose, target, label }: { onClose: () => void; target: DetailsTarget; label: string }) {
  const vault = useVault()
  const info = useRpc('vault.details', { vault, ...target }, { staleTime: 0 })
  const { data: skills } = useRpc('skills.list', {})
  const [form, setForm] = useState<Record<DetailKey, string> | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // A new record skill waiting on Retry extraction's consent.
  const [retrySkill, setRetrySkill] = useState<string | null>(null)
  const d = info.data

  useEffect(() => {
    if (d && !form) setForm({ ...d.values, skill: bare(d.values.skill) })
  }, [d, form])

  const sha = 'sha' in target ? target.sha : null
  const skillLocked = !!d && d.skill_costs && !d.reextract

  const save = async () => {
    if (!d || !form) return
    const changed: DetailValues = {}
    for (const f of FIELDS) {
      const now = form[f.key].trim()
      const was = f.key === 'skill' ? bare(d.values.skill) : d.values[f.key]
      if (now !== was) changed[f.key] = now
    }
    const { skill, ...rest } = changed
    const reextract = skill !== undefined && d.skill_costs && !!sha
    const values = reextract ? rest : changed
    setBusy(true)
    setError(null)
    try {
      if (Object.keys(values).length) {
        await runAction('set-details', { values, ...target })
        invalidate('vault.')
      }
      if (reextract) {
        setRetrySkill(skill ?? '')
        return
      }
      if (Object.keys(values).length) toast({ kind: 'success', title: 'Details saved', body: label })
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  if (retrySkill !== null && sha) {
    return (
      <RetryExtractionModal
        open
        onClose={onClose}
        shas={[sha]}
        label={label}
        intro={`The new record skill (${retrySkill || 'detected for this document'}) takes effect only when the document is extracted again. This sends its text to the extraction model again, with the models and effort in Settings, then runs the finishing step. The earlier facts stay until the new extraction has been checked and saved. Cancel keeps the earlier skill; any other changes are already saved.`}
        before={async () => {
          await runAction('set-details', { values: { skill: retrySkill }, sha })
          invalidate('vault.')
        }}
      />
    )
  }

  const set = (k: DetailKey, v: string) => setForm((f) => (f ? { ...f, [k]: v } : f))
  const input = (f: (typeof FIELDS)[number]) => {
    const value = form?.[f.key] ?? ''
    if (f.kind === 'textarea') {
      return <textarea className="textarea" rows={4} maxLength={MAX} value={value} onChange={(e) => set(f.key, e.target.value)} />
    }
    if (f.kind === 'skill') {
      const names = (skills?.skills ?? []).map((s) => s.name)
      return (
        <select className="select" value={value} disabled={skillLocked} onChange={(e) => set(f.key, e.target.value)}>
          <option value="">Detect for this document</option>
          {value && !names.includes(value) && <option value={value}>{value}</option>}
          {names.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      )
    }
    // A date picker, unless a hand-written sidecar holds a date it cannot show ("June 2024").
    const date = f.kind === 'date' && (!value || ISO_DATE.test(value))
    return <input className="input" type={date ? 'date' : 'text'} maxLength={MAX} value={value} onChange={(e) => set(f.key, e.target.value)} />
  }
  const hint = (f: (typeof FIELDS)[number]) => {
    if (f.kind !== 'skill' || !d?.skill_costs) return f.hint
    return skillLocked
      ? 'Changing it means extracting the document again, and its text is no longer kept for that.'
      : `${f.hint} Changing it means extracting the document again, which is billed: you see the estimate before anything is sent.`
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={
        <span className="row" style={{ gap: 8 }}>
          <PencilLine style={{ width: 18, height: 18, color: 'var(--accent)' }} />
          Edit details
        </span>
      }
      sub={label}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!form} onClick={() => void save()}>Save</Button>
        </>
      }
    >
      <div className="col" style={{ gap: 14 }}>
        <p className="muted" style={{ margin: 0 }}>
          Every field is optional. {d?.stage === 'added' ? 'A new source or date shows in the document’s note at once.' : 'They are read with the document when it is added.'}
        </p>
        {info.isError && <Callout tone="danger" title="Could not read the details">{errorMessage(info.error)}</Callout>}
        {!form && !info.isError && <Skeleton w="60%" />}
        {form && FIELDS.map((f) => <Field key={f.key} label={f.label} hint={hint(f)}>{input(f)}</Field>)}
        {error && <Callout tone="danger" title="Could not save the details">{error}</Callout>}
      </div>
    </Modal>
  )
}
