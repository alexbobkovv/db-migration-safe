-- Case 04b (SAFE): rewrite of case 01, step 1. Add the column nullable, no default.
-- One AccessExclusiveLock op per file + SET lock_timeout so eugene's E9/E4 stay clean.
SET lock_timeout = '5s';
ALTER TABLE orders ADD COLUMN processed_at timestamptz;
