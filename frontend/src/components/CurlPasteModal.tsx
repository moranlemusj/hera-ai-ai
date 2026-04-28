import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"

interface CurlPasteModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Submit handler. Throw to display the error to the user; resolve to close. */
  onSubmit: (curl: string) => Promise<void>
  title?: string
  description?: string
}

export function CurlPasteModal({
  open,
  onOpenChange,
  onSubmit,
  title = "Update Hera session",
  description,
}: CurlPasteModalProps) {
  const [curl, setCurl] = useState("")
  const [submitting, setSubmitting] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)

  const valid = curl.trim().length >= 10

  async function handleSubmit() {
    if (!valid) return
    setSubmitting(true)
    setErrMsg(null)
    try {
      await onSubmit(curl)
      setCurl("")
      onOpenChange(false)
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {description ?? (
              <>
                Open <code>app.hera.video</code>, log in, then DevTools → Network →
                right-click any request → <strong>Copy as cURL</strong>. Paste below.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={curl}
          onChange={(e) => setCurl(e.target.value)}
          placeholder="curl 'https://app.hera.video/api/templates?...' -H '...' -b '...'"
          rows={10}
          className="font-mono text-xs"
        />
        {errMsg && (
          <p className="text-sm text-destructive">{errMsg}</p>
        )}
        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!valid || submitting}>
            {submitting ? "Validating…" : "Validate & save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
