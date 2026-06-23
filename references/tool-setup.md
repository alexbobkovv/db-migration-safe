# Tool setup

This skill **shells out** to external binaries as separate processes; it does not bundle
or link them. Install only what the phase you are running needs:

- **PLAN / VALIDATE (static):** `squawk` + `eugene` (lint). No database required.
- **trace (real locks):** `eugene` + a Postgres (`initdb`/`pg_ctl` for the default
  temp-server, or network access to a disposable clone).
- **MySQL execute:** `gh-ost` or `pt-online-schema-change`.

Verify what is present:
```bash
for t in squawk eugene gh-ost pt-online-schema-change initdb pg_ctl psql; do
  command -v "$t" >/dev/null && echo "found: $t" || echo "missing: $t"
done
```

## squawk

```bash
npm install -g squawk-cli           # Node
pip install squawk-cli              # Python
# Docker (no install):
docker run --rm -v "$(pwd):/data" ghcr.io/sbdchd/squawk:latest /data/migration.sql
```
Prebuilt binaries: https://github.com/sbdchd/squawk/releases . (No confirmed
`cargo install`; use npm/pip/binary/Docker.)

Smoke test the JSON shape and `level` casing once:
```bash
printf 'CREATE INDEX idx ON t (c);\n' > /tmp/bad.sql
squawk --reporter json /tmp/bad.sql
```

## eugene

```bash
mise use ubi:kaaveland/eugene@latest          # recommended: prebuilt via mise/ubi
cargo install eugene                          # needs cmake + a C/C++ toolchain
# Docker:
docker run --rm -v "$(pwd):/data" ghcr.io/kaaveland/eugene:latest lint /data/migration.sql
```
Prebuilt binaries: https://github.com/kaaveland/eugene/releases . On macOS an unsigned
binary may be quarantined:
```bash
xattr -d com.apple.quarantine ./eugene
```

`eugene trace` default mode needs `initdb` and `pg_ctl` on `PATH` (a local Postgres
install — e.g. `brew install postgresql@16`, or the official apt/yum packages). The temp
server starts **empty**, so it can only trace self-contained migrations (that create the
tables they alter). To trace an `ALTER` on an existing table, point at a disposable clone
that already has the schema/data:
```bash
# eugene reads $PGPASS (a password value) or ~/.pgpass — REQUIRED even under trust auth:
PGPASS=<pw> eugene trace migration.sql -f json --disable-temporary \
  --host <clone-host> --port 5432 --user <u> --database <db>
```
> Verified gotcha: eugene uses `$PGPASS`, **not** libpq's `$PGPASSWORD`. Omitting it fails
> with a cryptic `IO ... NotFound` (it is looking for `~/.pgpass`).

## MySQL OSC tools

```bash
# gh-ost — https://github.com/github/gh-ost/releases (binary)
# pt-osc — part of Percona Toolkit:
#   https://docs.percona.com/percona-toolkit/installation.html
# spirit — https://github.com/cashapp/spirit/releases
```
gh-ost requires `binlog_format=ROW`, a PRIMARY/UNIQUE key, and **no foreign keys / no
triggers**. pt-osc requires a PRIMARY/UNIQUE key. See `mysql-catalog.md`.

## Ephemeral Postgres for trace, without a system install

If `initdb`/`pg_ctl` are unavailable, run a throwaway Postgres in Docker and point
`eugene trace` at it:
```bash
docker run -d --rm --name pg-trace -e POSTGRES_PASSWORD=pw -p 55432:5432 postgres:16
# wait for readiness, load your schema/data into it, then (note: PGPASS, not PGPASSWORD):
PGPASS=pw eugene trace migration.sql -f json --disable-temporary \
  --host 127.0.0.1 --port 55432 --user postgres --database postgres
docker stop pg-trace
```

## Licensing

squawk, eugene, and gh-ost are open source and invoked as **separate processes** (no
linking), so this skill's Apache-2.0 license is independent of theirs. Do **not** bundle
their binaries into this repo — installing them is the user's step, which keeps the
skill lightweight and license-clean.
