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

**Reading it honestly:** the frontier models (Sonnet, Opus) already know the zero-downtime
patterns and write safe DDL unaided — even on the Round-2 traps. So the skill's measurable
*baseline correction* is concentrated on the cheaper, faster model (Haiku: 1/3 and 2/4
unaided → clean with the skill), which otherwise ships table-rewriting and table-scanning
statements that take a large table down. What the skill adds for *every* model is what no
baseline can: each migration is **machine-verified clean** by squawk + eugene and **ships
with a generated rollback** — "probably safe by hand" becomes "proven safe and reversible."
