-- Case 08 (UNSAFE): add an index to a RANGE-partitioned parent table `events`.
-- Hazard: CREATE INDEX CONCURRENTLY is NOT supported on a partitioned parent — Postgres
--   raises `cannot create index on partitioned table "events" concurrently` and the
--   migration FAILS. (A plain non-concurrent CREATE INDEX on the parent would instead take
--   a SHARE lock that blocks writes across every partition for the whole build.)
-- NOTE: static linters cannot see that `events` is partitioned, so squawk + eugene both
--   PASS this file (0 errors). The hazard is caught by scripts/is_partitioned.sql in PLAN,
--   or by a clone dry-run — NOT by static lint. This is exactly why the probe exists.
-- Catalog rewrite: postgres-catalog.md #12 (see 08_partitioned_index_safe.sql).
CREATE INDEX CONCURRENTLY idx_events_user_id ON events (user_id);
