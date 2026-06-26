# Contributing

Thanks for helping make schema migrations less scary. This is a small, dependency-light
project and it intends to stay that way.

## What it is

`db-migration-safe` is a Claude/Agent skill plus three Python scripts that wrap existing
lock linters (`squawk`, `eugene`) and OSC tools (`gh-ost`/`pt-osc`). The scripts are
**Python 3 standard library only** — no `pip install`, no runtime dependencies. Please
keep it that way; if a change seems to need a third-party package, open an issue first so
we can find a stdlib path.

## Setup

- Python 3.8 or newer. That is all you need for `gen_rollback.py` and the MySQL
  heuristics in `analyze.py`.
- For the Postgres lint path and `trace.py`, install `squawk` and `eugene` per
  [`references/tool-setup.md`](references/tool-setup.md).

```bash
git clone <this repo> ~/.claude/skills/db-migration-safe
cd ~/.claude/skills/db-migration-safe
python3 scripts/gen_rollback.py evals/cases/05_rollback_input.sql   # no external tools needed
```

## Running the tests

Unit tests cover the scripts' parsing, inversion, severity, and exit-code logic. They are
standard-library only (`unittest` + `mock`) and need no database or external binaries:

```bash
python3 -m unittest discover -s tests -v
```

The eval suite is the end-to-end test suite. It is described in
[`evals/eval.md`](evals/eval.md); each block (M1–M6) lists the exact command and the
expected output.

- **No external tools:** rollback generation (M4) and MySQL heuristics (M6).
  ```bash
  python3 scripts/gen_rollback.py evals/cases/05_rollback_input.sql
  python3 scripts/analyze.py evals/cases/06_mysql_type_change.sql --dialect mysql
  ```
- **squawk + eugene:** static analysis (M1) and the clean-rewrite check (M2).
  ```bash
  for f in evals/cases/04*.sql; do
    python3 scripts/analyze.py "$f" --dialect postgres || echo "FAILED: $f"
  done
  ```
- **eugene + a Postgres:** real-lock trace (M3).

The verified tool versions live at the top of `evals/eval.md` (currently squawk 2.58.0,
eugene 0.8.3). If you test against newer ones, update that line.

## Adding a rule, rewrite, or case

- An unsafe operation and its zero-downtime rewrite belong in
  `references/postgres-catalog.md` (or `references/mysql-catalog.md`). Keep the catalog
  and the `analyze.py` / `gen_rollback.py` behavior in sync.
- A new rule id from squawk or eugene should be explained in
  `references/squawk-rules.md` / `references/eugene-hints.md`.
- A new behavior deserves an eval case in `evals/cases/` and a matching expectation in
  `evals/eval.md`.

## Invariants — please don't break these

- **Never emit a silently-wrong inverse.** `gen_rollback.py` must reverse an op
  correctly, mark it irreversible with a backup recipe, or say "manual rollback
  required" — never guess.
- **Deterministic verdicts.** `analyze.py` exits nonzero on any error-level finding and
  must not silent-pass a migration the linter could not parse.
- **EXECUTE stays user-triggered.** PLAN and VALIDATE may run freely; applying DDL is
  gated behaviorally — see the safety contract in `SKILL.md`.

## Reporting bugs

Use the bug report template. The most useful reports include the migration SQL (redacting
table/column names is fine), the `--dialect`, your `squawk --version` / `eugene --version`,
and the full output you got versus what you expected. "The verdict was wrong" — a false
positive, or a missed hazard — is the single most valuable kind of report, because it is
how the catalogs get better. If something could affect a production database, flag it
clearly in the issue.

## Releasing

1. Move the `## [Unreleased]` notes in `CHANGELOG.md` under a new version heading.
2. Bump `VERSION`. Semver: breaking changes to script flags, exit codes, rule ids, or
   rewrite shapes are major; new safe behavior is minor; fixes are patch.
3. Commit and tag `vX.Y.Z`.
