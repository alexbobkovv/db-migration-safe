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
`dangerous_locks_count`. The split safe rewrites (`04*.sql`) take no dangerous locks.

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
skill should lift every model from "writes the unsafe DDL" to "produces the safe,
reversible migration."
