# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
db-migration-safe follows [Semantic Versioning](https://semver.org/) for its public
surface: the `scripts/*` command-line flags and exit codes, and the rule ids and rewrite
shapes documented under `references/`. A change to any of those is breaking.

## [Unreleased]

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
