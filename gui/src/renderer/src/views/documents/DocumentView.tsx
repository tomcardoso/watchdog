// One document: the original on the left (a real PDF viewer, image, web page or extracted text),
// everything Watchdog learned from it on the right.

import { ArrowLeft, BookOpen, ExternalLink, FileText, FolderOpen, Link2, MessageSquare, Minus, Plus, Quote, Users, ListChecks, Info, StickyNote } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { Badge, Button, Callout, ErrorNote, Skeleton, Tabs } from '@renderer/components/ui'
import { fmtDate, plural } from '@renderer/lib/format'
import { useRpc } from '@renderer/lib/rpc'
import { navigate, toast, useApp, useVault } from '@renderer/lib/store'
import type { DocumentDetail } from '@shared/api'
import { DetailsTab, EntitiesTab, FactsTab, Highlight, NotesTab, SummaryTab, TextTab } from './DocPanels'
import { JumpTarget, PdfViewer } from './PdfViewer'
import './documents.css'

const BROWSER_IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'])
type TabId = 'facts' | 'summary' | 'entities' | 'text' | 'details' | 'notes'
const TAB_IDS: TabId[] = ['facts', 'summary', 'entities', 'text', 'details', 'notes']

function ImageViewer({ src }: { src: string }) {
  const [zoom, setZoom] = useState<number | null>(null) // null = fit
  const [nat, setNat] = useState<number | null>(null)
  const box = useRef<HTMLDivElement>(null)
  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <Button variant="ghost" size="sm" icon={Minus} tip="Zoom out" onClick={() => setZoom(Math.max(0.1, (zoom ?? 1) / 1.25))} />
        <span className="pdf-zoomlabel">{zoom ? Math.round(zoom * 100) + '%' : 'Fit'}</span>
        <Button variant="ghost" size="sm" icon={Plus} tip="Zoom in" onClick={() => setZoom(Math.min(8, (zoom ?? 1) * 1.25))} />
        <Button variant={zoom === null ? 'soft' : 'ghost'} size="sm" onClick={() => setZoom(null)}>Fit</Button>
        <Button variant="ghost" size="sm" onClick={() => setZoom(1)}>100%</Button>
      </div>
      <div className="img-scroll" ref={box}>
        <img
          src={src}
          alt="Original document"
          onLoad={(e) => setNat((e.target as HTMLImageElement).naturalWidth)}
          style={zoom === null || !nat ? { maxWidth: '100%', height: 'auto' } : { width: nat * zoom }}
        />
      </div>
    </div>
  )
}

function TextReader({ d, target, note, onOpen }: { d: DocumentDetail; target: JumpTarget | null; note?: string; onOpen: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [flash, setFlash] = useState<number | null>(null)
  useEffect(() => {
    if (!target) return
    const el = ref.current?.querySelector(`[data-p="${target.page}"]`)
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    setFlash(target.page)
    const t = setTimeout(() => setFlash(null), 1700)
    return () => clearTimeout(t)
  }, [target])
  return (
    <div className="pdf-viewer">
      <div className="pdf-toolbar">
        <span className="pdf-pagelabel">Extracted text · {plural(d.pages.length, 'page')}</span>
        <span className="spacer" />
        {d.original && <Button size="sm" icon={ExternalLink} onClick={onOpen}>Open original</Button>}
      </div>
      <div className="text-reader" ref={ref}>
        <div className="text-reader-note">
          <Callout tone="info">
            {note ?? `${d.ext.toUpperCase()} files can't be shown page by page here. This is the text Watchdog extracted from the original, which is what the facts were drawn from.`}
          </Callout>
        </div>
        {d.pages.length === 0 && <div className="text-reader-note faint">No extracted text is available for this document.</div>}
        {d.pages.map((p) => (
          <div key={p.page} data-p={p.page} className={'text-sheet' + (flash === p.page ? ' flash' : '')}>
            <span className="text-sheet-num">p. {p.page}</span>
            <Highlight text={p.text} q="" />
          </div>
        ))}
      </div>
    </div>
  )
}

function Original({ d, abs, target, onOpen }: { d: DocumentDetail; abs: string | null; target: JumpTarget | null; onOpen: () => void }) {
  const [pdfFailed, setPdfFailed] = useState<string | null>(null)
  useEffect(() => setPdfFailed(null), [d.sha])
  const ext = d.ext
  if (abs && ext === 'pdf' && !pdfFailed) return <PdfViewer path={abs} target={target} onFailed={setPdfFailed} />
  if (abs && BROWSER_IMAGE.has(ext)) return <ImageViewer src={window.watchdog.files.url(abs)} />
  if (abs && (ext === 'html' || ext === 'htm'))
    return (
      <div className="pdf-viewer">
        <div className="pdf-toolbar">
          <span className="pdf-pagelabel">Saved web page, shown without scripts</span>
          <span className="spacer" />
          <Button size="sm" icon={ExternalLink} onClick={onOpen}>Open original</Button>
        </div>
        <iframe className="html-frame" sandbox="" title="Original web page" src={window.watchdog.files.url(abs)} />
      </div>
    )
  return <TextReader d={d} target={target} onOpen={onOpen} note={pdfFailed ? `The PDF could not be displayed (${pdfFailed}). Showing the extracted text instead.` : undefined} />
}

function Loading() {
  return (
    <div className="docv">
      <div className="docv-head">
        <Skeleton w={160} h={12} />
        <Skeleton w="50%" h={24} />
        <Skeleton w="30%" h={12} />
      </div>
      <div className="docv-body" style={{ padding: 24, display: 'flex', gap: 24 }}>
        <Skeleton w="55%" h="100%" style={{ borderRadius: 4 }} />
        <div className="col grow" style={{ gap: 12 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} h={96} style={{ borderRadius: 13 }} />
          ))}
        </div>
      </div>
    </div>
  )
}

export default function DocumentView() {
  const vault = useVault()
  const route = useApp((s) => s.route)
  const sha = route.view === 'document' ? route.sha : ''
  const q = useRpc('vault.document', sha ? { vault, sha } : null)
  const [tab, setTab] = useState<TabId>('facts')
  const [target, setTarget] = useState<JumpTarget | null>(null)

  // Route-driven state: ?page= and ?tab= (re-applied whenever the route or document changes).
  const routePage = route.view === 'document' ? route.page : undefined
  const routeTab = route.view === 'document' ? route.tab : undefined
  useEffect(() => {
    setTarget(routePage ? { page: routePage, nonce: Math.random() } : null)
  }, [sha, routePage])
  useEffect(() => {
    setTab(TAB_IDS.includes(routeTab as TabId) ? (routeTab as TabId) : 'facts')
  }, [sha, routeTab])

  const d = q.data
  const abs = d?.original ? `${vault}/${d.original}` : null
  const title = d ? d.title || d.filename : ''
  const hasViewer = !!d && (d.ext === 'pdf' || d.pages.length > 0)

  const jump = useCallback((t: Omit<JumpTarget, 'nonce'>) => setTarget({ ...t, nonce: Math.random() }), [])

  const counts = useMemo(() => ({ facts: d?.facts.length ?? 0, entities: d?.entities.length ?? 0 }), [d])

  if (q.isLoading) return <Loading />
  if (q.isError || !d)
    return (
      <div className="page"><div className="page-inner">
        <ErrorNote error={q.error ?? new Error('Document not found')} retry={() => q.refetch()} />
        <div style={{ marginTop: 16 }}><Button icon={ArrowLeft} onClick={() => navigate({ view: 'documents' })}>Back to documents</Button></div>
      </div></div>
    )

  const copyCitation = () => {
    const bits = [`“${title}”`, d.filename + (d.date_of_document ? `, dated ${fmtDate(d.date_of_document)}` : ''), d.source ? `obtained from ${d.source}` : null, `SHA-256 ${d.sha.slice(0, 12)}`].filter(Boolean)
    void navigator.clipboard.writeText(bits.join('; ')).then(() => toast({ kind: 'success', title: 'Citation copied' }))
  }
  const openOriginal = () => {
    if (!abs) return
    void window.watchdog.shell.openPath(abs).then((err) => err && toast({ kind: 'error', title: 'Could not open the file', body: err }))
  }

  return (
    <div className="docv">
      <header className="docv-head">
        <nav className="docv-crumbs" aria-label="Breadcrumb">
          <button onClick={() => navigate({ view: 'documents' })}><ArrowLeft />Documents</button>
          <span>/</span>
          <span className="truncate" style={{ maxWidth: 420 }}>{title}</span>
        </nav>
        <div className="docv-titlerow">
          <div className="grow" style={{ minWidth: 280 }}>
            <h1 className="docv-title selectable">{title}</h1>
            <div className="docv-meta">
              {d.document_type && <Badge tone="accent">{d.document_type}</Badge>}
              {d.date_of_document && <span>{fmtDate(d.date_of_document)}</span>}
              {d.page_count ? <><span className="dot">·</span><span>{plural(d.page_count, 'page')}</span></> : null}
              {d.title && <><span className="dot">·</span><span className="mono truncate" style={{ maxWidth: 260 }}>{d.filename}</span></>}
              {d.near_duplicate_of && <Badge tone="warning" tip={d.near_duplicate_of}>possible duplicate</Badge>}
            </div>
          </div>
          <div className="docv-actions">
            <Button size="sm" icon={ExternalLink} disabled={!abs} onClick={openOriginal}>Open original</Button>
            <Button size="sm" icon={FolderOpen} disabled={!abs} onClick={() => abs && window.watchdog.shell.showItemInFolder(abs)}>Show in folder</Button>
            <Button
              size="sm"
              icon={BookOpen}
              disabled={!d.note}
              onClick={async () => {
                const ok = await window.watchdog.shell.openInObsidian(vault, d.note ?? undefined)
                if (!ok) toast({ kind: 'info', title: 'Obsidian could not be opened', body: 'Check that Obsidian is installed and this folder is open as a vault.' })
              }}
            >
              Open in Obsidian
            </Button>
            <Button size="sm" icon={MessageSquare} onClick={() => navigate({ view: 'ask', prompt: `/watchdog-query What does ${title} show?` })}>Ask Claude</Button>
            <Button size="sm" icon={Quote} onClick={copyCitation}>Copy citation</Button>
          </div>
        </div>
      </header>

      <div className="docv-body">
        <Group orientation="horizontal" id="docv-split">
          <Panel id="original" defaultSize="56%" minSize="28%">
            <div className="docv-pane-left">
              <Original d={d} abs={abs} target={target} onOpen={openOriginal} />
            </div>
          </Panel>
          <Separator className="docv-sep" />
          <Panel id="facts" defaultSize="44%" minSize="26%">
            <div className="docv-pane-right">
              <Tabs
                style={{ padding: '0 14px' }}
                value={tab}
                onChange={setTab}
                tabs={[
                  { value: 'facts', label: 'Facts', icon: ListChecks, count: counts.facts },
                  { value: 'summary', label: 'Summary', icon: FileText },
                  { value: 'entities', label: 'Entities', icon: Users, count: counts.entities },
                  { value: 'text', label: 'Text', icon: Link2 },
                  { value: 'details', label: 'Details', icon: Info },
                  { value: 'notes', label: 'Notes', icon: StickyNote }
                ]}
              />
              <div className="docv-right-body" key={tab}>
                {tab === 'facts' && <FactsTab facts={d.facts} jump={jump} hasViewer={hasViewer} />}
                {tab === 'summary' && <SummaryTab d={d} />}
                {tab === 'entities' && <EntitiesTab entities={d.entities} />}
                {tab === 'text' && <TextTab pages={d.pages} jump={jump} hasViewer={hasViewer} />}
                {tab === 'details' && <DetailsTab d={d} />}
                {tab === 'notes' && <NotesTab key={d.sha} d={d} />}
              </div>
            </div>
          </Panel>
        </Group>
      </div>
    </div>
  )
}
