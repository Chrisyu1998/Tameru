"""Gemini 3.1 Flash-Lite — per-transaction categorization.

Exactly one `ai_call_log` row is written per `categorize()` call, success
or failure. Writes go through the user JWT (CLAUDE.md invariant 14), not
the service role. The exception taxonomy maps one-to-one onto the
`error_code` values that evals and cost dashboards read.

Categorization is a function of merchant identity + the user's past
corrections. It does not see amount or currency. Amount is stored on
the transaction row and used by Day 5's confirm endpoint and Day 13's
Entry-Moment Insight, but it has no role in the category decision (see
categorize_v3 rationale in app/prompts/categorize.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from app.auth import AuthedUser
from app.db import supabase_for_user
from app.integrations.aicalllog import log_ai_call
from app.prompts.categories import ALLOWED_CATEGORIES
from app.prompts.categorize import PROMPT_VERSION, render_prompt
from app.util.merchant import normalize_merchant


@dataclass(frozen=True)
class CategorySuggestion:
    """Represent CategorySuggestion."""
    category: str
    confidence: float


class GeminiError(Exception):
    """Base class; subclasses map one-to-one onto ai_call_log.error_code."""

    error_code: str = "unknown"


class GeminiProviderError(GeminiError):
    """Represent GeminiProviderError."""
    error_code = "provider_error"


class GeminiTimeout(GeminiError):
    """Represent GeminiTimeout."""
    error_code = "timeout"


class GeminiJSONParseError(GeminiError):
    """Represent GeminiJSONParseError."""
    error_code = "json_parse_error"


class GeminiSchemaViolation(GeminiError):
    """Represent GeminiSchemaViolation."""
    error_code = "schema_violation"


_client: genai.Client | None = None


def categorize(
    merchant: str,
    user: AuthedUser,
) -> CategorySuggestion:
    """Return the model's category suggestion for one transaction.

    Every call writes exactly one ai_call_log row before returning or
    re-raising — including failures in preflight (env resolution, past-
    corrections read, prompt rendering) that happen before the Gemini
    request starts. The outer try covers all of it; `model` and
    `prompt_hash` are sentinel-initialized so the logger has usable
    values even when preflight dies halfway through.

    Amount is deliberately not a parameter here — see categorize_v3
    rationale in app/prompts/categorize.py. Callers that have an amount
    (Day 5 confirm, Day 9 propose_transaction) don't pass it.

    User-controlled merchant text is only ever passed to Gemini inside
    the `<merchant>...</merchant>` tag rendered by render_prompt, which
    explicitly marks its contents as untrusted data. The `contents`
    payload (the user-turn slot in Gemini's request shape) is a static
    "go" signal carrying no user input — see categorize_v4 rationale.
    """
    # Logging context — filled in as preflight progresses. If we fail
    # before a given value is computed, it stays on its sentinel and
    # the audit row still lands with enough info to distinguish
    # preflight failures from call failures.
    model: str = "unresolved"
    prompt_hash: str = ""
    input_tokens = 0
    output_tokens = 0
    start = time.perf_counter()

    try:
        # Preflight — ordered so each failure leaves the logging
        # context in a state that identifies WHERE we died:
        #   model unresolved, prompt_hash empty   -> env config bad
        #   model resolved,   prompt_hash empty   -> past-corrections
        #                                            read failed (Supabase
        #                                            down, bad JWT, etc.)
        #   model + prompt_hash both populated    -> failure is at or
        #                                            after the SDK call
        # _model_name() first so we never burn a DB round-trip on a call
        # we can't dispatch anyway.
        model = _model_name()
        normalized = normalize_merchant(merchant)
        past_corrections = _read_past_corrections(user)
        rendered = render_prompt(normalized, past_corrections)
        prompt_hash = hashlib.sha256(rendered.encode()).hexdigest()

        try:
            response = _gemini_client().models.generate_content(
                model=model,
                # Static string — NO user-controlled merchant text here.
                # Merchant flows to Gemini only via the system
                # instruction's <merchant> tag, which is defended
                # against prompt injection. See render_prompt + the
                # categorize_v4 rationale. Changing this line without
                # also adjusting the injection defense is a regression.
                contents="Categorize the merchant described in the system instruction. Return JSON only.",
                config=types.GenerateContentConfig(
                    system_instruction=rendered,
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "category": {
                                "type": "string",
                                "enum": list(ALLOWED_CATEGORIES),
                            },
                            "confidence": {"type": "number"},
                        },
                        "required": ["category", "confidence"],
                        "property_ordering": ["category", "confidence"],
                    },
                    http_options=types.HttpOptions(timeout=_timeout_ms()),
                ),
            )
        except GeminiError:
            raise
        except Exception as exc:
            raise _classify_sdk_error(exc) from exc

        input_tokens, output_tokens = _extract_tokens(response)

        raw_text = getattr(response, "text", None) or ""
        try:
            data = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise GeminiJSONParseError(
                f"Gemini returned non-JSON: {raw_text!r}"
            ) from exc

        category = data.get("category")
        confidence = data.get("confidence")
        if category not in ALLOWED_CATEGORIES:
            raise GeminiSchemaViolation(
                f"category {category!r} not in ALLOWED_CATEGORIES"
            )
        if not isinstance(confidence, (int, float)):
            raise GeminiSchemaViolation(
                f"confidence {confidence!r} is not a number"
            )
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise GeminiSchemaViolation(
                f"confidence {confidence!r} not in [0, 1]"
            )

        suggestion = CategorySuggestion(category=category, confidence=confidence)
        _write_log(
            user=user,
            model=model,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            start=start,
            success=True,
            error_code=None,
        )
        return suggestion

    except GeminiError as exc:
        _write_log(
            user=user,
            model=model,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            start=start,
            success=False,
            error_code=exc.error_code,
        )
        raise
    except Exception:
        _write_log(
            user=user,
            model=model,
            prompt_hash=prompt_hash,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            start=start,
            success=False,
            error_code="unknown",
        )
        raise


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------

def _gemini_client() -> genai.Client:
    """Lazy-init. Matches app/auth.py's lazy-JWKS pattern so import-time
    side-effects stay minimal and tests can patch before first call."""
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise GeminiProviderError("GEMINI_API_KEY is not set")
        _client = genai.Client(api_key=api_key)
    return _client

def _timeout_ms() -> int:
    """Gemini's API enforces a 10s minimum deadline; values below that
    return INVALID_ARGUMENT at request time rather than timing out
    locally. We default to 10 so the happy path works out of the box,
    and clamp any smaller configured value up to the minimum."""
    raw = os.environ.get("GEMINI_TIMEOUT_S", "10")
    seconds = max(float(raw), 10.0)
    return int(seconds * 1000)

def _model_name() -> str:
    """Resolve the Gemini model for this call.

    Two env vars, in priority order:
      * GEMINI_MODEL         — per-process override. Set this for eval
                                experiments or to flip to the preview
                                model temporarily.
      * GEMINI_MODEL_DEFAULT — platform-level default. Set once in the
                                deployment environment to the stable
                                GA model.

    Neither has a hardcoded fallback. If Google deprecates the model
    we're using, an operator updates the env var; no code change ships.
    Fail fast if both are absent — this is configuration, not
    guesswork.
    """
    model = os.environ.get("GEMINI_MODEL") or os.environ.get("GEMINI_MODEL_DEFAULT")
    if not model:
        raise GeminiProviderError(
            "Neither GEMINI_MODEL nor GEMINI_MODEL_DEFAULT is set. "
            "Set GEMINI_MODEL_DEFAULT in your environment (see .env.example)."
        )
    return model

def _read_past_corrections(user: AuthedUser) -> list[tuple[str, str]]:
    """Top 20 merchant_category rows for this user, updated_at DESC.

    RLS scopes the read automatically — no WHERE user_id needed. Matches
    DESIGN.md §8.4 "most recent correction wins" by ordering + limiting.
    """
    client = supabase_for_user(user.jwt)
    resp = (
        client.table("merchant_category")
        .select("merchant, category, updated_at")
        .order("updated_at", desc=True)
        .limit(20)
        .execute()
    )
    return [(row["merchant"], row["category"]) for row in (resp.data or [])]

def _extract_tokens(response: Any) -> tuple[int, int]:
    """Pull prompt/candidates token counts out of usage_metadata.

    Partial-failure responses can be missing metadata; we log zeros in
    that case rather than raising (the Day 4 prompt says 'log zeros').
    """
    meta = getattr(response, "usage_metadata", None)
    if meta is None:
        return (0, 0)
    input_tokens = getattr(meta, "prompt_token_count", 0) or 0
    output_tokens = getattr(meta, "candidates_token_count", 0) or 0
    return (int(input_tokens), int(output_tokens))

def _classify_sdk_error(exc: Exception) -> GeminiError:
    """Map a raw SDK / network exception onto our taxonomy.

    We don't import google.genai.errors.APIError directly because the
    exact class graph varies across SDK minor versions; pattern-matching
    on message + type name is forward-compatible.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    if "timeout" in name.lower() or "timeout" in msg or "timed out" in msg:
        return GeminiTimeout(f"Gemini timeout: {exc}")
    return GeminiProviderError(f"Gemini SDK error: {exc}")


def _elapsed_ms(start: float) -> int:
    """Return elapsed milliseconds from a perf_counter start value."""
    return int((time.perf_counter() - start) * 1000)


def _write_log(
    *,
    user: AuthedUser,
    model: str,
    prompt_hash: str,
    input_tokens: int,
    output_tokens: int,
    start: float,
    success: bool,
    error_code: str | None,
) -> None:
    """Write one Gemini categorization audit row."""
    log_ai_call(
        user.jwt,
        user_id=user.user_id,
        provider="google",
        model=model,
        task_type="categorization",
        prompt_version=PROMPT_VERSION,
        prompt_hash=prompt_hash,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=_elapsed_ms(start),
        success=success,
        error_code=error_code,
    )
