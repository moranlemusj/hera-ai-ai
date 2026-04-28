# Hera Agent — frontend

v0 webapp for the Hera Agent. Vite + React + TypeScript + Tailwind + shadcn/ui.

Two routes:
- `/` — Run page: paste a URL or prompt, watch the agent stream events live, get a stitched mp4.
- `/settings` — Hera session (cURL paste) + templates (counts + refresh).

The browser's native `EventSource` only supports GET, so the SSE endpoints
(`POST /run`, `POST /resume/{thread_id}`, `POST /admin/refresh_templates`) are
consumed via a small `fetch()`-based reader in `src/hooks/useEventStream.ts`.

## Run

The backend must be running first. From the repo root:

```bash
conda activate hera-agent
cd backend
HERA_MOCK=1 uvicorn app.main:app --port 8000      # mock mode = no quota burn
```

Then in another terminal:

```bash
cd frontend
npm install        # first time only
npm run dev        # opens http://localhost:5173
```

Vite's dev server proxies `/run`, `/resume/*`, `/video/*`, `/admin/*`, and
`/health` to `http://localhost:8000` (see `vite.config.ts`), so the frontend
code uses relative URLs everywhere.

## Lint + typecheck

```bash
npm run lint       # eslint
npm run build      # tsc -b && vite build (catches type errors)
```

## Layout

```
src/
├─ main.tsx              # entry — BrowserRouter + Layout + routes
├─ index.css             # Tailwind v4 + shadcn theme tokens
├─ pages/
│  ├─ RunPage.tsx        # form, timeline, ShotCards, interrupts, final video
│  └─ SettingsPage.tsx   # composes the two panels
├─ components/
│  ├─ Layout.tsx
│  ├─ AgentTimeline.tsx
│  ├─ ShotCard.tsx
│  ├─ QuotaInterruptDialog.tsx
│  ├─ CurlPasteModal.tsx
│  ├─ HeraSessionPanel.tsx
│  ├─ TemplatesPanel.tsx
│  └─ ui/                # shadcn-generated primitives
├─ hooks/
│  └─ useEventStream.ts  # SSE-over-POST consumer
├─ lib/
│  ├─ api.ts             # tiny typed JSON client for non-streaming endpoints
│  ├─ relativeTime.ts
│  └─ utils.ts           # cn() — shadcn helper
└─ types/
   └─ agent.ts           # mirrors the backend's SSE event shapes
```
