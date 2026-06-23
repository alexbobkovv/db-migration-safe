# squawk rules

`squawk` is a Postgres static linter (no DB). It reasons about statement **shape**, not
data size — it fires identically on an empty table and a 100M-row table. That is the
right behavior for a CI gate; pair it with `eugene trace` + `table_size.sql` to reason
about real cost.

- Docs: https://squawkhq.com/docs/ · Source: https://github.com/sbdchd/squawk
- Invoke: `squawk migration.sql` · glob `squawk 'migrations/*.sql'` · stdin
  `cat m.sql | squawk --stdin-filepath m.sql`
- JSON: `squawk --reporter json` → a **flat array** of violations.
- Exit: **nonzero on any finding**, 0 only when clean.

## JSON shape (per violation)

```json
{
  "file": "migration.sql",
  "line": 1,
  "column": 0,
  "level": "Warning",
  "message": "...",
  "help": "...",
  "rule_name": "require-concurrent-index-creation",
  "column_end": 0,
  "line_end": 1
}
```

> **Verified against squawk 2.58.0 (plan §10 risk, resolved):** squawk emits **most** lint
> findings at `level: "Warning"` (capitalized) — even destructive rules like
> `ban-drop-table` — and only a few (notably `syntax-error`) at `"Error"`. It signals
> hazards primarily through its **exit code** (nonzero on any finding). So the level field
> alone gives no usable hazard/style split.
>
> **How `analyze.py` derives severity:** it honors a native `"Error"` level (so a
> `syntax-error` is always an error), and otherwise assigns severity **by rule name** —
> the lock/blocking/destructive rules below become `error` (they gate the migration), the
> advisory `prefer-*` / `require-timeout-settings` / `ban-char-field` rules become
> `warning`, and **any rule not in the advisory set defaults to `error`** so a newly added
> hazard rule blocks rather than slipping through. Silence advisory rules you don't enforce
> via `.squawk.toml` `excluded_rules` (verified honored) or inline
> `-- squawk-ignore <rule>` (the rule name must match exactly, or squawk emits
> `unused-ignore`).

## Configuration

- `.squawk.toml`: `excluded_rules`, `included_rules`, `pg_version`,
  `assume_in_transaction`, `excluded_paths`.
- CLI: `--pg-version 13.0` to gate version-specific rules.
- Inline: `-- squawk-ignore <rule>` on the line above a statement.

## Key rules (subset of ~39)

| Rule | Catches | Catalog rewrite |
|---|---|---|
| `require-concurrent-index-creation` | `CREATE INDEX` without `CONCURRENTLY` | #4 |
| `require-concurrent-index-deletion` | `DROP INDEX` without `CONCURRENTLY` | #4 |
| `ban-concurrent-index-creation-in-transaction` | `CONCURRENTLY` inside a txn (it can't) | #4 |
| `constraint-missing-not-valid` | `ADD CONSTRAINT` without `NOT VALID` | #5, #10 |
| `adding-not-nullable-field` | `SET NOT NULL` on an existing column (scans) | #2, #3 |
| `adding-required-field` | new `NOT NULL` column with no safe default | #2 |
| `adding-field-with-default` | `ADD COLUMN ... DEFAULT` (volatile → rewrite) | #1 |
| `adding-foreign-key-constraint` | FK added and validated in one step | #5 |
| `disallowed-unique-constraint` | `ADD CONSTRAINT ... UNIQUE` (builds index locked) | #7 |
| `changing-column-type` | `ALTER COLUMN ... TYPE` rewrite | #6 |
| `ban-drop-column` | `DROP COLUMN` (app breakage, irreversible) | #9 |
| `ban-drop-table` | `DROP TABLE` | irreversible |
| `ban-drop-not-null` | `DROP NOT NULL` | review |
| `renaming-column` | rename breaks running app | #8 |
| `renaming-table` | rename breaks running app | #8 |
| `prefer-robust-stmts` | non-idempotent stmts (no `IF [NOT] EXISTS`) | — |
| `require-timeout-settings` | DDL without `lock_timeout`/`statement_timeout` | EXECUTE |
| `prefer-timestamptz` | `timestamp` instead of `timestamptz` | style |
| `prefer-text-field` | `varchar(n)` instead of `text` | style |
| `prefer-bigint-over-int` | `int` PK that may overflow | style |
| `prefer-identity` | `serial` instead of `GENERATED ... AS IDENTITY` | style |

The full list is in squawk's docs; `analyze.py` carries the rule id straight through to
the verdict, so any rule squawk emits is reported even if it is not in this table.

## Limits

Static only, no DB. Cannot distinguish 100 rows from 100k, cannot observe the real lock
mode/duration or which queries get blocked. For that, escalate to `eugene trace`
(`references/eugene-hints.md`, `scripts/trace.py`).
