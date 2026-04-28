/**
 * Generic SSE-over-POST consumer (the browser's EventSource only supports GET).
 *
 * Pass a URL + optional JSON body; each `data: {...}` line is JSON.parsed and
 * yielded to onEvent. Lines starting with `:` are SSE comments (keepalives)
 * and are silently dropped.
 *
 * Returns the run state machine + an `abort()` you can call from the UI to
 * stop the stream early. Cleans up on unmount automatically.
 */

import { useCallback, useEffect, useRef, useState } from "react"

interface UseEventStreamOptions<E> {
  onEvent: (ev: E) => void
  onHeaders?: (headers: Headers) => void
  onError?: (err: Error) => void
  onDone?: () => void
}

export type StreamState = "idle" | "streaming" | "done" | "error"

export interface StreamHandle {
  state: StreamState
  error: Error | null
  start: (req: { method: "POST" | "GET"; url: string; body?: unknown }) => void
  abort: () => void
}

export function useEventStream<E>(opts: UseEventStreamOptions<E>): StreamHandle {
  const [state, setState] = useState<StreamState>("idle")
  const [error, setError] = useState<Error | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  // Stash the latest options in a ref so callbacks below see the freshest
  // handlers without invalidating their identity. Updated post-render in an
  // effect (refs must not be mutated during render).
  const optsRef = useRef(opts)
  useEffect(() => {
    optsRef.current = opts
  })

  const abort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const start = useCallback(
    ({
      method,
      url,
      body,
    }: {
      method: "POST" | "GET"
      url: string
      body?: unknown
    }) => {
      abort() // any prior stream is dead to us
      const ctrl = new AbortController()
      abortRef.current = ctrl
      setError(null)
      setState("streaming")

      void (async () => {
        try {
          const resp = await fetch(url, {
            method,
            signal: ctrl.signal,
            headers:
              body !== undefined ? { "content-type": "application/json" } : undefined,
            body: body !== undefined ? JSON.stringify(body) : undefined,
          })

          if (!resp.ok) {
            const detail = await resp.text()
            throw new Error(
              `${resp.status} ${resp.statusText}${detail ? `: ${detail.slice(0, 200)}` : ""}`,
            )
          }
          optsRef.current.onHeaders?.(resp.headers)
          if (!resp.body) throw new Error("Response has no body")

          const reader = resp.body.getReader()
          const decoder = new TextDecoder("utf-8")
          let buffer = ""

          // SSE per spec allows any of \r\n, \n, or \r as line endings; in
          // practice servers (incl. sse-starlette) emit \r\n\r\n between
          // frames. Match either form.
          const FRAME_SEP = /\r?\n\r?\n/
          const LINE_SEP = /\r?\n/
          while (true) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })

            let match: RegExpExecArray | null
            while ((match = FRAME_SEP.exec(buffer))) {
              const frame = buffer.slice(0, match.index)
              buffer = buffer.slice(match.index + match[0].length)
              for (const line of frame.split(LINE_SEP)) {
                if (!line || line.startsWith(":")) continue // keepalive
                if (line.startsWith("data: ")) {
                  const payload = line.slice(6)
                  try {
                    const parsed = JSON.parse(payload) as E
                    optsRef.current.onEvent(parsed)
                  } catch (parseErr) {
                    console.warn("SSE parse error", parseErr, payload)
                  }
                }
              }
            }
          }
          setState("done")
          optsRef.current.onDone?.()
        } catch (err) {
          // AbortError from .abort() is expected, not an error condition.
          if (err instanceof DOMException && err.name === "AbortError") {
            setState("idle")
            return
          }
          const e = err instanceof Error ? err : new Error(String(err))
          setError(e)
          setState("error")
          optsRef.current.onError?.(e)
        }
      })()
    },
    [abort],
  )

  // Auto-cleanup on unmount.
  useEffect(() => {
    return () => abort()
  }, [abort])

  return { state, error, start, abort }
}
