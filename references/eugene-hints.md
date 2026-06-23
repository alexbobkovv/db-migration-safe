# eugene hints (lint + trace)

`eugene` has two modes:

- **`eugene lint`** — static AST analysis, no DB. Like squawk, fires on statement shape.
- **`eugene trace`** — **executes** the migration and inspects the *real* locks taken.
  This is the only tool here that can tell an empty table from a huge one.

Docs: https://kaveland.no/eugene/ · Source: https://github.com/kaaveland/eugene

## `eugene lint`

```bash
eugene lint migration.sql -f json
```

Flags: `-f json|plain|markdown`, `-i E3` (ignore a hint, repeatable),
`-a/--accept-failures`, `-g/--git-diff <ref>`, `-v name=value` (template vars). Inline
suppression: `-- eugene: ignore E3`. Exit **nonzero** on any hint; syntax errors always
fail.

## `eugene trace`

```bash
# default: throwaway temp server (initdb + pg_ctl in a temp dir, run, delete)
eugene trace migration.sql -f json

# external/disposable clone (rolls back by default; --commit to keep):
PGPASS=<pw> eugene trace migration.sql -f json \
  --disable-temporary --host db.staging --port 5432 --user app --database app
# eugene reads $PGPASS (a password value) or ~/.pgpass — REQUIRED even under trust auth,
# and NOT libpq's $PGPASSWORD; it does not read DATABASE_URL. The clone must already
# contain the table (the migration's ALTER target), or trace errors.
```

`trace` reports, per statement: the real lock mode (e.g. `AccessExclusiveLock`), how long
it was held, **which query classes it blocks**, whether a table/index was actually
rewritten, new/altered columns and constraints, and the effective `lock_timeout`.

### trace JSON — `FullTraceData`

```json
{
  "total_duration_millis": 0,
  "dangerous_locks_count": 0,
  "passed_all_checks": true,
  "all_locks_acquired": [ /* TracedLock */ ],
  "statements": [
    {
      "sql": "...",
      "duration_millis": 0,
      "locks_taken": [ /* TracedLock */ ],
      "lock_timeout_millis": 0,
      "triggered_rules": [ /* Hint */ ]
    }
  ]
}
```

`TracedLock`:
```json
{ "schema": "public", "object_name": "orders", "mode": "AccessExclusiveLock",
  "relkind": "r", "oid": 0, "maybe_dangerous": true,
  "blocked_queries": ["SELECT", "INSERT", "UPDATE", "DELETE"],
  "lock_duration_millis": 0 }
```

`Hint`:
```json
{ "id": "E10", "name": "...", "condition": "...", "effect": "...",
  "workaround": "...", "help": "...", "url": "..." }
```

> `trace.py` reads `FullTraceData` defensively (key names vary across eugene versions);
> confirm field names once with `eugene trace -f json` after install.

## Hints

`E*` are errors (fail), `W*` are warnings.

Mode column is taken directly from `eugene hints` (`has_lint`/`has_trace`) in eugene 0.8.3 —
not from assumptions. `lint+trace` = caught statically and confirmed at runtime;
`trace only` = only observable by executing; `lint only` = static check with no runtime form.

| id | Meaning | Mode | Workaround → catalog |
|---|---|---|---|
| E1 | New constraint added and validated in one step | lint+trace | `NOT VALID` → `VALIDATE` (#5, #10) |
| E2 | Validating a table with a new `NOT NULL` column / `SET NOT NULL` | lint+trace | CHECK pattern (#3) — **statically un-provable across files; `-- eugene: ignore E2`** |
| E3 | New `json` column should be `jsonb` | lint+trace | use `jsonb` |
| E4 | Statements running after an `AccessExclusiveLock` was taken | lint+trace | one AEL op per migration / new txn |
| E5 | Type change forcing a rewrite | lint+trace | expand-contract (#6) |
| E6 | Index created without `CONCURRENTLY` | lint+trace | `CONCURRENTLY` (#4) |
| E7 | New unique constraint builds an index under lock | lint+trace | unique index `CONCURRENTLY` → `USING INDEX` (#7) |
| E8 | Exclusion constraint — **no safe method exists** | lint+trace | accept downtime / redesign |
| E9 | Dangerous lock taken without a `lock_timeout` set | lint+trace | inline `SET lock_timeout = '5s';` before the statement |
| **E10** | **Table/index rewrite observed while holding a dangerous lock** | **trace only** | depends on op; expand-contract |
| E11 | Adding a `SERIAL` / `GENERATED ... STORED` column | lint only | use identity / nullable add |
| **E15** | **Missing index (e.g. an FK column without a covering index)** | **trace only** | add index `CONCURRENTLY` on the referencing column |
| W12 | Multiple `ALTER TABLE` where one will do | lint only | combine compatible actions (never across `NOT VALID`/`VALIDATE`) |
| W13 | Creating an enum | lint only | be aware enums are hard to change; consider a lookup table |
| W14 | Adding a primary key using an index | lint only | review |

**Trace-only hints (E10, E15)** are the payoff for spinning up a database — `lint` and
squawk cannot produce them. Escalate to `trace` whenever the verdict hinges on a possible
rewrite or FK index.

## Limits

eugene is early-stage. `lint` can false-positive (E2 most often — suppress per statement).
E8 (exclusion constraints) and E11 (serial/generated) have no safe workaround — they are
genuine "this op cannot be made online" signals. Prefer the binary or `mise`/Docker
install over `cargo install` (which needs cmake + a C/C++ toolchain) — see
`tool-setup.md`.
