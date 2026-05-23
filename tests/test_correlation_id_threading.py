"""Per-request `correlation_id` threading — Day 24 (DESIGN.md §14.5).

CorrelationIdMiddleware sits outermost in `app/main.py`'s middleware
stack. Acceptance:

  * A request that arrives WITHOUT an `X-Request-ID` header gets a
    fresh UUIDv4 minted by the middleware and the *same* id echoed
    back in the response's `X-Request-ID`.
  * A request that arrives WITH a well-formed `X-Request-ID` (e.g.
    from Railway's edge) sees that id echoed back unchanged.
  * Two consecutive requests get distinct ids — the mint is per-request,
    not per-process.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app

# asgi-correlation-id's default generator emits a UUIDv4 as 32 hex
# chars without hyphens (`uuid.uuid4().hex`). Tameru's test only cares
# that the id is UUIDv4-shaped — both the hyphenated and unhyphenated
# forms count. Library choice; do not over-pin.
_UUID_RE = re.compile(
    r"^(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    re.IGNORECASE,
)


@pytest.fixture
def client() -> TestClient:
    """Provide client."""
    return TestClient(app)


def test_response_carries_minted_x_request_id(client) -> None:
    """A request without an `X-Request-ID` gets a fresh UUID; the
    response echoes it back so callers can correlate downstream."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    minted = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert minted is not None
    assert _UUID_RE.match(minted), f"not a UUID: {minted!r}"


def test_response_echoes_provided_x_request_id(client) -> None:
    """An incoming well-formed UUID is honored verbatim — Railway's
    edge mints request ids upstream, and we want the same value to span
    edge logs, our stdout, and Sentry. The middleware's default
    validator accepts the unhyphenated `uuid.uuid4().hex` form."""
    incoming = uuid.uuid4().hex
    resp = client.get("/healthz", headers={"X-Request-ID": incoming})
    assert resp.status_code == 200
    echoed = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert echoed == incoming


def test_two_requests_get_distinct_minted_ids(client) -> None:
    """Each request mints its own UUID; ids are not shared per-process."""
    r1 = client.get("/healthz")
    r2 = client.get("/healthz")
    id1 = r1.headers.get("X-Request-ID") or r1.headers.get("x-request-id")
    id2 = r2.headers.get("X-Request-ID") or r2.headers.get("x-request-id")
    assert id1 and id2 and id1 != id2


def test_malformed_incoming_id_is_replaced(client) -> None:
    """A non-UUID `X-Request-ID` is replaced with a fresh UUIDv4 —
    asgi-correlation-id's default validator only accepts UUID4-shape
    ids. Prevents log-injection via a hostile upstream header."""
    resp = client.get("/healthz", headers={"X-Request-ID": "not-a-uuid"})
    echoed = resp.headers.get("X-Request-ID") or resp.headers.get("x-request-id")
    assert echoed is not None
    assert echoed != "not-a-uuid"
    assert _UUID_RE.match(echoed)
