// Processing history: the vault's processing.log, newest first, with OK / WARN / FAILED filtering.

import { History } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Empty, ErrorNote, Segmented, Skeleton, cx } from '@renderer/components/ui'
import { useRpc } from '@renderer/lib/rpc'
import { fmtDateTime } from '@renderer/lib/format'
import { useApp } from '@renderer/lib/store'

type Level = 'ok' | 'warn' | 'failed' | 'info'
const LINE = /^\[([^\]]+)\]\s*(.*)$/

function parse(line: string): { ts: string | null; text: string; level: Level } {
  const m = LINE.exec(line)
  const text = m ? m[2] : line
  const ts = m ? m[1] : null
  let level: Level = 'info'
  if (/^FAILED\b/.test(text) || /\bFAILED\b/.test(text.slice(0, 40))) level = 'failed'
  else if (/^WARN\b/.test(text)) level = 'warn'
  else if (/^OK\b/.test(text)) level = 'ok'
  return { ts, text, level }
}

export default function HistoryPanel() {
  const project = useApp((s) => s.project)!
  const q = useRpc('projects.log', { slug: project.slug, lines: 800 })
  const [filter, setFilter] = useState<'all' | 'ok' | 'warn' | 'failed'>('all')

  const rows = useMemo(() => {
    const all = (q.data?.lines ?? []).filter((l) => l.trim()).map(parse).reverse()
    return filter === 'all' ? all : all.filter((r) => r.level === filter)
  }, [q.data, filter])
  const counts = useMemo(() => {
    const c = { ok: 0, warn: 0, failed: 0 }
    for (const l of q.data?.lines ?? []) {
      const r = parse(l)
      if (r.level in c) c[r.level as keyof typeof c]++
    }
    return c
  }, [q.data])

  if (q.isLoading)
    return (
      <div className="col" style={{ gap: 8 }}>
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} h={18} />
        ))}
      </div>
    )
  if (q.error) return <ErrorNote error={q.error} retry={() => void q.refetch()} />
  if (!q.data?.lines.length)
    return (
      <Empty icon={History} title="No processing history yet">
        Every document the pipeline reads, extracts or fails on is recorded here, so you can see exactly what happened and when.
      </Empty>
    )
  return (
    <div className="col" style={{ gap: 12 }}>
      <div className="row">
        <Segmented
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'ok', label: `OK · ${counts.ok}` },
            { value: 'warn', label: `Warnings · ${counts.warn}` },
            { value: 'failed', label: `Failed · ${counts.failed}` }
          ]}
        />
        <span className="spacer" />
        <span className="faint" style={{ fontSize: 'var(--fs-sm)' }}>Most recent {q.data.lines.length} lines, newest first</span>
      </div>
      <div className="act-history selectable">
        {rows.length === 0 ? (
          <div className="act-log-empty">Nothing matches this filter.</div>
        ) : (
          rows.map((r, i) => (
            <div key={i} className={cx('act-hline', r.level)}>
              <span className="act-hline-t">{r.ts ? fmtDateTime(r.ts) : ''}</span>
              <span className="act-hline-x">{r.text}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
