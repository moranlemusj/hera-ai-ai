# Hera Agent

Agentic motion-graphics generator on top of [Hera](https://hera.video), built with LangGraph.

See:
- [`docs/PRD.md`](./docs/PRD.md) — staged plan and acceptance criteria
- [`docs/SYSTEM_DESIGN.md`](./docs/SYSTEM_DESIGN.md) — architecture, state, nodes, choices

## Setup

### 1. Conda env (Python + ffmpeg)

```bash
conda env create -f environment.yml
conda activate hera-agent
```

`ffmpeg` is installed inside the env via conda-forge, on PATH.

### 2. Backend deps (uv into the conda env)

```bash
cd backend
uv pip install --system -e .          # or  uv pip install --system -e ".[dev]"
```

`--system` tells uv to install into the active conda env's Python rather than creating its own `.venv`. **Do not run `uv sync`** — that always creates a rogue `.venv/`.

### 3. Environment variables

`backend/.env` is gitignored. Fill in:
- `NEON_DATABASE_URL` — pooled connection string from [console.neon.tech](https://console.neon.tech)
- `HERA_API_KEY` — Hera public REST API key
- `GOOGLE_API_KEY` — from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

Set `HERA_MOCK=1` while developing to avoid burning your monthly Hera quota.

### 4. Neon extensions (one-time)

In the Neon SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
```

### 5. Run

```bash
conda activate hera-agent
cd backend
uvicorn app.main:app --reload --port 8000
```

Hit [http://localhost:8000/health](http://localhost:8000/health) — should return `{"status": "ok", ...}` with both extensions present and migrations applied.
