// Where an investigation's latest finished export went (Activity → Maintenance): the graph's
// folder, or the copy for troubleshooting's zip.

interface ExportJobLike {
  op: string
  vault: string | null
  state: string
  finished: string | null
  result: unknown
}

/** The path the latest finished `op` job (`export` by default) in `vault` reports as its result's
 * `output`. Never a folder guessed here: the default is named after the investigation's folder,
 * which need not match its listed slug (a registered or moved folder). */
export function lastExportFolder(jobs: Record<string, ExportJobLike>, vault: string, op = 'export'): string | null {
  const done = Object.values(jobs)
    .filter((j) => j.op === op && j.vault === vault && j.state === 'done')
    .sort((a, b) => (a.finished ?? '').localeCompare(b.finished ?? ''))
  const result = done.at(-1)?.result as { output?: unknown } | null | undefined
  return typeof result?.output === 'string' ? result.output : null
}
