"""A6 — gen_rollback.py end-to-end on the eval fixtures (subprocess; no DB)."""
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GEN = os.path.join(REPO, "scripts", "gen_rollback.py")
CASE05 = os.path.join(REPO, "evals", "cases", "05_rollback_input.sql")
CASE07 = os.path.join(REPO, "evals", "cases", "07_quoted_identifiers.sql")


def run(*args):
    p = subprocess.run([sys.executable, GEN, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class Case05(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rc, cls.out, cls.err = run(CASE05)

    def test_reverses_recognized_ops(self):
        for needle in ("DROP TABLE IF EXISTS audit_log;",
                       "DROP COLUMN IF EXISTS region;",
                       "DROP CONSTRAINT IF EXISTS orders_region_check;",
                       "DROP INDEX CONCURRENTLY IF EXISTS idx_orders_region;",
                       "RENAME COLUMN ship_region TO region;",
                       "DROP NOT NULL"):
            self.assertIn(needle, self.out, needle)

    def test_type_change_is_manual(self):
        self.assertIn("MANUAL ROLLBACK REQUIRED", self.out)

    def test_drop_column_irreversible_with_backup_recipe(self):
        self.assertIn("IRREVERSIBLE", self.out)
        self.assertIn("legacy_notes_backup_", self.out)

    def test_two_noops(self):
        self.assertIn("2 no-op", self.err)

    def test_default_exit_0_strict_exit_1(self):
        self.assertEqual(self.rc, 0)
        rc, _, _ = run("--strict", CASE05)
        self.assertEqual(rc, 1)


class Case07Quoted(unittest.TestCase):
    def test_quoted_identifiers_invert(self):
        rc, out, err = run(CASE07)
        self.assertEqual(rc, 0)
        self.assertIn('DROP COLUMN IF EXISTS "user id"', out)
        self.assertIn("4 reversible, 0 irreversible, 0 manual", err)

    def test_strict_exit_0(self):
        rc, _, _ = run("--strict", CASE07)
        self.assertEqual(rc, 0)


class Usage(unittest.TestCase):
    def test_missing_file_exit_2(self):
        rc, _, err = run("nope.sql")
        self.assertEqual(rc, 2)
        self.assertIn("file not found", err)


if __name__ == "__main__":
    unittest.main()
