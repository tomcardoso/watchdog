// A document's first page as a thumbnail. PDFs are rendered with pdf.js (cached to disk by the
// main process); images are shown as themselves; everything else gets a typeset "paper" preview
// built from the title and summary, so a grid of mixed formats still reads as documents.

import { FileAudio, FileImage, FileSpreadsheet, FileText, FileVideo, Globe, Presentation } from 'lucide-react'
import { CSSProperties, useEffect, useRef, useState } from 'react'
import { MEDIA_EXTS, VIDEO_EXTS } from '@renderer/lib/media'
import { renderThumb } from '@renderer/lib/pdf'
import { useVault } from '@renderer/lib/store'
import '@renderer/styles/docthumb.css'

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff'])
const BROWSER_IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'])

export function extIcon(ext: string) {
  if (IMAGE.has(ext)) return FileImage
  if (['xlsx', 'xls', 'csv'].includes(ext)) return FileSpreadsheet
  if (['pptx', 'ppt'].includes(ext)) return Presentation
  if (['html', 'htm'].includes(ext)) return Globe
  if (MEDIA_EXTS.has(ext)) return VIDEO_EXTS.has(ext) ? FileVideo : FileAudio
  return FileText
}

const mem = new Map<string, string>()

interface Props {
  sha: string
  ext: string
  original: string | null // vault-relative
  title?: string | null
  summary?: string | null
  width?: number
  className?: string
  style?: CSSProperties
}

export function DocThumb({ sha, ext, original, title, summary, width = 220, className, style }: Props) {
  const vault = useVault()
  const abs = original ? `${vault}/${original}` : null
  // The `r2` invalidates thumbnails cached before pdf.js could decode JPEG 2000 and JBIG2 scans,
  // which were saved blank. Bump it whenever rendering changes what a thumbnail looks like.
  const key = `${sha}-w${width}-r2`
  const [src, setSrc] = useState<string | null>(() => mem.get(key) ?? null)
  const [failed, setFailed] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (ext !== 'pdf' || !abs || src || failed) return
    let cancelled = false
    const el = ref.current
    // Render only once the thumbnail scrolls into view.
    const io = new IntersectionObserver(async (entries) => {
      if (!entries.some((e) => e.isIntersecting)) return
      io.disconnect()
      try {
        const cached = await window.watchdog.thumbs.get(key)
        const url = cached ?? (await renderThumb(abs, width))
        if (!cached) void window.watchdog.thumbs.put(key, url)
        mem.set(key, url)
        if (!cancelled) setSrc(url)
      } catch {
        if (!cancelled) setFailed(true)
      }
    }, { rootMargin: '200px' })
    if (el) io.observe(el)
    return () => {
      cancelled = true
      io.disconnect()
    }
  }, [abs, ext, key, src, failed, width])

  const cls = ['doc-thumb', className].filter(Boolean).join(' ')
  if (BROWSER_IMAGE.has(ext) && abs) {
    return (
      <div className={cls} style={style} ref={ref}>
        <img src={window.watchdog.files.url(abs)} alt="" loading="lazy" draggable={false} />
      </div>
    )
  }
  if (ext === 'pdf' && !failed) {
    return (
      <div className={cls} style={style} ref={ref}>
        {src ? <img src={src} alt="" draggable={false} /> : <div className="doc-thumb-loading skeleton" />}
      </div>
    )
  }
  const Icon = extIcon(ext)
  return (
    <div className={cls + ' doc-thumb-paper'} style={style} ref={ref}>
      <div className="paper-head">
        <Icon />
        <span>{ext.toUpperCase()}</span>
      </div>
      <div className="paper-title">{title}</div>
      {summary && <div className="paper-body">{summary}</div>}
      <div className="paper-lines">
        {Array.from({ length: 9 }).map((_, i) => (
          <span key={i} style={{ width: `${70 + ((i * 37) % 30)}%` }} />
        ))}
      </div>
    </div>
  )
}
