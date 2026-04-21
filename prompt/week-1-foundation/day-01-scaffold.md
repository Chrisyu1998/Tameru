# Day 1 — Project scaffold

## Goal

Stand up the empty repo: directory layout, FastAPI hello-world, Supabase project pointer, env config, Dockerfile for Railway, and pre-commit secret scanning. No application logic yet.

## Read first

- `DESIGN.md` §5 (stack), §9.2 (key management), §12 (migrations workflow).
- `CLAUDE.md` (all of it — set the invariants in your head before writing any code).

## Deliverables

- Directory layout:
  - `app/` — FastAPI source (empty `__init__.py`, `main.py` with `GET /healthz` returning `{"ok": true}`)
  - `app/agent/` (empty for now)
  - `app/prompts/` (empty for now)
  - `app/integrations/` (empty for now)
  - `supabase/migrations/` (empty)
  - `evals/` (empty)
  - `frontend/` (empty — Day 8 fills this)
  - `tests/` (empty)
- Files at repo root:
  - `pyproject.toml` with FastAPI, uvicorn, supabase-py, anthropic, google-generativeai, sentry-sdk, pytest
  - `.env.example` listing every key from DESIGN.md §9.2 (no values)
  - `.gitignore` covering `.env`, `__pycache__`, `node_modules`, `dist`, `.venv`
  - `Dockerfile` that runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  - `railway.json` (or Procfile, your call) wiring up the build
  - `README.md` already exists at the repo root with project context, prompt-folder usage, and hard rules. **Do not overwrite it.** Add a "## Local development" section (or update the existing placeholder) with the actual commands once they exist: `pip install -e .`, `uvicorn app.main:app --reload`, `supabase start`, `docker build .`. Keep the rest of the README intact.
  - `.pre-commit-config.yaml` with `gitleaks` enabled
- A Supabase project URL + anon key recorded in `.env.example` as placeholders. The actual values go in `.env` (which is gitignored).

## Don't

- Don't add any tables, auth, or AI integration today. Those are Days 2–4.
- Don't install pre-commit globally; the `.pre-commit-config.yaml` is enough — running it is a per-dev choice.
- Don't write a long README. It will rot.

## Done when

- `uvicorn app.main:app` starts locally, `curl localhost:8000/healthz` returns `{"ok": true}`.
- `docker build .` succeeds.
- `git status` shows no `.env` and no secrets staged.
- `gitleaks detect --source .` exits clean.
