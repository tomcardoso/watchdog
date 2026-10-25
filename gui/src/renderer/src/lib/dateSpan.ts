// A timeline date or range as the app shows it (D330). The same rules as the notes'
// `dates.render_span`: "Apr–Jun 2024", "3–7 Apr 2024", "3 Apr – 7 Jun 2024", "2019–2021".

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const ISO = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/

function parts(s: string): number[] | null {
  const m = ISO.exec(s.trim())
  if (!m) return null
  return [m[1], m[2], m[3]].filter((x) => x !== undefined).map(Number)
}

function one(p: number[], withYear = true): string {
  const y = withYear ? ` ${p[0]}` : ''
  if (p.length === 3) return `${p[2]} ${MON[p[1] - 1]}${y}`
  if (p.length === 2) return `${MON[p[1] - 1]}${y}`
  return String(p[0])
}

/** A start date, or a start-and-end range, compactly; a malformed end shows the start alone. */
export function spanLabel(start: string, end?: string | null): string {
  const s = parts(start)
  if (!s) return start
  const e = end ? parts(end) : null
  if (!e) return one(s)
  if (s.length === 1 && e.length === 1) return `${s[0]}–${e[0]}`
  if (s.length === 2 && e.length === 2 && s[0] === e[0]) return `${MON[s[1] - 1]}–${MON[e[1] - 1]} ${s[0]}`
  if (s.length === 3 && e.length === 3 && s[0] === e[0]) {
    if (s[1] === e[1]) return `${s[2]}–${e[2]} ${MON[s[1] - 1]} ${s[0]}`
    return `${one(s, false)} – ${one(e, false)} ${s[0]}`
  }
  return `${one(s)} – ${one(e)}`
}
