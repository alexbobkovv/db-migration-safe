-- Case 04c (SAFE): rewrite of case 01, step 2. Default for NEW rows only (metadata-only).
-- Backfill existing rows in batches (see case 11 pattern) between this and 04d.
SET lock_timeout = '5s';
ALTER TABLE orders ALTER COLUMN processed_at SET DEFAULT now();
