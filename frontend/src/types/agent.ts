// Mirror of backend SSE event shapes (see backend/app/run.py).
// Lines starting with `:` are SSE comments (keepalives) — caller drops them.

export type ShotStatus =
  | "planned"
  | "rendering"
  | "ready"
  | "rejected"
  | "approved"
  | "failed"

export interface CriticDiagnosis {
  composition: "ok" | "weak"
  typography: "ok" | "weak"
  motion: "ok" | "weak" | "jittery"
  color: "ok" | "weak" | "off_brand"
  text_legibility: "ok" | "weak"
  overall_score: number
  notes: string
}

export interface ShotStatusEvent {
  type: "shot_status"
  idx: number
  status: ShotStatus
  video_id: string | null
  template_title: string | null
  template_id: string | null
  template_picked_reason: string | null
  // v1 fields
  score: number | null
  diagnosis: CriticDiagnosis | null
  attempts_count: number
  last_strategy: string | null
  last_strategy_rationale: string | null
}

export interface CriticDiagnosisEvent {
  type: "critic_diagnosis"
  idx: number
  score: number
  diagnosis: CriticDiagnosis
  attempts_count: number
}

export type StrategistStrategy =
  | "rewrite_prompt"
  | "switch_template"
  | "revise_via_parent"
  | "accept"
  | "escalate"
  // Sentinel for the very first attempt before any strategy was chosen.
  | "initial"

export interface StrategistDecisionEvent {
  type: "strategist_decision"
  idx: number
  strategy: StrategistStrategy
  rationale: string
  attempt: number
}

export interface CoherenceDiagnosisEvent {
  type: "coherence_diagnosis"
  after_idx: number
  coherent: boolean
  reason: string
  suggested_edits_count: number
}

export interface ReplanAppliedEvent {
  type: "replan_applied"
  edited_indices: number[]
  replans_total: number
}

export interface NodeExitEvent {
  type: "node_exit"
  node: string
  // patch is the full state diff for the node — opaque on the client side.
  patch: Record<string, unknown>
}

export interface LogEvent {
  type: "log"
  level: "info" | "warn" | "error"
  message: string
  thread_id?: string
}

export interface InterruptEvent {
  type: "interrupt"
  kind: "hera_quota_exhausted" | "plan_review" | "escalation" | "final_review"
  payload: Record<string, unknown>
  thread_id: string
}

export interface DoneEvent {
  type: "done"
  final_video_url: string
  final_video_path: string
}

export interface ScrapeProgressEvent {
  type: "progress"
  category: string
  page: number
  count: number
  inserted: number
  updated: number
  upsert_errors?: string[]
  error?: string
}

export interface ScrapeDoneEvent {
  type: "done"
  summary: {
    categories_scraped: string[]
    templates_seen: number
    inserted: number
    updated: number
    stale_marked: number
    failed_categories: { category: string; error: string }[]
  }
}

export interface ScrapeErrorEvent {
  type: "error"
  code: string
  message: string
}

export type AgentEvent =
  | ShotStatusEvent
  | NodeExitEvent
  | LogEvent
  | InterruptEvent
  | DoneEvent
  | CriticDiagnosisEvent
  | StrategistDecisionEvent
  | CoherenceDiagnosisEvent
  | ReplanAppliedEvent

export type ScrapeEvent = ScrapeProgressEvent | ScrapeDoneEvent | ScrapeErrorEvent
