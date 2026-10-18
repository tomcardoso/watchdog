// Formatting helpers: dates, numbers, sizes, plurals. Canadian English throughout.

const dateFmt = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: 'short', day: 'numeric' })
const dateTimeFmt = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
const numFmt = new Intl.NumberFormat('en-CA')

/** A date string as the pipeline writes it (2024, 2024-03, 2024-03-15, or ISO) → readable. */
export function fmtDate(s: string | null | undefined): string {
  if (!s) return ''
  const m = /^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$/.exec(s.trim())
  if (m) {
    const [, y, mo, d] = m
    if (!mo) return y
    const date = new Date(Number(y), Number(mo) - 1, d ? Number(d) : 1)
    if (!d) return date.toLocaleDateString('en-CA', { year: 'numeric', month: 'long' })
    return dateFmt.format(date)
  }
  const t = Date.parse(s)
  return Number.isNaN(t) ? s : dateFmt.format(new Date(t))
}

export function fmtDateTime(s: string | null | undefined): string {
  if (!s) return ''
  const t = Date.parse(s)
  return Number.isNaN(t) ? s : dateTimeFmt.format(new Date(t))
}

export function fmtRelative(s: string | null | undefined): string {
  if (!s) return ''
  const t = Date.parse(s)
  if (Number.isNaN(t)) return s
  const diff = (Date.now() - t) / 1000
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  if (diff < 86400 * 7) {
    const d = Math.round(diff / 86400)
    return d === 1 ? 'yesterday' : `${d} days ago`
  }
  return fmtDate(s)
}

export function fmtNum(n: number | null | undefined): string {
  return n === null || n === undefined ? '—' : numFmt.format(n)
}

export function fmtTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`
  return String(n)
}

export function fmtCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return '—'
  if (usd === 0) return '$0'
  if (usd < 0.01) return '<$0.01'
  return `$${usd.toFixed(usd < 10 ? 2 : 0)}`
}

export function fmtBytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${units[i]}`
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m ${s % 60}s`
  return `${Math.floor(m / 60)}h ${m % 60}m`
}

/** plural(3, 'document') → '3 documents'; plural(1, 'entity', 'entities') → '1 entity'. */
export function plural(n: number, one: string, many?: string): string {
  return `${fmtNum(n)} ${n === 1 ? one : many ?? one + 's'}`
}

export function basename(p: string): string {
  return p.split(/[\\/]/).pop() ?? p
}
