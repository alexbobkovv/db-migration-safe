-- Case 04f (SAFE): rewrite of case 01, step 3c. PG12+ skips the full scan because a
-- validated CHECK (04d/04e) already proves no nulls. Both linters flag this statically
-- (they cannot see the cross-file CHECK), so suppress the known false positives:
--   eugene E2 and squawk adding-not-nullable-field.
SET lock_timeout = '5s';
-- eugene: ignore E2
-- squawk-ignore adding-not-nullable-field
ALTER TABLE orders ALTER COLUMN processed_at SET NOT NULL;
