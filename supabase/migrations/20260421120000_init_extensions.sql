-- Extensions required by the Tameru schema.
--   pgcrypto: gen_random_uuid() used as DEFAULT on every UUID primary key.
--   pg_cron:  scheduled jobs for the subscription auto-logger (DESIGN.md §14.3)
--             and the AICallLog daily rollup (§14.1). No jobs are scheduled in
--             this migration; the extension must exist before any later
--             migration registers a job.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_cron;
