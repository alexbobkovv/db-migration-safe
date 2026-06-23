-- Case 01 (UNSAFE): add a NOT NULL column with a volatile default on a large table.
-- Hazard: volatile default rewrites the whole table under ACCESS EXCLUSIVE (outage).
-- Expected findings: squawk adding-field-with-default / adding-not-null-field;
--                    eugene E2 (and E5/E10 on a real rewrite via trace).
-- Catalog rewrites: postgres-catalog.md #1, #2, #3.
ALTER TABLE orders ADD COLUMN processed_at timestamptz NOT NULL DEFAULT now();
