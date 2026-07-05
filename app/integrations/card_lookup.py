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
from app.integrations.card_regions import ALLOWED_DOMAINS_BY_REGION
from app.models.card_credits import CardCreditsLookupResult, LookedUpCredit
from app.models.cards import (
    CardIssuer,
    CardLookupResult,
    CardProgram,
    CardRegion,
)
from app.prompts.categories import ALLOWED_CATEGORIES


__all__ = [
    "CARD_LOOKUP_PROMPT_VERSION",
    "CREDIT_LOOKUP_PROMPT_VERSION",
    "lookup_card",
    "lookup_card_credits",
]


# v2 — Tier 3 (DESIGN.md §6.6): the lookup is now region-aware. US keeps the
# category-multiplier prompt; JP/TW use a base-rate prompt. The version bumps
# so `ai_call_log.prompt_version` distinguishes the two eras of card lookups.
CARD_LOOKUP_PROMPT_VERSION = "card_lookup_v2"

# v1 — Phase 1 credit tracking (DESIGN.md §6.7). A separate card web_search
# prompt returning the card's list of recurring statement credits; distinct
# `credit_lookup` task_type so ai_call_log tells it apart from the multiplier
# lookup.
CREDIT_LOOKUP_PROMPT_VERSION = "credit_lookup_v1"

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

# Best-effort name-substring → issuer-domain map for pre-lookup allowlist
# widening. Keyed on lowercased fragments that appear in a card's *name*
# (the issuer isn't resolved yet at this point — we guess from the text).
# Misses are fine — the region allowlist still covers the lookup. Tier 3
# (DESIGN.md §6.6) added JP/TW fragments, including CJK forms, since a
# user may type "楽天カード" or "Rakuten Card".
_ISSUER_DOMAINS: dict[str, str] = {
    # US
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
    # JP
    "rakuten": "rakuten-card.co.jp",
    "楽天": "rakuten-card.co.jp",
    "smbc": "smbc-card.com",
    "sumitomo": "smbc-card.com",
    "三井住友": "smbc-card.com",
    "jcb": "jcb.co.jp",
    "aeon": "aeon.co.jp",
    "イオン": "aeon.co.jp",
    "epos": "eposcard.co.jp",
    "エポス": "eposcard.co.jp",
    "saison": "saisoncard.co.jp",
    "セゾン": "saisoncard.co.jp",
    # TW
    "cathay": "cathaybk.com.tw",
    "國泰": "cathaybk.com.tw",
    "esun": "esunbank.com.tw",
    "e.sun": "esunbank.com.tw",
    "玉山": "esunbank.com.tw",
    "ctbc": "ctbcbank.com",
    "中信": "ctbcbank.com",
    "taishin": "taishinbank.com.tw",
    "台新": "taishinbank.com.tw",
    "fubon": "fubon.com",
    "富邦": "fubon.com",
    "聯邦": "ubot.com.tw",
}


# Pydantic schema that Claude's response_format clamps to. Anthropic
# doesn't expose a strict JSON-schema response_format the way Gemini
# does, so the model is instructed via the system prompt to emit JSON
# and we json.loads + Pydantic-validate; bad output falls back to
# needs_manual=True rather than raising.
_SYSTEM_PROMPT_US = """\
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

  - annual_fee: numeric, in {home_currency}. Use the current published fee \
in {home_currency}; if the fee is published only in a different currency, \
return null (do NOT convert). Null if not found.

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


# Tier 3 (DESIGN.md §6.6) — region-specific issuer enums for the intl
# (JP/TW) base-rate prompt. The model is told the relevant short list so it
# returns a canonical key the `cards_issuer_check` CHECK accepts.
_INTL_ISSUERS: dict[CardRegion, str] = {
    "JP": '"rakuten", "smbc", "jcb", "aeon", "epos", "saison", "other"',
    "TW": '"cathay", "esun", "ctbc", "taishin", "fubon", "union", "other"',
}


# Phase 1 credit tracking (DESIGN.md §6.7). Returns the card's list of recurring
# statement credits. `{home_currency}` is `.replace`d in (not `.format` — the
# body has literal JSON braces). Under-claim by design: terms drift yearly, so a
# missed credit (user adds it) beats a phantom one.
_CREDIT_SYSTEM_PROMPT = """\
You research a single credit card's recurring STATEMENT CREDITS and return \
structured JSON.

A statement credit is a recurring, use-it-or-lose-it benefit that reimburses \
spending at a specific merchant or category on a fixed cadence (e.g. Amex \
Platinum's $75/quarter Lululemon credit, $100/quarter Resy credit, $200/year \
airline fee credit). Do NOT report:
  - earn multipliers / points bonuses (a different feature),
  - one-time welcome offers or sign-up bonuses,
  - benefits without a clear recurring dollar value.

Search the allowlisted authoritative sources and, for the card named in the \
user message, return each recurring statement credit with:

  - name: a short label, e.g. "Lululemon credit", "Uber Cash", "Airline fee \
credit".

  - amount: the per-PERIOD dollar value as a number, in {home_currency} (e.g. \
75 for a $75/quarter credit — the amount PER quarter, not the annual total). If \
the value is published only in a different currency, return null (do NOT \
convert).

  - cadence: one of ["monthly", "quarterly", "semiannual", "annual"] — how \
often the credit resets on the calendar.

  - merchant_hint: a short lowercase merchant token the credit applies to, e.g. \
"lululemon", "uber", "resy". Null for broad category credits with no single \
merchant.

Bias to UNDER-claiming: if you are not confident a credit is currently offered \
with these terms, omit it. A missed credit the user adds by hand is fine; a \
phantom credit is not.

Return your entire output as a single JSON object inside ```json fences:

```json
{"credits": [{"name": "Lululemon credit", "amount": 75, "cadence": "quarterly", "merchant_hint": "lululemon"}, {"name": "Airline fee credit", "amount": 200, "cadence": "annual", "merchant_hint": null}]}
```

If the card has no recurring statement credits (most no-annual-fee cards don't), \
return:

```json
{"credits": []}
```

Do not include any prose outside the JSON fences. Cite your sources via \
web_search — citations are extracted automatically by the caller.
"""


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


def lookup_card(
    card_name: str,
    user: AuthedUser,
    region: CardRegion = "US",
    home_currency: str = "USD",
) -> CardLookupResult:
    """Web-grounded, region-aware card reward lookup.

    Request:
        card_name: the user-facing card name, e.g. "Chase Sapphire Reserve".
                   Stripped + length-bounded by the route handler before
                   reaching here, but we defend defensively below.
        region: "US" → category-multiplier lookup against US sources.
                "JP"/"TW" → base-rate lookup against local sources (a single
                `base_reward_rate` + `rewards_currency`, no multipliers —
                DESIGN.md §6.6). Defaults to "US".
        home_currency: the user's home currency; the JP/TW prompt resolves
                the annual fee in it (no FX — fails to null on mismatch).

    Response:
        CardLookupResult — `needs_manual=True` whenever the lookup didn't
        produce usable structured fields. The UI uses that to render the
        manual-fill path. US results carry `multipliers`; JP/TW results
        carry `base_reward_rate` + `rewards_currency` instead.

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

    is_intl = region != "US"
    system_prompt = (
        _intl_system_prompt(region, home_currency)
        if is_intl
        else _us_system_prompt(home_currency)
    )
    model = _model_name()
    prompt_hash = hashlib.sha256(
        (system_prompt + "\n---\n" + name).encode()
    ).hexdigest()
    start = time.perf_counter()
    input_tokens = 0
    output_tokens = 0
    success = False
    error_code: str | None = None

    try:
        client = _anthropic_client()
        allowed_domains = list(
            ALLOWED_DOMAINS_BY_REGION[region]
        ) + _inferred_issuer_domains(name)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
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

        network = _normalize_network(parsed.get("network"))
        issuer = _normalize_issuer(parsed.get("issuer"))
        annual_fee = _coerce_number(parsed.get("annual_fee"))

        if is_intl:
            # JP/TW base-rate shape: a single base earn rate + a free-text
            # rewards label, no category multipliers and no rewards-program
            # enum (program stays "Other" at the proposal layer).
            base_reward_rate = _coerce_number(parsed.get("base_reward_rate"))
            rewards_currency = _normalize_rewards_currency(
                parsed.get("rewards_currency")
            )
            if (
                network is None
                and issuer is None
                and base_reward_rate is None
                and not rewards_currency
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
                network=network,
                issuer=issuer,
                base_reward_rate=base_reward_rate,
                rewards_currency=rewards_currency,
                annual_fee=annual_fee,
                source_urls=citations,
                needs_manual=False,
            )

        program = _normalize_program(parsed.get("program"))
        multipliers = _normalize_multipliers(parsed.get("multipliers"))

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


def lookup_card_credits(
    card_name: str,
    user: AuthedUser,
    home_currency: str = "USD",
) -> CardCreditsLookupResult:
    """Web-grounded lookup of a card's recurring statement credits (§6.7).

    Request:
        card_name: the user-facing card name, e.g. "Amex Platinum".
        home_currency: amounts resolve in it; the prompt fails to null (no FX)
            when a credit's value is published in a different currency.

    Response:
        CardCreditsLookupResult — `credits` is a list of `LookedUpCredit`
        (name / amount / cadence / merchant_hint). `needs_manual=True` with an
        empty list means "found nothing usable — offer manual add." Under-claim
        by design: a card with no documented credits returns an empty list.

    Never raises (mirrors `lookup_card`). Writes exactly one ai_call_log row
    (provider="anthropic", task_type="credit_lookup") via the user-JWT path
    (invariant 14), success or failure.
    """
    name = (card_name or "").strip()
    if not name:
        return CardCreditsLookupResult(needs_manual=True, raw_text="empty card name")

    system_prompt = _credit_system_prompt(home_currency)
    model = _model_name()
    prompt_hash = hashlib.sha256(
        (system_prompt + "\n---\n" + name).encode()
    ).hexdigest()
    start = time.perf_counter()
    input_tokens = 0
    output_tokens = 0
    success = False
    error_code: str | None = None

    try:
        client = _anthropic_client()
        # Credit tracking is US-premium-card-shaped in Phase 1 (JP/TW credit
        # localization is deferred — TODO.md), so the lookup uses the US
        # source allowlist plus any inferred issuer domain.
        allowed_domains = list(
            ALLOWED_DOMAINS_BY_REGION["US"]
        ) + _inferred_issuer_domains(name)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=1024,
                system=system_prompt,
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
                            "List the recurring statement credits for the "
                            f"credit card named: {name!r}. "
                            "Return only the JSON object described in the system prompt."
                        ),
                    }
                ],
            )
        except anthropic.RateLimitError as exc:
            error_code = CardLookupRateLimited.error_code
            return CardCreditsLookupResult(
                needs_manual=True,
                raw_text=f"rate_limited: {exc.__class__.__name__}",
            )
        except anthropic.APIError as exc:
            error_code = CardLookupProviderError.error_code
            return CardCreditsLookupResult(
                needs_manual=True,
                raw_text=f"provider_error: {exc.__class__.__name__}",
            )

        usage = getattr(response, "usage", None)
        if usage is not None:
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

        if _tool_result_errored(response):
            error_code = CardLookupToolUnavailable.error_code
            return CardCreditsLookupResult(
                needs_manual=True,
                raw_text="web_search unavailable — check Claude Console > Privacy",
            )

        try:
            parsed = _extract_json(response)
        except CardLookupParseError as exc:
            error_code = CardLookupParseError.error_code
            return CardCreditsLookupResult(needs_manual=True, raw_text=str(exc))

        citations = _extract_citations(response, allowed_domains)
        credits = _extract_credits(parsed.get("credits"))
        # The API call succeeded even when the card has zero documented
        # credits — that's a valid, common answer (a no-AF card), not a
        # failure. `needs_manual` just tells the UI to offer manual add.
        success = True
        return CardCreditsLookupResult(
            credits=credits,
            source_urls=citations,
            needs_manual=not credits,
        )

    except Exception as exc:  # pragma: no cover - defensive
        error_code = type(exc).__name__
        return CardCreditsLookupResult(
            needs_manual=True,
            raw_text=f"unexpected: {error_code}",
        )
    finally:
        log_ai_call(
            user.jwt,
            user_id=user.user_id,
            provider="anthropic",
            model=model,
            task_type="credit_lookup",
            prompt_version=CREDIT_LOOKUP_PROMPT_VERSION,
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


def _us_system_prompt(home_currency: str) -> str:
    """US (category-multiplier) prompt with the annual fee anchored to the
    user's home currency.

    A `.replace` (not `.format`) on the `{home_currency}` token because the
    prompt body contains literal `{...}` JSON braces that `.format` would
    choke on. Matters for the mixed-wallet case (region='US' but a non-USD
    home currency, e.g. a JP user adding a US card): without this the AF
    came back in USD and was stored into `cards.annual_fee`, which the app
    reads as the user's single home currency. Same fail-closed-to-null-on-
    mismatch rule as the intl prompt — no FX. `home_currency` is in the
    returned text, so it flows into `prompt_hash` (audit distinctness).
    """
    return _SYSTEM_PROMPT_US.replace("{home_currency}", home_currency)


def _intl_system_prompt(region: CardRegion, home_currency: str) -> str:
    """Base-rate card-lookup prompt for non-US (JP/TW) cards.

    Diverges from the US prompt in three deliberate ways (memory.md
    2026-06-02 scope decision): it asks for a single **base earn rate** and
    a free-text **rewards-currency label** instead of category multipliers
    (JP/TW rewards are partner-economy / user-selected / mobile-pay driven
    and a one-shot lookup can't capture per-category bonuses stably); it
    lists the region's issuer enum; and it resolves the annual fee in the
    user's `home_currency` rather than USD.

    `home_currency` is interpolated into the prompt, so it participates in
    the `prompt_hash` — a JPY user and a TWD user get distinct audit hashes.
    """
    issuers = _INTL_ISSUERS[region]
    return f"""\
You research a single credit card and return structured JSON.

For the card named in the user message, search the allowlisted authoritative \
sources and extract:

  - network: one of ["visa", "mastercard", "amex", "jcb", "diners", \
"other"]. Return null if you cannot determine it.

  - issuer: one of [{issuers}]. Use the issuing bank's canonical Tameru \
identifier (snake_case, lowercase). For an issuer outside the list return \
"other". Return null only if the issuer cannot be determined at all.

  - base_reward_rate: the card's BASE reward rate as a percent number \
(e.g. 1.0 for 1%, 0.5 for 0.5%). This is the everyday earn rate on general \
spending, NOT a category bonus. Do NOT return per-category multipliers — \
rewards on these cards depend on partner ecosystems, user-selected plans, \
or mobile-pay binding that cannot be captured reliably here. Null if not \
found.

  - rewards_currency: a short label for what the card earns \
(e.g. "Rakuten Points", "現金回饋", "LINE Points", "JCB Oki Doki Points"). \
Null if not found.

  - annual_fee: numeric, in {home_currency}. Use the current published fee \
in {home_currency}; if the fee is published only in a different currency, \
return null (do NOT convert). Null if not found.

Return your entire output as a single JSON object inside ```json fences:

```json
{{"network": "jcb", "issuer": "rakuten", "base_reward_rate": 1.0, "rewards_currency": "Rakuten Points", "annual_fee": 0}}
```

If you cannot find the card or sources disagree wildly, return:

```json
{{"network": null, "issuer": null, "base_reward_rate": null, "rewards_currency": null, "annual_fee": null}}
```

Do not include any prose outside the JSON fences. Cite your sources via \
web_search — citations are extracted automatically by the caller.
"""


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
        # US
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
        # JP
        "rakuten",
        "smbc",
        "jcb",
        "aeon",
        "epos",
        "saison",
        # TW
        "cathay",
        "esun",
        "ctbc",
        "taishin",
        "fubon",
        "union",
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
    # JP folds
    if cleaned in ("rakuten_card",):
        return "rakuten"
    if cleaned in (
        "sumitomo_mitsui",
        "sumitomo_mitsui_banking_corporation",
        "smbc_card",
        "三井住友",
    ):
        return "smbc"
    if cleaned in ("aeon_card",):
        return "aeon"
    if cleaned in ("epos_card",):
        return "epos"
    if cleaned in ("saison_card",):
        return "saison"
    # TW folds
    if cleaned in ("cathay_united", "cathay_united_bank", "國泰世華", "國泰"):
        return "cathay"
    if cleaned in ("e.sun", "e_sun", "esun_bank", "玉山"):
        return "esun"
    if cleaned in ("ctbc_bank", "中信", "中國信託"):
        return "ctbc"
    if cleaned in ("taishin_bank", "台新"):
        return "taishin"
    if cleaned in ("fubon_bank", "taipei_fubon", "富邦"):
        return "fubon"
    if cleaned in ("union_bank", "union_bank_of_taiwan", "聯邦", "聯邦銀行"):
        return "union"
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
    if cleaned in ("visa", "mastercard", "amex", "discover", "jcb", "diners", "other"):
        return cleaned
    if cleaned in ("american express", "amex card", "amexco"):
        return "amex"
    if cleaned in ("master card", "mc"):
        return "mastercard"
    if cleaned in ("diners club", "diners club international", "dinersclub"):
        return "diners"
    return None


def _normalize_rewards_currency(value: Any) -> str | None:
    """Coerce the free-text rewards label to a bounded string, or None.

    Tier 3 (DESIGN.md §6.6). Unlike the closed enums this is free text
    ("Rakuten Points", "現金回饋", "LINE Points"), so we only strip and
    length-bound it — an empty or absurdly long value becomes None and the
    user fills it on the parse card.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 60:
        return None
    return cleaned


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


def _credit_system_prompt(home_currency: str) -> str:
    """Credit-list prompt with the amount anchored to the user's home currency.

    `.replace` (not `.format`) because the prompt body contains literal `{...}`
    JSON braces. Same fail-closed-to-null-on-currency-mismatch rule as the AF
    prompt (§6.7 / invariant 13); no FX. `home_currency` flows into prompt_hash.
    """
    return _CREDIT_SYSTEM_PROMPT.replace("{home_currency}", home_currency)


def _extract_credits(value: Any) -> list[LookedUpCredit]:
    """Coerce the model's `credits` array into validated LookedUpCredit rows.

    Drops entries with an empty name, an invalid cadence, or a garbage amount.
    The under-claim bias means a malformed row is silently skipped rather than
    surfaced as a phantom credit.
    """
    if not isinstance(value, list):
        return []
    out: list[LookedUpCredit] = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        cadence = _normalize_cadence(entry.get("cadence"))
        if cadence is None:
            continue
        hint = entry.get("merchant_hint")
        merchant_hint = (
            hint.strip().lower()[:80]
            if isinstance(hint, str) and hint.strip()
            else None
        )
        try:
            out.append(
                LookedUpCredit(
                    name=name.strip()[:120],
                    amount=_coerce_number(entry.get("amount")),
                    cadence=cadence,  # type: ignore[arg-type]
                    merchant_hint=merchant_hint,
                )
            )
        except Exception:  # pragma: no cover - defensive
            continue
    return out


def _normalize_cadence(value: Any) -> str | None:
    """Map model output onto the closed credit-cadence enum, or None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if cleaned in ("monthly", "quarterly", "semiannual", "annual"):
        return cleaned
    if cleaned in ("month",):
        return "monthly"
    if cleaned in ("quarter",):
        return "quarterly"
    if cleaned in (
        "semi-annual",
        "semi annual",
        "biannual",
        "half-yearly",
        "twice a year",
    ):
        return "semiannual"
    if cleaned in ("year", "yearly", "annually"):
        return "annual"
    return None
