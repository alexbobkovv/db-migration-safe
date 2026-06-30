# Postgres: unsafe operation → safe rewrite catalog

The core knowledge of this skill. Every rewrite is built on two Postgres primitives:

- **`CONCURRENTLY`** — build or drop an index without taking an `ACCESS EXCLUSIVE` lock,
  so writes are not blocked. Must run **outside** a transaction block.
- **`NOT VALID` → `VALIDATE CONSTRAINT`** — add a constraint in two phases. Adding it
  `NOT VALID` takes a brief lock and skips the full-table check; the later `VALIDATE
  CONSTRAINT` scans the table under a weak `SHARE UPDATE EXCLUSIVE` lock that does **not**
  block reads or writes.

MySQL has neither primitive — see `mysql-catalog.md`.

## Lock levels you care about

| Lock | Blocks | Taken by |
|---|---|---|
| `ACCESS EXCLUSIVE` | everything (incl. `SELECT`) | most `ALTER TABLE`, `CREATE INDEX` (non-concurrent), table rewrites |
| `SHARE` | writes | non-concurrent index build |
| `SHARE UPDATE EXCLUSIVE` | nothing (other DDL/vacuum only) | `CREATE INDEX CONCURRENTLY`, `VALIDATE CONSTRAINT`, `SET NOT NULL` in PG12+ with a validated CHECK |

The hazard is rarely the lock *level* alone — it is **lock level × how long it is held**.
An `ACCESS EXCLUSIVE` lock for 2ms (a metadata-only change) is fine; the same lock held
for the duration of a full-table rewrite is an outage. That is why size matters
(`scripts/table_size.sql`) and why `eugene trace` exists.

## Making rewrites lint-clean (apply to every step below)

The per-operation SQL below shows the lock-reducing *shape*. To also pass `eugene lint`
(and squawk) cleanly — the VALIDATE gate — apply these three rules, verified against
eugene 0.8.3 + squawk 2.58.0:

1. **Prepend `SET lock_timeout = '5s';`** to every statement that takes an
   `ACCESS EXCLUSIVE` lock (almost every `ALTER TABLE`, and `VALIDATE CONSTRAINT`).
   Without it, eugene raises **E9** ("dangerous lock without timeout"). `CONCURRENTLY`
   index builds take `SHARE UPDATE EXCLUSIVE`, so they need no timeout.
2. **One dangerous (AEL) operation per migration file.** Two AEL statements in one
   file/transaction triggers eugene **E4** ("statements after `AccessExclusiveLock`").
   In particular, put `ADD CONSTRAINT ... NOT VALID` and its `VALIDATE CONSTRAINT` in
   **separate** files. (`CONCURRENTLY` and `VALIDATE` do not count as AEL.)
3. **`SET NOT NULL` is statically un-provable.** Both linters flag it (eugene **E2**,
   squawk **adding-not-nullable-field**) because they cannot see the validated CHECK in
   the previous file. Suppress the known false positives inline:
   ```sql
   -- eugene: ignore E2
   -- squawk-ignore adding-not-nullable-field
   ```

See `evals/cases/04*.sql` for the fully lint-clean form of every rewrite below.

---

## 1. `ADD COLUMN` with a DEFAULT

**Why dangerous:** a *volatile* default (e.g. `now()`, `gen_random_uuid()`) rewrites the
whole table under `ACCESS EXCLUSIVE`. **PG11+: a *constant* default is metadata-only** and
safe — Postgres stores it as a "missing value" and does not rewrite.

**Unsafe (volatile default on a big table):**
```sql
ALTER TABLE orders ADD COLUMN created_at timestamptz NOT NULL DEFAULT now();
```

**Safe rewrite:**
```sql
-- 1. add the column nullable, no default
ALTER TABLE orders ADD COLUMN created_at timestamptz;
-- 2. set the default for NEW rows only (metadata-only)
ALTER TABLE orders ALTER COLUMN created_at SET DEFAULT now();
-- 3. backfill existing rows in batches (see #11)
-- 4. add NOT NULL via the CHECK pattern (see #3) if required
```

A constant default needs no rewrite on PG11+:
```sql
ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'pending';  -- safe, PG11+
```

## 2. `ADD COLUMN NOT NULL` (no usable default)

**Why dangerous:** forces a table rewrite/validation to prove no nulls exist.

**Safe rewrite:** add nullable → set a default for new rows → backfill (#11) → add the
NOT NULL via the CHECK pattern (#3). Same shape as #1.

## 3. `SET NOT NULL` on an existing column

**Why dangerous:** a plain `SET NOT NULL` full-scans the table under `ACCESS EXCLUSIVE`.

**Unsafe:**
```sql
ALTER TABLE users ALTER COLUMN email SET NOT NULL;
```

**Safe rewrite (PG12+ can then skip the second scan):**
```sql
-- 1. add the check NOT VALID (brief lock, no scan)
ALTER TABLE users ADD CONSTRAINT users_email_not_null CHECK (email IS NOT NULL) NOT VALID;
-- 2. validate under SHARE UPDATE EXCLUSIVE (does not block reads/writes) — separate file (E4)
ALTER TABLE users VALIDATE CONSTRAINT users_email_not_null;
-- 3. PG12+: SET NOT NULL now skips the scan because a validated CHECK proves it.
--    Linters can't see the CHECK across files — suppress the false positives:
-- eugene: ignore E2
-- squawk-ignore adding-not-nullable-field
ALTER TABLE users ALTER COLUMN email SET NOT NULL;
-- 4. optional: drop the now-redundant check
ALTER TABLE users DROP CONSTRAINT users_email_not_null;
```

On PG11 and earlier, keep the validated `CHECK` instead of `SET NOT NULL`.

## 4. `CREATE INDEX` without `CONCURRENTLY`

**Why dangerous:** blocks all writes to the table for the entire build.

**Unsafe:**
```sql
CREATE INDEX idx_orders_user_id ON orders (user_id);
```

**Safe rewrite (outside any transaction):**
```sql
CREATE INDEX CONCURRENTLY idx_orders_user_id ON orders (user_id);
```

`CONCURRENTLY` cannot run inside a transaction block. If it fails midway it leaves an
`INVALID` index — drop it before retrying:
```sql
DROP INDEX CONCURRENTLY IF EXISTS idx_orders_user_id;
```

## 5. `ADD FOREIGN KEY`

**Why dangerous:** validating the FK blocks writes on **both** tables.

**Unsafe:**
```sql
ALTER TABLE orders ADD CONSTRAINT orders_user_fk FOREIGN KEY (user_id) REFERENCES users (id);
```

**Safe rewrite:**
```sql
-- 1. add the constraint without validating (enforced for new/changed rows immediately)
ALTER TABLE orders ADD CONSTRAINT orders_user_fk
  FOREIGN KEY (user_id) REFERENCES users (id) NOT VALID;
-- 2. validate existing rows under a weak lock
ALTER TABLE orders VALIDATE CONSTRAINT orders_user_fk;
```

Also ensure the **referencing** column has a covering index (eugene E15) — without it,
FK checks and cascading deletes are slow.

## 6. Change column TYPE

**Why dangerous:** a full table rewrite under `ACCESS EXCLUSIVE` (in MySQL, a COPY).
Some Postgres casts are **metadata-only** and do not rewrite, e.g. `varchar(n)` → `text`,
or widening `varchar(n)` → `varchar(m)` where `m > n`.

**Unsafe (rewriting cast):**
```sql
ALTER TABLE events ALTER COLUMN payload TYPE jsonb USING payload::jsonb;
```

**Safe rewrite — expand/contract:**
```sql
-- 1. add the new column
ALTER TABLE events ADD COLUMN payload_jsonb jsonb;
-- 2. dual-write from the app (or a trigger) so new writes populate both
-- 3. backfill existing rows in batches (#11)
-- 4. switch reads to payload_jsonb, deploy
-- 5. drop the old column (#9, irreversible — back up first)
ALTER TABLE events DROP COLUMN payload;
ALTER TABLE events RENAME COLUMN payload_jsonb TO payload;
```

## 7. `ADD CONSTRAINT ... UNIQUE`

**Why dangerous:** builds the backing unique index under `ACCESS EXCLUSIVE`.

**Unsafe:**
```sql
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
```

**Safe rewrite:**
```sql
-- 1. build the unique index concurrently (outside txn)
CREATE UNIQUE INDEX CONCURRENTLY users_email_key ON users (email);
-- 2. attach it as the constraint (fast — reuses the existing index)
ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE USING INDEX users_email_key;
```

## 8. Rename column / table

**Why dangerous:** the running application has the old name cached; the rename breaks it
the instant it commits, until every app instance is redeployed.

**Safe rewrite — expand/contract or compatibility view:**
```sql
-- Option A: expand-contract
--   add new col → dual-write → backfill → switch reads → drop old (#9)
-- Option B: rename behind a compatibility VIEW
ALTER TABLE users RENAME COLUMN signup_date TO created_at;
CREATE VIEW users_compat AS SELECT *, created_at AS signup_date FROM users;
--   deploy app reading the new name, then drop the view
```

The rename itself takes only a brief lock — the danger is application breakage, not the DB.

## 9. Drop column

**Why dangerous:** ORMs/apps cache the column list and throw `column does not exist`
until redeployed; can cascade to dependent objects. **Irreversible** from DDL alone.

**Safe sequence:**
```sql
-- 1. stop referencing the column in application code + deploy
-- 2. back up before dropping (gen_rollback.py emits this):
CREATE TABLE users_legacy_field_backup_20260623 AS
  SELECT id, legacy_field FROM users;
-- 3. drop (explicit acknowledgement)
ALTER TABLE users DROP COLUMN legacy_field;
```

The drop takes a brief `ACCESS EXCLUSIVE` lock (no rewrite) but is logically destructive.

## 10. `ADD CHECK` constraint

**Why dangerous:** validating the check scans the table and blocks writes.

**Unsafe:**
```sql
ALTER TABLE orders ADD CONSTRAINT orders_total_positive CHECK (total >= 0);
```

**Safe rewrite:**
```sql
ALTER TABLE orders ADD CONSTRAINT orders_total_positive CHECK (total >= 0) NOT VALID;
ALTER TABLE orders VALIDATE CONSTRAINT orders_total_positive;
```

## 11. Backfill via a single `UPDATE`

**Why dangerous:** one big `UPDATE` holds row locks for its whole duration, bloats WAL,
and causes replica lag.

**Unsafe:**
```sql
UPDATE orders SET status = 'legacy' WHERE status IS NULL;
```

**Safe rewrite — bounded batches in separate short transactions:**
```sql
-- run in a loop (psql \gexec, a script, or the app), repeat until 0 rows:
UPDATE orders SET status = 'legacy'
WHERE id IN (
  SELECT id FROM orders WHERE status IS NULL ORDER BY id LIMIT 5000
);
-- COMMIT between batches; sleep briefly to let replication/autovacuum catch up
```

## 12. Index on a PARTITIONED table

**Why dangerous:** `CREATE INDEX CONCURRENTLY` is **not supported on a partitioned parent**
— Postgres raises `cannot create index on partitioned table ... concurrently` and the
migration **fails outright**. The naive fallback, a plain `CREATE INDEX` on the parent, *does*
run but takes a `SHARE` lock that recurses into **every** partition and blocks writes across
the whole table for the entire build — an outage on a large partitioned table. Static linters
do not know a table is partitioned, so they pass **both** forms; probe with
`scripts/is_partitioned.sql` before indexing, or escalate to a clone dry-run.

**Unsafe (errors at runtime on a partitioned parent):**
```sql
CREATE INDEX CONCURRENTLY idx_events_user_id ON events (user_id);
```

**Safe rewrite — build per-partition concurrently, then attach to an `ONLY` parent index:**
```sql
-- 1. build the index CONCURRENTLY on each existing partition (each its own statement, outside a txn)
CREATE INDEX CONCURRENTLY idx_events_user_id_2026_06 ON events_2026_06 (user_id);
CREATE INDEX CONCURRENTLY idx_events_user_id_2026_07 ON events_2026_07 (user_id);
-- 2. create the parent index ON ONLY — metadata-only, no recursion; it starts INVALID
CREATE INDEX idx_events_user_id ON ONLY events (user_id);
-- 3. attach each partition's index; the parent flips to VALID once all are attached
ALTER INDEX idx_events_user_id ATTACH PARTITION idx_events_user_id_2026_06;
ALTER INDEX idx_events_user_id ATTACH PARTITION idx_events_user_id_2026_07;
```

Partitions created later via `CREATE TABLE ... PARTITION OF events` inherit the index
automatically once the parent index exists. The same `ONLY`-parent-then-attach shape applies
to a `UNIQUE`/`PRIMARY KEY` (the partition key must be part of the columns) and to
`CHECK`/`FK` constraints added `NOT VALID` and validated per partition.

**Dropping a partitioned index is also not concurrent.** `DROP INDEX CONCURRENTLY` on the
parent raises `cannot drop partitioned index ... concurrently`, and a child index cannot be
dropped on its own (`cannot drop index <child> because index <parent> requires it` — the
children are owned by the parent). A `DROP INDEX` is metadata-only, though — no pages are
scanned or rewritten — so the safe path is a **bounded, non-concurrent** drop on the parent,
which cascades to every child:
```sql
SET lock_timeout = '5s';        -- fail fast instead of queueing behind a long query
DROP INDEX idx_events_user_id;  -- brief AccessExclusiveLock, no data work; CONCURRENTLY is unavailable here
```
squawk's `require-concurrent-index-deletion` rule recommends `CONCURRENTLY` here — exactly the
form that errors on a partitioned index — so suppress it with
`-- squawk-ignore require-concurrent-index-deletion`.

---

## PG-version nuances to encode

- **PG11**: constant column defaults are metadata-only (no rewrite); volatile defaults
  still rewrite. `ADD COLUMN ... DEFAULT <const>` is safe from PG11.
- **PG12**: `SET NOT NULL` skips the full scan when a **validated** `CHECK (c IS NOT
  NULL)` already exists on the column. This is what makes rewrite #3 cheap.
- **Always**: `CREATE/DROP INDEX CONCURRENTLY` and `VALIDATE CONSTRAINT` must each be
  their own statement, never inside an explicit transaction with other work.
