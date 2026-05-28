#!/usr/bin/env bash
# Local Playwright E2E runner against deployed prod.
#
# Mints a fresh E2E user via `scripts/e2e_user.py create`, runs the
# `playwright.deployed.config.ts` suite against the live Vercel + Railway
# + prod Supabase stack, and tears the user down on the way out — even
# if Playwright fails or the script is interrupted (Ctrl-C).
#
# Usage:
#   ./scripts/e2e_local.sh                # run all 5 specs
#   ./scripts/e2e_local.sh 02-chat-log-transaction.spec.ts    # run one
#   ./scripts/e2e_local.sh --headed       # run with browser visible
#
# Any args after the script name are forwarded verbatim to
# `npx playwright test`.
#
# Cost per run: ~$0.10 in Anthropic + Gemini calls. Writes ~12
# ephemeral rows to prod Supabase that are CASCADE-deleted at teardown.
# `ai_call_log` rows from the run are retained (audit trail).
#
# Required entries in repo-root `.env`:
#   SMOKE_SUPABASE_URL                  prod Supabase URL
#   SMOKE_SUPABASE_ANON_KEY             prod anon key (RLS-scoped)
#   SMOKE_SUPABASE_SERVICE_ROLE_KEY     prod service-role key (admin scope)
#
# Optional env overrides (sensible defaults if unset):
#   E2E_BASE_URL    — deployed frontend (default: https://tameru-seven.vercel.app)

set -euo pipefail

# Resolve repo root from this script's location so the runner works
# regardless of CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# Activate the project venv if it exists. Skip silently when running
# from a shell that already has the right Python on PATH (CI doesn't
# use this script, so this is just dev-ergonomic).
if [[ -f .venv/bin/activate ]]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Sanity-check the toolchain before we mint a real user.
command -v python >/dev/null || {
    echo "✗ python not on PATH" >&2
    exit 2
}
command -v npm >/dev/null || {
    echo "✗ npm not on PATH" >&2
    exit 2
}

CREDS_FILE="$(mktemp -t e2e_creds.XXXXXX)"

# Cleanup runs on any exit path — success, failure, or interrupt.
# Captures the user_id from the creds file (already sourced) and
# `e2e_user.py delete` is itself idempotent on 404, so a partially-
# failed mint won't crash teardown.
cleanup() {
    local exit_code=$?
    set +e
    if [[ -n "${E2E_USER_ID:-}" ]]; then
        python scripts/e2e_user.py delete --user-id="$E2E_USER_ID"
    fi
    rm -f "$CREDS_FILE"
    exit "$exit_code"
}
trap cleanup EXIT INT TERM

echo "→ minting E2E user"
python scripts/e2e_user.py create > "$CREDS_FILE"
# shellcheck disable=SC1090
set -a
source "$CREDS_FILE"
set +a

# Pull the anon key out of .env into VITE_SUPABASE_ANON_KEY for the
# Playwright helper (which fetches Supabase REST `/auth/v1/token`).
# Strips surrounding quotes if any. We use grep+cut rather than a full
# dotenv parser because Tameru's .env is a plain KEY=VALUE format.
VITE_SUPABASE_ANON_KEY="$(
    grep -E '^SMOKE_SUPABASE_ANON_KEY=' .env \
        | cut -d= -f2- \
        | sed -E 's/^["'\'']//; s/["'\'']$//'
)"
if [[ -z "$VITE_SUPABASE_ANON_KEY" ]]; then
    echo "✗ SMOKE_SUPABASE_ANON_KEY not found in .env" >&2
    exit 2
fi
export VITE_SUPABASE_ANON_KEY

# Other env the Playwright config + helpers need. SMOKE_SUPABASE_URL
# is read from .env automatically by sourcing the dotenv (skipped here
# — we read it manually for clarity).
export VITE_SUPABASE_URL="$(
    grep -E '^SMOKE_SUPABASE_URL=' .env \
        | cut -d= -f2- \
        | sed -E 's/^["'\'']//; s/["'\'']$//'
)"
export E2E_BASE_URL="${E2E_BASE_URL:-https://tameru-seven.vercel.app}"

echo "→ running playwright against $E2E_BASE_URL"
( cd frontend && npx playwright test --config=playwright.deployed.config.ts "$@" )
# trap fires here; no manual exit needed.
