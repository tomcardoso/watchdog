// How a document's text was read, for the Documents screen (D372): the OCR engine pre-processing
// recorded, in words a reporter can use. Documents pre-processed before D372 have no engine
// recorded, and read as unknown rather than guessed.

const ENGINE_LABELS: Record<string, string> = {
  ocrmac: 'Apple Vision',
  rapidocr: 'RapidOCR',
  tesseract: 'Tesseract',
  easyocr: 'EasyOCR',
  nemotron: 'Nemotron OCR'
}

function engineLabel(engine: string): string {
  return engine
    .split('+')
    .map((e) => ENGINE_LABELS[e] ?? e)
    .join(' and ')
}

function pageList(pages: number[]): string {
  return pages.length === 1 ? `page ${pages[0]}` : `pages ${pages.join(', ')}`
}

/** One line saying what read a document's text, or null when nothing was recorded. */
export function readWith(metadata: Record<string, unknown> | null | undefined): string | null {
  const engine = metadata?.ocr_engine
  if (typeof engine !== 'string' || !engine || engine === 'unknown') return null
  if (engine === 'none') return "The file's own text"
  const label = engineLabel(engine)
  const version = typeof metadata?.ocr_engine_version === 'string' && !engine.includes('+') ? ` ${metadata.ocr_engine_version}` : ''
  const pages = Array.isArray(metadata?.ocr_pages) ? (metadata.ocr_pages as number[]) : []
  if (metadata?.ocr_used) return pages.length ? `${label}${version} (OCR on ${pageList(pages)})` : `${label}${version} (OCR on every page)`
  return `The file's own text; ${label}${version} for any images`
}
