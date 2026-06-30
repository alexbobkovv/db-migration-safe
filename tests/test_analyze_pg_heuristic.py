"""A5 — Postgres stdlib fallback heuristics (pure; no squawk/eugene/DB needed).

The fallback runs only when neither linter is installed (or with --no-external); it lets
PLAN/CI gate with zero install. Hermetic: subprocess cases force the path with
--no-external so they hold whether or not squawk/eugene are on PATH.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANALYZE = os.path.join(REPO, "scripts", "analyze.py")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


def rules(sql):
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(sql)
        path = fh.name
    try:
        findings, _ = analyze.analyze_postgres_heuristic(path)
    finally:
        os.unlink(path)
    return {f["rule"]: f["level"] for f in findings}


def run(*args):
    p = subprocess.run([sys.executable, ANALYZE, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class PgHeuristics(unittest.TestCase):
    def test_bare_create_index_error(self):
        self.assertEqual(rules("CREATE INDEX i ON orders (user_id);")
                         ["pg-create-index-not-concurrent"], "error")

    def test_concurrent_index_clean(self):
        self.assertEqual(rules("CREATE INDEX CONCURRENTLY i ON orders (user_id);"), {})

    def test_volatile_default_error(self):
        self.assertEqual(rules("ALTER TABLE orders ADD COLUMN c timestamptz NOT NULL DEFAULT now();")
                         ["pg-add-column-volatile-default"], "error")

    def test_constant_default_not_null_clean(self):
        # PG11+: a constant default is metadata-only — must NOT be flagged.
        self.assertEqual(rules("ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'x';"), {})

    def test_add_column_not_null_no_default_error(self):
        self.assertEqual(rules("ALTER TABLE orders ADD COLUMN c int NOT NULL;")
                         ["pg-add-column-not-null"], "error")

    def test_nullable_add_clean(self):
        self.assertEqual(rules("ALTER TABLE orders ADD COLUMN c timestamptz;"), {})

    def test_set_not_null_error(self):
        self.assertEqual(rules("ALTER TABLE users ALTER COLUMN email SET NOT NULL;")
                         ["pg-set-not-null"], "error")

    def test_fk_without_not_valid_error(self):
        self.assertEqual(rules("ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (uid) REFERENCES users (id);")
                         ["pg-add-fk-not-valid"], "error")

    def test_fk_not_valid_clean(self):
        self.assertEqual(
            rules("ALTER TABLE orders ADD CONSTRAINT fk FOREIGN KEY (uid) REFERENCES users (id) NOT VALID;"), {})

    def test_check_without_not_valid_error(self):
        self.assertEqual(rules("ALTER TABLE orders ADD CONSTRAINT c CHECK (total >= 0);")
                         ["pg-add-check-not-valid"], "error")

    def test_unique_constraint_error(self):
        self.assertEqual(rules("ALTER TABLE users ADD CONSTRAINT u UNIQUE (email);")
                         ["pg-add-unique-constraint"], "error")

    def test_unique_using_index_clean(self):
        self.assertEqual(rules("ALTER TABLE users ADD CONSTRAINT u UNIQUE USING INDEX u_idx;"), {})

    def test_type_change_error(self):
        self.assertEqual(rules("ALTER TABLE events ALTER COLUMN payload TYPE jsonb USING payload::jsonb;")
                         ["pg-column-type-change"], "error")

    def test_rename_warning(self):
        self.assertEqual(rules("ALTER TABLE users RENAME COLUMN a TO b;")["pg-rename"], "warning")

    def test_drop_column_warning(self):
        self.assertEqual(rules("ALTER TABLE users DROP COLUMN legacy;")["pg-drop-column"], "warning")

    def test_unbatched_update_warning(self):
        self.assertEqual(rules("UPDATE orders SET status = 'x' WHERE status IS NULL;")
                         ["pg-backfill-update"], "warning")


class PgHeuristicCli(unittest.TestCase):
    def test_no_external_flags_unsafe_exit_1(self):
        rc, out, _ = run(os.path.join(REPO, "evals", "cases", "02_bare_create_index.sql"),
                         "--no-external")
        self.assertEqual(rc, 1)
        self.assertIn("HEURISTIC MODE", out)
        self.assertIn("pg-create-index-not-concurrent", out)

    def test_no_external_safe_rewrite_exit_0(self):
        rc, _, _ = run(os.path.join(REPO, "evals", "cases", "04b_add_column_nullable.sql"),
                       "--no-external")
        self.assertEqual(rc, 0)

    def test_json_carries_heuristic_flag(self):
        import json
        rc, out, _ = run(os.path.join(REPO, "evals", "cases", "01_add_column_not_null_default.sql"),
                         "--no-external", "--json")
        data = json.loads(out)
        self.assertTrue(data["heuristic_fallback"])
        self.assertFalse(data["summary"]["passed"])
        self.assertEqual(rc, 1)

    def test_missing_binaries_falls_back_not_exit_2(self):
        # Simulate "neither installed" via nonexistent bin paths: Postgres now degrades to
        # the heuristic instead of the old hard exit 2.
        rc, out, _ = run(os.path.join(REPO, "evals", "cases", "03_alter_column_type.sql"),
                         "--squawk-bin", "/nonexistent/squawk",
                         "--eugene-bin", "/nonexistent/eugene")
        self.assertEqual(rc, 1)
        self.assertIn("HEURISTIC MODE", out)
        self.assertIn("pg-column-type-change", out)


if __name__ == "__main__":
    unittest.main()
