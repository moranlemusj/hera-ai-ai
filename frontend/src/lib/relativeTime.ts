/** Format a duration (in seconds) as "2d 3h" / "12h 34m" / "5m" / "now". */
export function formatDurationShort(seconds: number | null | undefined): string {
  if (seconds == null) return "—"
  if (seconds <= 0) return "now"
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m`
  return "<1m"
}

/** Time-since helper for ISO timestamps. */
export function formatRelativePast(iso: string | null | undefined): string {
  if (!iso) return "never"
  const ts = Date.parse(iso)
  if (Number.isNaN(ts)) return iso
  const seconds = Math.max(0, (Date.now() - ts) / 1000)
  if (seconds < 60) return "just now"
  return `${formatDurationShort(seconds)} ago`
}
