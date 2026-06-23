-- Case 02 (UNSAFE): build an index without CONCURRENTLY.
-- Hazard: blocks all writes to the table for the entire build.
-- Expected findings: squawk require-concurrent-index-creation; eugene E6.
-- Catalog rewrite: postgres-catalog.md #4.
CREATE INDEX idx_orders_user_id ON orders (user_id);
