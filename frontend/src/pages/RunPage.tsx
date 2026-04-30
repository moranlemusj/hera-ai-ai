import { useCallback, useEffect, useMemo, useReducer, useState } from "react"
import { useParams } from "react-router-dom"
import { toast } from "sonner"
import { AgentTimeline } from "@/components/AgentTimeline"
import { QuotaInterruptDialog } from "@/components/QuotaInterruptDialog"
import { ShotCard } from "@/components/ShotCard"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { useEventStream } from "@/hooks/useEventStream"
import { cn } from "@/lib/utils"
import type {
  AgentEvent,
  CriticDiagnosis,
  ShotStatus,
  StrategistStrategy,
} from "@/types/agent"

// Shape returned by GET /runs/{thread_id} — mirrors what the backend reads
// out of the LangGraph checkpoint.
interface RunSnapshot {
  thread_id: string
  run_id: string | null
  shot_list: PersistedShot[]
  coherence_diagnoses: PersistedCoherenceDiagnosis[]
  replans: number
  final_video_url: string | null
  error: string | null
}

interface PersistedAttempt {
  strategy?: StrategistStrategy
  rationale?: string
  prompt?: string
  template_id?: string | null
  video_id?: string | null
  score?: number
  diagnosis?: CriticDiagnosis
}

interface PersistedShot {
  idx: number
  status: ShotStatus
  template_title?: string | null
  template_picked_reason?: string | null
  video_id?: string | null
  score?: number | null
  diagnosis?: CriticDiagnosis | null
  attempts?: PersistedAttempt[]
}

interface PersistedCoherenceDiagnosis {
  after_idx: number
  coherent: boolean
  reason: string
  suggested_edits?: unknown[]
}

interface AttemptTrace {
  attempt: number
  strategy: StrategistStrategy
  rationale: string
}

interface ShotState {
  idx: number
  status: ShotStatus
  templateTitle: string | null
  templatePickedReason: string | null
  videoId: string | null
  score: number | null
  diagnosis: CriticDiagnosis | null
  attempts: AttemptTrace[]
}

interface CoherenceTrace {
  after_idx: number
  coherent: boolean
  reason: string
  suggested_edits_count: number
}

interface QuotaInterrupt {
  reason: string
  current_count: number
  current_cap: number
}

interface RunState {
  threadId: string | null
  shots: Map<number, ShotState>
  completedNodes: Set<string>
  currentNode: string | null
  errors: string[]
  finalVideoUrl: string | null
  quotaInterrupt: QuotaInterrupt | null
  coherence: CoherenceTrace[]
  replans: number
}

type RunAction =
  | { type: "reset" }
  | { type: "thread_id"; value: string }
  | { type: "event"; event: AgentEvent }
  | { type: "clear_quota_interrupt" }
  | { type: "populate_from_snapshot"; snapshot: RunSnapshot }

// Names of every node in the v1 graph; the timeline uses this set to render
// the progression bar. Keep in sync with backend graph/build.py.
const ALL_NODES = [
  "intake",
  "fetch_article",
  "planner",
  "render_one",
  "poll_one",
  "critic",
  "strategist",
  "coherence_check",
  "replanner",
  "assemble",
] as const

function snapshotToRunState(snapshot: RunSnapshot): RunState {
  const shots = new Map<number, ShotState>()
  for (const s of snapshot.shot_list) {
    const attempts: AttemptTrace[] = (s.attempts ?? []).map((a, i) => ({
      attempt: i + 1,
      strategy: a.strategy ?? "initial",
      rationale: a.rationale ?? "",
    }))
    shots.set(s.idx, {
      idx: s.idx,
      status: s.status,
      templateTitle: s.template_title ?? null,
      templatePickedReason: s.template_picked_reason ?? null,
      videoId: s.video_id ?? null,
      score: s.score ?? null,
      diagnosis: s.diagnosis ?? null,
      attempts,
    })
  }
  const coherence = snapshot.coherence_diagnoses.map((c) => ({
    after_idx: c.after_idx,
    coherent: c.coherent,
    reason: c.reason,
    suggested_edits_count: (c.suggested_edits ?? []).length,
  }))
  return {
    threadId: snapshot.thread_id,
    shots,
    completedNodes: new Set(ALL_NODES),
    currentNode: null,
    errors: snapshot.error ? [snapshot.error] : [],
    finalVideoUrl: snapshot.final_video_url,
    quotaInterrupt: null,
    coherence,
    replans: snapshot.replans,
  }
}

const initialState: RunState = {
  threadId: null,
  shots: new Map(),
  completedNodes: new Set(),
  currentNode: null,
  errors: [],
  finalVideoUrl: null,
  quotaInterrupt: null,
  coherence: [],
  replans: 0,
}

function reducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "reset":
      return initialState
    case "thread_id":
      return { ...state, threadId: action.value }
    case "clear_quota_interrupt":
      return { ...state, quotaInterrupt: null }
    case "populate_from_snapshot":
      return snapshotToRunState(action.snapshot)
    case "event": {
      const ev = action.event
      switch (ev.type) {
        case "node_exit": {
          const completedNodes = new Set(state.completedNodes)
          completedNodes.add(ev.node)
          return { ...state, completedNodes, currentNode: ev.node }
        }
        case "shot_status": {
          const shots = new Map(state.shots)
          const prior = shots.get(ev.idx)
          shots.set(ev.idx, {
            idx: ev.idx,
            status: ev.status,
            templateTitle: ev.template_title,
            templatePickedReason: ev.template_picked_reason,
            videoId: ev.video_id,
            // Score/diagnosis ride along on shot_status updates from the critic.
            score: ev.score ?? prior?.score ?? null,
            diagnosis: ev.diagnosis ?? prior?.diagnosis ?? null,
            // Preserve the appended attempts trail across status updates;
            // the actual append happens in critic_diagnosis / strategist_decision.
            attempts: prior?.attempts ?? [],
          })
          return { ...state, shots }
        }
        case "critic_diagnosis": {
          const shots = new Map(state.shots)
          const prior = shots.get(ev.idx)
          if (!prior) return state
          shots.set(ev.idx, {
            ...prior,
            score: ev.score,
            diagnosis: ev.diagnosis,
          })
          return { ...state, shots }
        }
        case "strategist_decision": {
          const shots = new Map(state.shots)
          const prior = shots.get(ev.idx)
          if (!prior) return state
          shots.set(ev.idx, {
            ...prior,
            attempts: [
              ...prior.attempts,
              {
                attempt: ev.attempt,
                strategy: ev.strategy,
                rationale: ev.rationale,
              },
            ],
          })
          return { ...state, shots }
        }
        case "coherence_diagnosis":
          return {
            ...state,
            coherence: [
              ...state.coherence,
              {
                after_idx: ev.after_idx,
                coherent: ev.coherent,
                reason: ev.reason,
                suggested_edits_count: ev.suggested_edits_count,
              },
            ],
          }
        case "replan_applied":
          return { ...state, replans: ev.replans_total }
        case "log":
          return ev.level === "error"
            ? { ...state, errors: [...state.errors, ev.message] }
            : state
        case "interrupt":
          if (ev.kind === "hera_quota_exhausted") {
            const p = ev.payload as Partial<QuotaInterrupt>
            return {
              ...state,
              quotaInterrupt: {
                reason: p.reason ?? "quota exhausted",
                current_count: p.current_count ?? 0,
                current_cap: p.current_cap ?? 0,
              },
            }
          }
          return state
        case "done":
          return { ...state, finalVideoUrl: ev.final_video_url }
      }
    }
  }
}

export function RunPage() {
  const { threadId: replayThreadId } = useParams<{ threadId: string }>()
  const isReplay = Boolean(replayThreadId)
  const [userPrompt, setUserPrompt] = useState("")
  const [sourceUrl, setSourceUrl] = useState("")
  const [run, dispatch] = useReducer(reducer, initialState)
  const [replayLoading, setReplayLoading] = useState(false)
  const [replayError, setReplayError] = useState<string | null>(null)

  // Replay mode: fetch the persisted state and pre-fill the reducer once.
  useEffect(() => {
    if (!replayThreadId) return
    let cancelled = false
    // eslint-disable-next-line react-hooks/set-state-in-effect -- we genuinely need to enter loading state when the fetch kicks off
    setReplayLoading(true)
    setReplayError(null)
    fetch(`/api/runs/${encodeURIComponent(replayThreadId)}`)
      .then(async (resp) => {
        if (!resp.ok) {
          const text = await resp.text()
          throw new Error(`HTTP ${resp.status}: ${text}`)
        }
        return (await resp.json()) as RunSnapshot
      })
      .then((snapshot) => {
        if (cancelled) return
        dispatch({ type: "populate_from_snapshot", snapshot })
      })
      .catch((err: Error) => {
        if (cancelled) return
        setReplayError(err.message)
        toast.error(`Failed to load run: ${err.message}`)
      })
      .finally(() => {
        if (!cancelled) setReplayLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [replayThreadId])

  const onEvent = useCallback((ev: AgentEvent) => {
    dispatch({ type: "event", event: ev })
    // Side effects (toasts) live outside the reducer — pure state in, side
    // effects out.
    if (ev.type === "log" && ev.level === "error") toast.error(ev.message)
    if (ev.type === "done") toast.success("Run complete!")
    if (ev.type === "interrupt" && ev.kind !== "hera_quota_exhausted") {
      toast.warning(`Unhandled interrupt: ${ev.kind}`)
    }
  }, [])

  const onHeaders = useCallback((headers: Headers) => {
    const tid = headers.get("x-thread-id")
    if (tid) dispatch({ type: "thread_id", value: tid })
  }, [])

  const stream = useEventStream<AgentEvent>({ onEvent, onHeaders })

  const start = () => {
    dispatch({ type: "reset" })
    const body: { user_prompt?: string; source_url?: string } = {}
    if (userPrompt.trim()) body.user_prompt = userPrompt.trim()
    if (sourceUrl.trim()) body.source_url = sourceUrl.trim()
    stream.start({ method: "POST", url: "/run", body })
  }

  const handleQuotaResume = (newCap: number | null) => {
    if (!run.threadId) {
      dispatch({ type: "clear_quota_interrupt" })
      return
    }
    const body: { new_cap?: number } = {}
    if (newCap != null) body.new_cap = newCap
    dispatch({ type: "clear_quota_interrupt" })
    stream.start({ method: "POST", url: `/resume/${run.threadId}`, body })
  }

  const handleQuotaCancel = () => {
    dispatch({ type: "clear_quota_interrupt" })
    stream.abort()
  }

  const sortedShots = useMemo(
    () => Array.from(run.shots.values()).sort((a, b) => a.idx - b.idx),
    [run.shots],
  )

  const inputValid = userPrompt.trim().length > 0 || sourceUrl.trim().length > 0
  const streaming = stream.state === "streaming"

  return (
    <div className="space-y-6">
      {isReplay ? (
        <Card>
          <CardHeader>
            <CardTitle>Replaying past run</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <p className="text-xs text-muted-foreground font-mono">
              thread: {replayThreadId}
            </p>
            {replayLoading && (
              <p className="text-sm text-muted-foreground">Loading snapshot…</p>
            )}
            {replayError && (
              <p className="text-destructive text-sm">{replayError}</p>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Generate a motion graphic</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="prompt">Prompt (optional)</Label>
              <Textarea
                id="prompt"
                placeholder="Make a 30s explainer about quarterly revenue trends, focus on the YoY growth..."
                value={userPrompt}
                onChange={(e) => setUserPrompt(e.target.value)}
                rows={3}
                disabled={streaming}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="url">Source URL (optional)</Label>
              <Input
                id="url"
                type="url"
                placeholder="https://www.bloomberg.com/..."
                value={sourceUrl}
                onChange={(e) => setSourceUrl(e.target.value)}
                disabled={streaming}
              />
              <p className="text-xs text-muted-foreground">
                Provide either a prompt, a URL, or both (the prompt acts as a lens
                on the article).
              </p>
            </div>
            <div className="flex gap-2">
              <Button onClick={start} disabled={!inputValid || streaming}>
                {streaming ? "Running…" : "Run agent"}
              </Button>
              {streaming && (
                <Button variant="outline" onClick={() => stream.abort()}>
                  Cancel
                </Button>
              )}
            </div>
            {run.threadId && (
              <p className="text-xs text-muted-foreground font-mono">
                thread: {run.threadId}
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {(streaming || run.completedNodes.size > 0) && (
        <>
          <Separator />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-[260px_1fr]">
            <AgentTimeline
              completed={run.completedNodes}
              current={run.currentNode}
            />
            <div className="space-y-3">
              {sortedShots.length === 0 ? (
                <p className="text-sm text-muted-foreground italic">
                  Waiting for the planner to produce a shot list…
                </p>
              ) : (
                sortedShots.map((shot) => (
                  <ShotCard
                    key={shot.idx}
                    idx={shot.idx}
                    status={shot.status}
                    templateTitle={shot.templateTitle}
                    templatePickedReason={shot.templatePickedReason}
                    videoId={shot.videoId}
                    score={shot.score}
                    diagnosis={shot.diagnosis}
                    attempts={shot.attempts}
                  />
                ))
              )}
            </div>
          </div>
        </>
      )}

      {run.coherence.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Coherence checks</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            {run.coherence.map((c, i) => (
              <div
                key={i}
                className="flex items-start justify-between gap-3 rounded-md border bg-card px-3 py-1.5"
              >
                <div className="flex-1">
                  <p className="text-xs">
                    <span className="text-muted-foreground">After shot {c.after_idx + 1}:</span>{" "}
                    {c.reason}
                  </p>
                </div>
                <Badge
                  className={cn(
                    "shrink-0 text-xs",
                    c.coherent
                      ? "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200"
                      : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
                  )}
                >
                  {c.coherent
                    ? "coherent"
                    : `replanning (${c.suggested_edits_count} edit${c.suggested_edits_count === 1 ? "" : "s"})`}
                </Badge>
              </div>
            ))}
            {run.replans > 0 && (
              <p className="pt-1 text-xs text-muted-foreground">
                {run.replans} replan{run.replans === 1 ? "" : "s"} applied
              </p>
            )}
          </CardContent>
        </Card>
      )}

      {run.errors.length > 0 && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle className="text-destructive text-sm">Errors</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {run.errors.map((e, i) => (
              <p key={i} className="font-mono text-xs">
                {e}
              </p>
            ))}
          </CardContent>
        </Card>
      )}

      {run.finalVideoUrl && (
        <Card>
          <CardHeader>
            <CardTitle>Final video</CardTitle>
          </CardHeader>
          <CardContent>
            <video
              src={run.finalVideoUrl}
              controls
              className="w-full rounded-md border"
            />
            <p className="mt-2 text-xs text-muted-foreground font-mono">
              {run.finalVideoUrl}
            </p>
          </CardContent>
        </Card>
      )}

      {run.quotaInterrupt && (
        <QuotaInterruptDialog
          open
          currentCount={run.quotaInterrupt.current_count}
          currentCap={run.quotaInterrupt.current_cap}
          reason={run.quotaInterrupt.reason}
          onCancel={handleQuotaCancel}
          onResume={handleQuotaResume}
        />
      )}
    </div>
  )
}
