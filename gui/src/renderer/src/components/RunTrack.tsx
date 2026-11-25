// The Add documents track (D413): one bar from reading to the briefing, a section per stage that
// fills by its own count, with the pause to confirm as a diamond between Read and Extract. The
// dialog shows it with labels; the corner card shows it bare (`compact`).

import type { Track } from '@renderer/lib/addTrack'
import './runtrack.css'

export function RunTrack({ track, compact }: { track: Track; compact?: boolean }) {
  return (
    <div className={compact ? 'run-track compact' : 'run-track'}>
      {!compact && (track.headline || track.side) && (
        <div className="run-track-line">
          <b>{track.headline}</b>
          {track.side && <span className="faint truncate">{track.side}</span>}
        </div>
      )}
      <ol className="run-track-bars" aria-label="Progress through adding documents">
        {track.sections.map((s) => (
          <li key={s.key} className={`${s.key === 'confirm' ? 'rt-gate ' : ''}${s.state}`} aria-current={s.state === 'now' ? 'step' : undefined}>
            {s.key === 'confirm' ? (
              <span className="diamond" title={s.state === 'skipped' ? 'Confirm: not needed this time' : 'Confirm before anything goes to a model'} />
            ) : (
              <span className={s.pulse && s.state === 'now' ? 'bar pulse' : 'bar'}>
                <i style={{ width: `${Math.round(s.fraction * 100)}%` }} />
              </span>
            )}
            {!compact && s.key !== 'confirm' && (
              <>
                <span className="t">
                  {s.label}
                  <span className={s.local ? 'where-tag local' : 'where-tag model'}>{s.local ? 'this computer' : 'model'}</span>
                </span>
                <span className="d tnum">{s.state === 'stopped' ? `Stopped${s.where ? ` · ${s.where}` : ''}` : s.where}</span>
              </>
            )}
            {!compact && s.key === 'confirm' && <span className="sr-only">Confirm: {s.state}</span>}
          </li>
        ))}
      </ol>
    </div>
  )
}
