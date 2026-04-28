import { HeraSessionPanel } from "@/components/HeraSessionPanel"
import { TemplatesPanel } from "@/components/TemplatesPanel"

export function SettingsPage() {
  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold">Settings</h2>
      <HeraSessionPanel />
      <TemplatesPanel />
    </div>
  )
}
