// Mirror of backend SSE event shapes (see backend/app/run.py).
// Lines starting with `:` are SSE comments (keepalives) — caller drops them.

export type ShotStatus =
  | "planned"
  | "rendering"
  | "ready"
  | "rejected"
  | "approved"
  | "failed"

export interface ShotStatusEvent {
  type: "shot_status"
  idx: number
  status: ShotStatus
  video_id: string | null
  template_title: string | null
  template_id: string | null
  template_picked_reason: string | null
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

export type ScrapeEvent = ScrapeProgressEvent | ScrapeDoneEvent | ScrapeErrorEvent
