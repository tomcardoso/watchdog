// pdf.js, loaded once. Documents are fetched through the wdfile:// protocol (see main/protocol.ts).

import * as pdfjs from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import type { PDFDocumentProxy } from 'pdfjs-dist'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl
export { pdfjs }

/** A colour token's current value, for canvas drawing (which can't use CSS variables). */
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const docs = new Map<string, Promise<PDFDocumentProxy>>()

/** Open (and memoize) a PDF by absolute path. */
export function openPdf(absPath: string): Promise<PDFDocumentProxy> {
  let p = docs.get(absPath)
  if (!p) {
    p = pdfjs.getDocument({ url: window.watchdog.files.url(absPath) }).promise
    p.catch(() => docs.delete(absPath))
    docs.set(absPath, p)
    if (docs.size > 24) {
      const oldest = docs.keys().next().value
      if (oldest && oldest !== absPath) {
        void docs.get(oldest)?.then((d) => d.loadingTask.destroy()).catch(() => undefined)
        docs.delete(oldest)
      }
    }
  }
  return p
}

// Thumbnails render one or two at a time so a grid of 200 documents doesn't stall the UI.
let active = 0
const queue: (() => void)[] = []
function slot<T>(fn: () => Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const run = () => {
      active++
      fn()
        .then(resolve, reject)
        .finally(() => {
          active--
          queue.shift()?.()
        })
    }
    if (active < 2) run()
    else queue.push(run)
  })
}

/** Render page 1 of a PDF to a PNG data URL `width` px wide. */
export function renderThumb(absPath: string, width: number): Promise<string> {
  return slot(async () => {
    const doc = await openPdf(absPath)
    const page = await doc.getPage(1)
    const base = page.getViewport({ scale: 1 })
    const scale = (width * 2) / base.width // 2× for crisp HiDPI thumbnails
    const vp = page.getViewport({ scale })
    const canvas = document.createElement('canvas')
    canvas.width = Math.ceil(vp.width)
    canvas.height = Math.ceil(vp.height)
    const ctx = canvas.getContext('2d')!
    ctx.fillStyle = cssVar('--pdf-page')
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    await page.render({ canvasContext: ctx, viewport: vp, canvas }).promise
    return canvas.toDataURL('image/png')
  })
}
