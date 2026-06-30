"""A4 — analyze.py CLI exit codes / JSON / version, and build_verdict counts.

Hermetic: the MySQL heuristic path needs no binaries. The Postgres install-free fallback
(when neither squawk nor eugene is present) is covered in test_analyze_pg_heuristic.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANALYZE = os.path.join(REPO, "scripts", "analyze.py")
CASE06 = os.path.join(REPO, "evals", "cases", "06_mysql_type_change.sql")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


def run(*args):
    p = subprocess.run([sys.executable, ANALYZE, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Cli(unittest.TestCase):
    def test_missing_file_exit_2(self):
        rc, _, err = run("does_not_exist.sql", "--dialect", "mysql")
        self.assertEqual(rc, 2)
        self.assertIn("file not found", err)

    def test_mysql_error_finding_exit_1(self):
        rc, out, _ = run(CASE06, "--dialect", "mysql")
        self.assertEqual(rc, 1)
        self.assertIn("mysql-column-type-copy", out)

    def test_mysql_clean_exit_0(self):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
            fh.write("ALTER TABLE t ADD COLUMN c int, ALGORITHM=INPLACE, LOCK=NONE;")
            path = fh.name
        try:
            rc, _, _ = run(path, "--dialect", "mysql")
        finally:
            os.unlink(path)
        self.assertEqual(rc, 0)

    def test_json_output_parses(self):
        rc, out, _ = run(CASE06, "--dialect", "mysql", "--json")
        data = json.loads(out)
        self.assertEqual(data["summary"]["passed"], False)
        self.assertEqual(rc, 1)

    def test_version_matches_file(self):
        with open(os.path.join(REPO, "VERSION")) as f:
            version = f.read().strip()
        rc, out, _ = run("--version")
        self.assertIn(version, out)


class BuildVerdict(unittest.TestCase):
    def test_counts_and_passed(self):
        findings = [analyze.finding("squawk", "r1", "error", 1, "m", "h"),
                    analyze.finding("eugene", "W12", "warning", 2, "m", "h"),
                    analyze.finding("eugene", "W13", "warning", 3, "m", "h")]
        v = analyze.build_verdict(findings, {}, "postgres", "m.sql")
        self.assertEqual(v["summary"]["error"], 1)
        self.assertEqual(v["summary"]["warning"], 2)
        self.assertFalse(v["summary"]["passed"])

    def test_passed_true_when_no_errors(self):
        v = analyze.build_verdict([analyze.finding("eugene", "W12", "warning", 1, "m", "h")],
                                  {}, "postgres", "m.sql")
        self.assertTrue(v["summary"]["passed"])


if __name__ == "__main__":
    unittest.main()
