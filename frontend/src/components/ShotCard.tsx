import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { ShotStatus } from "@/types/agent"

interface ShotCardProps {
  idx: number
  status: ShotStatus
  templateTitle: string | null
  templatePickedReason: string | null
  videoId: string | null
}

const STATUS_STYLES: Record<ShotStatus, string> = {
  planned: "bg-muted text-muted-foreground",
  rendering: "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200",
  ready: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  approved: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  rejected: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  failed: "bg-destructive/10 text-destructive",
}

export function ShotCard({
  idx,
  status,
  templateTitle,
  templatePickedReason,
  videoId,
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
          <Badge className={cn("uppercase tracking-wide", STATUS_STYLES[status])}>
            {status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-2 pt-0 text-sm">
        {templatePickedReason && (
          <p className="text-muted-foreground leading-relaxed">
            {templatePickedReason}
          </p>
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
