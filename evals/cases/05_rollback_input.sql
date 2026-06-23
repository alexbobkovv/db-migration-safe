-- Case 05 (ROLLBACK): mixed forward migration to exercise gen_rollback.py.
-- Expect: CREATE TABLE / ADD COLUMN / ADD CONSTRAINT / CREATE INDEX CONCURRENTLY /
-- RENAME COLUMN / SET NOT NULL  => auto-reversed.
-- DROP COLUMN => irreversible (backup recipe). VALIDATE => no-op. TYPE change => manual.
-- SET lock_timeout => no-op (session setting, no schema inverse).
SET lock_timeout = '5s';
CREATE TABLE audit_log (id bigserial PRIMARY KEY, payload jsonb);
ALTER TABLE orders ADD COLUMN region text;
ALTER TABLE orders ADD CONSTRAINT orders_region_check CHECK (region IS NOT NULL) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_region_check;
CREATE INDEX CONCURRENTLY idx_orders_region ON orders (region);
ALTER TABLE orders RENAME COLUMN region TO ship_region;
ALTER TABLE orders ALTER COLUMN ship_region SET NOT NULL;
ALTER TABLE orders ALTER COLUMN total TYPE numeric(12,2);
ALTER TABLE orders DROP COLUMN legacy_notes;
