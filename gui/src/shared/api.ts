// TypeScript mirror of gui/API.md — the contract with `python -m watchdog.gui.server`.
// Keep the two in step: a shape changed in one is changed in the other.

export type EntityType = 'person' | 'organization' | 'public-body' | 'place' | 'asset' | 'proceeding'
export const ENTITY_TYPES: EntityType[] = ['person', 'organization', 'public-body', 'place', 'asset', 'proceeding']

// ── app ──────────────────────────────────────────────────────────────────────
export interface AppInfo {
  version: string
  python: string
  python_version: string
  platform: string
  watchdog_home: string
  config_file: string
  setup_complete: boolean
  projects_dir: string | null
  claude_code: { installed: boolean; logged_in: boolean }
  obsidian_installed: boolean
}

// ── projects ─────────────────────────────────────────────────────────────────
export interface ProjectStats {
  documents: number
  entities: number
  last_ingest: string | null
  incoming: number
  awaiting: number
  failed: number
}
export interface Project {
  slug: string
  name: string
  description: string | null
  path: string
  archived: boolean
  created: string | null
  health: string | null
  access: boolean // the user has allowed Watchdog to work in this folder
  stats: ProjectStats
}
export interface ProjectStatus {
  project: Project
  by_type: Record<string, number>
  documents_by_type: Record<string, number>
  locks: { chew: boolean; ingest: boolean }
  pending_finalization: { docs: number; entities: number } | null
  size_bytes: number
}
export interface DoctorIssue { slug: string; name: string; path: string; problem: string; suggestion: string }

// ── vault ────────────────────────────────────────────────────────────────────
export interface DocumentRow {
  sha: string
  filename: string
  title: string | null
  document_type: string | null
  date_of_document: string | null
  page_count: number | null
  record_skill: string | null
  ingested_at: string | null
  near_duplicate_of: string | null
  note: string | null
  original: string | null
  fulltext: string | null
  ext: string
  entity_count: number
  source: string | null
  obtained: string | null
  summary: string | null
  /** "audio" or "video" for a recording, else null (D273). */
  media_kind?: 'audio' | 'video' | null
  duration_seconds?: number | null
}

export interface EntityRef { id: string; name: string; type: EntityType | string }

export interface EntityRow {
  id: string
  name: string
  type: EntityType | string
  aliases: string[]
  doc_count: number
  role_count: number
  contradiction_count: number
  first_seen: string | null
  last_updated: string | null
  note: string | null
  has_summary: boolean
  summary: string | null
}

export interface Summary {
  name: string
  path: string
  briefing: { path: string; name: string; date: string | null } | null
  headline: string | null
  contradictions: number
  leads: number
  near_duplicates: number
  possible_same: number
  alerts: number
  incoming: number
  awaiting_dig: number
  awaiting_bark: number
  pending_finalize: boolean
  failed: number
  research_urls: number
  context_unseeded: boolean
  has_work: boolean
  totals: { documents: number; entities: number; pages: number; events: number }
  recent_documents: DocumentRow[]
  top_entities: EntityRow[]
  verification: VerificationSummary
}

// The verification ledger (D271): a reporter's own check of a fact.
export type VerifyStatus = 'verified' | 'disputed' | 'unverifiable'
export interface FactMark { status: VerifyStatus; note: string | null; by: string | null; at: string | null }
export interface VerificationSummary {
  facts: number; verified: number; disputed: number; unverifiable: number; unmarked: number
  unlocated: number; orphaned: number; read_only?: boolean
}
export type PassageMethod = 'quote' | 'matched' | 'unlocated'
export interface LedgerFact {
  id: string; fact: string; page: number | null; basis: 'stated' | 'inferred' | string
  sha: string; filename: string; title: string
  passage_method: PassageMethod | null; passage_page: number | null; mark: FactMark | null
}
export interface OrphanMark extends FactMark { id: string; fact: string; page: number | null; sha256: string | null; filename: string | null }

export interface Fact {
  id: string
  fact: string
  page: number | null
  basis: 'stated' | 'inferred' | string
  date: string | null
  quote: string | null
  entities: EntityRef[]
  figure_note: string | null
  quote_note?: string | null
  added_by: string | null
  passage: string | null                 // the supporting text: the resolved quote or a matched passage (D270)
  passage_page: number | null
  passage_method: PassageMethod | null   // null: the document predates passages
  passage_score: number | null
  mark: FactMark | null
}

/** A recording's time map (D273): page n is the block from `start` to `end` seconds. */
export interface MediaInfo {
  kind: 'audio' | 'video'
  duration_seconds: number | null
  page_seconds: number | null
  pages: { page: number; start: number; end: number }[]
  language: string | null
  model: string | null
}

export interface DocumentDetail extends DocumentRow {
  frontmatter: Record<string, unknown>
  body: string
  facts: Fact[]
  entities: (EntityRef & { role: string | null })[]
  pages: { page: number; text: string }[]
  file_metadata: Record<string, unknown>
  sidecar: Record<string, unknown> | null
  metadata: Record<string, unknown> | null
  extract_model: string | null
  extract_effort: string | null
  record_skill_hash: string | null
  duplicates: { sha: string; filename: string; note: string | null }[]
  /** Audio and video only (D273). */
  media: MediaInfo | null
}

export interface TimelineEvent {
  date: string
  precision: 'day' | 'month' | 'year' | null
  text: string
  entities: EntityRef[]
  sha: string | null
  filename: string | null
  page: number | null
  note: string | null
  disputed: boolean                    // the reporter disputes the fact behind it (D285)
}

export interface Relationship {
  role: string
  target_id: string
  target_name: string | null
  target_type: string | null
  direction: 'out' | 'in'
  docs: string[]
}

/** One of an entity's facts (D280): a document fact tagged to the entity, with its source. */
export interface EntityFact extends Fact {
  sha: string
  title: string | null
  doc_date: string | null
  note: string | null
}

/** The AI-written summary of an entity, kept in the registry (D280). */
export interface EntitySynthesis {
  summary: string
  analysis: string | null
  by: 'model' | 'session' | 'carried' | string | null
  model: string | null
  made_at: string | null
  facts_total: number | null
  facts_shown: number | null
  stale: 'merge' | 'undo' | string | null
  stale_notice: string | null          // the out-of-date warning the note shows (D285)
  /** The summary and analysis with their citations rendered as fact links (D283). */
  summary_md?: string
  analysis_md?: string | null
  citations?: CitationCounts
}

/** How a text's fact citations resolved (D283). */
export interface CitationCounts {
  cited: number
  linked: number
  unknown: number
  missing: number
  disputed: number
}

/** The fact a citation link names. */
export interface CitedFact {
  id: string
  sha: string
  fact: string
  page: number | null
  title: string | null
  note: string | null
  date: string | null
  basis: string | null
  mark: VerifyStatus | null
  passage: string | null
  passage_page: number | null
}

export interface CitationStatus {
  target: string
  block: string
  status: 'found' | 'not_found'
  fact: CitedFact | null
  disputed?: boolean
  label?: string | null
}

export interface CitationReport {
  checked: number
  citations: number
  found: number
  not_found: number
  disputed: number
  unresolved_short: number
  pages: { path: string; items: CitationStatus[]; citations: number; found: number; not_found: number; disputed: number; unresolved_short: number }[]
}

export interface EntityDetail extends EntityRow {
  frontmatter: Record<string, unknown>
  body: string
  sections: {
    summary: string | null
    analysis: string | null
    contradictions: string | null
    timeline: string | null
    relationships: string | null
    notes: string | null
  }
  documents: DocumentRow[]
  relationships: Relationship[]
  contradictions: { rid: string; summary: string; text: string; resolved: boolean }[]
  timeline: TimelineEvent[]
  facts: EntityFact[]                  // every fact tagged to the entity, in date order (D280)
  synthesis: EntitySynthesis | null
  legacy_claims: string | null         // claims an older version recorded with no stored extraction
}

export interface GraphData {
  nodes: { id: string; name: string; type: string; doc_count: number }[]
  edges: { source: string; target: string; role: string; docs: string[] }[] // docs: document sha256s
}

export type NoteKind = 'entity' | 'document' | 'briefing' | 'query' | 'wiki' | 'other'
export interface Note {
  path: string
  exists: boolean
  frontmatter: Record<string, unknown>
  body: string
  title: string | null
  kind: NoteKind
}

export type LinkKind = 'document' | 'entity' | 'briefing' | 'query' | 'wiki' | 'note' | 'original' | 'fulltext' | 'missing'
export interface ResolvedLink { path: string | null; kind: LinkKind; sha: string | null; page: number | null }

export interface PipelineState {
  incoming: { name: string; path: string; size: number; modified: string; sidecar: boolean }[]
  chew_failed: { name: string; path: string; size: number }[]
  skipped: { name: string; path: string; size: number; reason: string | null }[]
  queued: { sha: string; filename: string; page_count: number | null; est_tokens: number | null; staged: boolean }[]
  failed: { sha: string; filename: string; reason: string | null }[]
  pending_finalization: { docs: number; entities: number } | null
  locks: { chew: boolean; ingest: boolean }
  research_urls: number
  batch_pending: Record<string, unknown> | null
}

export interface BriefingRow {
  path: string
  name: string
  kind: 'briefing' | 'leads' | 'alerts' | 'research'
  date: string | null
  title: string | null
}

export interface DocumentRequest {
  rid: string
  type: string | null
  what: string
  why: string | null
  likely_source: string | null
  cited_in: { sha: string; filename: string; note: string | null }[]
  added: string | null
}

// ── ingest ───────────────────────────────────────────────────────────────────
export type Effort = 'low' | 'medium' | 'high' | 'xhigh' | 'max'
export interface RunOptions {
  extractor_model?: string
  classifier_model?: string
  finalizer_model?: string
  finalizer_reconciliation_model?: string
  finalizer_synthesis_model?: string
  finalizer_timeline_model?: string
  finalizer_briefing_model?: string
  extractor_effort?: Effort
  classifier_effort?: Effort
  finalizer_effort?: Effort
  concurrency?: number
  classify_pages?: number
  skill?: string
  limit?: number
  verify?: boolean | null
  force?: boolean
  wait?: boolean
  skip_briefing?: boolean
  chew_workers?: number
  chunk_workers?: number
}

export interface Preflight {
  documents_to_send: number
  incoming: number
  queued: number
  staged: number
  failed: number
  pending_finalization: { docs: number; entities: number } | null
  auth: { mode: 'subscription' | 'api-key' | 'none' | null; ok: boolean; reason: string | null }
  models: { stage: string; backend: string | null; model: string; effort: string | null; label: string }[]
  auto_approve: { enabled: boolean; approve: boolean; blocker: string | null }
  warning_text: string
}

export interface Estimate {
  text: string
  estimate: Record<string, unknown> | null
  all_models: { label: string; provider: string; cost_usd: number; note: string | null }[] | null
}

// ── jobs ─────────────────────────────────────────────────────────────────────
export type JobState = 'running' | 'done' | 'failed' | 'cancelled'
export interface DocProgress { filename: string; state: string; detail: string | null }
export interface ProgressState {
  stage: string | null
  done: number | null
  total: number | null
  current: string | null
  /** A transient detail beside the stage, such as which recording is being transcribed. */
  note?: string | null
  docs: Record<string, DocProgress>
}
export interface Job {
  id: string
  label: string
  kind: string
  vault: string | null
  args: string[]
  state: JobState
  exit_code: number | null
  started: string
  finished: string | null
  progress: ProgressState
}
export interface LogLine { t: string; stream: 'out' | 'err'; text: string }
export interface ActionResult { code: number; stdout: string; stderr: string }

// ── search ───────────────────────────────────────────────────────────────────
export interface SearchPassage { filename: string; page: number | null; text: string; score: number; sha?: string | null; note?: string | null; original?: string | null }
export interface SearchNote { note_path: string; preview: string; score: number }
export interface SearchExact { kind: string; title: string | null; path: string | null; page: number | null; text: string; sha?: string | null; note?: string | null; original?: string | null }
export interface SearchResult {
  query: string
  index_empty: boolean
  exact_error: string | null
  semantic_error?: string | null
  /** The engine's background setup has not finished, so only exact matches were searched (D272). */
  semantic_pending?: boolean
  exact: SearchExact[]
  passages: SearchPassage[]
  notes: SearchNote[]
}
export interface BatchResult {
  terms: { term: string; checked: boolean; hits: { vault_name?: string; kind: string; title: string | null; note: string | null; page: number | null; text: string }[] }[]
}
export interface EverywhereResult {
  vaults: { slug: string; name: string; path: string; entity_hits: { id: string; name: string; type: string; note: string | null }[]; exact: SearchExact[] }[]
  skipped: { slug: string; reason: string }[]
}

// ── review ───────────────────────────────────────────────────────────────────
export type ReviewKind = 'contradictions' | 'leads' | 'alerts' | 'duplicates' | 'merges'
export const REVIEW_KINDS: ReviewKind[] = ['contradictions', 'leads', 'alerts', 'duplicates', 'merges']
export interface ReviewItem { kind: ReviewKind; rid: string; title: string; detail: string[]; note: string | null; pair?: SamePair }

// Entity identity (D279): a "possible same" pair left for the reporter, and the merge log.
export type MergeTier = 'high' | 'medium' | 'low' | 'manual'
export interface SameFact { id: string; sha: string; fact: string; page: number | null; document: string; date: string | null; disputed?: boolean }
export interface SameSide { id: string; name: string; type: string; aliases: string[]; documents: string[]; facts: SameFact[]; roles: { relationship: string; target: string }[]; note: string | null; doc_count: number }
export interface MergeEvidence { surface?: string | null; identifier?: { scheme: string; value: string } | null; shared?: { relationship: string; target_id: string; target_name: string }[] }
export interface SamePair { tier: MergeTier; rule: string; model_declined: boolean; evidence: MergeEvidence; a: SameSide; b: SameSide }
export interface MergeLogEntry {
  id: string
  keep: { id: string; name: string; type: string; exists: boolean; note: string | null }
  merged: { id: string; name: string; type: string }
  tier: MergeTier
  decided_by: 'rule' | 'model' | 'reporter'
  rule: string | null
  reason: string
  model: string | null
  reporter: string | null
  same_name: boolean
  at: string | null
  first_at: string | null
  identifier: { scheme: string; value: string } | null
  shared: { relationship: string; target_id: string; target_name: string }[]
  documents: { sha: string; title: string }[]
  document_count: number
  facts: { id: string; fact: string; page: number | null; sha: string; title: string; disputed: boolean }[]
  undo_available: boolean
  undo_reason: string | null
  undone: { at: string; by: string; split_id: string } | null
}

// ── settings ─────────────────────────────────────────────────────────────────
export type SettingKind = 'bool' | 'int' | 'float' | 'choice' | 'model' | 'effort' | 'path' | 'text' | 'secret'
export interface SettingKey {
  key: string
  short: string
  help: string
  default: unknown
  current: unknown
  display: string
  kind: SettingKind
  choices: string[] | null
  is_set?: boolean
}
export interface SettingsSchema { sections: { title: string; blurb: string; keys: SettingKey[] }[] }
export interface ModelChoice {
  value: string
  label: string
  provider: string
  backend: string | null
  input_per_mtok: number | null
  output_per_mtok: number | null
  context_window: number | null
  efforts: string[]
  notes: string | null
}
export interface AuthStatus {
  claude: { mode: string; logged_in: boolean; reason: string | null }
  stages: { stage: string; value: string; provider: string; ready: boolean; billing: string | null }[]
  keys: { provider: string; masked: string; in_use: string; source: 'stored' | 'env' }[]
  providers?: { provider: string; label: string; env: string; requires_key: boolean; base_url_setting: string | null; base_url: string | null; ready: boolean }[]
}
export interface SkillInfo { name: string; description: string; source: 'package' | 'user' }
export interface SetupCheck {
  deps: { label: string; ok: boolean; hint: string | null; required?: boolean }[]
  playwright: boolean
  gliner_model: boolean
  projects_dir: string | null
  config_exists: boolean
}

/** Which local models are on disk (`setup.models`). `reranker` is null when it is switched off. */
export interface SetupModels {
  docling: boolean
  gliner: boolean
  embedding: boolean
  reranker: boolean | null
  ocr: string | null
  claude_cli: string | null
  /** The configured transcription model is downloaded (D273). Downloaded on demand. */
  transcription?: boolean
  transcription_model?: string
  transcription_size_mb?: number
}

// ── usage ────────────────────────────────────────────────────────────────────
export interface UsageRunRow {
  ts: string
  file: string
  calls: number
  input_tokens: number
  output_tokens: number
  cost_usd: number
  backends: string
  subscription: boolean
  stages: Record<string, number> // stage → cost
  cache_read_tokens?: number
  cache_write_tokens?: number
  latency_s?: number
}
export interface UsageRun {
  ts: string
  stages: { stage: string; model: string; backend: string; calls: Record<string, unknown>[]; totals: Record<string, number>; wall_seconds: number | null }[]
  totals: Record<string, number>
  subscription_note: string | null
  peak_concurrency?: number | null
  batch_note?: string | null
  corpus?: { documents: number; pages: number } | null
  cost_per_page?: number | null
}

// ── research ─────────────────────────────────────────────────────────────────
export interface ResearchStatus {
  queued: { url: string; title: string | null; source_type: string | null; relevance: string | null }[]
  wayback_configured: boolean
}

// ── chat ─────────────────────────────────────────────────────────────────────
export type ChatMode = 'ask' | 'context' | 'research'
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  text: string
  tool?: { name: string; input: unknown; result: string | null; is_error: boolean }
  ts: string
}
export interface ChatSessionRow { session: string; mode: ChatMode; title: string; started: string; updated: string; model: string | null }

// ── the method table: name → [params, result] ────────────────────────────────
export interface Methods {
  'access.list': [Record<string, never>, { enforced: boolean; file: string; folders: { path: string; label: string; granted: string | null }[] }]
  'app.ping': [Record<string, never>, { ok: boolean }]
  'app.info': [Record<string, never>, AppInfo]

  'projects.list': [{ all?: boolean }, Project[]]
  'projects.get': [{ slug: string }, Project]
  'projects.forPath': [{ path: string }, Project | null]
  'projects.status': [{ slug: string }, ProjectStatus]
  'projects.log': [{ slug: string; lines?: number }, { lines: string[] }]
  'projects.doctor': [Record<string, never>, { issues: DoctorIssue[] }]

  'vault.summary': [{ vault: string }, Summary]
  'vault.documents': [{ vault: string }, DocumentRow[]]
  'vault.document': [{ vault: string; sha: string }, DocumentDetail]
  'vault.entities': [{ vault: string }, EntityRow[]]
  'vault.entity': [{ vault: string; id: string }, EntityDetail]
  'vault.graph': [{ vault: string }, GraphData]
  'vault.timeline': [{ vault: string }, { events: TimelineEvent[] }]
  'vault.note': [{ vault: string; path: string }, Note]
  'vault.saveNotes': [{ vault: string; path: string; text: string }, { ok: boolean }]
  'vault.resolveLink': [{ vault: string; target: string }, ResolvedLink]
  'vault.citations': [{ vault: string; links: string[] }, Record<string, CitationStatus>]
  'vault.checkCitations': [{ vault: string }, CitationReport]
  'vault.pipeline': [{ vault: string }, PipelineState]
  'vault.briefings': [{ vault: string }, BriefingRow[]]
  'vault.notes': [{ vault: string }, { path: string; kind: 'query' | 'wiki'; title: string; modified: string }[]]
  'vault.readFile': [{ vault: string; path: string }, { text: string; exists: boolean }]
  'vault.writeFile': [{ vault: string; path: string; text: string }, { ok: boolean }]
  'vault.requests': [{ vault: string }, { open: DocumentRequest[]; resolved_count: number }]
  'vault.contextFiles': [{ vault: string }, { name: string; size: number; modified: string }[]]
  'vault.migrate': [{ vault: string }, { changes: string[] }]

  'verify.mark': [{ vault: string; id: string; status: VerifyStatus | null; note?: string | null }, FactMark & { id: string }]
  'verify.facts': [{ vault: string }, { summary: VerificationSummary; facts: LedgerFact[]; orphaned: OrphanMark[]; reporter: string }]

  'ingest.preflight': [{ vault: string; options?: RunOptions }, Preflight]
  'ingest.estimate': [{ vault: string; stage: 'dig' | 'bark'; all_models?: boolean; options?: RunOptions }, Estimate]

  'jobs.start': [{ vault: string | null; args: string[]; label: string; kind?: string }, Job]
  'jobs.cancel': [{ id: string }, { ok: boolean }]
  'jobs.list': [Record<string, never>, Job[]]
  'jobs.get': [{ id: string }, Job & { log: LogLine[] }]
  'jobs.flags': [{ command: 'add' | 'dig' | 'bark' | 'chew'; options: RunOptions }, { args: string[] }]
  'jobs.rebuildNotes': [{ vault: string }, Job]
  'jobs.undoMerge': [{ vault: string; id: string }, Job]
  'action.run': [{ vault: string | null; args: string[]; timeout?: number }, ActionResult]

  'search.query': [{ vault: string; query: string; top?: number; threshold?: number | null; rerank?: boolean }, SearchResult]
  'search.batch': [{ vault: string | null; terms: string[]; everywhere?: boolean }, BatchResult]
  'search.everywhere': [{ query: string; top?: number }, EverywhereResult]
  'search.status': [{ vault: string }, { total: number; documents: number; notes: number }]

  'review.items': [{ vault: string; kinds?: ReviewKind[] }, { items: ReviewItem[]; counts: Record<ReviewKind, number> }]
  'review.resolve': [{ vault: string; rids: string[] }, { resolved: string[] }]
  'review.unresolve': [{ vault: string; rids: string[] }, { unresolved: string[] }]
  'review.resolved': [{ vault: string }, { items: { rid: string; label: string; resolved_at: string; kind: string }[] }]
  'review.sync': [{ vault: string }, { resolved: string[]; unresolved: string[] }]
  'review.leads': [{ vault: string }, Record<string, unknown>]
  'review.watchlist': [{ vault: string }, { terms: string[]; text: string }]
  'review.mergeLog': [{ vault: string; limit?: number }, { merges: MergeLogEntry[]; total: number; too_new: boolean; undo_available: boolean }]
  'review.mergePreview': [{ vault: string; keep: string; merge: string }, { keep: EntityRow; merge: EntityRow; both_have_summary: boolean; type_mismatch?: boolean }]

  'settings.schema': [Record<string, never>, SettingsSchema]
  'settings.set': [{ key: string; value: string }, { key: string; value: unknown; display: string }]
  'settings.models': [Record<string, never>, { models: ModelChoice[]; efforts: string[] }]
  'auth.status': [Record<string, never>, AuthStatus]
  'auth.setAnthropicMode': [{ mode: 'subscription' | 'api-key'; key?: string }, AuthStatus]
  'auth.setKey': [{ provider: string; key: string }, AuthStatus]
  'auth.deleteKey': [{ provider: string }, AuthStatus]
  'auth.setBaseUrl': [{ provider: 'local' | 'openrouter'; url: string }, AuthStatus]
  'skills.list': [Record<string, never>, { skills: SkillInfo[]; user_dir: string }]
  'skills.read': [{ name: string }, { name: string; text: string }]
  'setup.check': [Record<string, never>, SetupCheck]
  'setup.models': [Record<string, never>, SetupModels]
  'setup.downloadModel': [{ model: 'transcription' }, Job]
  'setup.complete': [{ projects_dir?: string; auto_approve?: boolean }, { projects_dir: string; ocr_engine: string | null; auto_approve: boolean }]
  'auth.routeIngestion': [{ provider: string; model: string }, AuthStatus]

  'usage.runs': [{ vault: string }, { runs: UsageRunRow[]; corpus: { documents: number; pages: number } | null }]
  'usage.run': [{ vault: string; ts?: string }, UsageRun]

  'research.status': [{ vault: string }, ResearchStatus]

  'chat.start': [{ vault: string; mode: ChatMode; model?: string | null; prompt?: string }, { session: string }]
  'chat.send': [{ session: string; text: string }, { ok: boolean }]
  'chat.interrupt': [{ session: string }, { ok: boolean }]
  'chat.close': [{ session: string }, { ok: boolean; research_queued: number }]
  'chat.permission': [{ session: string; request_id: string; allow: boolean; always?: boolean }, { ok: boolean }]
  'chat.list': [{ vault: string }, ChatSessionRow[]]
  'chat.get': [{ session: string }, { session: string; mode: ChatMode; title: string; messages: ChatMessage[] }]
  'chat.resume': [{ session: string }, { session: string }]
  'chat.delete': [{ session: string }, { ok: boolean }]
}
export type MethodName = keyof Methods
export type Params<M extends MethodName> = Methods[M][0]
export type Result<M extends MethodName> = Methods[M][1]

// ── events (server → app, plus a few from the Electron main process) ─────────
export interface Events {
  'server.ready': { protocol: number; pid: number; methods: string[] }
  'backend.status': BackendStatus
  'job.started': { job: Job }
  'job.log': { id: string; lines: LogLine[] }
  'job.progress': { id: string; progress: ProgressState; event: Record<string, unknown> }
  'job.finished': { job: Job }
  'chat.delta': { session: string; message_id: string; text: string }
  'chat.message': { session: string; message: ChatMessage }
  'chat.permission': { session: string; request_id: string; tool: string; input: unknown; description: string | null }
  'chat.status': { session: string; state: 'thinking' | 'idle' | 'closed' | 'error'; detail: string | null; cost_usd: number | null }
  'menu.command': { command: string }
  'files.dropped': { paths: string[] }
  'update.state': UpdateState
  'engine.progress': EngineProgress
  'claude.signin': { url: string | null }
}
export type EventName = keyof Events

/** A folder the user has allowed Watchdog to change (main/access.ts). */
export interface FolderGrant { path: string; label: string; granted: string }

/** The in-app updater's state (main/updater.ts). */
export interface UpdateState {
  state: 'idle' | 'available' | 'downloading' | 'ready'
  version: string | null
  percent: number | null
  error: string | null
}

export interface BackendStatus {
  state: 'starting' | 'ready' | 'error' | 'stopped'
  python: string | null
  source: string | null // where the python was found: env | settings | pipx | path | dev
  message: string | null
  stderrTail: string[]
  /** No Python that can run Watchdog was found, and the app can build its own (the engine). */
  needsEngine?: boolean
}

// ── the managed engine (built by the main process; see src/main/engine.ts) ───
export type EngineState = 'idle' | 'running' | 'done' | 'failed' | 'cancelled'
export interface EngineStep {
  id: string
  label: string
  optional: boolean
  /** 1: needed before the app can be used. 2: installed in the background (D272). */
  phase: 1 | 2
  state: 'pending' | 'running' | 'done' | 'warning' | 'failed' | 'skipped'
  detail: string | null
}
/** One snapshot of an install run; `log` carries only the lines added since the last event. */
export interface EngineProgress {
  id: number
  state: EngineState
  /** The phase the run is in (or ended in). */
  phase: 1 | 2 | null
  steps: EngineStep[]
  log: string[]
  error: string | null
}
export interface EngineStatus {
  /** 'installing' while a run is in progress; otherwise the run's failed/cancelled state or the engine's own. */
  state: 'missing' | 'outdated' | 'ready' | 'installing' | 'failed' | 'cancelled'
  /** Phase 1 (what the app needs to open): 'ready' when installed and matching the app. */
  engine: 'missing' | 'outdated' | 'ready'
  /** Phase 2 too: every library and the model step. Adding documents waits for this. */
  complete: boolean
  dir: string
  installedVersion: string | null
  bundledVersion: string | null
  modelsDone: boolean
  modelResults: Record<string, 'ok' | 'warn'> | null
  canInstall: boolean
  /** The Python the backend runs on when it is not the managed engine. */
  usingExternal: string | null
  /** WATCHDOG_FORCE_ONBOARDING: "1" or the id of the step to start on. */
  forceOnboarding: string | null
  simulated: boolean
  /** ~/.watchdog/config.json exists: the existing signal that setup has been done. */
  setupConfigExists: boolean
  /** The current (or last) run, so a screen opened mid-install can catch up. */
  run: { id: number; state: EngineState; phase: 1 | 2 | null; steps: EngineStep[]; log: string[]; error: string | null }
}

// ── the bridge exposed on window.watchdog by the preload script ──────────────
export interface WatchdogBridge {
  rpc<M extends MethodName>(method: M, params?: Params<M>): Promise<Result<M>>
  on<E extends EventName>(event: E, cb: (data: Events[E]) => void): () => void
  backend: {
    status(): Promise<BackendStatus>
    restart(): Promise<BackendStatus>
    choosePython(): Promise<BackendStatus>
  }
  engine: {
    status(): Promise<EngineStatus>
    install(opts?: { fresh?: boolean }): Promise<EngineStatus>
    cancel(): Promise<void>
    reinstall(): Promise<EngineStatus>
  }
  claude: {
    status(): Promise<{ installed: boolean; loggedIn: boolean }>
    /** Opens the browser; resolves when the person has signed in, or with the reason it did not. */
    signIn(): Promise<{ ok: boolean; message: string | null }>
    cancel(): Promise<void>
  }
  dialog: {
    openFiles(opts?: { title?: string; folders?: boolean; multi?: boolean }): Promise<string[]>
    /** `grant`: why Watchdog will change files there; choosing the folder grants access to it. */
    openFolder(opts?: { title?: string; grant?: string }): Promise<string | null>
    saveFile(opts?: { title?: string; defaultPath?: string }): Promise<string | null>
    confirm(opts: { title: string; message: string; detail?: string; confirm: string; destructive?: boolean }): Promise<boolean>
  }
  shell: {
    openPath(path: string): Promise<string>
    showItemInFolder(path: string): void
    openExternal(url: string): Promise<void>
    openInObsidian(vault: string, note?: string): Promise<boolean>
  }
  files: {
    pathForFile(file: File): string
    url(absPath: string): string
  }
  thumbs: {
    get(key: string): Promise<string | null> // data URL or null
    put(key: string, dataUrl: string): Promise<void>
  }
  prefs: {
    get<T = unknown>(key: string): Promise<T | null>
    set(key: string, value: unknown): Promise<void>
  }
  notify(title: string, body: string): void
  access: {
    list(): Promise<FolderGrant[]>
    /** Ask the user, in a native prompt, to allow `path`. Resolves true once granted. */
    request(path: string, label: string, why: string): Promise<boolean>
    revoke(path: string): Promise<FolderGrant[]>
  }
  updates: {
    get(): Promise<UpdateState>
    check(): Promise<void>
    download(): Promise<void>
    install(): Promise<void>
  }
  platform: string
}
