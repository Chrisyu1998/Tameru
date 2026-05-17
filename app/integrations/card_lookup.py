"""Claude Haiku + `web_search_20250305` — card multiplier lookup.

Replaces the original Perplexity Sonar plan (DESIGN.md §0, §6.1, §7.4).
Anthropic's web_search server tool is enforced with an `allowed_domains`
allowlist of authoritative card-rewards sources, plus an inferred issuer
domain when we can guess one. Citations land on the response in
`web_search_result_location` blocks and feed `cards.source_urls`.

Exactly one `ai_call_log` row is written per `lookup_card()` call,
success or failure, via the user-JWT path (CLAUDE.md invariant 14).
Provider/model/task_type: `anthropic` / `claude-haiku-4-5` /
`card_lookup`. Failures fall back to `needs_manual=True` rather than
raising — the UI's manual-fill path is the user-facing recovery.

Privacy: only the public card name (and the host-inferred issuer
domain) leaves Tameru. No transaction data, no last_four, no PII.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Any

import anthropic
from anthropic import Anthropic

from app.auth import AuthedUser
from app.integrations.aicalllog import log_ai_call
from app.models.cards import (
    CARD_LOOKUP_ALLOWED_DOMAINS,
    CardIssuer,
    CardLookupResult,
    CardProgram,
)
from app.prompts.categories import ALLOWED_CATEGORIES


__all__ = ["CARD_LOOKUP_PROMPT_VERSION", "lookup_card"]


CARD_LOOKUP_PROMPT_VERSION = "card_lookup_v1"

# Bound the web_search tool's internal iterations. A well-known card
# typically needs one search; ambiguous names may need two. Three is the
# upper bound where additional searches stop adding signal and just burn
# the $10/1k fee.
_MAX_WEB_SEARCHES = 3

# Claude can occasionally cite outside an allowlisted host when reasoning
# from a fresh fetch; this rejects any URL whose host isn't on the per-call
# allowlist before we hand the list to the response. Cheap belt-and-braces
# over the API-level enforcement.
_HOST_RE = re.compile(r"^https?://([^/]+)", re.IGNORECASE)

_DEFAULT_MODEL = "claude-haiku-4-5"

# Best-effort issuer → domain map. Misses (unknown issuer) are fine — the
# global allowlist still covers the lookup; we just don't widen it.
_ISSUER_DOMAINS: dict[str, str] = {
    "chase": "chase.com",
    "amex": "americanexpress.com",
    "american express": "americanexpress.com",
    "citi": "citi.com",
    "citibank": "citi.com",
    "capital one": "capitalone.com",
    "discover": "discover.com",
    "bank of america": "bankofamerica.com",
    "wells fargo": "wellsfargo.com",
    "us bank": "usbank.com",
    "barclays": "barclays.com",
    "bilt": "biltrewards.com",
}


# Pydantic schema that Claude's response_format clamps to. Anthropic
# doesn't expose a strict JSON-schema response_format the way Gemini
# does, so the model is instructed via the system prompt to emit JSON
# and we json.loads + Pydantic-validate; bad output falls back to
# needs_manual=True rather than raising.
_SYSTEM_PROMPT = """\
You research a single credit card and return structured JSON.

For the card named in the user message, search the allowlisted authoritative \
sources and extract:

  - program: one of ["UR", "MR", "TYP", "Bilt", "Other"] — the card's \
rewards program (UR = Chase Ultimate Rewards, MR = Amex Membership Rewards, \
TYP = Citi ThankYou Points, Bilt = Bilt Rewards, Other = anything else / \
cashback / unknown). If you cannot determine the program with high \
confidence, return "Other".

  - network: one of ["visa", "mastercard", "amex", "discover", "other"] — \
the card network the product runs on. Most card products are network-fixed: \
all Chase Sapphire products are Visa, all Amex-issued cards are Amex, Citi \
Double Cash is Mastercard, Discover It is Discover, etc. Fill this whenever \
the card product is unambiguously associated with one network. Return null \
ONLY when the same product is genuinely available on multiple networks \
(rare — Costco's co-branded card history is one example) or when the \
sources don't agree.

  - issuer: one of ["chase", "amex", "citi", "capital_one", "discover", \
"bank_of_america", "wells_fargo", "usaa", "bilt", "barclays", "us_bank", \
"synchrony", "other"]. Use the issuing bank's canonical Tameru identifier \
(snake_case, lowercase). Map common variants: "American Express" → "amex", \
"Citibank" → "citi", "Capital One" → "capital_one", "BofA"/"Bank of America" \
→ "bank_of_america", "US Bank" → "us_bank". For an issuer outside the list \
(e.g. a small regional bank), return "other". Return null only if the \
issuer cannot be determined at all.

  - multipliers: object mapping the card's bonus categories to their \
multiplier value as a number (e.g. {"Dining": 3, "Travel": 3}). Use Tameru's \
category labels where they match the card's bonus categories. Omit a category \
if the card has no bonus on it (don't write "Other": 1 unless the card has a \
non-1× base earn).

  - annual_fee: USD numeric. Use the current published fee. Null if not \
found.

Return your entire output as a single JSON object inside ```json fences:

```json
{"program": "UR", "network": "visa", "issuer": "chase", "multipliers": {"Dining": 3, "Travel": 3}, "annual_fee": 95}
```

If you cannot find the card or sources disagree wildly, return:

```json
{"program": null, "network": null, "issuer": null, "multipliers": {}, "annual_fee": null}
```

Do not include any prose outside the JSON fences. Cite your sources via \
web_search — citations are extracted automatically by the caller.
"""

# Allowed Tameru category labels — used to nudge Claude toward our enum
# without forcing it to fail when a card uses an off-list label. We do
# soft normalization in `_normalize_multipliers` instead of rejecting.
_ALLOWED_CATEGORY_HINTS: tuple[str, ...] = tuple(sorted(ALLOWED_CATEGORIES))


class CardLookupError(Exception):
    """Base for taxonomized lookup failures.

    Callers don't need to differentiate — lookup_card never raises these
    upward; it logs them, returns a needs_manual=True result, and the UI's
    manual-fill path handles user recovery. The class exists so the audit
    log's `error_code` is informative.
    """

    error_code: str = "unknown"


class CardLookupProviderError(CardLookupError):
    """Anthropic SDK / network / 5xx error."""
    error_code = "provider_error"


class CardLookupRateLimited(CardLookupError):
    """Anthropic 429 — too many requests on chat or web_search budget."""
    error_code = "rate_limited"


class CardLookupParseError(CardLookupError):
    """Model emitted unparseable JSON or schema-violating fields."""
    error_code = "parse_error"


class CardLookupToolUnavailable(CardLookupError):
    """`web_search_tool_result_error` from Anthropic.

    Most commonly fires when the org admin hasn't enabled web_search in
    Claude Console → Settings → Privacy. DESIGN.md §16 flags this as a
    one-time setup step.
    """
    error_code = "tool_unavailable"


_client: Anthropic | None = None


def lookup_card(card_name: str, user: AuthedUser) -> CardLookupResult:
    """Web-grounded card multiplier lookup.

    Request:
        card_name: the user-facing card name, e.g. "Chase Sapphire Reserve".
                   Stripped + length-bounded by the route handler before
                   reaching here, but we defend defensively below.

    Response:
        CardLookupResult — `needs_manual=True` whenever the lookup didn't
        produce usable structured fields. The UI uses that to render the
        manual-fill path.

    Never raises. Every exit path writes one ai_call_log row with
    provider="anthropic", model=<resolved>, task_type="card_lookup" via
    the user-JWT path (CLAUDE.md invariant 14). Failures land as a
    `needs_manual` result rather than propagating so the caller (route
    or `propose_card` tool impl) doesn't need to think about taxonomy.
    """
    name = (card_name or "").strip()
    if not name:
        # The route's Pydantic validator already rejects this; defending
        # against a programmer mistake calling us with an empty string.
        # Don't log — no real API call was attempted.
        return CardLookupResult(needs_manual=True, raw_text="empty card name")

    model = _model_name()
    prompt_hash = hashlib.sha256(
        (_SYSTEM_PROMPT + "\n---\n" + name).encode()
    ).hexdigest()
    start = time.perf_counter()
    input_tokens = 0
    output_tokens = 0
    success = False
    error_code: str | None = None

    try:
        client = _anthropic_client()
        allowed_domains = list(CARD_LOOKUP_ALLOWED_DOMAINS) + _inferred_issuer_domains(
            name
        )

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=[
                    {
                        "type": "web_search_20250305",
                        "name": "web_search",
                        "max_uses": _MAX_WEB_SEARCHES,
                        "allowed_domains": allowed_domains,
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Research the credit card named: {name!r}. "
                            "Return only the JSON object described in the system prompt."
                        ),
                    }
                ],
            )
        except anthropic.RateLimitError as exc:
            error_code = CardLookupRateLimited.error_code
            input_tokens, output_tokens = (0, 0)
            return CardLookupResult(
                needs_manual=True,
                raw_text=f"rate_limited: {exc.__class__.__name__}",
            )
        except anthropic.APIError as exc:
            error_code = CardLookupProviderError.error_code
            return CardLookupResult(
                needs_manual=True,
                raw_text=f"provider_error: {exc.__class__.__name__}",
            )

        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        # Tool-error short-circuit. The most common case (org admin didn't
        # enable web_search) surfaces here as a web_search_tool_result block
        # whose inner content has `type: web_search_tool_result_error`. We
        # could differentiate `unavailable` vs `max_uses_exceeded` but
        # both land at the same user-facing result (manual fill); the
        # error_code on the audit row preserves enough debug signal.
        if _tool_result_errored(response):
            error_code = CardLookupToolUnavailable.error_code
            return CardLookupResult(
                needs_manual=True,
                raw_text="web_search unavailable — check Claude Console > Privacy",
            )

        try:
            parsed = _extract_json(response)
        except CardLookupParseError as exc:
            error_code = CardLookupParseError.error_code
            return CardLookupResult(needs_manual=True, raw_text=str(exc))

        citations = _extract_citations(response, allowed_domains)

        program = _normalize_program(parsed.get("program"))
        network = _normalize_network(parsed.get("network"))
        issuer = _normalize_issuer(parsed.get("issuer"))
        multipliers = _normalize_multipliers(parsed.get("multipliers"))
        annual_fee = _coerce_number(parsed.get("annual_fee"))

        # If every meaningful field came back empty / null, treat it as a
        # low-confidence miss and route the user to manual entry. Citations
        # alone aren't enough — they could point at a generic page that
        # mentions the card without the data we need.
        if (
            program is None
            and network is None
            and issuer is None
            and not multipliers
            and annual_fee is None
        ):
            success = True  # the API call succeeded; the answer was empty
            return CardLookupResult(
                needs_manual=True,
                raw_text="model returned no usable fields",
                source_urls=citations,
            )

        success = True
        return CardLookupResult(
            program=program,
            network=network,
            issuer=issuer,
            multipliers=multipliers,
            annual_fee=annual_fee,
            source_urls=citations,
            needs_manual=False,
        )

    except Exception as exc:  # pragma: no cover - defensive
        error_code = type(exc).__name__
        return CardLookupResult(
            needs_manual=True,
            raw_text=f"unexpected: {error_code}",
        )
    finally:
        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="anthropic",
            model=model,
            task_type="card_lookup",
            prompt_version=CARD_LOOKUP_PROMPT_VERSION,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=int((time.perf_counter() - start) * 1000),
            success=success,
            error_code=error_code,
        )


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _anthropic_client() -> Anthropic:
    """Lazy singleton. Mirrors `app/agent/loop.py::_anthropic_client`.

    Kept module-private so import of this file doesn't require
    ANTHROPIC_API_KEY at import time — same pattern the chat loop uses
    so the test process can run without the env being set.
    """
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise CardLookupProviderError("ANTHROPIC_API_KEY is not set")
        _client = Anthropic()
    return _client


def _model_name() -> str:
    """Resolve the Claude model for the lookup call.

    Reuses `ANTHROPIC_MODEL` (set by app/agent/loop.py operators) when
    present so eval experiments swap both chat and lookup together. No
    separate env knob: the lookup is short and infrequent — there's no
    reason to differentiate.
    """
    return os.environ.get("ANTHROPIC_MODEL") or _DEFAULT_MODEL


def _inferred_issuer_domains(card_name: str) -> list[str]:
    """Best-effort issuer-domain widening.

    Keeps the allowlist tight in the common case and grows it only when
    we can guess the issuer from the card's name. False positives are
    harmless (an off-issuer URL just won't help); the global allowlist
    covers the meat of the lookup.
    """
    lowered = card_name.lower()
    domains: list[str] = []
    for issuer_key, domain in _ISSUER_DOMAINS.items():
        if issuer_key in lowered:
            domains.append(domain)
    return domains


def _tool_result_errored(response: Any) -> bool:
    """Return True if any `web_search_tool_result` block carries an error.

    Anthropic returns 200 even when the org hasn't enabled web search;
    the failure shows up as a `web_search_tool_result` block whose
    nested content is a single `web_search_tool_result_error` object.
    """
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) != "web_search_tool_result":
            continue
        content = getattr(block, "content", None)
        # The SDK returns a `WebSearchToolResultError` Pydantic model for
        # the error branch and a list of result blocks for success.
        if isinstance(content, list):
            continue
        if content is not None:
            return True
    return False


def _extract_json(response: Any) -> dict[str, Any]:
    """Pull the JSON object out of Claude's `text` blocks.

    Strategy: concatenate all text blocks, find the last ```json …```
    fence (Claude occasionally restates the schema first), then fall
    back to the first `{ ... }` substring. Either way, we json.loads
    and require a dict result.
    """
    text_parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(getattr(block, "text", "") or "")
    blob = "\n".join(text_parts).strip()
    if not blob:
        raise CardLookupParseError("model returned no text blocks")

    fenced = re.findall(r"```json\s*(\{.*?\})\s*```", blob, flags=re.DOTALL)
    candidate: str | None
    if fenced:
        candidate = fenced[-1]
    else:
        first = blob.find("{")
        last = blob.rfind("}")
        if first < 0 or last < first:
            raise CardLookupParseError(f"no JSON object in: {blob[:200]!r}")
        candidate = blob[first : last + 1]

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise CardLookupParseError(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CardLookupParseError(f"expected JSON object, got {type(data).__name__}")
    return data


def _extract_citations(response: Any, allowlist: list[str]) -> list[str]:
    """Collect citation URLs from web_search_result_location blocks.

    Two sources:
      1. `web_search_result_location` inside text-block citations (the
         model emits these when it cites a source for a specific span).
      2. `web_search_result` items inside `web_search_tool_result.content`
         (the raw fetch list, present whether or not the model later
         cites every entry).

    Deduplicates, preserves first-seen order, defends against off-allowlist
    hosts (cheap belt-and-braces over the API-level enforcement).
    """
    seen: list[str] = []
    seen_set: set[str] = set()

    def _maybe_add(url: str | None) -> None:
        """Dedup + host-allowlist filter for a single candidate citation URL."""
        if not url or not isinstance(url, str):
            return
        if url in seen_set:
            return
        host_match = _HOST_RE.match(url)
        if not host_match:
            return
        host = host_match.group(1).lower()
        # Allow exact match OR subdomain match against the allowlist.
        if not any(host == d or host.endswith("." + d) for d in allowlist):
            return
        seen_set.add(url)
        seen.append(url)

    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            for cit in getattr(block, "citations", None) or []:
                if getattr(cit, "type", None) == "web_search_result_location":
                    _maybe_add(getattr(cit, "url", None))
        elif btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):
                for entry in content:
                    if getattr(entry, "type", None) == "web_search_result":
                        _maybe_add(getattr(entry, "url", None))

    return seen


def _normalize_program(value: Any) -> CardProgram | None:
    """Map model output onto the closed `CardProgram` enum, or None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if cleaned in ("UR", "MR", "TYP", "Bilt", "Other"):
        return cleaned  # type: ignore[return-value]
    return None


def _normalize_issuer(value: Any) -> CardIssuer | None:
    """Map model output onto the closed `CardIssuer` enum, or None.

    The system prompt already instructs Claude to return canonical
    snake_case identifiers, but real web text contains friendly variants
    ("American Express", "Capital One", "BofA") that the model occasionally
    leaks through. This helper folds the common cases without forcing
    the model to retry. Anything genuinely outside the enum returns None
    so the caller can decide to fall back to "other" + needs_manual=True
    rather than serializing a value the DB CHECK constraint would reject.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower().replace(" ", "_")
    if cleaned in (
        "chase",
        "amex",
        "citi",
        "capital_one",
        "discover",
        "bank_of_america",
        "wells_fargo",
        "usaa",
        "bilt",
        "barclays",
        "us_bank",
        "synchrony",
        "other",
    ):
        return cleaned  # type: ignore[return-value]
    # Friendly-name folds — matches the system-prompt guidance and absorbs
    # the most common model variants.
    if cleaned in ("american_express", "amex_card", "amexco"):
        return "amex"
    if cleaned in ("citibank",):
        return "citi"
    if cleaned in ("capitalone",):
        return "capital_one"
    if cleaned in ("bofa", "boa"):
        return "bank_of_america"
    if cleaned in ("wellsfargo",):
        return "wells_fargo"
    if cleaned in ("usbank", "u.s._bank", "u.s_bank"):
        return "us_bank"
    if cleaned in ("bilt_rewards",):
        return "bilt"
    return None


def _normalize_network(value: Any) -> str | None:
    """Map model output onto the closed `CardNetwork` enum, or None.

    Accepts mild variants ("Visa", "VISA", "amex", "american express") and
    folds them to the canonical lowercase value the DB CHECK constraint
    requires. Anything outside the enum returns None so the caller routes
    through `needs_manual` rather than serializing a value the confirm
    endpoint will then reject.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned in ("visa", "mastercard", "amex", "discover", "other"):
        return cleaned
    if cleaned in ("american express", "amex card", "amexco"):
        return "amex"
    if cleaned in ("master card", "mc"):
        return "mastercard"
    return None


def _normalize_multipliers(value: Any) -> dict[str, float]:
    """Coerce multiplier dict to {str: float}; drop garbage entries."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in value.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, bool):  # bool is a subclass of int in Python
            continue
        if not isinstance(v, (int, float)):
            continue
        if v <= 0:
            continue
        out[k.strip()] = float(v)
    return out


def _coerce_number(value: Any) -> Any:
    """Return Decimal-friendly number, or None for null/garbage.

    Returns a Python int/float — the Pydantic model coerces to Decimal
    on validation. Keeps this helper free of decimal-import noise.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return value
    if isinstance(value, str):
        cleaned = value.strip().replace("$", "").replace(",", "")
        if not cleaned:
            return None
        try:
            n = float(cleaned)
        except ValueError:
            return None
        return n if n >= 0 else None
    return None
