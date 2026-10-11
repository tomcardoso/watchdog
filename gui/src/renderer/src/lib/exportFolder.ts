// Where an investigation's latest finished graph export went (Activity → Maintenance → Export).

interface ExportJobLike {
  op: string
  vault: string | null
  state: string
  finished: string | null
  result: unknown
}

/** The folder the latest finished `export` job in `vault` reports in its result. Never a folder
 * guessed here: the default is named after the investigation's folder, which need not match its
 * listed slug (a registered or moved folder). */
export function lastExportFolder(jobs: Record<string, ExportJobLike>, vault: string): string | null {
  const done = Object.values(jobs)
    .filter((j) => j.op === 'export' && j.vault === vault && j.state === 'done')
    .sort((a, b) => (a.finished ?? '').localeCompare(b.finished ?? ''))
  const result = done.at(-1)?.result as { output?: unknown } | null | undefined
  return typeof result?.output === 'string' ? result.output : null
}
