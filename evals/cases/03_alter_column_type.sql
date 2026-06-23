-- Case 03 (UNSAFE): change a column's type (rewriting cast).
-- Hazard: full table rewrite under ACCESS EXCLUSIVE.
-- Expected findings: squawk changing-column-type; eugene E5 (E10 on real rewrite via trace).
-- Catalog rewrite: postgres-catalog.md #6 (expand-contract).
ALTER TABLE events ALTER COLUMN payload TYPE jsonb USING payload::jsonb;
