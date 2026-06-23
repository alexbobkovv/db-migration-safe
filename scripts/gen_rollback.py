#!/usr/bin/env python3
"""Generate a reverse (DOWN) migration from a forward Postgres migration.

Inverts recognized cataloged operations, flags irreversible ops (DROP COLUMN/TABLE,
type changes) with a data-backup recipe, and emits an explicit "manual rollback
required" for unrecognized shapes — never a silently-wrong inverse. Stdlib only.

Usage:
    python3 scripts/gen_rollback.py migration.sql > migration.rollback.sql

Exit codes:
    0  generated
    1  generated, but contains irreversible/manual items and --strict was given
    2  usage error
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

IDENT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
QNAME = rf'{IDENT}(?:\.{IDENT})*'
TODAY = date.today().strftime("%Y%m%d")


def skill_version():
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def split_statements(sql):
    """Quote-aware split on ';'. Sufficient for cataloged DDL; does not handle
    dollar-quoted function bodies (out of scope for v1)."""
    stmts, buf, quote = [], [], None
    for ch in sql:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            s = "".join(buf).strip()
            if s:
                stmts.append(s)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


def collapse(s):
    return re.sub(r"\s+", " ", s).strip()


def split_top_level_commas(s):
    """Split on commas not inside parens or quotes — for multi-action ALTER TABLE."""
    parts, buf, depth, quote = [], [], 0, None
    for ch in s:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return parts


def bare(name):
    """Last component of a qualified name, unquoted — for building backup table names."""
    return name.split(".")[-1].strip('"')


# --- result constructors ----------------------------------------------------

def ok(forward, inverse):
    return {"forward": forward, "status": "ok", "lines": [inverse]}


def noop(forward, why):
    return {"forward": forward, "status": "noop", "lines": [f"-- no rollback needed: {why}"]}


def manual(forward, why, extra=None):
    lines = [f"-- MANUAL ROLLBACK REQUIRED: {why}"]
    if extra:
        lines.append(extra)
    return {"forward": forward, "status": "manual", "lines": lines}


def irreversible(forward, lines):
    return {"forward": forward, "status": "irreversible", "lines": lines}


# --- inversion --------------------------------------------------------------

def invert_alter(orig, table, action):
    subs = split_top_level_commas(action)
    if len(subs) > 1:
        # multi-action ALTER: invert each action independently, undo in reverse order
        results = [invert(f"ALTER TABLE {table} {sub.strip().rstrip(';')}") for sub in subs]
        rank = {"ok": 0, "noop": 0, "manual": 1, "irreversible": 2}
        lines, worst = [], "ok"
        for r in reversed(results):
            lines.extend(r["lines"])
            if rank[r["status"]] > rank[worst]:
                worst = r["status"]
        return {"forward": orig, "status": worst, "lines": lines}

    a = action.strip().rstrip(";")

    m = re.match(rf"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?({IDENT})\b", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} DROP COLUMN IF EXISTS {m.group(1)};")

    m = re.match(rf"DROP\s+COLUMN\s+(?:IF\s+EXISTS\s+)?({IDENT})\b", a, re.I)
    if m:
        col = m.group(1)
        backup = f"{bare(table)}_{bare(col)}_backup_{TODAY}"
        return irreversible(orig, [
            f"-- IRREVERSIBLE: DROP COLUMN {table}.{col} loses data and the column definition.",
            f"-- The forward migration MUST snapshot first (run BEFORE the drop):",
            f"--   CREATE TABLE {backup} AS SELECT <pk>, {col} FROM {table};",
            f"-- Best-effort structural restore (fill in the original column type):",
            f"ALTER TABLE {table} ADD COLUMN {col} <TYPE>;  -- TODO: original type unknown",
            f"-- Data restore (only if the backup table exists):",
            f"-- UPDATE {table} t SET {col} = b.{col} FROM {backup} b WHERE t.<pk> = b.<pk>;",
        ])

    m = re.match(rf"ADD\s+CONSTRAINT\s+({IDENT})\b", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {m.group(1)};")

    m = re.match(rf"DROP\s+CONSTRAINT\s+(?:IF\s+EXISTS\s+)?({IDENT})\b", a, re.I)
    if m:
        return manual(orig, f"cannot recreate constraint {m.group(1)} from its name alone; "
                            f"restore the original ADD CONSTRAINT definition")

    if re.match(r"VALIDATE\s+CONSTRAINT\b", a, re.I):
        return noop(orig, "VALIDATE CONSTRAINT has no inverse (validation only)")

    m = re.match(rf"RENAME\s+COLUMN\s+({IDENT})\s+TO\s+({IDENT})", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} RENAME COLUMN {m.group(2)} TO {m.group(1)};")

    m = re.match(rf"RENAME\s+CONSTRAINT\s+({IDENT})\s+TO\s+({IDENT})", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} RENAME CONSTRAINT {m.group(2)} TO {m.group(1)};")

    m = re.match(rf"RENAME\s+TO\s+({IDENT})", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {m.group(1)} RENAME TO {bare(table)};")

    m = re.match(rf"ALTER\s+COLUMN\s+({IDENT})\s+SET\s+NOT\s+NULL", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} ALTER COLUMN {m.group(1)} DROP NOT NULL;")

    m = re.match(rf"ALTER\s+COLUMN\s+({IDENT})\s+DROP\s+NOT\s+NULL", a, re.I)
    if m:
        return ok(orig, f"ALTER TABLE {table} ALTER COLUMN {m.group(1)} SET NOT NULL;")

    m = re.match(rf"ALTER\s+COLUMN\s+({IDENT})\s+SET\s+DEFAULT\b", a, re.I)
    if m:
        return manual(orig,
                      f"reverses to DROP DEFAULT, but the previous default of {m.group(1)} "
                      f"is unknown; verify before applying:",
                      f"-- ALTER TABLE {table} ALTER COLUMN {m.group(1)} DROP DEFAULT;")

    m = re.match(rf"ALTER\s+COLUMN\s+({IDENT})\s+DROP\s+DEFAULT", a, re.I)
    if m:
        return manual(orig, f"the previous default of {m.group(1)} is unknown; "
                            f"restore it from the original schema")

    m = re.match(rf"ALTER\s+COLUMN\s+({IDENT})\s+(?:SET\s+DATA\s+)?TYPE\b", a, re.I)
    if m:
        return manual(orig, f"type change on {m.group(1)} is potentially irreversible "
                            f"(narrowing loses data); the original type is unknown")

    return manual(orig, "unrecognized ALTER TABLE action")


def invert(stmt):
    orig = stmt.strip()
    s = collapse(stmt)

    # Session settings (SET/RESET/SHOW) and transaction control (BEGIN/COMMIT/...)
    # are not schema changes and have no inverse. The forward migration carries
    # `SET lock_timeout` per the EXECUTE guidance; reversing it is a no-op, not a
    # manual action.
    if re.match(r"(?:SET|RESET|SHOW)\b", s, re.I) or re.match(
            r"(?:BEGIN|START\s+TRANSACTION|COMMIT|END|ROLLBACK|SAVEPOINT|RELEASE)\b", s, re.I):
        return noop(orig, "session setting / transaction control has no schema inverse")

    m = re.match(rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({QNAME})", s, re.I)
    if m:
        return ok(orig, f"DROP TABLE IF EXISTS {m.group(1)};")

    m = re.match(rf"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?({QNAME})\b",
                 s, re.I)
    if m:
        name = m.group(2)
        if name.upper() == "ON":  # anonymous index: CREATE INDEX [CONCURRENTLY] ON t (...)
            return manual(orig, "anonymous index (no name given); Postgres auto-generates "
                                "the name, so the DROP target cannot be derived")
        conc = "CONCURRENTLY " if m.group(1) else ""
        return ok(orig, f"DROP INDEX {conc}IF EXISTS {name};")

    if re.match(r"DROP\s+INDEX\b", s, re.I):
        return manual(orig, "cannot recreate a dropped index from its name alone; "
                            "restore the original CREATE INDEX statement")

    m = re.match(rf"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?({QNAME})", s, re.I)
    if m:
        tbl = m.group(1)
        backup = f"{bare(tbl)}_backup_{TODAY}"
        return irreversible(orig, [
            f"-- IRREVERSIBLE: DROP TABLE {tbl} destroys the table and its data.",
            f"-- The forward migration MUST snapshot first (run BEFORE the drop):",
            f"--   CREATE TABLE {backup} AS TABLE {tbl};",
            f"-- Data restore is only possible if that backup exists:",
            f"-- CREATE TABLE {tbl} AS TABLE {backup};  -- indexes/constraints/defaults are NOT copied",
            f"-- Manual rollback required: recreate {tbl} from your schema definition.",
        ])

    m = re.match(rf"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?({QNAME})\s+(.*)$", s, re.I | re.S)
    if m:
        return invert_alter(orig, m.group(1), m.group(2))

    return manual(orig, "unrecognized statement shape")


def main():
    ap = argparse.ArgumentParser(description="Generate a reverse migration from a forward Postgres migration.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {skill_version()}")
    ap.add_argument("migration")
    ap.add_argument("--strict", action="store_true",
                    help="exit 1 if any op is irreversible or needs manual rollback")
    args = ap.parse_args()

    path = Path(args.migration)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    statements = split_statements(strip_sql_comments(path.read_text()))
    results = [invert(st) for st in statements]
    counts = {"ok": 0, "irreversible": 0, "manual": 0, "noop": 0}
    for r in results:
        counts[r["status"]] += 1

    out = [
        f"-- Reverse migration for {path.name}",
        f"-- Generated by db-migration-safe gen_rollback.py on {date.today().isoformat()}",
        f"-- Applies the statements below to undo the forward migration.",
        f"-- Review every block: irreversible/manual items need your attention.",
        "",
    ]
    # reverse order: undo the last forward statement first
    for r in reversed(results):
        fwd = collapse(r["forward"])
        if len(fwd) > 100:
            fwd = fwd[:97] + "..."
        out.append(f"-- forward: {fwd}")
        out.extend(r["lines"])
        out.append("")
    print("\n".join(out))

    sys.stderr.write(
        f"gen_rollback: {counts['ok']} reversible, {counts['irreversible']} irreversible, "
        f"{counts['manual']} manual, {counts['noop']} no-op.\n")
    if args.strict and (counts["irreversible"] or counts["manual"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
