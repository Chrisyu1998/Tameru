# Tameru — one-shot setup for manually testing /chat/turn.
#
# Usage:
#   source scripts/manual_test_setup.sh
#
# When this finishes you will have:
#   * SUPABASE_URL / SUPABASE_ANON_KEY / SUPABASE_SERVICE_ROLE_KEY exported
#   * A fresh test user (admin-created, email auto-confirmed)
#   * JWT / USER_ID / DEVICE_ID / CARD_ID exported
#   * The user already bootstrapped (Day 7 device gate satisfied)
#   * A handful of transactions + one subscription seeded
#   * Read-tool helpers: chat, tameru_audit
#   * Day 9b write-surface helpers: confirm_last_propose, list_goals,
#     list_txns, deactivate_card, seed_second_card
#
# Source-only — don't `bash` it; the env vars and shell functions need to
# live in the parent shell. Safe to re-run (creates a fresh user each time).

set -u

# ---- Pre-flight ----------------------------------------------------------

if [ -z "${BASH_VERSION:-}${ZSH_VERSION:-}" ]; then
  echo "Source this in bash or zsh." >&2
  return 1 2>/dev/null || exit 1
fi

for cmd in curl jq uuidgen supabase python3; do
  if ! command -v "$cmd" >/dev/null; then
    echo "ERROR: '$cmd' not found in PATH." >&2
    return 1 2>/dev/null || exit 1
  fi
done

# ---- Supabase env --------------------------------------------------------

_status=$(supabase status -o json 2>/dev/null) || {
  echo "ERROR: 'supabase status' failed — is the local stack running? Try: supabase start" >&2
  return 1 2>/dev/null || exit 1
}
export SUPABASE_URL=$(echo "$_status" | jq -r .API_URL)
export SUPABASE_ANON_KEY=$(echo "$_status" | jq -r .ANON_KEY)
export SUPABASE_SERVICE_ROLE_KEY=$(echo "$_status" | jq -r .SERVICE_ROLE_KEY)

if [ -z "$SUPABASE_URL" ] || [ "$SUPABASE_URL" = "null" ]; then
  echo "ERROR: SUPABASE_URL empty — supabase status returned unexpected JSON." >&2
  return 1 2>/dev/null || exit 1
fi
echo "✓ Supabase env loaded ($SUPABASE_URL)"

# ---- Backend reachability ------------------------------------------------

if ! curl -fsS http://localhost:8000/healthz >/dev/null 2>&1; then
  echo "WARN: backend not responding at http://localhost:8000/healthz" >&2
  echo "       Start it in another shell:" >&2
  echo "         source .venv/bin/activate && uvicorn app.main:app --reload --env-file .env" >&2
  echo "       (The --env-file flag loads SUPABASE_URL / ANTHROPIC_API_KEY /" >&2
  echo "        GEMINI_API_KEY into uvicorn's environment. Without it,/chat/turn 500s" >&2
  echo "        on the JWKS fetch because SUPABASE_URL isn't set in the uvicorn shell.)" >&2
  echo "       (Continuing setup anyway — seeding + bootstrap go straight to Supabase.)" >&2
else
  echo "✓ Backend reachable"
fi

# ---- Test user (admin-created, auto-confirmed) ---------------------------

_tag=$(uuidgen | tr 'A-Z' 'a-z' | cut -c1-8)
export EMAIL="manual-test-${_tag}@tameru.local"
export PASSWORD="manual-test-$(uuidgen)"

_create=$(curl -fsS -X POST "$SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\",\"email_confirm\":true}") || {
  echo "ERROR: admin create_user failed." >&2
  return 1 2>/dev/null || exit 1
}
export USER_ID=$(echo "$_create" | jq -r .id)
echo "✓ Test user created: $EMAIL ($USER_ID)"

# Password-grant to mint a JWT scoped to this user.
export JWT=$(curl -fsS -X POST "$SUPABASE_URL/auth/v1/token?grant_type=password" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | jq -r .access_token)
if [ -z "$JWT" ] || [ "$JWT" = "null" ]; then
  echo "ERROR: failed to mint JWT after admin create." >&2
  return 1 2>/dev/null || exit 1
fi
echo "✓ JWT minted (${#JWT} chars)"

# ---- Bootstrap (Day 7 device gate) ---------------------------------------

export DEVICE_ID="manual-$(uuidgen)"
_boot_code=$(curl -s -o /tmp/tameru_boot.json -w "%{http_code}" -X POST http://localhost:8000/auth/bootstrap \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"device_id\":\"$DEVICE_ID\",\"home_currency\":\"USD\"}")
if [ "$_boot_code" = "200" ]; then
  echo "✓ User bootstrapped ($DEVICE_ID)"
else
  echo "ERROR: bootstrap returned HTTP $_boot_code"
  echo "       Response body:"
  cat /tmp/tameru_boot.json 2>/dev/null || true
  echo ""
  echo "       Most common cause: the uvicorn shell is missing SUPABASE_URL."
  echo "       In Terminal A, before starting uvicorn, run:"
  echo "         export \$(supabase status -o json | jq -r 'to_entries[] | \"\\(.key)=\\(.value)\"' | grep -E '^(API_URL|ANON_KEY|SERVICE_ROLE_KEY)=' | sed 's/^API_URL/SUPABASE_URL/;s/^ANON_KEY/SUPABASE_ANON_KEY/;s/^SERVICE_ROLE_KEY/SUPABASE_SERVICE_ROLE_KEY/')"
  echo "       Or simpler: source the same vars this script uses."
fi

# ---- Seed: card + transactions + subscription ----------------------------

_card_resp=$(curl -fsS -X POST "$SUPABASE_URL/rest/v1/cards" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -H "Prefer: return=representation" \
  -d "{\"user_id\":\"$USER_ID\",\"name\":\"Manual Test Card\",\"issuer\":\"Chase\",\"program\":\"UR\"}")
export CARD_ID=$(echo "$_card_resp" | jq -r '.[0].id')
echo "✓ Card seeded ($CARD_ID)"

# Date helpers — macOS BSD date first, GNU date fallback.
_d() { date -u -v"${1}d" +%Y-%m-%d 2>/dev/null || date -u -d "${1} day" +%Y-%m-%d; }
TODAY=$(date -u +%Y-%m-%d)
YESTERDAY=$(_d -1)
WEEK_AGO=$(_d -7)
TOMORROW=$(_d +1)        # used in section 10 of the manual playbook
NEXT_BILL=$(_d +10)

_seed_txn() {
  curl -fsS -X POST "$SUPABASE_URL/rest/v1/transactions" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -d "{\"user_id\":\"$USER_ID\",\"card_id\":\"$CARD_ID\",\"merchant\":\"$1\",\"amount\":\"$2\",\"date\":\"$3\",\"category\":\"$4\",\"source\":\"manual\",\"client_request_id\":\"$(uuidgen | tr 'A-Z' 'a-z')\"}" >/dev/null
}

_seed_txn "Trader Joe's"  45.20 "$TODAY"     "Groceries"
_seed_txn "Trader Joe's"  18.75 "$YESTERDAY" "Groceries"
_seed_txn "Sushi Yasaka"  62.00 "$TODAY"     "Dining"
_seed_txn "Blue Bottle"    5.50 "$YESTERDAY" "Coffee Shops"
_seed_txn "Blue Bottle"    6.25 "$WEEK_AGO"  "Coffee Shops"
_seed_txn "Uber"          14.00 "$WEEK_AGO"  "Transit"
echo "✓ 6 transactions seeded"

curl -fsS -X POST "$SUPABASE_URL/rest/v1/subscriptions" \
  -H "apikey: $SUPABASE_ANON_KEY" \
  -H "Authorization: Bearer $JWT" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"$USER_ID\",\"card_id\":\"$CARD_ID\",\"name\":\"Netflix\",\"amount\":\"15.99\",\"frequency\":\"monthly\",\"start_date\":\"$TODAY\",\"next_billing_date\":\"$NEXT_BILL\",\"category\":\"Streaming\",\"status\":\"active\"}" >/dev/null
echo "✓ 1 subscription seeded"

# ---- Shell helpers (persist after source) --------------------------------

chat() {
  # Threads conversation_id across calls so multi-turn flows actually
  # share context. The first call sends no conversation_id (server mints
  # one); subsequent calls send back the id the server returned, which
  # causes the route to load the last 5 turns from chat_turn_trace
  # before invoking the agent loop. Call `chat_reset` to start a fresh
  # conversation when you want one.
  if [ -z "${1:-}" ]; then
    echo "usage: chat \"your message here\"     (chat_reset to start over)" >&2
    return 2
  fi
  local body
  if [ -n "${TAMERU_CONV_ID:-}" ]; then
    body=$(jq -nc --arg m "$1" --arg c "$TAMERU_CONV_ID" '{message:$m, conversation_id:$c}')
  else
    body=$(jq -nc --arg m "$1" '{message:$m}')
  fi
  local code
  code=$(curl -s -o /tmp/tameru_chat.json -w "%{http_code}" -X POST http://localhost:8000/chat/turn \
    -H "Authorization: Bearer $JWT" \
    -H "X-Device-Id: $DEVICE_ID" \
    -H "Content-Type: application/json" \
    -d "$body")
  echo "HTTP $code"
  if jq -e . /tmp/tameru_chat.json >/dev/null 2>&1; then
    # Capture the conversation_id for the next turn. Only update on a
    # parseable response — a 500 should not clobber a working session.
    local new_cid
    new_cid=$(jq -r '.conversation_id // empty' /tmp/tameru_chat.json)
    if [ -n "$new_cid" ]; then
      export TAMERU_CONV_ID="$new_cid"
    fi
    jq . /tmp/tameru_chat.json
  else
    echo "(non-JSON response):"
    cat /tmp/tameru_chat.json
    echo
  fi
}

chat_reset() {
  # Drop the threaded conversation_id so the next `chat` call starts a
  # fresh conversation (no history loaded). Useful when you want to test
  # cold-start behavior or when a stale conversation_id is interfering.
  unset TAMERU_CONV_ID
  echo "✓ conversation reset — next 'chat' call will start a new one"
}

tameru_audit() {
  local limit="${1:-10}"
  curl -s "$SUPABASE_URL/rest/v1/ai_call_log?select=task_type,model,prompt_version,success,error_code,input_tokens,output_tokens,timestamp&task_type=eq.chat_turn&order=timestamp.desc&limit=$limit" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" | jq
}

tameru_seed_future_dining() {
  # Helper for the Issue-1 manual test (section 10 of the playbook).
  _seed_txn "Future Dining (manual test)" 999.00 "$TOMORROW" "Dining"
  echo "Seeded \$999 Dining row dated $TOMORROW"
}

# ---- Day 9b: propose-then-confirm + set_goal helpers --------------------

# Extract the result payload from the most recent propose_transaction
# tool_use call in /tmp/tameru_chat.json. If a turn produced multiple
# propose_transaction calls (e.g. "$7 at Blue Bottle, $15 at Sweetgreen,
# $23 at Whole Foods"), pass an index — 0 for the first, 1 for the
# second, etc. Default is the LAST propose_transaction in the turn.
_last_propose_payload() {
  local idx="${1:-last}"
  if [ ! -s /tmp/tameru_chat.json ]; then
    echo "ERROR: no /tmp/tameru_chat.json — run 'chat ...' first." >&2
    return 1
  fi
  if [ "$idx" = "last" ]; then
    jq -e '[.tool_calls[]? | select(.name=="propose_transaction") | .result] | last // empty' /tmp/tameru_chat.json
  else
    jq -e --argjson i "$idx" '[.tool_calls[]? | select(.name=="propose_transaction") | .result] | .[$i] // empty' /tmp/tameru_chat.json
  fi
}

confirm_last_propose() {
  # Usage: confirm_last_propose            # confirms the last proposal
  #        confirm_last_propose 0          # confirms the first proposal
  #        confirm_last_propose --with-card $CARD_ID  # override card_id
  #
  # POSTs a TransactionProposal payload from the last /chat/turn response
  # to /transactions/confirm. The payload is the chat tool's result
  # verbatim — exactly what the parse-card UI would post after the user
  # taps "looks right." If --with-card is supplied, the payload's card_id
  # is overridden (use this when the defensive guard dropped a
  # hallucinated card_id to null and you want to test the confirm path
  # with a real card).
  local idx="last" override_card_id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --with-card) override_card_id="$2"; shift 2 ;;
      *)           idx="$1"; shift ;;
    esac
  done

  local payload
  payload=$(_last_propose_payload "$idx") || {
    echo "ERROR: no propose_transaction result at index '$idx' in last chat response." >&2
    return 1
  }
  if [ -z "$payload" ]; then
    echo "ERROR: extracted empty propose_transaction payload — is the last turn's tool_calls empty?" >&2
    return 1
  fi
  if [ -n "$override_card_id" ]; then
    payload=$(echo "$payload" | jq --arg cid "$override_card_id" '.card_id = $cid')
  fi
  echo "Confirming proposal:"
  echo "$payload" | jq .

  local code
  code=$(curl -s -o /tmp/tameru_confirm.json -w "%{http_code}" \
    -X POST http://localhost:8000/transactions/confirm \
    -H "Authorization: Bearer $JWT" \
    -H "X-Device-Id: $DEVICE_ID" \
    -H "Content-Type: application/json" \
    -d "$payload")
  echo "HTTP $code"
  jq . /tmp/tameru_confirm.json 2>/dev/null || cat /tmp/tameru_confirm.json
}

list_goals() {
  # Read all of the user's goals via PostgREST. RLS scopes the read.
  curl -fsS "$SUPABASE_URL/rest/v1/goals?select=*&order=updated_at.desc" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" | jq
}

list_txns() {
  # Read the user's most recent transactions. Useful after confirm_last_propose
  # to verify the row actually landed.
  local limit="${1:-10}"
  curl -fsS "$SUPABASE_URL/rest/v1/transactions?select=id,merchant,amount,date,category,card_id,gemini_suggestion,source,client_request_id&order=created_at.desc&limit=$limit" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" | jq
}

deactivate_card() {
  # Usage: deactivate_card $CARD_ID
  # Sets active=false on a card you own. Use to verify _card_belongs_to_user
  # drops inactive UUIDs to None on propose. After deactivating, paste the
  # OLD card_id (still in your context from earlier `get_cards` results)
  # into a propose_transaction call and confirm the proposal comes back
  # with card_id=null.
  local cid="${1:?usage: deactivate_card <card_id>}"
  curl -fsS -X PATCH "$SUPABASE_URL/rest/v1/cards?id=eq.$cid" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -H "Prefer: return=representation" \
    -d '{"active": false}' | jq '.[0] | {id, name, active}'
}

seed_second_card() {
  # Usage: seed_second_card "Amex Gold" MR
  # Adds a second active card so you can test alias resolution
  # ("on my Amex" vs "on my Chase") through get_cards + propose_transaction.
  local name="${1:-Amex Gold}"
  local program="${2:-MR}"
  curl -fsS -X POST "$SUPABASE_URL/rest/v1/cards" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" \
    -H "Content-Type: application/json" \
    -H "Prefer: return=representation" \
    -d "{\"user_id\":\"$USER_ID\",\"name\":\"$name\",\"issuer\":\"Amex\",\"program\":\"$program\"}" \
    | jq '.[0] | {id, name, program, active}'
}

# ---- Day 9c: merchant canonicalization helpers ---------------------------
#
# The Python helpers below import from `app.prompts.chat`. They need the
# project venv on PATH — run `source .venv/bin/activate` in this shell
# before calling them. They degrade with a clear error otherwise.

view_top_merchants() {
  # Query the `top_user_merchants` view directly via PostgREST under the
  # user's JWT. This is the raw view data — what render_user_merchants
  # reads before it wraps the rows in framing text. RLS firing here is
  # how you visually confirm `security_invoker = true` on the view DDL:
  # if it weren't set, you'd see every user's merchants here too.
  curl -fsS "$SUPABASE_URL/rest/v1/top_user_merchants?select=*" \
    -H "apikey: $SUPABASE_ANON_KEY" \
    -H "Authorization: Bearer $JWT" | jq
}

show_user_merchants_block() {
  # Print render_user_merchants($JWT) — the exact string that lands in
  # block[1] of the system prompt. Use to verify your seeded merchants
  # appear with the expected frequencies before firing a chat turn that
  # depends on canonicalization.
  python3 - <<'PY' 2>&1
import os, sys
try:
    from app.prompts.chat import render_user_merchants
except ImportError:
    sys.exit("ERROR: cannot import app.prompts.chat — activate the venv first:\n  source .venv/bin/activate")
print(render_user_merchants(os.environ["JWT"]))
PY
}

show_system_prompt() {
  # Print the two-block content array render_system_prompt returns. Block 0
  # is the static preamble with cache_control: ephemeral (cached across all
  # users); block 1 is the per-user dynamic tail (Today is … + merchants,
  # uncached). Confirm the shape before sending it to Claude.
  python3 - <<'PY' 2>&1
import json, os, sys
try:
    from app.prompts.chat import render_system_prompt, system_prompt_hash
    from app.agent.tools import tool_schemas
except ImportError:
    sys.exit("ERROR: cannot import app modules — activate the venv first:\n  source .venv/bin/activate")
blocks = render_system_prompt(user_jwt=os.environ["JWT"])
for i, b in enumerate(blocks):
    cc = b.get("cache_control")
    print(f"=== block[{i}] (type={b['type']}, cache_control={cc}, chars={len(b['text'])}) ===")
    print(b["text"])
    print()
print(f"prompt_hash (block[0] + tool schemas): {system_prompt_hash(blocks, tool_schemas())}")
PY
}

seed_canonical_merchant() {
  # Seed N visits (default 5) to a canonical merchant name so it ranks
  # high enough to land in top_user_merchants. Usage:
  #
  #   seed_canonical_merchant                              # 5 × Kentucky Fried Chicken
  #   seed_canonical_merchant "Trader Joe's"               # 5 × Trader Joe's
  #   seed_canonical_merchant "Kentucky Fried Chicken" 8   # 8 visits
  #
  # After this, asking Claude "spent $10 at KFC" should canonicalize to
  # the seeded name via the merchants block.
  local name="${1:-Kentucky Fried Chicken}"
  local n="${2:-5}"
  local i
  for ((i=1; i<=n; i++)); do
    _seed_txn "$name" "12.00" "$TODAY" "Dining"
  done
  echo "✓ Seeded $n visits to '$name'"
}

test_canonicalization() {
  # End-to-end smoke for Day 9c. Seeds canonical history, prints the
  # merchants block so you can see what Claude sees, then fires a chat
  # turn that uses a variant spelling. Inspect tool_calls[].input.merchant
  # in the chat response — it should be the seeded canonical name, not
  # the variant the user typed.
  local canonical="${1:-Kentucky Fried Chicken}"
  local variant="${2:-KFC}"
  echo "Seeding 5 × '$canonical'…"
  seed_canonical_merchant "$canonical" 5
  echo ""
  echo "Current merchants block (block[1] of the system prompt):"
  echo "--------------------------------------------------------"
  show_user_merchants_block
  echo "--------------------------------------------------------"
  echo ""
  echo "Asking Claude: 'spent \$10 at $variant'"
  echo "Expect tool_calls[].input.merchant == '$canonical' (not '$variant')."
  echo ""
  chat "spent \$10 at $variant"
}

tameru_teardown() {
  curl -s -X DELETE "$SUPABASE_URL/auth/v1/admin/users/$USER_ID" \
    -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" >/dev/null
  echo "✓ Test user $EMAIL deleted"
  unset JWT USER_ID DEVICE_ID CARD_ID EMAIL PASSWORD TAMERU_CONV_ID
  unset -f chat chat_reset tameru_audit tameru_seed_future_dining tameru_teardown \
    confirm_last_propose list_goals list_txns deactivate_card seed_second_card \
    view_top_merchants show_user_merchants_block show_system_prompt \
    seed_canonical_merchant test_canonicalization \
    _last_propose_payload 2>/dev/null
}

set +u

echo ""
echo "Ready."
echo ""
echo "Read tools (Day 9a):"
echo "  chat \"How much on dining this month?\""
echo "  chat \"Show me my coffee transactions\""
echo "  chat \"Where did my money go this month?\""
echo ""
echo "Propose-then-confirm (Day 9b):"
echo "  chat \"spent \$47 at Trader Joe's on my Manual Test Card\""
echo "  confirm_last_propose                # POST the proposal to /transactions/confirm"
echo "  list_txns 5                         # verify the row landed"
echo ""
echo "Set goal (Day 9b — direct write carve-out):"
echo "  chat \"set my dining budget to \$300 a month\""
echo "  list_goals"
echo "  chat \"actually \$250 a month\""
echo "  list_goals                          # still one row, amount=250"
echo ""
echo "Defensive guard tests (Day 9b):"
echo "  seed_second_card \"Amex Gold\" MR    # for alias-resolution tests"
echo "  deactivate_card \$CARD_ID            # then propose with the now-inactive UUID"
echo ""
echo "Merchant canonicalization (Day 9c):"
echo "  view_top_merchants                  # raw view rows under your JWT (RLS check)"
echo "  show_user_merchants_block           # the string render_user_merchants returns"
echo "  show_system_prompt                  # both system-prompt blocks + prompt_hash"
echo "  seed_canonical_merchant             # 5 × 'Kentucky Fried Chicken' (or pass a name)"
echo "  test_canonicalization               # seed history + ask Claude about 'KFC'"
echo "  (these helpers need: source .venv/bin/activate)"
echo ""
echo "Diagnostics:"
echo "  tameru_audit                        # last 10 chat_turn ai_call_log rows"
echo "  tameru_seed_future_dining           # for Issue-1 future-date test"
echo "  tameru_teardown                     # remove the test user when done"
