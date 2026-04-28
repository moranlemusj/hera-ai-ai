/**
 * Tiny typed API client for the non-streaming endpoints. The streaming ones
 * (POST /run, POST /resume, POST /admin/refresh_templates) go through
 * useEventStream directly.
 */

const BASE = ""  // proxied via vite.config.ts

interface SessionStatus {
  status: "missing" | "active" | "expiring" | "expired"
  expires_at: string | null
  last_validated: string | null
  seconds_until_expiry: number | null
}

interface CategorySummary {
  category: string
  active: number
  stale: number
  missing_embedding: number
  last_seen: string | null
}

export interface TemplatesSummary {
  total: number
  active: number
  last_seen: string | null
  per_category: CategorySummary[]
}

export interface HealthResponse {
  status: "ok"
  mock_mode: boolean
  db: {
    postgres_version: string
    extensions: string
    missing_extensions: string
  }
  versions: Record<string, string>
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(BASE + path, {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(init?.headers ?? {}),
    },
  })
  if (!resp.ok) {
    let detail: unknown
    try {
      detail = await resp.json()
    } catch {
      detail = await resp.text()
    }
    const err = new Error(`${resp.status} ${resp.statusText}`) as Error & { detail?: unknown }
    err.detail = detail
    throw err
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

export const api = {
  health: () => request<HealthResponse>("/health"),
  getHeraSession: () => request<SessionStatus>("/admin/hera_session"),
  postHeraSession: (curl: string) =>
    request<SessionStatus>("/admin/hera_session", {
      method: "POST",
      body: JSON.stringify({ curl }),
    }),
  deleteHeraSession: () =>
    request<void>("/admin/hera_session", { method: "DELETE" }),
  getTemplatesSummary: () => request<TemplatesSummary>("/admin/templates_summary"),
}

export type { SessionStatus, CategorySummary }
