-- Case 04g (SAFE): rewrite of case 03, expand-contract step 1. Add the new-typed column;
-- dual-write + batched backfill happen in the app, then reads switch and the old column
-- is dropped in a later, acknowledged migration (irreversible — see gen_rollback.py).
SET lock_timeout = '5s';
ALTER TABLE events ADD COLUMN payload_jsonb jsonb;
