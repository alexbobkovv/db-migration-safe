-- is_partitioned.sql — report whether a table is a partitioned parent, and list its
-- partitions. Static linters (squawk / eugene) cannot see partitioning, yet it changes
-- which rewrite is safe: CREATE INDEX CONCURRENTLY is INVALID on a partitioned parent
-- (see references/postgres-catalog.md #12). Probe before indexing/constraining a table.
--
-- Usage:
--   psql "$DATABASE_URL" -v tablename=public.events -f scripts/is_partitioned.sql

\if :{?tablename}
\else
  \echo 'ERROR: pass -v tablename=<schema.table>  (e.g. -v tablename=public.events)'
  \quit
\endif

SELECT
  c.oid::regclass                                   AS "table",
  (c.relkind = 'p')                                 AS is_partitioned_parent,
  CASE WHEN c.relkind = 'p'
       THEN pg_get_partkeydef(c.oid) END            AS partition_strategy,
  (SELECT count(*) FROM pg_inherits i WHERE i.inhparent = c.oid) AS partition_count
FROM pg_class c
WHERE c.oid = :'tablename'::regclass;

-- The leaf partitions you must build the index on, one CONCURRENTLY each (catalog #12):
SELECT inhrelid::regclass AS partition
FROM pg_inherits
WHERE inhparent = :'tablename'::regclass
ORDER BY 1;
