// Record a verified contradiction on an entity: `watchdog review add-contradiction <id> …`.

import { useEffect, useMemo, useState } from 'react'
import { Button, Callout, Field, Modal } from '@renderer/components/ui'
import type { DocumentRow } from '@shared/api'
import { runAction } from '@renderer/lib/jobs'
import { errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { toast, useVault } from '@renderer/lib/store'

interface Side {
  value: string
  doc: string
  page: string
}
const blank = (): Side => ({ value: '', doc: '', page: '' })
const slugOf = (d: DocumentRow) => (d.note ?? '').replace(/\.md$/, '').split('/').pop() ?? ''

export default function ContradictionModal({ open, onClose, entityId, entityName, entityDocs }: { open: boolean; onClose: () => void; entityId: string; entityName: string; entityDocs: DocumentRow[] }) {
  const vault = useVault()
  const all = useRpc('vault.documents', open ? { vault } : null)
  const [label, setLabel] = useState('')
  const [a, setA] = useState<Side>(blank())
  const [b, setB] = useState<Side>(blank())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setLabel('')
    setA(blank())
    setB(blank())
    setError('')
  }, [open])

  const mine = useMemo(() => new Set(entityDocs.map((d) => d.sha)), [entityDocs])
  const others = useMemo(() => (all.data ?? []).filter((d) => !mine.has(d.sha) && slugOf(d)), [all.data, mine])
  const valid = label.trim() && a.value.trim() && a.doc && b.value.trim() && b.doc && [a, b].every((s) => !s.page || /^\d+$/.test(s.page))

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const args = ['review', 'add-contradiction', entityId, '--label', label.trim(), '--a', a.value.trim(), '--a-doc', a.doc, '--b', b.value.trim(), '--b-doc', b.doc]
      if (a.page) args.push('--a-page', a.page)
      if (b.page) args.push('--b-page', b.page)
      const out = await runAction(args)
      invalidate('vault.', 'review.')
      toast({ kind: /already present/i.test(out) ? 'info' : 'success', title: /already present/i.test(out) ? 'Already recorded' : 'Contradiction recorded', body: `On ${entityName}.` })
      onClose()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const docSelect = (side: Side, set: (s: Side) => void) => (
    <select className="select" value={side.doc} onChange={(e) => set({ ...side, doc: e.target.value })}>
      <option value="">Choose a document…</option>
      {entityDocs.length > 0 && (
        <optgroup label={`Documents mentioning ${entityName}`}>
          {entityDocs.filter((d) => slugOf(d)).map((d) => (
            <option key={d.sha} value={slugOf(d)}>
              {d.title || d.filename}
            </option>
          ))}
        </optgroup>
      )}
      {others.length > 0 && (
        <optgroup label="Other documents">
          {others.map((d) => (
            <option key={d.sha} value={slugOf(d)}>
              {d.title || d.filename}
            </option>
          ))}
        </optgroup>
      )}
    </select>
  )

  const sideForm = (name: string, side: Side, set: (s: Side) => void, ph: string) => (
    <div className="ent-form-side">
      <div className="eyebrow">{name}</div>
      <Field label="What it says">
        <input className="input" value={side.value} placeholder={ph} onChange={(e) => set({ ...side, value: e.target.value })} />
      </Field>
      <Field label="Source document">{docSelect(side, set)}</Field>
      <Field label="Page (optional)">
        <input className="input" inputMode="numeric" value={side.page} placeholder="e.g. 4" onChange={(e) => set({ ...side, page: e.target.value.replace(/[^\d]/g, '') })} style={{ maxWidth: 110 }} />
      </Field>
    </div>
  )

  return (
    <Modal
      open={open}
      onClose={onClose}
      width="wide"
      title="Record a contradiction"
      sub={`On ${entityName}. Use this once you have checked a disputed fact against both sources yourself.`}
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" loading={busy} disabled={!valid} onClick={() => void submit()}>
            Add to {entityName.length > 24 ? 'entity' : entityName}
          </Button>
        </>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <Callout tone="info">
          This writes a contradiction into the entity’s Contradictions section in the same format the pipeline uses, so it can be marked handled like any other. No model is called.
        </Callout>
        <Field label="Disputed fact" hint="A short label, for example “Date of incorporation”.">
          <input className="input" value={label} onChange={(e) => setLabel(e.target.value)} autoFocus />
        </Field>
        <div className="ent-form-grid">
          {sideForm('First value', a, setA, 'e.g. March 3, 2019')}
          {sideForm('Conflicting value', b, setB, 'e.g. March 9, 2019')}
        </div>
        {error && <Callout tone="danger" title="Could not record it">{error}</Callout>}
      </div>
    </Modal>
  )
}
