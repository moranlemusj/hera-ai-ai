import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

const NODE_ORDER = [
  "intake",
  "fetch_article",
  "planner",
  "render_one",
  "poll_one",
  "assemble",
] as const

type NodeName = (typeof NODE_ORDER)[number]

const NODE_LABELS: Record<NodeName, string> = {
  intake: "Intake",
  fetch_article: "Fetch article",
  planner: "Planner",
  render_one: "Render shot",
  poll_one: "Poll shot",
  assemble: "Assemble",
}

interface AgentTimelineProps {
  /** Set of node names that have completed (emitted at least one node_exit). */
  completed: ReadonlySet<string>
  /** The most recent node we've seen — shown as "active" only if not yet completed.
   *  This prevents the render_one ⇄ poll_one loop from pulsing both nodes after they've
   *  each fired at least once. */
  current: string | null
}

export function AgentTimeline({ completed, current }: AgentTimelineProps) {
  return (
    <Card>
      <CardContent className="py-4">
        <ol className="space-y-1.5">
          {NODE_ORDER.map((node) => {
            const isDone = completed.has(node)
            const isCurrent = current === node && !isDone
            const isPending = !isDone && !isCurrent
            return (
              <li
                key={node}
                className={cn(
                  "flex items-center gap-3 text-sm",
                  isPending && "text-muted-foreground/60",
                )}
              >
                <span
                  className={cn(
                    "inline-flex h-2.5 w-2.5 rounded-full transition-colors",
                    isDone && "bg-green-500",
                    isCurrent && "bg-blue-500 animate-pulse",
                    isPending && "bg-muted",
                  )}
                />
                <span className={cn(isDone && "line-through")}>
                  {NODE_LABELS[node]}
                </span>
                {isCurrent && (
                  <span className="text-xs text-muted-foreground">
                    in progress…
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      </CardContent>
    </Card>
  )
}
