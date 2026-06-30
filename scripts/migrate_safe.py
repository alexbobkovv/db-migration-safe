#!/usr/bin/env python3
"""migrate-safe — one entrypoint over the db-migration-safe scripts.

A thin dispatcher so CI, pre-commit, the Claude skill, and (later) the MCP server share a
single command surface. No analysis logic lives here — each subcommand hands its arguments
straight to the underlying script's own argparse and returns its exit code unchanged.

Usage:
    migrate-safe analyze   migration.sql --dialect postgres [--no-external] [--json]
    migrate-safe trace     migration.sql [--host ... --user ... --database ...]
    migrate-safe rollback  migration.sql [--strict]

Run `migrate-safe <subcommand> --help` for the full flags of each. Exit codes are the
underlying script's (analyze/trace: 0 pass, 1 findings, 2 usage; rollback: 0/1/2).
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBCOMMANDS = {
    "analyze": "analyze.py",
    "trace": "trace.py",
    "rollback": "gen_rollback.py",
}


def skill_version():
    try:
        return (HERE.parent / "VERSION").read_text().strip()
    except OSError:
        return "unknown"


def _load(filename):
    spec = importlib.util.spec_from_file_location(filename[:-3], HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0 if argv else 2
    if argv[0] == "--version":
        print(f"migrate-safe {skill_version()}")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd not in SUBCOMMANDS:
        print(f"error: unknown subcommand {cmd!r}; choose from "
              f"{', '.join(SUBCOMMANDS)}.", file=sys.stderr)
        return 2
    module = _load(SUBCOMMANDS[cmd])
    # Hand the remaining args to the target script's argparse (it reads sys.argv).
    sys.argv = [f"migrate-safe {cmd}"] + rest
    return module.main()


if __name__ == "__main__":
    sys.exit(main())
