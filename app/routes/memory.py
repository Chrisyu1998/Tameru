"""User-memory REST endpoints — Day 16 (DESIGN.md §7.6 layer 2).

Three operations:

  * `GET /memory`              — paginated read of the user's facts,
                                  ordered the same way the chat prompt
                                  renders them (relevance DESC, then
                                  reinforced_at DESC).
  * `PATCH /memory/{id}`       — edit fact text or relevance_score.
                                  Manual edits count as reinforcement
                                  per §7.6 — `reinforced_at` is bumped
                                  to now() so Day 17's 90-day time-decay
                                  sweep does not prune what the user
                                  just curated.
  * `DELETE /memory/{id}`      — hard delete. If a future distillation
                                  re-extracts the same fact text, the
                                  row reappears with a new id (and the
                                  unique-index upsert path is what makes
                                  that re-creation safe).

All three endpoints run with the user's JWT under user_memory's
`FOR ALL` RLS policy. No service role; tests in
`tests/contracts/test_no_service_role_leak.py` would flag a regression.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthedUser, get_current_user_with_device
from app.db import supabase_for_user

router = APIRouter(prefix="/memory", tags=["memory"])

# Mirror the chat prompt's render cap (DESIGN.md §7.6 — 60 facts max).
# Pagination still allowed in case the Settings panel ever wants more
# than the prompt cap on screen for debugging.
DEFAULT_LIMIT = 60
MAX_LIMIT = 200


class MemoryFactRow(BaseModel):
    """One user_memory row, surfaced to the Settings panel."""

    id: UUID
    fact: str
    category: str
    relevance_score: float
    reinforced_at: dt.datetime
    created_at: dt.datetime


class MemoryListResponse(BaseModel):
    """List response shape — facts plus the in-prompt cap for UI hints."""

    facts: list[MemoryFactRow]
    capacity: int = 60


class MemoryPatchRequest(BaseModel):
    """Patch body. Both fields optional; at least one required to write."""

    model_config = ConfigDict(extra="forbid")

    fact: str | None = Field(default=None, min_length=1)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)


@router.get("", response_model=MemoryListResponse)
def list_memory(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    user: AuthedUser = Depends(get_current_user_with_device),
) -> MemoryListResponse:
    """Return the user's distilled memory, ordered the way chat sees it.

    Query: `?limit=60&offset=0`. Defaults match the §7.6 60-fact cap.

    Response: `{facts: [...], capacity: 60}`. `capacity` lets the UI show
    the "X / 60 facts" indicator without re-deriving the constant.

    Empty list is a normal response — a new user with no chat history
    has no memory yet. The frontend renders an empty-state message
    rather than treating it as an error.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("user_memory")
        .select("id, fact, category, relevance_score, reinforced_at, created_at")
        .order("relevance_score", desc=True)
        .order("reinforced_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    rows = resp.data or []
    return MemoryListResponse(
        facts=[MemoryFactRow.model_validate(row) for row in rows],
    )


@router.patch("/{memory_id}", response_model=MemoryFactRow)
def patch_memory(
    memory_id: UUID,
    patch: MemoryPatchRequest,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> MemoryFactRow:
    """Update a memory fact's text and/or score; always bump reinforced_at.

    Bumping `reinforced_at` on every PATCH (even score-only edits) is
    deliberate: the user manually touched this row, so it's still
    relevant to them, and Day 17's time-decay sweep should not prune it
    just because no chat conversation has organically re-mentioned it.

    Empty patch body (no fields set) returns the row unchanged. RLS or a
    nonexistent id both surface as 404 — the caller cannot distinguish.
    """
    client = supabase_for_user(user.jwt)

    current = (
        client.table("user_memory")
        .select("id, fact, category, relevance_score, reinforced_at, created_at")
        .eq("id", str(memory_id))
        .execute()
    )
    if not current.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    provided = patch.model_fields_set
    update: dict[str, object] = {
        "reinforced_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    if "fact" in provided and patch.fact is not None:
        update["fact"] = patch.fact
    if "relevance_score" in provided and patch.relevance_score is not None:
        update["relevance_score"] = patch.relevance_score

    if len(update) == 1:
        # Only the reinforced_at touch — skip the write so an empty PATCH
        # doesn't generate spurious churn. Return the current row.
        return MemoryFactRow.model_validate(current.data[0])

    resp = (
        client.table("user_memory")
        .update(update)
        .eq("id", str(memory_id))
        .execute()
    )
    if not resp.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return MemoryFactRow.model_validate(resp.data[0])


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: UUID,
    user: AuthedUser = Depends(get_current_user_with_device),
) -> Response:
    """Hard-delete a memory fact.

    Per §7.6 the delete is durable for *this version of the fact*, not a
    permanent topic ban. If the user organically re-mentions the same
    topic in a future conversation, distillation will re-create the row
    (with a new id) on the next piggyback fire.

    RLS makes "delete nonexistent" and "delete someone else's row"
    indistinguishable from the caller's seat — both return 204 without
    a write. Matches the cards / transactions DELETE behavior.
    """
    client = supabase_for_user(user.jwt)
    client.table("user_memory").delete().eq("id", str(memory_id)).execute()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
