"""A3 — MySQL InnoDB Online DDL heuristics (pure; no MySQL server needed)."""
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


def rules(sql):
    with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as fh:
        fh.write(sql)
        path = fh.name
    try:
        findings, _ = analyze.analyze_mysql(path)
    finally:
        os.unlink(path)
    return {f["rule"]: f["level"] for f in findings}


class MysqlHeuristics(unittest.TestCase):
    def test_modify_column_is_copy_error(self):
        self.assertEqual(rules("ALTER TABLE t MODIFY COLUMN c bigint;")["mysql-column-type-copy"], "error")

    def test_change_column_is_copy_error(self):
        self.assertIn("mysql-column-type-copy", rules("ALTER TABLE t CHANGE COLUMN a b bigint;"))

    def test_drop_primary_key_error(self):
        self.assertEqual(rules("ALTER TABLE t DROP PRIMARY KEY;")["mysql-drop-primary-key"], "error")

    def test_fulltext_index_error(self):
        self.assertEqual(rules("ALTER TABLE t ADD FULLTEXT(c);")["mysql-fulltext-spatial-index"], "error")

    def test_add_foreign_key_warning(self):
        self.assertEqual(rules("ALTER TABLE t ADD CONSTRAINT fk FOREIGN KEY (a) REFERENCES u(id);")
                         ["mysql-add-foreign-key"], "warning")

    def test_drop_column_warning(self):
        self.assertEqual(rules("ALTER TABLE t DROP COLUMN c;")["mysql-drop-column"], "warning")

    def test_rename_warning(self):
        self.assertEqual(rules("ALTER TABLE t RENAME TO t2;")["mysql-rename"], "warning")

    def test_missing_algorithm_lock_is_flagged(self):
        self.assertIn("mysql-pin-algorithm-lock", rules("ALTER TABLE t DROP COLUMN c;"))

    def test_explicit_algorithm_lock_not_flagged(self):
        r = rules("ALTER TABLE t ADD COLUMN c int, ALGORITHM=INPLACE, LOCK=NONE;")
        self.assertNotIn("mysql-pin-algorithm-lock", r)

    def test_non_alter_table_ignored(self):
        self.assertEqual(rules("SELECT 1; CREATE TABLE t (id int);"), {})

    def test_case06_fixture_expected_set(self):
        path = os.path.join(REPO, "evals", "cases", "06_mysql_type_change.sql")
        findings, _ = analyze.analyze_mysql(path)
        got = {f["rule"]: f["level"] for f in findings}
        self.assertEqual(got.get("mysql-column-type-copy"), "error")
        self.assertEqual(got.get("mysql-add-foreign-key"), "warning")
        self.assertEqual(got.get("mysql-pin-algorithm-lock"), "warning")


if __name__ == "__main__":
    unittest.main()
