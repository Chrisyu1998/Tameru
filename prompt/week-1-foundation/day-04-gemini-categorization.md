# Day 4 — Gemini 3.1 Flash-Lite categorization with versioned prompts and AICallLog

## Goal

A typed `categorize(merchant, user) -> CategorySuggestion` library function that calls `gemini-3.1-flash-lite-preview`, returns a category from a closed enum, factors in this user's recent `merchant_category` corrections, and logs every call — success and failure — to `ai_call_log` using the caller's JWT.

No HTTP endpoint today. Imported by Day 9's `propose_transaction` tool (to suggest a category when Claude doesn't provide one) and by Day 5's `POST /transactions/confirm` endpoint (for server-side validation when a proposal arrives without a category).

## Read first

- `DESIGN.md` §6.2 (entry flow + home currency), §7.4 (model assignment), §8.4 (`merchant_category`), §8.7 (`users_meta.home_currency`), §8.8 (`ai_call_log` fields + revised RLS shape).
- `CLAUDE.md` model table and architectural invariants 1, 13, 14.
- `app/auth.py` — how `AuthedUser.jwt` is produced.
- `app/db.py::supabase_for_user` — the only sanctioned way for handlers to reach Supabase.

## Architectural notes

- **`ai_call_log` writes use the user JWT with a narrow INSERT policy.** The policy is `WITH CHECK (user_id = auth.uid())`. The logger runs inside the request and uses `supabase_for_user(user.jwt)`. No service role. This preserves CLAUDE.md invariant 1 (invariant 14 makes it explicit).
- **`amount` is not a parameter of `categorize()`.** Categorization is a function of merchant identity + the user's past corrections, nothing else. Passing amount subtly encouraged price-based reasoning ("that's too much for groceries, must be bulk shopping") and made the same merchant categorize inconsistently across price points. Amount remains a `Decimal` on the transaction row (stored, in the user's home currency — invariant 13) and flows through Day 5's confirm endpoint and Day 13's Entry-Moment Insight; it just does not touch the category decision. See `categorize_v3` rationale in `app/prompts/categorize.py`.
- **Merchant normalization is shared.** Lowercase + strip + collapse interior whitespace. Used by this module's lookup path and by Day 5's write path.
- **Category output is a closed enum.** The model chooses from a fixed set defined in code. Unknown outputs are a schema violation, not a coerced "Other."
- **User-controlled merchant text reaches Gemini only through the defense-wrapped `<merchant>` tag in `system_instruction`.** The `contents` payload (Gemini's user-turn slot) is a static "go" signal carrying no user input. Passing merchant text into `contents` would defeat the prompt-injection defense render_prompt builds, because `contents` has no `<merchant>` wrapper and no "treat as untrusted" instruction. See `categorize_v4` rationale.
- **`categorize()` writes exactly one `ai_call_log` row per call — including preflight failures.** `_model_name()`, `_read_past_corrections`, and `render_prompt` all live inside the outer try block; the logger picks up sentinel values (`model="unresolved"`, `prompt_hash=""`) when preflight dies before Gemini is called. An unset `GEMINI_MODEL_DEFAULT` in prod must show up as a `success=false, error_code="provider_error"` row, not silent invisibility.

## Deliverables

### `app/util/merchant.py` (new, tiny)

- `def normalize_merchant(raw: str) -> str:` — lowercase, `.strip()`, collapse interior whitespace to single spaces.

### `app/prompts/categories.py` (new)

- `ALLOWED_CATEGORIES: tuple[str, ...]` — closed enum of 15, aligned with credit-card reward multiplier groupings so the Entry-Moment Insight card-mismatch rule (§6.2) can tie merchant category to card earn rates:
  `Groceries`, `Dining`, `Coffee Shops`, `Gas`, `Transit`, `Travel`, `Streaming`, `Subscriptions`, `Entertainment`, `Shopping`, `Drugstores`, `Home`, `Utilities`, `Health`, `Other`.
- Used to render the list into the prompt AND to validate Gemini's response. Single source of truth.
- `Other` stays as an escape hatch despite Plaid/industry guidance against catch-alls — without it, Gemini hallucinates fake categories on truly ambiguous merchants and we take schema-violation errors unnecessarily. Monitor `Other` frequency in `ai_call_log` as a signal that the taxonomy needs a new category.

### `app/prompts/categorize.py` (new)

- `PROMPT_VERSION = "categorize_v4"` (v1: 10-category flat taxonomy; v2: 15 card-reward-aligned categories with disambiguation descriptions; v3: dropped `amount`; v4: closed a prompt-injection gap — merchant text now flows to Gemini only via the defense-wrapped `<merchant>` tag in `system_instruction`, the `contents` payload is a static "go" signal with no user-controlled substrings).
- `_CATEGORY_DESCRIPTIONS: dict[str, str]` — one-line description per allowed category that renders into the prompt. Disambiguates the borderline cases Gemini will hit (Starbucks = Coffee Shops not Dining; Uber = Transit not Travel; CVS = Drugstores not Health; Home Depot = Home not Shopping). Module-import-time `assert` fails loudly if descriptions and `ALLOWED_CATEGORIES` drift.
- `def render_prompt(merchant: str, past_corrections: list[tuple[str, str]]) -> str:`
  - Enumerate `ALLOWED_CATEGORIES` with descriptions in the system prompt.
  - List `past_corrections` most-recent-first (matches §8.4 "most recent wins"). Empty list is fine — render an empty section, not nothing at all, so the prompt shape is deterministic.
  - Wrap merchant in `<merchant>...</merchant>` and instruct the model to treat its contents as untrusted data, not instructions. Minimal prompt-injection defense — cheap and habit-forming.
  - Instruct JSON output only: `{"category": "<one of the allowed>", "confidence": 0.0-1.0}`. No prose.

### `app/integrations/aicalllog.py` (new)

- `class AICallLogError(Exception):` — raised if the audit INSERT itself fails. A silent audit miss is worse than a loud error.
- `def log_ai_call(user_jwt: str, *, user_id: UUID, provider: Literal["anthropic", "google", "perplexity"], model: str, task_type: str, prompt_version: str, prompt_hash: str, input_tokens: int, output_tokens: int, latency_ms: int, success: bool, error_code: str | None = None) -> None:`
  - Single INSERT via `supabase_for_user(user_jwt)`. Do not swallow DB errors — re-raise as `AICallLogError`.
- **Never import `supabase_admin` here.** `tests/test_no_service_role_leak.py` fails CI if it does.

### `app/integrations/gemini.py` (new)

- `@dataclass(frozen=True) class CategorySuggestion:` with `category: str`, `confidence: float`. `category` is guaranteed to be in `ALLOWED_CATEGORIES`.
- Exception taxonomy, each mapping to a distinct `error_code` written to `ai_call_log`:
  - `GeminiProviderError` → `provider_error` (SDK/network/5xx)
  - `GeminiTimeout` → `timeout`
  - `GeminiJSONParseError` → `json_parse_error` (response present, invalid JSON)
  - `GeminiSchemaViolation` → `schema_violation` (valid JSON, `category` not in enum or `confidence` not in `[0, 1]`)
- `def categorize(merchant: str, user: AuthedUser) -> CategorySuggestion:`
  - Normalize merchant.
  - Read the top 20 `merchant_category` rows for this user ordered by `updated_at DESC` via `supabase_for_user(user.jwt)`. RLS scopes the read.
  - Render the prompt. Compute `prompt_hash = sha256(rendered.encode()).hexdigest()`.
  - Wrap the SDK call in `time.perf_counter()` for `latency_ms`.
  - Call Gemini with `response_mime_type="application/json"` and timeout `GEMINI_TIMEOUT_S`.
  - Parse JSON, validate against the enum and confidence range.
  - Read tokens from the SDK's `usage_metadata`: `prompt_token_count` → `input_tokens`, `candidates_token_count` → `output_tokens`. If the SDK returns no metadata (shouldn't happen on success, can happen on partial failure), log zeros.
- **Every call writes exactly one `ai_call_log` row** — success or failure — before returning or re-raising. Shape:
  ```python
  try:
      # ... call + parse + validate
      log_ai_call(..., success=True)
      return suggestion
  except GeminiError as exc:
      log_ai_call(..., success=False, error_code=exc.error_code, ...)
      raise
  except Exception:
      # Unknown shape — audit must still close, but keep the error_code
      # distinguishable from taxonomy'd cases so it shows up in dashboards.
      log_ai_call(..., success=False, error_code="unknown", ...)
      raise
  ```
  The bare `except Exception` re-raises so the original stack reaches the caller; it exists only to guarantee audit completeness.
- Config from env only — **no hardcoded model strings in the code**:
  - `GEMINI_API_KEY` (required; `GeminiProviderError` on first use if missing).
  - `GEMINI_MODEL` (per-process override; typically unset in prod, used for eval experiments).
  - `GEMINI_MODEL_DEFAULT` (platform-level default; the stable GA model). At least one of `GEMINI_MODEL` or `GEMINI_MODEL_DEFAULT` must be set — both absent is a fail-fast error. Operators rotate `GEMINI_MODEL_DEFAULT` if Google deprecates the chosen model; no code change ships.
  - `GEMINI_TIMEOUT_S` (default `10`; Gemini's API enforces a 10s minimum deadline, smaller values return `INVALID_ARGUMENT` at request time, so the code clamps up to 10 if a smaller value is configured).

### `tests/test_categorize.py`

- **Mocked parsing** — 5 cases: Trader Joe's → Groceries, Blue Bottle Coffee → Coffee Shops, Shell → Gas, Netflix → Streaming, CVS Pharmacy → Drugstores. Each case targets a category that required the v2 split (i.e., would have bucketed differently under v1's flat taxonomy), so the tests prove the new taxonomy is live. Assert `CategorySuggestion` fields.
- **Mocked schema violation** — Gemini returns `{"category": "Food & Beverage"}`. Assert `GeminiSchemaViolation` raised AND one `ai_call_log` row with `success=false, error_code="schema_violation"`.
- **Mocked provider error** — SDK raises. Assert `GeminiProviderError` raised AND one `ai_call_log` row with `success=false, error_code="provider_error"`.
- **Past corrections rendered** — insert 3 `merchant_category` rows for the test user; assert the rendered prompt contains all three in `updated_at DESC` order.
- **Smoke (`-m smoke`)** — real Gemini call against the 5 cases. Requires `GEMINI_API_KEY`. Uses the test user from `tests/conftest.py` (same plumbing as `tests/test_rls_contract.py`). After the run, asserts 5 `ai_call_log` rows exist for that `user_id` with non-null `input_tokens`, `output_tokens`, `latency_ms`.

### `evals/categorization.yaml`

10-row skeleton. Full 100 lands on Day 22.

```yaml
- merchant: "Trader Joe's"
  amount: "47.32"
  expected_category: "Groceries"
- merchant: "Nobu Malibu"
  amount: "185.00"
  expected_category: "Dining"
# ... 8 more
```

## Don't

- Don't expose this as an HTTP endpoint today — Day 5 owns the transport.
- Don't catch Gemini errors and return a default `CategorySuggestion`. Log the failure and re-raise.
- Don't hardcode the model string; use `GEMINI_MODEL`.
- Don't import `supabase_admin` anywhere in this module. Reads and the `ai_call_log` INSERT both go through the user JWT.
- Don't write to `merchant_category` from this module. That's Day 5's job when the user confirms or overrides the suggestion.
- Don't fetch FX rates, look at `users_meta.home_currency`, or vary behavior by currency. Amounts are scalars here.
- Don't widen the exception taxonomy without a migration. `error_code` values get queried by evals and dashboards.

## Done when

- `pytest tests/test_categorize.py` passes (mocks + policy coverage).
- `pytest tests/test_categorize.py -m smoke` returns the expected category for all 5 cases.
- After the smoke run, this SQL returns 5 rows with every listed column populated:
  ```sql
  select provider, model, task_type, prompt_version, prompt_hash,
         input_tokens, output_tokens, latency_ms, success, error_code
  from ai_call_log
  where user_id = :test_user
  order by timestamp desc
  limit 5;
  ```
- `tests/test_no_service_role_leak.py` still passes.
