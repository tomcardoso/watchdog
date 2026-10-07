// New investigation and Fetch links.

import { FileText, FolderOpen, Link2 } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Button, Callout, Field, Modal } from '@renderer/components/ui'
import { runAction, startJob } from '@renderer/lib/jobs'
import { call, errorMessage, invalidate, useRpc } from '@renderer/lib/rpc'
import { basename, plural } from '@renderer/lib/format'
import { toast, useApp } from '@renderer/lib/store'

function useCommand(name: string, onOpen: () => void): [boolean, (b: boolean) => void] {
  const [open, setOpen] = useState(false)
  useEffect(() => {
    const on = (e: Event) => {
      if ((e as CustomEvent<string>).detail === name) {
        onOpen()
        setOpen(true)
      }
    }
    window.addEventListener('wd:command', on)
    return () => window.removeEventListener('wd:command', on)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name])
  return [open, setOpen]
}

export function NewInvestigationDialog() {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [dir, setDir] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const { data: info } = useRpc('app.info', {}, { staleTime: Infinity })
  const [open, setOpen] = useCommand('new-investigation', () => {
    setName('')
    setDesc('')
    setError('')
    setDir('')
  })
  const parent = dir || info?.projects_dir || ''

  const create = async () => {
    setBusy(true)
    setError('')
    try {
      if (parent && !(await window.watchdog.access.request(parent, 'new investigations', 'Watchdog creates each new investigation as a folder here.'))) {
        setError('Watchdog needs your permission to create the investigation in that folder.')
        return
      }
      const args = ['new', name.trim(), ...(desc.trim() ? ['--description', desc.trim()] : []), ...(parent ? ['--dir', parent] : [])]
      await runAction(args)
      const list = await call('projects.list', {})
      invalidate('projects.')
      const made = list.filter((p) => p.name === name.trim()).sort((a, b) => (b.created ?? '').localeCompare(a.created ?? ''))[0]
      setOpen(false)
      if (made) useApp.getState().setProject(made)
      else toast({ kind: 'success', title: 'Investigation created', body: name.trim() })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      title="New investigation"
      sub="Creates a folder on this computer with incoming for documents to add and context for background material."
      footer={
        <>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" disabled={!name.trim()} loading={busy} onClick={() => void create()}>Create investigation</Button>
        </>
      }
    >
      <div className="col gap-16">
        <Field label="Name">
          <input className="input" autoFocus value={name} placeholder="Harbour Authority contracts" onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && name.trim() && void create()} />
        </Field>
        <Field label="One-line description" hint="Optional. You can change it later from the Overview.">
          <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </Field>
        <Field label="Where to keep it">
          <div className="row">
            <span className="mono truncate grow dlg-path" title={parent}>{parent || 'Default folder'}</span>
            <Button icon={FolderOpen} onClick={() => void window.watchdog.dialog.openFolder({ title: 'Choose the parent folder', grant: 'new investigations' }).then((d) => d && setDir(d))}>Choose…</Button>
          </div>
        </Field>
        {error && <Callout tone="danger">{error}</Callout>}
      </div>
    </Modal>
  )
}

export function FetchLinksDialog() {
  const [text, setText] = useState('')
  const [file, setFile] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useCommand('fetch-links', () => {
    setText('')
    setFile('')
    setError('')
  })
  const urls = text.split(/\s+/).map((u) => u.trim()).filter(Boolean)
  const bad = urls.filter((u) => !/^https?:\/\//i.test(u))
  const ready = file ? true : urls.length > 0 && bad.length === 0

  const go = async () => {
    setBusy(true)
    setError('')
    try {
      const targets = file ? [file] : urls
      await startJob(['research', 'fetch', ...targets], file ? 'Downloading links' : `Downloading ${plural(urls.length, 'link')}`, 'fetch')
      setOpen(false)
      toast({ kind: 'info', title: 'Downloading links', body: 'Progress is in the corner. Downloaded files wait in incoming until you add them.' })
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onClose={() => setOpen(false)}
      title="Fetch web links"
      sub="Each link is downloaded into incoming with a small file recording where it came from and when. Downloaded pages still need to be added."
      footer={
        <>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" icon={Link2} disabled={!ready} loading={busy} onClick={() => void go()}>
            {file ? 'Download links from file' : urls.length ? `Download ${plural(urls.length, 'link')}` : 'Download'}
          </Button>
        </>
      }
    >
      <div className="col gap-12">
        <Field label="Links" hint="One per line." error={!file && bad.length ? `Not a web address: ${bad[0]}` : undefined}>
          <textarea className="textarea" rows={7} autoFocus disabled={!!file} value={text} placeholder="https://example.org/report.pdf" onChange={(e) => setText(e.target.value)} />
        </Field>
        <div className="row">
          <Button icon={FileText} onClick={() => void window.watchdog.dialog.openFiles({ title: 'Choose a links file', multi: false }).then((p) => p[0] && setFile(p[0]))}>Use a links file…</Button>
          {file && (
            <>
              <span className="mono truncate grow" title={file}>{basename(file)}</span>
              <Button variant="ghost" size="sm" onClick={() => setFile('')}>Clear</Button>
            </>
          )}
        </div>
        {error && <Callout tone="danger">{error}</Callout>}
      </div>
    </Modal>
  )
}
