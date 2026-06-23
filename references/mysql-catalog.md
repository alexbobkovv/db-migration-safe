# MySQL: the honest hybrid path (materially weaker than Postgres)

**There is no free static MySQL lock-linter.** `gh-ost`/`pt-osc`/`spirit`/fb-OSC
*execute* online schema change but do no hazard analysis; `skeema` lints style/policy,
not locks; Atlas's MySQL lock rules (`MY130–136`) are Pro-only. So the MySQL path in this
skill is a hybrid of three deterministic tactics. Do **not** present it as having parity
with the Postgres path.

The hybrid:

1. Encode the InnoDB Online DDL matrix as heuristic rules (below) — `analyze.py
   --dialect mysql` applies them by regex.
2. Force explicit `ALGORITHM=…, LOCK=…` clauses so MySQL fails loudly instead of
   silently falling back to a blocking COPY.
3. Delegate large rewrites to an OSC tool (`gh-ost` / `pt-osc` / `spirit`).
4. Treat `--dry-run` / gh-ost no-op as **structural validity only**, never lock-hazard
   analysis.

---

## 1. InnoDB Online DDL matrix (the heuristics)

InnoDB classifies each ALTER by **algorithm** and **locking**:

- `ALGORITHM=INSTANT` — metadata-only, no table touch. Safest. (MySQL 8.0+; 8.0.12+ for
  `ADD COLUMN`, 8.0.29+ for adding a column in any position / dropping a column.)
- `ALGORITHM=INPLACE` — rebuilt/modified in place, usually `LOCK=NONE` (DML allowed).
- `ALGORITHM=COPY` — full table copy, typically `LOCK=SHARED` or `EXCLUSIVE`. **This is
  the outage.** Blocks writes (or everything) for the whole copy.

Heuristics `analyze.py` flags as COPY / blocking (verify against your exact MySQL version):

| Operation | Typical algorithm | Flagged? |
|---|---|---|
| `ADD COLUMN` (last position, 8.0.12+) | INSTANT | ok |
| `ADD COLUMN` in the middle / `DROP COLUMN` (< 8.0.29) | COPY | **flag** |
| Change column **type** (e.g. INT→BIGINT) | COPY | **flag** |
| Change column to/from `NULL`/`NOT NULL` | INPLACE (often) | warn |
| Add/drop secondary index | INPLACE, `LOCK=NONE` | ok |
| Add `FULLTEXT` / `SPATIAL` index | INPLACE but `LOCK=SHARED` | **flag** |
| `DROP PRIMARY KEY` (alone) | COPY | **flag** |
| Add/drop `PRIMARY KEY` + re-cluster | COPY | **flag** |
| Add foreign key with `foreign_key_checks=1` | COPY-ish / locks | **flag** |
| Change character set / row format | COPY | **flag** |
| Rename column / rename table | INSTANT/INPLACE | warn (app breakage, as PG #8) |

## 2. Force `ALGORITHM=` and `LOCK=` so MySQL fails loudly

By default MySQL silently downgrades to COPY when an op cannot be done in place. Pin the
intent so the server raises an error instead of quietly blocking production:

```sql
-- errors if the op cannot be done without blocking DML:
ALTER TABLE orders ADD INDEX idx_user (user_id), ALGORITHM=INPLACE, LOCK=NONE;

-- errors if the op is not metadata-only:
ALTER TABLE orders ADD COLUMN note varchar(255), ALGORITHM=INSTANT;
```

If the statement errors, the op is not safe online — that *is* the lint signal. Rewrite it
or delegate to an OSC tool.

## 3. Delegate big rewrites to an OSC tool

For any op the matrix flags as COPY on a large table, do not run the bare `ALTER`. Use an
online schema change tool that builds a shadow table and swaps it in:

- **gh-ost** — triggerless, reads the binlog. Requires `binlog_format=ROW`, a
  PRIMARY/UNIQUE key, and **no foreign keys and no triggers** on the table.
  ```bash
  gh-ost --host=... --database=app --table=orders \
    --alter="ADD COLUMN note varchar(255)" \
    --execute   # omit --execute for a no-op dry run (structural check only)
  ```
- **pt-online-schema-change** — trigger-based. Requires a PRIMARY/UNIQUE key; tolerates
  FKs (with `--alter-foreign-keys-method`) but triggers conflict.
  ```bash
  pt-online-schema-change --alter "ADD COLUMN note varchar(255)" \
    D=app,t=orders --execute   # --dry-run for structural check only
  ```
- **cashapp/spirit** — newer, fast, binlog-based; similar constraints to gh-ost.

## 4. Limits — state these plainly

- `--dry-run` and gh-ost's no-op mode confirm the change is **structurally valid and
  applicable**. They do **not** analyze lock hazards. There is no MySQL equivalent of
  `eugene trace`.
- The matrix above is version-sensitive; INSTANT/INPLACE coverage expands across 8.0.x.
  Always gate on the target server's exact version.
- The honest summary: **Postgres** = free static lock linter + clean
  `NOT VALID`/`CONCURRENTLY` rewrites + real `trace`. **MySQL** = matrix heuristics +
  loud `ALGORITHM=/LOCK=` gating + execute-online tooling. Weaker, by the state of the
  ecosystem, not by choice.
