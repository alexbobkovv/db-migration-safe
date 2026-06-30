#!/usr/bin/env python3
"""Orchestrate squawk + eugene lint (Postgres) or InnoDB heuristics (MySQL) into a
single merged verdict. Standard library only.

Usage:
    python3 scripts/analyze.py migration.sql --dialect postgres [--pg-version 13.0] [--json]
    python3 scripts/analyze.py migration.sql --dialect mysql
    python3 scripts/analyze.py migration.sql --no-external   # install-free heuristic pass

When neither squawk nor eugene is installed (or with --no-external), the Postgres path
falls back to a stdlib heuristic over the cataloged ops so PLAN/CI still gate with zero
install. It is clearly labeled non-authoritative; install the linters for the VALIDATE gate.

Exit codes:
    0  no error-level findings (pass)
    1  one or more error-level findings (fail / CI gate)
    2  usage error
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


# --- generic helpers --------------------------------------------------------

def skill_version():
    try:
        return (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def run_tool(cmd):
    """Run a subprocess. Returns (found, returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return (False, None, "", "")
    return (True, proc.returncode, proc.stdout, proc.stderr)


# squawk emits most lint findings at level "Warning" and only a few (e.g. syntax-error) at
# "Error", signalling hazards primarily via its exit code. So its level field alone gives no
# usable hazard/style split. analyze_squawk honors a native "Error" level, and otherwise
# derives severity by rule name: lock/blocking/destructive rules gate the migration (error);
# advisory style rules are warnings; any unknown rule defaults to error so a newly added
# hazard rule blocks rather than slipping through. Silence advisory rules you don't enforce
# via .squawk.toml excluded_rules.
SQUAWK_ADVISORY = frozenset({
    "prefer-robust-stmts",
    "require-timeout-settings",
    "prefer-timestamptz",
    "prefer-text-field",
    "prefer-bigint-over-int",
    "prefer-bigint-over-smallint",
    "prefer-identity",
    "ban-char-field",
})


def squawk_severity(rule):
    return "warning" if rule in SQUAWK_ADVISORY else "error"


def finding(source, rule, level, line, message, help_text):
    return {
        "source": source,
        "rule": rule,
        "level": level,
        "line": line,
        "message": (message or "").strip(),
        "help": (help_text or "").strip(),
    }


def tool_error(source, detail):
    """An error-level finding for when a linter ran but failed (e.g. could not parse the
    SQL). Surfaced so the gate FAILS instead of silently passing an unanalyzed migration."""
    return finding(source, "tool-error", "error", None,
                   f"{source} could not analyze the file — treated as FAIL, not PASS.",
                   (detail or "").strip()[:500])


def strip_sql_comments(sql):
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def split_statements(sql):
    """Naive but quote-aware split on ';'. Sufficient for cataloged DDL shapes;
    does not handle dollar-quoted function bodies (out of scope)."""
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


# --- squawk (Postgres static) ----------------------------------------------

def analyze_squawk(path, pg_version, squawk_bin):
    """Returns (findings_list_or_None, status_string). None => not installed."""
    cmd = [squawk_bin, "--reporter", "json"]
    if pg_version:
        cmd += ["--pg-version", pg_version]
    cmd.append(str(path))
    found, code, out, err = run_tool(cmd)
    if not found:
        return None, "not installed (skipped — see references/tool-setup.md)"
    if not out.strip():
        if code:  # non-zero exit with no JSON => squawk failed; never silent-pass
            return [tool_error("squawk", err)], "error: " + (err.strip() or "squawk failed")
        return [], "ok"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [tool_error("squawk", err or out)], "error: could not parse squawk JSON output"
    items = data if isinstance(data, list) else data.get("violations", [])
    out_findings = []
    for v in items:
        rule = v.get("rule_name") or "squawk"
        # honor squawk's native Error level (e.g. syntax-error); else derive by rule name
        lvl = str(v.get("level") or "").strip().lower()
        sev = "error" if lvl.startswith("e") else squawk_severity(rule)
        out_findings.append(finding("squawk", rule, sev,
                                    v.get("line"), v.get("message"), v.get("help")))
    return out_findings, "ok"


# --- eugene lint (Postgres static) -----------------------------------------

def analyze_eugene(path, eugene_bin):
    """Returns (findings_list_or_None, status_string). None => not installed.

    Parses defensively: eugene's lint JSON key names vary across versions
    (plan §10). E* hints are errors, W* are warnings.
    """
    cmd = [eugene_bin, "lint", str(path), "-f", "json"]
    found, code, out, err = run_tool(cmd)
    if not found:
        return None, "not installed (skipped — see references/tool-setup.md)"
    if not out.strip():
        if code:  # eugene writes parse errors to stderr + empty stdout; never silent-pass
            return [tool_error("eugene", err)], "error: " + (err.strip() or "eugene failed")
        return [], "ok"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return [tool_error("eugene", err or out)], "error: could not parse eugene JSON output"
    reports = data if isinstance(data, list) else [data]
    out_findings = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        for stmt in report.get("statements") or []:
            if not isinstance(stmt, dict):
                continue
            line = stmt.get("line_number") or stmt.get("line")
            rules = (stmt.get("triggered_rules") or stmt.get("lints")
                     or stmt.get("hints") or [])
            for r in rules:
                rid = str(r.get("id") or "")
                level = "error" if rid.upper().startswith("E") else "warning"
                out_findings.append(finding(
                    "eugene", rid or (r.get("name") or "eugene"), level, line,
                    r.get("name") or r.get("effect") or r.get("condition"),
                    r.get("workaround") or r.get("help")))
    return out_findings, "ok"


# --- Postgres stdlib fallback (only when neither squawk nor eugene is installed) --------
#
# A deliberately thin pattern check over the same 12 ops as references/postgres-catalog.md,
# so PLAN and the CI gate still work with ZERO external install. It is NOT a third linter:
# whenever squawk or eugene is present, analyze.py defers to them and this never runs.
# Findings carry source "heuristic-pg" and the run is banner-flagged as non-authoritative —
# it cannot see table size, partitioning, or a cross-file validated CHECK, so the VALIDATE
# gate still wants the real linters.

_PG_VOLATILE_DEFAULT = re.compile(
    r"\bDEFAULT\b[^,]*?\b(?:now|clock_timestamp|statement_timestamp|timeofday|random|"
    r"gen_random_uuid|uuid_generate_v\d|nextval)\s*\(", re.I)


def _pg_alter_findings(stmt):
    """Findings for one ALTER TABLE statement. Multi-action ALTERs are scanned by
    independent regex matches — coarse, but sufficient for a heuristic."""
    out = []

    def add(rule, level, msg, help_text):
        out.append(finding("heuristic-pg", rule, level, None, msg, help_text))

    if re.search(r"\bADD\s+COLUMN\b", stmt, re.I):
        if _PG_VOLATILE_DEFAULT.search(stmt):
            add("pg-add-column-volatile-default", "error",
                "ADD COLUMN with a volatile DEFAULT rewrites the whole table under ACCESS EXCLUSIVE.",
                "Add the column nullable, SET DEFAULT, then batch-backfill. postgres-catalog.md #1.")
        elif re.search(r"\bNOT\s+NULL\b", stmt, re.I) and not re.search(r"\bDEFAULT\b", stmt, re.I):
            add("pg-add-column-not-null", "error",
                "ADD COLUMN ... NOT NULL with no default scans/rewrites the table to prove no nulls.",
                "Add nullable -> SET DEFAULT -> backfill -> NOT NULL via a validated CHECK. postgres-catalog.md #2,#3.")

    if re.search(r"\bSET\s+NOT\s+NULL\b", stmt, re.I):
        add("pg-set-not-null", "error",
            "SET NOT NULL full-scans the table under ACCESS EXCLUSIVE.",
            "ADD CHECK (c IS NOT NULL) NOT VALID -> VALIDATE -> SET NOT NULL. postgres-catalog.md #3.")

    if re.search(r"\bFOREIGN\s+KEY\b", stmt, re.I) and not re.search(r"\bNOT\s+VALID\b", stmt, re.I):
        add("pg-add-fk-not-valid", "error",
            "Adding a FOREIGN KEY validates existing rows, blocking writes on both tables.",
            "Add it NOT VALID, then VALIDATE CONSTRAINT separately. postgres-catalog.md #5.")

    if re.search(r"\bADD\b[^;]*\bCHECK\b", stmt, re.I) and not re.search(r"\bNOT\s+VALID\b", stmt, re.I):
        add("pg-add-check-not-valid", "error",
            "Adding a CHECK constraint scans the table and blocks writes during validation.",
            "Add it NOT VALID, then VALIDATE CONSTRAINT separately. postgres-catalog.md #10.")

    if re.search(r"\bADD\s+(?:CONSTRAINT\s+\S+\s+)?UNIQUE\b", stmt, re.I) \
            and not re.search(r"\bUSING\s+INDEX\b", stmt, re.I):
        add("pg-add-unique-constraint", "error",
            "ADD UNIQUE builds its backing index under ACCESS EXCLUSIVE.",
            "CREATE UNIQUE INDEX CONCURRENTLY -> ADD CONSTRAINT ... USING INDEX. postgres-catalog.md #7.")

    if re.search(r"\bALTER\s+COLUMN\s+\S+\s+(?:SET\s+DATA\s+)?TYPE\b", stmt, re.I):
        add("pg-column-type-change", "error",
            "ALTER COLUMN TYPE can rewrite the whole table under ACCESS EXCLUSIVE (some casts are metadata-only).",
            "Expand-contract: new col -> dual-write -> backfill -> swap. postgres-catalog.md #6.")

    if re.search(r"\bRENAME\b", stmt, re.I):
        add("pg-rename", "warning",
            "Renaming a column/table breaks the running app until every instance is redeployed.",
            "Expand-contract or a compatibility VIEW. postgres-catalog.md #8.")

    if re.search(r"\bDROP\s+COLUMN\b", stmt, re.I):
        add("pg-drop-column", "warning",
            "DROP COLUMN is irreversible from DDL and breaks apps that still reference the column.",
            "Stop referencing it + deploy, back up, then drop. postgres-catalog.md #9.")
    return out


def analyze_postgres_heuristic(path):
    """Returns (findings, status). Stdlib fallback — see the note above."""
    raw = Path(path).read_text()
    findings = []
    for stmt in split_statements(strip_sql_comments(raw)):
        s = re.sub(r"\s+", " ", stmt).strip()
        if re.match(r"CREATE\s+(?:UNIQUE\s+)?INDEX\b", s, re.I):
            if not re.search(r"\bCONCURRENTLY\b", s, re.I):
                findings.append(finding(
                    "heuristic-pg", "pg-create-index-not-concurrent", "error", None,
                    "CREATE INDEX without CONCURRENTLY takes a SHARE lock and blocks writes for the whole build.",
                    "CREATE INDEX CONCURRENTLY (outside a txn). postgres-catalog.md #4."))
        elif re.match(r"DROP\s+INDEX\b", s, re.I):
            if not re.search(r"\bCONCURRENTLY\b", s, re.I):
                findings.append(finding(
                    "heuristic-pg", "pg-drop-index-not-concurrent", "warning", None,
                    "DROP INDEX without CONCURRENTLY briefly takes ACCESS EXCLUSIVE (fine if quick).",
                    "Prefer DROP INDEX CONCURRENTLY on a normal table; on a partitioned parent it is unavailable. postgres-catalog.md #12."))
        elif re.match(r"ALTER\s+TABLE\b", s, re.I):
            findings += _pg_alter_findings(s)
        elif re.match(r"UPDATE\b", s, re.I):
            findings.append(finding(
                "heuristic-pg", "pg-backfill-update", "warning", None,
                "A single large UPDATE holds row locks for its full duration, bloats WAL, and lags replicas.",
                "Backfill in bounded batches in separate short transactions. postgres-catalog.md #11."))
    return findings, "heuristic — install squawk/eugene for authoritative analysis"


# --- MySQL InnoDB Online DDL heuristics (no free static linter exists) ------

MYSQL_RULES = [
    (re.compile(r"\b(?:MODIFY|CHANGE)\s+COLUMN\b|\bMODIFY\b(?!\s+COLUMN)", re.I),
     "error", "mysql-column-type-copy",
     "MODIFY/CHANGE column can trigger ALGORITHM=COPY (full table rebuild, blocks writes).",
     "Pin ALGORITHM=INPLACE, LOCK=NONE to test; else delegate to gh-ost/pt-osc. See mysql-catalog.md."),
    (re.compile(r"\bDROP\s+PRIMARY\s+KEY\b", re.I),
     "error", "mysql-drop-primary-key",
     "DROP PRIMARY KEY forces ALGORITHM=COPY (re-clusters the whole table).",
     "Delegate to gh-ost/pt-osc."),
    (re.compile(r"\bADD\s+(?:FULLTEXT|SPATIAL)\b", re.I),
     "error", "mysql-fulltext-spatial-index",
     "Adding a FULLTEXT/SPATIAL index runs INPLACE but with LOCK=SHARED (blocks writes).",
     "Schedule a window or delegate to an OSC tool."),
    (re.compile(r"\bADD\s+(?:CONSTRAINT\s+\S+\s+)?FOREIGN\s+KEY\b", re.I),
     "warning", "mysql-add-foreign-key",
     "Adding a FK with foreign_key_checks=1 can copy/lock the table.",
     "Delegate to pt-osc, or add with care after confirming version behavior."),
    (re.compile(r"\bDROP\s+COLUMN\b", re.I),
     "warning", "mysql-drop-column",
     "DROP COLUMN is INSTANT only on MySQL 8.0.29+, otherwise COPY; data is lost.",
     "Confirm the server version; back up before dropping."),
    (re.compile(r"\bRENAME\b", re.I),
     "warning", "mysql-rename",
     "Renaming a column/table breaks the running app until redeployed.",
     "Use expand-contract or a compatibility view (see postgres-catalog.md #8 for the pattern)."),
]


def analyze_mysql(path):
    raw = Path(path).read_text()
    findings = []
    for stmt in split_statements(strip_sql_comments(raw)):
        if not re.match(r"\s*ALTER\s+TABLE\b", stmt, re.I):
            continue
        for rx, level, rule, msg, help_text in MYSQL_RULES:
            if rx.search(stmt):
                findings.append(finding("heuristic", rule, level, None, msg, help_text))
        if not (re.search(r"\bALGORITHM\s*=", stmt, re.I)
                and re.search(r"\bLOCK\s*=", stmt, re.I)):
            findings.append(finding(
                "heuristic", "mysql-pin-algorithm-lock", "warning", None,
                "ALTER TABLE without explicit ALGORITHM=/LOCK= — MySQL may silently fall "
                "back to a blocking COPY.",
                "Add ALGORITHM=INPLACE, LOCK=NONE so the server errors instead of blocking."))
    return findings, "ok"


# --- verdict + output -------------------------------------------------------

# Findings from squawk and eugene are intentionally NOT deduplicated (plan §6.3): the two
# linters frame the same hazard differently (e.g. squawk:require-concurrent-index-creation
# vs eugene:E6 + E9) and each carries a distinct, useful workaround. print_human sorts by
# (level, line) so a statement's findings from both tools sit together.
def build_verdict(findings, tools, dialect, path, heuristic=False):
    errors = sum(1 for f in findings if f["level"] == "error")
    warnings = sum(1 for f in findings if f["level"] == "warning")
    return {
        "file": str(path),
        "dialect": dialect,
        "tools": tools,
        "heuristic_fallback": heuristic,
        "summary": {
            "error": errors,
            "warning": warnings,
            "info": len(findings) - errors - warnings,
            "passed": errors == 0,
        },
        "findings": findings,
    }


def print_human(verdict):
    s = verdict["summary"]
    print(f"db-migration-safe — analysis of {verdict['file']} (dialect: {verdict['dialect']})\n")
    if verdict.get("heuristic_fallback"):
        print("  !! HEURISTIC MODE — neither squawk nor eugene was run.\n"
              "     Findings are from a stdlib pattern check over the cataloged ops, not the\n"
              "     deterministic linters. Install them for authoritative analysis and the\n"
              "     VALIDATE gate: references/tool-setup.md\n")
    for name, status in verdict["tools"].items():
        print(f"  {name}: {status}")
    print()
    if not verdict["findings"]:
        print("No findings.\n")
    else:
        order = {"error": 0, "warning": 1, "info": 2}
        print(f"FINDINGS ({s['error']} error, {s['warning']} warning, {s['info']} info)\n")
        for f in sorted(verdict["findings"], key=lambda x: (order.get(x["level"], 3), x["line"] or 0)):
            loc = f"L{f['line']}" if f["line"] else "—"
            print(f"  {f['level'].upper():7} {loc:>5}  [{f['source']}:{f['rule']}]")
            if f["message"]:
                print(f"          {f['message']}")
            if f["help"]:
                print(f"          → {f['help']}")
            print()
    if s["passed"]:
        print(f"VERDICT: PASS — 0 errors, {s['warning']} warning(s).")
    else:
        cat = "postgres-catalog.md" if verdict["dialect"] == "postgres" else "mysql-catalog.md"
        print(f"VERDICT: FAIL — {s['error']} error-level finding(s). "
              f"Rewrite via references/{cat}, then re-run analyze.py.")


def _sarif_level(level):
    return {"error": "error", "warning": "warning"}.get(level, "note")


def to_sarif(verdicts):
    """Merge one or more verdicts into a single SARIF 2.1.0 run for GitHub code scanning.
    Findings with no line number are anchored at line 1 (the linter located the statement,
    not a byte offset). An empty results list is valid SARIF and clears stale alerts."""
    rules, results = {}, []
    for v in verdicts:
        for f in v["findings"]:
            rid = f"{f['source']}:{f['rule']}"
            if rid not in rules:
                entry = {"id": rid, "shortDescription": {"text": (f["message"] or rid)[:300]}}
                if f["help"]:
                    entry["fullDescription"] = {"text": f["help"]}
                rules[rid] = entry
            text = f["message"] or rid
            if f["help"]:
                text = f"{text} -> {f['help']}"
            results.append({
                "ruleId": rid,
                "level": _sarif_level(f["level"]),
                "message": {"text": text},
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {"uri": v["file"]},
                        "region": {"startLine": f["line"] if (f["line"] or 0) >= 1 else 1},
                    }
                }],
            })
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [{
            "tool": {"driver": {
                "name": "db-migration-safe",
                "informationUri": "https://github.com/alexbobkovv/db-migration-safe",
                "version": skill_version(),
                "rules": list(rules.values()),
            }},
            "results": results,
        }],
    }


def analyze_one(path, args):
    """Analyze a single file → verdict (uses the heuristic fallback when no linter ran)."""
    findings, tools, heuristic = [], {}, False
    if args.dialect == "postgres":
        sq = eu = None
        if not args.no_external:
            sq, sq_status = analyze_squawk(path, args.pg_version, args.squawk_bin)
            eu, eu_status = analyze_eugene(path, args.eugene_bin)
            tools["squawk"] = sq_status
            tools["eugene lint"] = eu_status
            if sq:
                findings += sq
            if eu:
                findings += eu
        if sq is None and eu is None:
            # No external linter ran (absent, or --no-external): fall back to the stdlib
            # heuristic so PLAN and the CI gate still work with zero install. The verdict is
            # banner-flagged non-authoritative; it never silently replaces the real linters.
            hf, hf_status = analyze_postgres_heuristic(path)
            tools["postgres-heuristics"] = hf_status
            findings += hf
            heuristic = True
    else:
        mh, mh_status = analyze_mysql(path)
        tools["mysql-heuristics"] = mh_status
        findings += mh
    return build_verdict(findings, tools, args.dialect, path, heuristic)


def main():
    ap = argparse.ArgumentParser(description="Analyze SQL migration(s) for lock/blocking hazards.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {skill_version()}")
    ap.add_argument("migration", nargs="+", help="path(s) to the migration .sql file(s)")
    ap.add_argument("--dialect", choices=["postgres", "mysql"], default="postgres")
    ap.add_argument("--pg-version", default=None, help="gate Postgres version rules, e.g. 13.0")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--sarif", action="store_true",
                    help="emit SARIF 2.1.0 for GitHub code scanning (merged across files)")
    ap.add_argument("--no-external", action="store_true",
                    help="skip squawk/eugene; use the stdlib heuristic pass only (install-free)")
    ap.add_argument("--squawk-bin", default="squawk")
    ap.add_argument("--eugene-bin", default="eugene")
    args = ap.parse_args()

    paths = [Path(m) for m in args.migration]
    missing = [str(p) for p in paths if not p.is_file()]
    for m in missing:
        print(f"error: file not found: {m}", file=sys.stderr)

    verdicts = [analyze_one(p, args) for p in paths if p.is_file()]

    if verdicts:
        if args.sarif:
            print(json.dumps(to_sarif(verdicts), indent=2))
        elif args.json:
            # Single file keeps the historical object shape; multiple files emit a list.
            print(json.dumps(verdicts[0] if len(verdicts) == 1 else verdicts, indent=2))
        else:
            for i, v in enumerate(verdicts):
                if i:
                    print()
                print_human(v)
    elif args.sarif:
        print(json.dumps(to_sarif([]), indent=2))

    if missing:
        return 2
    return 0 if all(v["summary"]["passed"] for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
