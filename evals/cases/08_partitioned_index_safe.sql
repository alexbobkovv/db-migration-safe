-- Case 08 (SAFE): rewrite of 08_partitioned_index_unsafe.sql per postgres-catalog.md #12.
-- Build the index CONCURRENTLY on each existing partition, create the parent index ON ONLY
-- (metadata-only, starts INVALID), then ATTACH each partition index; the parent flips to
-- VALID once all are attached. Every step keeps `events` online. Future partitions inherit
-- the index automatically once the parent index exists.
-- Each CREATE INDEX CONCURRENTLY must run OUTSIDE a transaction. Verified end-to-end against
-- a real partitioned table on PostgreSQL 16 (parent index indisvalid = true).
CREATE INDEX CONCURRENTLY idx_events_user_id_2026_06 ON events_2026_06 (user_id);
CREATE INDEX CONCURRENTLY idx_events_user_id_2026_07 ON events_2026_07 (user_id);
-- The parent CREATE INDEX ON ONLY is metadata-only (the partitioned parent holds no rows),
-- but neither linter knows `events` is a parent, so both false-positive here exactly as they
-- silently pass the unsafe form — squawk wants CONCURRENTLY (impossible on a parent) and
-- eugene raises E6. Bound the brief catalog lock and suppress the known false positives:
SET lock_timeout = '5s';
-- eugene: ignore E6
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX idx_events_user_id ON ONLY events (user_id);
ALTER INDEX idx_events_user_id ATTACH PARTITION idx_events_user_id_2026_06;
ALTER INDEX idx_events_user_id ATTACH PARTITION idx_events_user_id_2026_07;
