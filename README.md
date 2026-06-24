# db-migration-safe

[![CI](https://github.com/alexbobkovv/db-migration-safe/actions/workflows/ci.yml/badge.svg)](https://github.com/alexbobkovv/db-migration-safe/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)
![Dependencies: stdlib only](https://img.shields.io/badge/dependencies-stdlib_only-success.svg)
[![Postgres-first](https://img.shields.io/badge/postgres-first-336791.svg)](references/postgres-catalog.md)
[![Verified: squawk 2.58 · eugene 0.8.3](https://img.shields.io/badge/verified-squawk_2.58_%C2%B7_eugene_0.8.3-blue.svg)](references/tool-setup.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

An open-source Claude/Agent **Skill** that makes SQL schema migrations safe. It detects
locking and blocking hazards, rewrites unsafe DDL into zero-downtime multi-step
migrations, auto-generates rollbacks, and gates execution behind a
`plan → validate → execute` workflow.

- **Postgres-first** — strong and deterministic (static lint → safe rewrite → real-lock
  trace → rollback → gated execute).
- **MySQL** — an honest hybrid (InnoDB matrix heuristics + execute-online tooling),
  materially weaker, and labeled as such.
- **Engine-agnostic** — works on hand-written `.sql` or any ORM's generated SQL.
- **Free / Apache-2.0** — it *wraps* existing open-source linters; it does not rebuild them.

## Why this exists

The migration-safety space splits into two halves that do not overlap:

- **Shallow pattern-skills** that describe expand-contract in prose but do zero pre-flight
  lock analysis, no rollback generation, no tool integration.
- **Excellent standalone lock linters that no skill wraps** — `squawk`, `eugene`,
  `gh-ost`/`pt-osc`.

The one serious competitor, **Atlas Agent Skills**, is engine-locked *and* paywalls the
rules that matter: as of Atlas v0.38 (2025-10-28), every Postgres concurrency/lock
analyzer (`PG101–105`, `PG301–311`) and the MySQL blocking rules (`MY130–136`) are Pro.

**No open-source skill orchestrates `squawk` + `eugene` (or the MySQL equivalents) into a
plan→validate→execute loop with size-aware lock reasoning and auto-generated rollbacks.**
This fills that gap.

| | Atlas Agent Skills | squawk / eugene alone | **db-migration-safe** |
|---|---|---|---|
| Lock-hazard analysis | paywalled (Pro) | yes, but unwrapped | wraps squawk + eugene `lint` + `trace`, one verdict |
| Engine lock-in | requires Atlas engine | none | none — raw `.sql` or any ORM |
| "100 rows vs 100k" | — | `trace` only, manual | `trace` on an ephemeral DB, orchestrated |
| Safe rewrite | describes | warns | emits the concrete multi-step rewrite |
| Rollback generation | up/down convention | none | generated from the DDL; flags irreversible ops |
| Cost | paid lock rules | free | free / OSS |

## Install

This is a Claude Code skill. Drop it where your skills live:

```bash
git clone https://github.com/alexbobkovv/db-migration-safe ~/.claude/skills/db-migration-safe
```

Then install the binaries it shells out to (only what you need) — see
[`references/tool-setup.md`](references/tool-setup.md):

```bash
npm install -g squawk-cli          # or pip install squawk-cli
mise use ubi:kaaveland/eugene@latest
```

The skill **shells out** to `squawk`/`eugene`/`gh-ost` as separate processes — it does
not bundle them, which keeps it lightweight and license-clean.

## Quickstart

```bash
# 1. PLAN — static analysis (no DB)
python3 scripts/analyze.py migration.sql --dialect postgres

# 2. rewrite the flagged statements per references/postgres-catalog.md → migration.safe.sql

# 3. VALIDATE — re-lint until zero error-level findings
python3 scripts/analyze.py migration.safe.sql --dialect postgres

# 4. ROLLBACK — generate the reverse migration
python3 scripts/gen_rollback.py migration.safe.sql > migration.rollback.sql

# 5. TRACE (optional) — observe real locks on an ephemeral Postgres
python3 scripts/trace.py migration.safe.sql

# 6. EXECUTE — user-triggered only; apply with lock_timeout + CONCURRENTLY (see SKILL.md)
```

## Workflow

| Phase | What it does | Touches your DB? |
|---|---|---|
| **PLAN** | `squawk` + `eugene lint` → merged verdict; size probe; optional `trace` | no |
| **VALIDATE** | re-lint the rewrite; gate on zero error-level findings | no |
| **EXECUTE** | apply with `lock_timeout`, `CONCURRENTLY`, batched backfill | **yes — user-triggered only** |

`PLAN`/`VALIDATE` are analysis only and safe to auto-run. **EXECUTE never auto-fires** —
it runs only when you explicitly ask to apply the migration (see the Safety contract in
`SKILL.md`).

## CI gate

`analyze.py` exits nonzero on any error-level finding, so it drops straight into CI:

```yaml
# .github/workflows/migrations.yml
name: migration-safety
on: pull_request
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install -g squawk-cli
      - run: mise use ubi:kaaveland/eugene@latest
      - run: |
          for f in $(git diff --name-only origin/${{ github.base_ref }}... -- '*.sql'); do
            python3 scripts/analyze.py "$f" --dialect postgres || exit 1
          done
```

## Versioning & upgrading

The installed version is in `VERSION`, and every script reports it:

```bash
python3 scripts/analyze.py --version
```

It is a skill you install by cloning, so you upgrade by pulling:

```bash
git -C ~/.claude/skills/db-migration-safe pull
```

[`CHANGELOG.md`](CHANGELOG.md) records what changed. The project follows semver for its
public surface — the `scripts/*` flags and exit codes, and the rule ids and rewrite
shapes under `references/`. A change to any of those is breaking (major); new safe
behavior is a minor bump; fixes are patches.

## Layout

```
SKILL.md            entry point (workflow + safety contract)
references/         postgres-catalog · mysql-catalog · squawk-rules · eugene-hints · tool-setup
scripts/           analyze.py · trace.py · gen_rollback.py · table_size.sql  (stdlib only)
evals/             baseline cases + methodology
CHANGELOG.md        what changed per version
CONTRIBUTING.md     dev setup · eval workflow · invariants
```

## Contributing

Issues and PRs welcome — especially "the verdict was wrong" reports (a false positive, or
a hazard the tool missed), which is how the catalogs improve. The scripts are
standard-library-only by design; please keep new code dependency-free. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev setup, how to run the evals, and the
invariants to preserve.

## License

Apache-2.0. The skill invokes third-party binaries (`squawk`, `eugene`, `gh-ost`, …) as
separate processes; it does not bundle or statically link them, so their licenses are
independent. Install them yourself per `references/tool-setup.md`.
