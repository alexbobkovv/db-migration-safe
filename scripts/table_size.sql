-- table_size.sql — estimate a table's row count and on-disk size so PLAN can reason
-- about real lock cost (static linters cannot tell 100 rows from 100k).
--
-- Usage:
--   psql "$DATABASE_URL" -v tablename=public.orders -f scripts/table_size.sql
--
-- reltuples is an ANALYZE/autovacuum estimate; -1 (PG14+) means "never analyzed".

\if :{?tablename}
\else
  \echo 'ERROR: pass -v tablename=<schema.table>  (e.g. -v tablename=public.orders)'
  \quit
\endif

SELECT
  c.oid::regclass                                  AS "table",
  CASE WHEN c.reltuples < 0 THEN NULL
       ELSE c.reltuples::bigint END                AS est_rows,
  pg_size_pretty(pg_total_relation_size(c.oid))    AS total_size,
  pg_size_pretty(pg_relation_size(c.oid))          AS table_size,
  (SELECT count(*) FROM pg_index i WHERE i.indrelid = c.oid) AS index_count,
  s.last_analyze,
  s.last_autoanalyze
FROM pg_class c
LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
WHERE c.oid = :'tablename'::regclass;
