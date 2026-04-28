import { useCallback, useState } from "react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Skeleton } from "@/components/ui/skeleton"
import { useEventStream } from "@/hooks/useEventStream"
import { useFetchedResource } from "@/hooks/useFetchedResource"
import { api, type CategorySummary } from "@/lib/api"
import { formatRelativePast } from "@/lib/relativeTime"
import type { ScrapeEvent } from "@/types/agent"

interface ProgressState {
  category: string
  page: number
  count: number
  inserted: number
  updated: number
}

export function TemplatesPanel() {
  const { data: summary, loading, refresh } = useFetchedResource(
    api.getTemplatesSummary,
    (err) => toast.error(`Failed to load templates: ${err.message}`),
  )
  const [progress, setProgress] = useState<ProgressState | null>(null)
  const [scraping, setScraping] = useState<string | null>(null) // "all" | <category>

  const onEvent = useCallback((ev: ScrapeEvent) => {
    if (ev.type === "progress") {
      setProgress({
        category: ev.category,
        page: ev.page,
        count: ev.count,
        inserted: ev.inserted,
        updated: ev.updated,
      })
      if (ev.error) {
        toast.error(`Scrape error in ${ev.category}: ${ev.error}`)
      }
    } else if (ev.type === "done") {
      const s = ev.summary
      toast.success(
        `Scrape complete: ${s.templates_seen} seen, ${s.inserted} new, ${s.updated} updated`,
      )
    } else if (ev.type === "error") {
      toast.error(`${ev.code}: ${ev.message}`)
    }
  }, [])

  const stream = useEventStream<ScrapeEvent>({
    onEvent,
    onDone: () => {
      setScraping(null)
      setProgress(null)
      refresh()
    },
    onError: () => {
      setScraping(null)
      setProgress(null)
    },
  })

  const startScrape = (category: string | null) => {
    setScraping(category ?? "all")
    setProgress(null)
    stream.start({
      method: "POST",
      url: "/admin/refresh_templates",
      body: { category },
    })
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Templates</CardTitle>
          <Button
            size="sm"
            onClick={() => startScrape(null)}
            disabled={scraping !== null}
          >
            {scraping === "all" ? "Scraping all…" : "Refresh all"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {progress && (
          <div className="space-y-1">
            <div className="flex justify-between text-xs text-muted-foreground">
              <span>
                Scraping {progress.category} · page {progress.page} ·{" "}
                {progress.count} records ({progress.inserted} new,{" "}
                {progress.updated} updated)
              </span>
            </div>
            <Progress value={Math.min(100, progress.page * 10)} />
          </div>
        )}

        {loading || !summary ? (
          <div className="space-y-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-9 w-full" />
            ))}
          </div>
        ) : (
          <>
            <div className="text-sm">
              <strong className="text-foreground">{summary.active}</strong>{" "}
              <span className="text-muted-foreground">
                active templates ({summary.total} total)
              </span>
              {summary.last_seen && (
                <span className="text-muted-foreground">
                  {" "}
                  · last seen {formatRelativePast(summary.last_seen)}
                </span>
              )}
            </div>
            <div className="space-y-1">
              {summary.per_category.length === 0 ? (
                <p className="text-muted-foreground italic">
                  No templates yet — run a scrape to populate the catalog.
                </p>
              ) : (
                summary.per_category.map((cat) => (
                  <CategoryRow
                    key={cat.category}
                    cat={cat}
                    onRefresh={() => startScrape(cat.category)}
                    disabled={scraping !== null}
                    scraping={scraping === cat.category}
                  />
                ))
              )}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}

function CategoryRow({
  cat,
  onRefresh,
  disabled,
  scraping,
}: {
  cat: CategorySummary
  onRefresh: () => void
  disabled: boolean
  scraping: boolean
}) {
  return (
    <div className="flex items-center justify-between rounded-md border bg-card px-3 py-2">
      <div className="flex flex-col">
        <span className="font-medium">{cat.category}</span>
        <span className="text-xs text-muted-foreground">
          {cat.active} active
          {cat.stale > 0 && `, ${cat.stale} stale`}
          {cat.missing_embedding > 0 &&
            `, ${cat.missing_embedding} missing embedding`}
          {cat.last_seen && ` · ${formatRelativePast(cat.last_seen)}`}
        </span>
      </div>
      <Button
        size="sm"
        variant="ghost"
        onClick={onRefresh}
        disabled={disabled}
      >
        {scraping ? "Scraping…" : "Refresh"}
      </Button>
    </div>
  )
}
