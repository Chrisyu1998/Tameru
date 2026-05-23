"""Scheduled jobs that run outside the request lifecycle.

Files in this directory are sanctioned to import `supabase_admin`
(CLAUDE.md invariant 1) because by definition they have no user JWT
in scope — they iterate users, write `ai_call_log` rows under
`task_type='digest'` or similar, and call `email_log_insert_idempotent`.
The directory-level exclusion in `tests/contracts/test_no_service_role_leak.py`
lets that import live here without flagging the leak guard.
"""
