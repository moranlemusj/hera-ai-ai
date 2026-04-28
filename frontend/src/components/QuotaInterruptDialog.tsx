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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface QuotaInterruptDialogProps {
  open: boolean
  currentCount: number
  currentCap: number
  reason: string
  onCancel: () => void
  onResume: (newCap: number | null) => void
}

export function QuotaInterruptDialog({
  open,
  currentCount,
  currentCap,
  reason,
  onCancel,
  onResume,
}: QuotaInterruptDialogProps) {
  const [newCap, setNewCap] = useState(String(currentCap + 50))

  function handleResume() {
    const parsed = parseInt(newCap, 10)
    onResume(Number.isFinite(parsed) && parsed > currentCap ? parsed : null)
  }

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onCancel()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Hera quota reached</DialogTitle>
          <DialogDescription>
            {reason} ({currentCount} / {currentCap}). The agent is paused — confirm
            below to continue.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="new-cap" className="text-sm">
            Raise the in-process cap to
          </Label>
          <Input
            id="new-cap"
            type="number"
            value={newCap}
            onChange={(e) => setNewCap(e.target.value)}
            min={currentCap + 1}
          />
          <p className="text-xs text-muted-foreground">
            Leave at the default to lift the cap for this run, or set equal to the
            current cap to just retry without raising.
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onCancel}>
            Cancel run
          </Button>
          <Button onClick={handleResume}>Continue</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
