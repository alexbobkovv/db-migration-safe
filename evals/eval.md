# Evals

Per Anthropic skill guidance: **write evals first, establish a baseline without the
skill, then build the minimum to pass.** Run across Haiku / Sonnet / Opus.

## Baseline (no skill)

Ask the model to write each migration with no skill loaded. It typically produces the
*unsafe* DDL in `cases/01–03` and `06` — the exact statements that cause an outage on a
large table. Record those as the baseline failures.

## With the skill

Load `db-migration-safe`, give the same prompt, and check the skill drives the workflow.

### Static analysis (M1) — needs squawk + eugene (Postgres)

```bash
python3 scripts/analyze.py evals/cases/01_add_column_not_null_default.sql --dialect postgres
python3 scripts/analyze.py evals/cases/02_bare_create_index.sql --dialect postgres
python3 scripts/analyze.py evals/cases/03_alter_column_type.sql --dialect postgres
```

PASS = each exits **nonzero** with the expected rule ids. Verified with squawk 2.58.0 +
eugene 0.8.3 — e.g. case 02 reports `squawk:require-concurrent-index-creation` +
`eugene:E6` + `eugene:E9` (3 errors). A migration the linter cannot parse also fails
(synthetic `tool-error`) — it never silent-passes. If the binaries are absent,
`analyze.py` exits 2 with install guidance — install per `references/tool-setup.md` first.

### Safe rewrite re-lints clean (M2)

The safe rewrites are split one-dangerous-op-per-file (see `references/postgres-catalog.md`,
"Making rewrites lint-clean"). Every file must exit 0 under **both** linters:

```bash
for f in evals/cases/04*.sql; do
  python3 scripts/analyze.py "$f" --dialect postgres || echo "FAILED: $f"
done
```

PASS = every `04*.sql` exits **0** (verified with squawk 2.58.0 + eugene 0.8.3). Each file
carries `SET lock_timeout` where it takes an `AccessExclusiveLock` (else eugene E9), keeps
one AEL op per file (else E4), and `04f_set_not_null.sql` suppresses the unavoidable static
false positives (`-- eugene: ignore E2`, `-- squawk-ignore adding-not-nullable-field`).
Residual output is advisory warnings only.

### Real-lock trace (M3) — needs eugene + Postgres

`trace` needs the table to **pre-exist**, so run it against a disposable clone (eugene's
default temp server starts empty). eugene reads `$PGPASS` (or `~/.pgpass`) for the password
even under trust auth:

```bash
# against a clone that already has the table:
PGPASS=<pw> python3 scripts/trace.py migration.sql \
  --host <clone-host> --port 5432 --user <u> --database <db>
```

PASS (verified): a bare `CREATE INDEX` reports `ShareLock` (blocks writes) + E6/E9; an
`ALTER COLUMN ... TYPE` reports `AccessExclusiveLock` on the table and its pkey (blocks
everything) + E5/E9 and **E10 (real rewrite observed)**, with nonzero
`dangerous_locks_count`.

Two trace nuances confirmed against a real 3M-row table:

- **The non-concurrent safe rewrites PASS** (`passed_all_checks: true`) even when a
  metadata-only step still takes a brief `AccessExclusiveLock` — e.g. `04b` (`ADD COLUMN`
  nullable, bounded by `lock_timeout`). eugene counts that lock in `dangerous_locks_count`
  but still passes, so `trace.py` exits `0`. (A nonzero count alone never overrides a PASS.)
- **`04a` (`CREATE INDEX CONCURRENTLY`) cannot be traced at all**: eugene runs the whole
  script in one transaction and Postgres forbids `CONCURRENTLY` there (SQLSTATE 25001), so
  `trace.py` exits `2` with that explanation. Its safety is established by static lint (M2)
  and the known `SHARE UPDATE EXCLUSIVE` lock, not by trace.

### Rollback generation (M4) — no DB, no external tools

```bash
python3 scripts/gen_rollback.py evals/cases/05_rollback_input.sql
```

PASS (verify in the output):
- `CREATE TABLE audit_log` → `DROP TABLE IF EXISTS audit_log;`
- `ADD COLUMN region` → `DROP COLUMN IF EXISTS region;`
- `ADD CONSTRAINT orders_region_check` → `DROP CONSTRAINT IF EXISTS orders_region_check;`
- `VALIDATE CONSTRAINT` → "no rollback needed"
- `CREATE INDEX CONCURRENTLY idx_orders_region` → `DROP INDEX CONCURRENTLY IF EXISTS idx_orders_region;`
- `RENAME COLUMN region TO ship_region` → `RENAME COLUMN ship_region TO region;`
- `SET NOT NULL` → `DROP NOT NULL`
- `SET lock_timeout` → "no rollback needed" (session setting, no schema inverse — **not** MANUAL)
- `ALTER COLUMN total TYPE` → **MANUAL** (original type unknown)
- `DROP COLUMN legacy_notes` → **IRREVERSIBLE** with a `*_legacy_notes_backup_*` recipe
- reversal statements emitted in **reverse** order (drop the table last)

Summary line: `2 no-op` (VALIDATE CONSTRAINT + SET lock_timeout).

`gen_rollback.py --strict evals/cases/05_rollback_input.sql` exits 1 (contains
irreversible/manual items).

### Quoted-identifier rollback (M4b) — no DB, no external tools

Regression for ISSUE-001 (found by /qa): quoted column / index / constraint names
must invert, not fall through to "manual rollback required".

```bash
python3 scripts/gen_rollback.py evals/cases/07_quoted_identifiers.sql
```

PASS (verify in the output):
- `ADD COLUMN "user id"` → `DROP COLUMN IF EXISTS "user id";`
- `CREATE UNIQUE INDEX CONCURRENTLY "My Idx"` → `DROP INDEX CONCURRENTLY IF EXISTS "My Idx";`
- `ADD CONSTRAINT "my chk"` → `DROP CONSTRAINT IF EXISTS "my chk";`
- `CREATE TABLE "My Orders"` → `DROP TABLE IF EXISTS "My Orders";`
- summary `4 reversible, 0 irreversible, 0 manual`; `gen_rollback.py --strict` exits 0.

### MySQL heuristics (M6) — no external tools

```bash
python3 scripts/analyze.py evals/cases/06_mysql_type_change.sql --dialect mysql
```

PASS = exit nonzero; findings include `mysql-column-type-copy` (error),
`mysql-add-foreign-key` (warning), `mysql-pin-algorithm-lock` (warning).

### Partitioned-table index (M7) — needs Postgres; static lint is INSUFFICIENT here

`CREATE INDEX CONCURRENTLY` on a partitioned *parent* errors at runtime; a plain
`CREATE INDEX` on the parent blocks writes across every partition. Static linters cannot see
that a table is partitioned, so squawk **and** eugene PASS the unsafe form — the catch is the
`is_partitioned.sql` probe (PLAN) or a clone dry-run, not static lint.

```bash
# static lint PASSES the unsafe form (that is the point of this case):
python3 scripts/analyze.py evals/cases/08_partitioned_index_unsafe.sql --dialect postgres   # exits 0
# the deterministic catch the skill adds:
psql "$DATABASE_URL" -v tablename=public.events -f scripts/is_partitioned.sql               # is_partitioned_parent = t
# the safe rewrite re-lints clean and applies online:
python3 scripts/analyze.py evals/cases/08_partitioned_index_safe.sql --dialect postgres      # exits 0
```

PASS (verified on PG 16): the unsafe form errors (`cannot create index on partitioned table
"events" concurrently`); `is_partitioned.sql` reports the parent + its partitions; the safe
per-partition rewrite applies cleanly and leaves the parent index `indisvalid = true`. The
safe file suppresses two false positives on the `ON ONLY` parent line
(`-- eugene: ignore E6`, `-- squawk-ignore require-concurrent-index-creation`) that fire only
because the linters cannot see it is a metadata-only parent index — the same blind spot that
lets the unsafe form slip through.

### Partitioned-table DROP INDEX (M8) — needs Postgres; static lint MISLEADS here

Dropping an index from a partitioned table has **no** concurrent form: `DROP INDEX
CONCURRENTLY` on the parent errors, and a child index cannot be dropped on its own (it is
owned by the parent). Static lint not only passes the erroring `CONCURRENTLY` form — squawk's
`require-concurrent-index-deletion` rule actively *recommends* `CONCURRENTLY`, the exact form
that fails. The catch is again `is_partitioned.sql` (PLAN), not static lint.

```bash
# static lint PASSES the unsafe CONCURRENTLY form (that is the point of this case):
python3 scripts/analyze.py evals/cases/09_partitioned_drop_index_unsafe.sql --dialect postgres   # exits 0
# the safe rewrite re-lints clean (after suppressing the misleading squawk rule) and applies:
python3 scripts/analyze.py evals/cases/09_partitioned_drop_index_safe.sql --dialect postgres       # exits 0
```

PASS (verified on PG 16): the unsafe form errors (`cannot drop partitioned index
"idx_events_user_id" concurrently`); the tempting per-child workaround `DROP INDEX
CONCURRENTLY <child>` also errors (`... because index idx_events_user_id requires it`); the
safe bounded, non-concurrent `DROP INDEX` on the parent lints clean and removes every index
row. Because a `DROP INDEX` is metadata-only, the parent's brief `AccessExclusiveLock` —
bounded by `lock_timeout` — is the correct trade, and the only one available.

## Scoring

A case passes when, with the skill, the model (a) runs `analyze.py`, (b) reports the
hazard in plain language tied to the rule id, (c) emits the cataloged safe rewrite that
re-lints clean, and (d) generates a reviewed rollback. Track pass rate per model; the
skill should lift weaker models from "writes the unsafe DDL" to "produces the safe,
reversible migration," and give *every* model machine-verification and a generated
rollback in place of unaided judgment.

## Cross-model results (measured 2026-06-26)

Run with squawk 2.58.0 + eugene 0.8.3 across three models — Haiku 4.5, Sonnet 4.6,
Opus 4.8 — in two rounds against large `orders`/`customers` tables: common operations,
then deliberately subtle traps. A task is scored *safe at baseline* when the unaided model
avoids the outage pattern (full-table rewrite, unbounded `AccessExclusiveLock`, or a
blocking index/constraint build); single-statement answers were verified through
`analyze.py`. *With the skill*, a task passes when the model runs `analyze.py`, names the
hazard by rule id, emits the cataloged rewrite that re-lints to **0 errors**, and generates
a rollback.

### Round 1 — common operations

T1 add `NOT NULL timestamptz` default `now()` · T2 create index on `orders(user_id)` ·
T3 change `orders.total` `integer`→`bigint`

| | Haiku 4.5 | Sonnet 4.6 | Opus 4.8 |
|---|---|---|---|
| Baseline safe | **1 / 3** | 3 / 3 | 3 / 3 |
| With skill | **3 / 3** | 3 / 3 | 3 / 3 |

Baseline misses (Haiku): T1 unbounded `ADD COLUMN ... DEFAULT now()` (`eugene:E9`); T3
`ALTER COLUMN TYPE` full rewrite (`changing-column-type`/`E5`). Opus's T1 (`lock_timeout`-
bounded single statement) `analyze`-PASSes; Sonnet/Opus otherwise wrote expand-contract.

### Round 2 — subtle traps

C1 add FK `orders(customer_id)→customers(id)` · C2 add `UNIQUE(order_number)` · C3 add
`NOT NULL uuid` defaulting to `gen_random_uuid()` · C4 one migration that adds a column,
adds an FK, *and* `SET NOT NULL`s an existing column

| | Haiku 4.5 | Sonnet 4.6 | Opus 4.8 |
|---|---|---|---|
| Baseline safe | **2 / 4** | 4 / 4 | 4 / 4 |
| With skill | **4 / 4** | 4 / 4 | 4 / 4 |

Baseline misses (Haiku): C3 `ADD COLUMN ... NOT NULL DEFAULT gen_random_uuid()` — a
*volatile* default that rewrites the whole table under `AccessExclusiveLock`; C4 a direct
`ALTER COLUMN status SET NOT NULL` that scans the table under the same lock. Haiku also
wrote C1 as `ADD CONSTRAINT ... NOT VALID` with no follow-up `VALIDATE` — lock-safe, but
the constraint never enforces existing rows (a correctness gap the skill's two-phase step
closes). Sonnet and Opus handled every trap unaided — FK `NOT VALID`, `UNIQUE` via
`CONCURRENTLY` + `USING INDEX`, the volatile-default backfill, and `SET NOT NULL` through a
validated `CHECK`.

### Round 3 — partitioned indexes (a frontier-model gap)

Both tasks operate on `events`, a RANGE-partitioned parent (~800M rows across monthly
partitions) under constant load. `CONCURRENTLY` — the reflexive zero-downtime answer for a
single table — is **rejected by Postgres on a partitioned parent index**, in both directions:

- **P1 — build:** add a btree index on `events(user_id)`. The "obvious" `CREATE INDEX
  CONCURRENTLY ON events (user_id)` errors (`cannot create index ... concurrently`).
- **P2 — drop:** drop the unused `idx_events_user_id`. The "obvious" `DROP INDEX
  CONCURRENTLY idx_events_user_id` errors (`cannot drop partitioned index ... concurrently`),
  and the tempting per-child workaround `DROP INDEX CONCURRENTLY <child>` errors too — child
  indexes are owned by the parent and cannot be dropped on their own.

| (P1 build · P2 drop) | Haiku 4.5 | Sonnet 4.6 | Opus 4.8 |
|---|---|---|---|
| Baseline safe | **0 / 2** | **0 / 2** | 2 / 2 |
| With skill | **2 / 2** | **2 / 2** | 2 / 2 |

Baseline misses, P1: Haiku and Sonnet both wrote `CREATE INDEX CONCURRENTLY ON events
(user_id)` and *confidently asserted* it "cascades to all partitions automatically" — a
hallucinated feature, verified failing on PG 16 across two Sonnet runs. P2: Haiku wrote the
single erroring `DROP INDEX CONCURRENTLY` (again claiming it "cascades to all partition
indexes automatically"); Sonnet *did* know `CONCURRENTLY` is rejected on the parent but then
hallucinated a per-child `DROP INDEX CONCURRENTLY <child>` sequence — which Postgres also
forbids, so its migration still fails at the first statement. Opus got **both** right unaided,
both runs: the per-partition `CONCURRENTLY` → `CREATE INDEX ON ONLY` → `ATTACH` build for P1,
and the bounded non-concurrent `DROP INDEX` on the parent for P2 (explicitly ruling out the
parent-`CONCURRENTLY` and per-child paths). With catalog #12 in context, Sonnet produced the
correct sequence for both (verified on PG 16: parent index `indisvalid = true` after P1; no
index rows remain after P2).

This is the one place the skill measurably corrects a *frontier* model, because the hazard
combines two things the others don't: a feature-restriction blind spot **and** a static-lint
blind spot. squawk/eugene cannot see partitioning, so they pass the erroring `CONCURRENTLY`
forms — and for the drop, squawk's `require-concurrent-index-deletion` rule actively
*recommends* the form that fails. Only the `is_partitioned.sql` probe (PLAN) catches it.

The gap is specific to *indexes*. Probing adjacent partitioned-table operations, baseline
Sonnet handled them unaided and was **not** fooled: adding a `UNIQUE`/`PK` on a non-partition
-key column (it correctly called the native constraint impossible and proposed a shadow-table
+ trigger), `DETACH PARTITION` (correct `DETACH ... CONCURRENTLY`, outside a txn), and
attaching a populated table (correct pre-validated matching `CHECK` so `ATTACH` skips the
scan). The common thread of the two it *fails* is a reflexive `CONCURRENTLY` on a partitioned
index — the one spot where the familiar single-table primitive is silently unavailable.

**Reading it honestly:** the frontier models (Sonnet, Opus) already know the zero-downtime
patterns and write safe DDL unaided on Rounds 1–2 and on most of Round 3's neighborhood. The
one measured exception is the partitioned-*index* pair above, where Sonnet (and Haiku)
reach for an unsupported `CONCURRENTLY` form and only Opus gets it right unaided. So the
skill's measurable *baseline correction* is concentrated on the cheaper, faster model (Haiku:
1/3 and 2/4 unaided → clean with the skill) plus this frontier-model gap for Sonnet — all
cases where the model otherwise ships statements that take a large table down or fail at
deploy. What the skill adds for *every* model is what no baseline can: each migration is
**machine-verified clean** by squawk + eugene and **ships with a generated rollback** —
"probably safe by hand" becomes "proven safe and reversible."
