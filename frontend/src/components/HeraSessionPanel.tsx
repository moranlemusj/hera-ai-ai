import { useEffect, useState } from "react"
import { toast } from "sonner"
import { CurlPasteModal } from "@/components/CurlPasteModal"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useFetchedResource } from "@/hooks/useFetchedResource"
import { api, type SessionStatus } from "@/lib/api"
import { formatDurationShort, formatRelativePast } from "@/lib/relativeTime"
import { cn } from "@/lib/utils"

const STATUS_BADGE: Record<SessionStatus["status"], string> = {
  active: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  expiring: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  expired: "bg-destructive/10 text-destructive",
  missing: "bg-muted text-muted-foreground",
}

// Throttle visibility-driven refetches: don't refetch if we already loaded
// within this window.
const FOCUS_REFETCH_MIN_MS = 30_000

export function HeraSessionPanel() {
  const { data: status, loading, refresh } = useFetchedResource(
    api.getHeraSession,
    (err) => toast.error(`Failed to load Hera session: ${err.message}`),
  )
  const [open, setOpen] = useState(false)

  // Refresh when the user comes back to the tab — but only if we haven't
  // refetched recently (avoids hammering the backend on every alt-tab).
  useEffect(() => {
    let lastFetchedAt = Date.now()
    const onVisibilityChange = () => {
      if (document.visibilityState !== "visible") return
      if (Date.now() - lastFetchedAt < FOCUS_REFETCH_MIN_MS) return
      lastFetchedAt = Date.now()
      refresh()
    }
    document.addEventListener("visibilitychange", onVisibilityChange)
    return () => document.removeEventListener("visibilitychange", onVisibilityChange)
  }, [refresh])

  const onSubmit = async (curl: string) => {
    await api.postHeraSession(curl)
    refresh()
    toast.success("Hera session saved")
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Hera session</CardTitle>
          <Button size="sm" onClick={() => setOpen(true)}>
            Update from cURL
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {loading || !status ? (
          <p className="text-muted-foreground">Loading…</p>
        ) : (
          <>
            <div className="flex items-center gap-2">
              <Badge className={cn("uppercase", STATUS_BADGE[status.status])}>
                {status.status}
              </Badge>
              {(status.status === "active" || status.status === "expiring") &&
                status.seconds_until_expiry != null && (
                  <span className="text-muted-foreground">
                    expires in{" "}
                    {formatDurationShort(status.seconds_until_expiry)}
                  </span>
                )}
            </div>
            {status.last_validated && (
              <p className="text-xs text-muted-foreground">
                last validated {formatRelativePast(status.last_validated)}
              </p>
            )}
            {status.status === "missing" && (
              <p className="text-muted-foreground">
                No session stored. Paste a fresh cURL from the Hera dashboard
                to enable template scraping.
              </p>
            )}
          </>
        )}
        <CurlPasteModal
          open={open}
          onOpenChange={setOpen}
          onSubmit={onSubmit}
          title="Update Hera session"
        />
      </CardContent>
    </Card>
  )
}
