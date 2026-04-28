/**
 * Tiny fetch-on-mount + manual-refresh hook.
 *
 * Wraps the load/cancel/refetch pattern shared by HeraSessionPanel and
 * TemplatesPanel. Loading is derived (`data === null`), so the eslint
 * `set-state-in-effect` rule has nothing to complain about and we don't carry
 * a redundant boolean.
 */

import { useCallback, useEffect, useState } from "react"

interface UseFetchedResource<T> {
  data: T | null
  error: Error | null
  loading: boolean
  refresh: () => void
}

export function useFetchedResource<T>(
  loader: () => Promise<T>,
  onError?: (err: Error) => void,
): UseFetchedResource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [tick, setTick] = useState(0)

  const refresh = useCallback(() => {
    setTick((n) => n + 1)
  }, [])

  useEffect(() => {
    let cancelled = false
    loader()
      .then((d) => {
        if (cancelled) return
        setData(d)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const e = err instanceof Error ? err : new Error(String(err))
        setError(e)
        onError?.(e)
      })
    return () => {
      cancelled = true
    }
    // We deliberately depend only on `tick` — passing `loader` as a dep would
    // require every caller to wrap it in useCallback. Instead, calling
    // refresh() is the contract for "the loader's behavior changed."
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick])

  return { data, error, loading: data === null && error === null, refresh }
}
