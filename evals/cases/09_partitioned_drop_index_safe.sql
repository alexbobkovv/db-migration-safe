-- Case 09 (SAFE): rewrite of 09_partitioned_drop_index_unsafe.sql per postgres-catalog.md #12.
-- A partitioned index cannot be dropped CONCURRENTLY (and its child indexes cannot be dropped
-- independently), so the safe path is a bounded, NON-concurrent DROP on the parent. Dropping an
-- index is metadata-only — no index pages are scanned or rewritten — so the AccessExclusiveLock
-- is held only momentarily. `lock_timeout` makes it fail fast and retry instead of queueing
-- behind a long-running query and stalling the table. Dropping the parent cascades to every
-- child index. Verified end-to-end on PostgreSQL 16 (no index rows remain).
SET lock_timeout = '5s';
-- squawk wants CONCURRENTLY, which is impossible on a partitioned index — suppress the false
-- positive (the same blind spot that lets the unsafe CONCURRENTLY form silently pass static lint):
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX idx_events_user_id;
