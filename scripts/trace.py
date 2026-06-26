#!/usr/bin/env python3
"""Run `eugene trace` and summarize the real locks a migration takes. Standard library only.

By default eugene spins up a throwaway temp Postgres (needs initdb/pg_ctl on PATH),
runs the migration, inspects the locks, and deletes it. Point at a disposable clone with
--host (rolls back by default; --commit to keep). Never run trace against production.

Usage:
    python3 scripts/trace.py migration.sql
    python3 scripts/trace.py migration.sql --host db.staging --user app --database app

Exit codes:
    0  eugene's passed_all_checks is true (a strong-but-acceptable lock that only bumps
       dangerous_locks_count does not override eugene's own PASS verdict)
    1  passed_all_checks is false; or, on older eugene without that field, dangerous locks
       were observed
    2  eugene not installed, file missing, or trace could not run
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path


def skill_version():
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def locks_of(stmt):
    return (stmt.get("locks_taken") or stmt.get("locks")
            or stmt.get("new_locks_taken") or [])


def print_human(data, path):
    print(f"db-migration-safe — eugene trace of {path}\n")
    for idx, stmt in enumerate(data.get("statements") or [], 1):
        sql = " ".join((stmt.get("sql") or "").split())
        if len(sql) > 100:
            sql = sql[:97] + "..."
        print(f"  [{idx}] {sql}")
        dur = stmt.get("duration_millis", stmt.get("lock_duration_millis"))
        if dur is not None:
            print(f"      duration: {dur} ms")
        for lk in locks_of(stmt):
            obj = ".".join(x for x in [lk.get("schema"), lk.get("object_name")] if x)
            dang = "  (DANGEROUS)" if lk.get("maybe_dangerous") else ""
            print(f"      lock: {lk.get('mode', '?')} on {obj or '?'}{dang}")
            blocked = lk.get("blocked_queries") or []
            if blocked:
                print(f"            blocks: {', '.join(blocked)}")
        for r in stmt.get("triggered_rules") or []:
            print(f"      hint {r.get('id', '')}: {r.get('name', '')}")
            if r.get("workaround"):
                print(f"            → {r['workaround']}")
        print()
    total = data.get("total_duration_millis")
    if total is not None:
        print(f"total duration: {total} ms")
    print(f"dangerous locks: {data.get('dangerous_locks_count', 0)}")
    passed = data.get("passed_all_checks")
    if passed is True:
        print("VERDICT: PASS — no dangerous locks / all checks passed.")
    elif passed is False:
        print("VERDICT: FAIL — dangerous locks or failed checks; see hints above.")
    else:
        print("VERDICT: review the locks above.")


def main():
    ap = argparse.ArgumentParser(description="Trace real locks for a migration via eugene.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {skill_version()}")
    ap.add_argument("migration")
    ap.add_argument("--host")
    ap.add_argument("--port", type=int, default=5432)
    ap.add_argument("--user")
    ap.add_argument("--database")
    ap.add_argument("--commit", action="store_true",
                    help="keep changes on an external DB (default rolls back)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--eugene-bin", default="eugene")
    args = ap.parse_args()

    path = Path(args.migration)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    cmd = [args.eugene_bin, "trace", str(path), "-f", "json"]
    if args.host:
        cmd += ["--disable-temporary", "--host", args.host, "--port", str(args.port)]
        if args.user:
            cmd += ["--user", args.user]
        if args.database:
            cmd += ["--database", args.database]
        if args.commit:
            cmd += ["--commit"]
    elif args.commit:
        print("warning: --commit only applies with an external DB (--host); ignoring.",
              file=sys.stderr)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        print("error: eugene is not installed. See references/tool-setup.md.\n"
              "trace also needs a Postgres: initdb/pg_ctl on PATH for the default temp "
              "server, or --host pointing at a disposable clone.", file=sys.stderr)
        return 2

    if not proc.stdout.strip():
        print("error: eugene trace produced no output.", file=sys.stderr)
        if proc.stderr.strip():
            print(proc.stderr, file=sys.stderr)
        # Postgres' own message (SQLSTATE 25001) is the reliable signal. Don't also match
        # bare "CONCURRENTLY": eugene echoes the failing SQL to stderr, so an unrelated
        # failure of a migration that merely contains CONCURRENTLY would get the wrong hint.
        if "cannot run inside a transaction block" in proc.stderr:
            print("\nhint: eugene runs the whole migration in a single transaction, so it "
                  "cannot trace CREATE/DROP INDEX CONCURRENTLY (Postgres forbids those "
                  "inside a transaction). Trace the non-concurrent statements separately; "
                  "the CONCURRENTLY step takes only a SHARE UPDATE EXCLUSIVE lock (no "
                  "read/write block) and its safety is established by static lint "
                  "(scripts/analyze.py), not trace.", file=sys.stderr)
        elif "initdb" in proc.stderr or "pg_ctl" in proc.stderr:
            print("\nhint: the default temp-server needs initdb/pg_ctl on PATH (install a "
                  "local Postgres), or run a throwaway Postgres in Docker and pass --host. "
                  "See the 'Ephemeral Postgres' section of references/tool-setup.md.",
                  file=sys.stderr)
        elif args.host:
            print("\nhint: eugene reads $PGPASS (a password value) or ~/.pgpass for the "
                  "connection — set it (this is NOT libpq's PGPASSWORD). Also ensure the "
                  "target table already exists in --database. See references/tool-setup.md.",
                  file=sys.stderr)
        return 2
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print("error: could not parse eugene trace JSON.", file=sys.stderr)
        print(proc.stdout[:2000], file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print_human(data, path)

    # eugene's passed_all_checks is the authoritative gate (it already weighs lock mode,
    # duration, lock_timeout, rewrites, and ignored hints). A nonzero dangerous_locks_count
    # alone — e.g. the brief AccessExclusiveLock of a metadata-only ADD COLUMN — must NOT
    # override a PASS, or trace would fail safe migrations and contradict its own verdict.
    passed = data.get("passed_all_checks")
    if passed is True:
        return 0
    if passed is False:
        return 1
    # older eugene without passed_all_checks: fall back to the dangerous-lock count
    return 1 if (data.get("dangerous_locks_count") or 0) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
