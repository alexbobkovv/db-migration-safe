"""A6 — migrate_safe.py dispatcher: routes subcommands to the underlying scripts and
passes their exit codes through unchanged. Uses paths that need no binaries/DB."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DISPATCH = os.path.join(REPO, "scripts", "migrate_safe.py")
CASE06 = os.path.join(REPO, "evals", "cases", "06_mysql_type_change.sql")
CASE02 = os.path.join(REPO, "evals", "cases", "02_bare_create_index.sql")
CASE05 = os.path.join(REPO, "evals", "cases", "05_rollback_input.sql")


def run(*args):
    p = subprocess.run([sys.executable, DISPATCH, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Dispatcher(unittest.TestCase):
    def test_analyze_mysql_routes_and_passes_exit_1(self):
        rc, out, _ = run("analyze", CASE06, "--dialect", "mysql")
        self.assertEqual(rc, 1)
        self.assertIn("mysql-column-type-copy", out)

    def test_analyze_no_external_routes(self):
        rc, out, _ = run("analyze", CASE02, "--no-external")
        self.assertEqual(rc, 1)
        self.assertIn("pg-create-index-not-concurrent", out)

    def test_rollback_routes_exit_0(self):
        rc, out, _ = run("rollback", CASE05)
        self.assertEqual(rc, 0)
        self.assertIn("DROP TABLE IF EXISTS audit_log", out)

    def test_unknown_subcommand_exit_2(self):
        rc, _, err = run("frobnicate")
        self.assertEqual(rc, 2)
        self.assertIn("unknown subcommand", err)

    def test_version(self):
        version = open(os.path.join(REPO, "VERSION")).read().strip()
        rc, out, _ = run("--version")
        self.assertEqual(rc, 0)
        self.assertIn(version, out)

    def test_no_args_prints_usage_exit_2(self):
        rc, out, _ = run()
        self.assertEqual(rc, 2)
        self.assertIn("migrate-safe", out)


if __name__ == "__main__":
    unittest.main()
