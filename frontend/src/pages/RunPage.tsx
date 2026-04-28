import { useCallback, useMemo, useReducer, useState } from "react"
import { toast } from "sonner"
import { AgentTimeline } from "@/components/AgentTimeline"
import { QuotaInterruptDialog } from "@/components/QuotaInterruptDialog"
import { ShotCard } from "@/components/ShotCard"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import { Textarea } from "@/components/ui/textarea"
import { useEventStream } from "@/hooks/useEventStream"
import type { AgentEvent, ShotStatus } from "@/types/agent"

interface ShotState {
  idx: number
  status: ShotStatus
  templateTitle: string | null
  templatePickedReason: string | null
  videoId: string | null
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
}

type RunAction =
  | { type: "reset" }
  | { type: "thread_id"; value: string }
  | { type: "event"; event: AgentEvent }
  | { type: "clear_quota_interrupt" }

const initialState: RunState = {
  threadId: null,
  shots: new Map(),
  completedNodes: new Set(),
  currentNode: null,
  errors: [],
  finalVideoUrl: null,
  quotaInterrupt: null,
}

function reducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case "reset":
      return initialState
    case "thread_id":
      return { ...state, threadId: action.value }
    case "clear_quota_interrupt":
      return { ...state, quotaInterrupt: null }
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
          shots.set(ev.idx, {
            idx: ev.idx,
            status: ev.status,
            templateTitle: ev.template_title,
            templatePickedReason: ev.template_picked_reason,
            videoId: ev.video_id,
          })
          return { ...state, shots }
        }
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
  const [userPrompt, setUserPrompt] = useState("")
  const [sourceUrl, setSourceUrl] = useState("")
  const [run, dispatch] = useReducer(reducer, initialState)

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
                  />
                ))
              )}
            </div>
          </div>
        </>
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
