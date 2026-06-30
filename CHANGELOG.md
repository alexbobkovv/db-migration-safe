# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
db-migration-safe follows [Semantic Versioning](https://semver.org/) for its public
surface: the `scripts/*` command-line flags and exit codes, and the rule ids and rewrite
shapes documented under `references/`. A change to any of those is breaking.

## [Unreleased]

### Added
- **GitHub Action** (`action.yml`) — drop-in `uses: alexbobkovv/db-migration-safe@v1` that
  analyzes the `.sql` files changed in a PR, uploads **SARIF** so findings annotate the diff
  inline (and appear in the Security tab), and fails the check on any error-level finding. It
  installs `squawk` for authoritative analysis and degrades to the stdlib heuristic if that
  install fails, so the gate never silently no-ops. Validated in CI.
- **SARIF 2.1.0 output** — `analyze.py --sarif` (merged across files) for GitHub code
  scanning. Findings with no line number anchor at line 1; an empty result set is valid and
  clears stale alerts.
- **Multi-file analysis** — `analyze.py` now accepts one or more `.sql` paths. A single file
  keeps the historical `--json` object shape; multiple files emit a JSON list; `--sarif`
  always merges into one run. This is what lets the pre-commit hook and the Action hand a
  batch of changed files to one invocation.
- **pre-commit hook** (`.pre-commit-hooks.yaml`, id `db-migration-safe`) — flags lock/blocking
  hazards in changed `*.sql` on commit. Runs install-free via the heuristic, uses
  squawk/eugene when on PATH.
- **Install-free Postgres fallback.** When neither `squawk` nor `eugene` is installed (or
  with the new `analyze.py --no-external` flag), the Postgres path degrades to a stdlib
  heuristic over the 12 cataloged ops, so `PLAN` and the CI gate still flag obviously-unsafe
  DDL (`CREATE INDEX` without `CONCURRENTLY`, volatile `ADD COLUMN` default, type change,
  direct `SET NOT NULL`, un-validated FK/CHECK/UNIQUE, …) with **zero install** instead of
  the previous hard exit `2`. The verdict is banner-flagged `HEURISTIC MODE` /
  `heuristic_fallback: true` and is explicitly non-authoritative — it cannot see table size,
  partitioning, or a cross-file validated CHECK. Whenever either linter is present,
  `analyze.py` defers to it and the fallback never runs. New eval block M1b; covered by
  `tests/test_analyze_pg_heuristic.py`.
- **`migrate-safe` dispatcher** (`scripts/migrate_safe.py`) — one entrypoint over
  `analyze` / `trace` / `rollback` so CI, pre-commit, the Claude skill, and a future MCP
  server share a single command surface. Pure routing; each subcommand keeps its own flags
  and exit codes. Covered by `tests/test_dispatcher.py`.

### Fixed
- `scripts/trace.py` no longer contradicts itself on a migration that eugene passes: it
  now exits `0` whenever `passed_all_checks` is true, even when a strong but acceptable
  lock (e.g. the brief `AccessExclusiveLock` of a metadata-only `ADD COLUMN` bounded by
  `lock_timeout`) makes `dangerous_locks_count` nonzero. Previously it printed
  `VERDICT: PASS` and still exited `1`. The dangerous-lock count is now only used as a
  fallback gate on older eugene builds that omit `passed_all_checks`.
- `scripts/trace.py` prints a correct hint when `eugene trace` fails because the script
  contains `CREATE/DROP INDEX CONCURRENTLY`: eugene runs the whole migration in one
  transaction, which Postgres forbids for `CONCURRENTLY`, so those statements cannot be
  traced and their safety is established by static lint instead. Previously the failure
  surfaced only the generic "table must already exist" hint.

### Added
- Partitioned-table index handling (`references/postgres-catalog.md` #12). `CONCURRENTLY`
  is unsupported on a partitioned parent index in **both** directions — `CREATE INDEX
  CONCURRENTLY` and `DROP INDEX CONCURRENTLY` each error at runtime — and static linters
  cannot see partitioning, so they pass the very forms that fail (squawk even *recommends*
  the erroring `CONCURRENTLY` drop). Adds the per-partition `CONCURRENTLY` → `CREATE INDEX
  ON ONLY` → `ATTACH` build, the bounded non-concurrent drop, and `scripts/is_partitioned.sql`
  to detect a partitioned parent during PLAN. New eval cases `08_partitioned_index_*` and
  `09_partitioned_drop_index_*`, verified on PostgreSQL 16; cross-model results in
  `evals/eval.md` (Round 3 — the one place the skill corrects a frontier model, not just Haiku).
- Standard-library unit-test suite under `tests/` (`unittest` + `mock`, no DB or external
  binaries) covering the parsing, inversion, severity, and exit-code logic of all three
  scripts; runs in CI.

## [0.1.0] - 2026-06-23

Initial release.

### Added
- `plan → validate → execute` workflow with a behavioral safety gate on EXECUTE
  (`SKILL.md`).
- `scripts/analyze.py` — merges `squawk` + `eugene lint` (Postgres) or InnoDB Online DDL
  heuristics (MySQL) into one verdict; exits nonzero on any error-level finding, so it
  doubles as a CI gate. Prints the squawk/eugene install commands inline when neither
  binary is found.
- `scripts/trace.py` — real-lock observation via `eugene trace` against an ephemeral or
  disposable Postgres.
- `scripts/gen_rollback.py` — generates the reverse migration and flags irreversible or
  manual shapes instead of guessing. Handles quoted identifiers, and treats session
  settings (`SET lock_timeout`, …) and transaction control (`BEGIN`/`COMMIT`/…) as no-ops
  rather than demanding a manual rollback.
- `scripts/table_size.sql` — `pg_class.reltuples` size probe.
- `VERSION` file and a `--version` flag on `analyze.py`, `trace.py`, and
  `gen_rollback.py`, so you can tell which build you have installed.
- Reference catalogs and tool setup under `references/`.
- `CONTRIBUTING.md` and a bug-report issue template.
- Baseline eval corpus and methodology under `evals/`.

[Unreleased]: https://github.com/alexbobkovv/db-migration-safe/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alexbobkovv/db-migration-safe/releases/tag/v0.1.0
