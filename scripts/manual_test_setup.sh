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
#   * `chat "..."` and `tameru_audit` shell functions available
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
  echo "         source .venv/bin/activate && uvicorn app.main:app --reload" >&2
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
  if [ -z "${1:-}" ]; then
    echo "usage: chat \"your message here\"" >&2
    return 2
  fi
  local code
  code=$(curl -s -o /tmp/tameru_chat.json -w "%{http_code}" -X POST http://localhost:8000/chat/turn \
    -H "Authorization: Bearer $JWT" \
    -H "X-Device-Id: $DEVICE_ID" \
    -H "Content-Type: application/json" \
    -d "$(jq -nc --arg m "$1" '{message:$m}')")
  echo "HTTP $code"
  if jq -e . /tmp/tameru_chat.json >/dev/null 2>&1; then
    jq . /tmp/tameru_chat.json
  else
    echo "(non-JSON response):"
    cat /tmp/tameru_chat.json
    echo
  fi
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

tameru_teardown() {
  curl -s -X DELETE "$SUPABASE_URL/auth/v1/admin/users/$USER_ID" \
    -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
    -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" >/dev/null
  echo "✓ Test user $EMAIL deleted"
  unset JWT USER_ID DEVICE_ID CARD_ID EMAIL PASSWORD
  unset -f chat tameru_audit tameru_seed_future_dining tameru_teardown 2>/dev/null
}

set +u

echo ""
echo "Ready. Try:"
echo "  chat \"How much on dining this month?\""
echo "  chat \"Show me my coffee transactions\""
echo "  chat \"Where did my money go this month?\""
echo "  tameru_audit              # last 10 chat_turn ai_call_log rows"
echo "  tameru_seed_future_dining # for Issue-1 future-date test"
echo "  tameru_teardown           # remove the test user when done"
