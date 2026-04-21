# Day 4 — Gemini 3.1 Flash-Lite categorization with versioned prompts and AICallLog

## Goal

A typed `categorize(merchant, amount, user_id) -> CategorySuggestion` function that calls `gemini-3.1-flash-lite-preview`, uses a versioned prompt template, factors in this user's `merchant_category` corrections, and logs every call to `ai_call_log`.

## Read first

- `DESIGN.md` §6.2 (entry flow), §7.4 (model assignment), §8.4 (merchant_category), §8.8 (ai_call_log fields).
- `CLAUDE.md` model table.

## Deliverables

- `app/prompts/categorize.py`:
  - A `PROMPT_VERSION = "categorize_v1"` constant.
  - A function `render_prompt(merchant, amount, past_corrections: list[tuple[str, str]]) -> str` returning the full system prompt.
  - The prompt instructs Gemini to return a strict JSON object: `{"category": "...", "confidence": 0.0-1.0}`.
- `app/integrations/gemini.py`:
  - `categorize(merchant, amount, user_jwt)` — looks up past corrections from `merchant_category` (top 20 by `updated_at`), renders the prompt, calls Gemini with `response_mime_type="application/json"`, parses JSON, returns the structured suggestion.
  - All Gemini SDK config reads from env: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.1-flash-lite-preview`).
  - Wraps the call in a try/except that always writes to `ai_call_log` — both successes and failures.
- `app/integrations/aicalllog.py`:
  - `log_ai_call(user_id, provider, model, task_type, prompt_version, prompt_hash, input_tokens, output_tokens, latency_ms, success, error_code=None)` — single insert helper used by every AI integration.
  - `prompt_hash = sha256(rendered_prompt).hexdigest()`.
- `tests/test_categorize.py`:
  - Mocked Gemini responses for 5 known cases (Trader Joe's → Groceries, Nobu → Dining, etc.). Assert correct parsing.
  - One real-call smoke test gated behind `pytest -m smoke` (requires `GEMINI_API_KEY`).
- `evals/categorization.yaml` skeleton: 10 rows to start. The full 100 lands on Day 22.

## Don't

- Don't expose this as an HTTP endpoint today — it's library code, called by Day 5's transactions endpoint.
- Don't catch and swallow Gemini errors — log them and re-raise. The Day 5 endpoint decides how to surface failures to the user.
- Don't hardcode the model string in code. Use the env var.

## Done when

- `pytest tests/test_categorize.py` passes (mocks).
- `pytest tests/test_categorize.py -m smoke` returns sensible categorizations for the 5 test cases.
- After running the smoke test, `select * from ai_call_log` shows one row per call with all fields populated.
