import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { CriticDiagnosis, ShotStatus, StrategistStrategy } from "@/types/agent"

interface AttemptTrace {
  strategy: StrategistStrategy
  rationale: string
  attempt: number
}

interface ShotCardProps {
  idx: number
  status: ShotStatus
  templateTitle: string | null
  templatePickedReason: string | null
  videoId: string | null
  // v1 — critic + strategist
  score: number | null
  diagnosis: CriticDiagnosis | null
  attempts: AttemptTrace[]
}

const STATUS_STYLES: Record<ShotStatus, string> = {
  planned: "bg-muted text-muted-foreground",
  rendering: "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200",
  ready: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  approved: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  rejected: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  failed: "bg-destructive/10 text-destructive",
}

const STRATEGY_LABELS: Record<StrategistStrategy, string> = {
  rewrite_prompt: "rewrote prompt",
  switch_template: "switched template",
  revise_via_parent: "revised via parent",
  accept: "accepted",
  escalate: "escalated",
  initial: "initial render",
}

function ScoreBadge({ score }: { score: number }) {
  // Match the backend's ACCEPT_THRESHOLD of 0.7.
  const passing = score >= 0.7
  return (
    <Badge
      className={cn(
        "font-mono text-xs",
        passing
          ? "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200"
          : "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
      )}
    >
      score {score.toFixed(2)}
    </Badge>
  )
}

export function ShotCard({
  idx,
  status,
  templateTitle,
  templatePickedReason,
  videoId,
  score,
  diagnosis,
  attempts,
}: ShotCardProps) {
  const usingPromptOnly = templateTitle == null
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm font-medium">
            <span className="text-muted-foreground">Shot {idx + 1} —</span>{" "}
            {usingPromptOnly ? (
              <span className="italic">prompt only (no template)</span>
            ) : (
              templateTitle
            )}
          </CardTitle>
          <div className="flex items-center gap-2">
            {score != null && <ScoreBadge score={score} />}
            <Badge className={cn("uppercase tracking-wide", STATUS_STYLES[status])}>
              {status}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0 text-sm">
        {templatePickedReason && (
          <p className="text-muted-foreground leading-relaxed">
            {templatePickedReason}
          </p>
        )}
        {diagnosis && (
          <div className="space-y-1 rounded-md bg-muted/40 px-3 py-2">
            <p className="text-xs font-semibold text-muted-foreground">
              critic notes
            </p>
            <p className="text-xs leading-relaxed">{diagnosis.notes}</p>
          </div>
        )}
        {attempts.length > 1 && (
          <div className="space-y-0.5">
            <p className="text-xs font-semibold text-muted-foreground">
              attempts ({attempts.length})
            </p>
            <ol className="space-y-0.5 text-xs">
              {attempts.map((a) => (
                <li key={a.attempt} className="flex gap-2">
                  <span className="font-mono text-muted-foreground">
                    #{a.attempt}
                  </span>
                  <span className="font-medium">
                    {STRATEGY_LABELS[a.strategy]}
                  </span>
                  {a.rationale && (
                    <span className="text-muted-foreground">— {a.rationale}</span>
                  )}
                </li>
              ))}
            </ol>
          </div>
        )}
        {videoId && (
          <p className="text-xs text-muted-foreground font-mono">
            video_id: {videoId}
          </p>
        )}
      </CardContent>
    </Card>
  )
}
