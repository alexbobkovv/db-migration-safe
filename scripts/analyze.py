#!/usr/bin/env python3
"""Orchestrate squawk + eugene lint (Postgres) or InnoDB heuristics (MySQL) into a
single merged verdict. Standard library only.

Usage:
    python3 scripts/analyze.py migration.sql --dialect postgres [--pg-version 13.0] [--json]
    python3 scripts/analyze.py migration.sql --dialect mysql

Exit codes:
    0  no error-level findings (pass)
    1  one or more error-level findings (fail / CI gate)
    2  usage error, or no analyzer available for the dialect
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
def build_verdict(findings, tools, dialect, path):
    errors = sum(1 for f in findings if f["level"] == "error")
    warnings = sum(1 for f in findings if f["level"] == "warning")
    return {
        "file": str(path),
        "dialect": dialect,
        "tools": tools,
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


def main():
    ap = argparse.ArgumentParser(description="Analyze a SQL migration for lock/blocking hazards.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {skill_version()}")
    ap.add_argument("migration", help="path to the migration .sql file")
    ap.add_argument("--dialect", choices=["postgres", "mysql"], default="postgres")
    ap.add_argument("--pg-version", default=None, help="gate Postgres version rules, e.g. 13.0")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--squawk-bin", default="squawk")
    ap.add_argument("--eugene-bin", default="eugene")
    args = ap.parse_args()

    path = Path(args.migration)
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2

    findings, tools = [], {}
    if args.dialect == "postgres":
        sq, sq_status = analyze_squawk(path, args.pg_version, args.squawk_bin)
        eu, eu_status = analyze_eugene(path, args.eugene_bin)
        tools["squawk"] = sq_status
        tools["eugene lint"] = eu_status
        if sq:
            findings += sq
        if eu:
            findings += eu
        if sq is None and eu is None:
            print("error: neither squawk nor eugene is installed; cannot analyze Postgres.\n"
                  "Install one (details in references/tool-setup.md):\n"
                  "  npm install -g squawk-cli          # or: pip install squawk-cli\n"
                  "  mise use ubi:kaaveland/eugene@latest", file=sys.stderr)
            return 2
    else:
        mh, mh_status = analyze_mysql(path)
        tools["mysql-heuristics"] = mh_status
        findings += mh

    verdict = build_verdict(findings, tools, args.dialect, path)
    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print_human(verdict)
    return 0 if verdict["summary"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
