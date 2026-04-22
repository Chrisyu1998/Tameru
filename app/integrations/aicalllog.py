"""Audit logger for every Gemini, Claude, and Perplexity call.

Writes one row to `ai_call_log` per API call. Uses the caller's JWT via
`supabase_for_user` and the table's narrow INSERT policy (`WITH CHECK
(user_id = auth.uid())`) — CLAUDE.md invariant 14. Never imports
`supabase_admin`; `tests/test_no_service_role_leak.py` enforces that.

A failed audit INSERT surfaces as `AICallLogError`. We do not swallow it:
an AI call that succeeded but whose log failed is worse than one that
failed loudly, because cost accounting and regression detection (§8.8,
§11) both silently drift otherwise.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from app.db import supabase_for_user


class AICallLogError(Exception):
    """The audit INSERT itself failed. Callers should propagate."""


def log_ai_call(
    user_jwt: str,
    *,
    user_id: UUID,
    provider: Literal["anthropic", "google", "perplexity"],
    model: str,
    task_type: str,
    prompt_version: str,
    prompt_hash: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    success: bool,
    error_code: str | None = None,
) -> None:
    """Insert one ai_call_log row attributed to `user_id`.

    RLS's narrow INSERT policy rejects any row whose `user_id` differs
    from the JWT's `auth.uid()`. Callers must pass the JWT of the user
    the call was made on behalf of.
    """
    client = supabase_for_user(user_jwt)
    payload = {
        "user_id": str(user_id),
        "provider": provider,
        "model": model,
        "task_type": task_type,
        "prompt_version": prompt_version,
        "prompt_hash": prompt_hash,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": latency_ms,
        "success": success,
        "error_code": error_code,
    }
    try:
        client.table("ai_call_log").insert(payload).execute()
    except Exception as exc:
        raise AICallLogError(
            f"ai_call_log insert failed for user={user_id} task={task_type}: {exc}"
        ) from exc
