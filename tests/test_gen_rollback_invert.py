"""A5 — gen_rollback.invert / invert_alter matrix (pure; no DB)."""
import os
import sys
import unittest
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import gen_rollback as gr  # noqa: E402


def inv(stmt):
    return gr.invert(stmt)


def line(stmt):
    r = gr.invert(stmt)
    return r["status"], r["lines"][0]


class CreateDrop(unittest.TestCase):
    def test_create_table(self):
        self.assertEqual(line("CREATE TABLE t (id int)"), ("ok", "DROP TABLE IF EXISTS t;"))

    def test_create_index_concurrently_if_not_exists(self):
        self.assertEqual(line("CREATE INDEX CONCURRENTLY IF NOT EXISTS i ON t (c)"),
                         ("ok", "DROP INDEX CONCURRENTLY IF EXISTS i;"))

    def test_create_index_plain(self):
        self.assertEqual(line("CREATE INDEX i ON t (c)"), ("ok", "DROP INDEX IF EXISTS i;"))

    def test_anonymous_index_is_manual(self):
        self.assertEqual(inv("CREATE INDEX ON t (c)")["status"], "manual")

    def test_drop_index_is_manual(self):
        self.assertEqual(inv("DROP INDEX i")["status"], "manual")

    def test_drop_table_irreversible_with_backup(self):
        r = inv("DROP TABLE t")
        self.assertEqual(r["status"], "irreversible")
        self.assertTrue(any(f"t_backup_{gr.TODAY}" in ln for ln in r["lines"]))


class AlterColumn(unittest.TestCase):
    def test_add_column(self):
        self.assertEqual(line("ALTER TABLE t ADD COLUMN c int"),
                         ("ok", "ALTER TABLE t DROP COLUMN IF EXISTS c;"))

    def test_add_column_if_not_exists(self):
        self.assertEqual(inv("ALTER TABLE t ADD COLUMN IF NOT EXISTS c int")["status"], "ok")

    def test_drop_column_irreversible_with_backup(self):
        r = inv("ALTER TABLE t DROP COLUMN c")
        self.assertEqual(r["status"], "irreversible")
        self.assertTrue(any(f"t_c_backup_{gr.TODAY}" in ln for ln in r["lines"]))

    def test_add_constraint(self):
        self.assertEqual(line("ALTER TABLE t ADD CONSTRAINT k CHECK (c > 0) NOT VALID"),
                         ("ok", "ALTER TABLE t DROP CONSTRAINT IF EXISTS k;"))

    def test_drop_constraint_is_manual(self):
        self.assertEqual(inv("ALTER TABLE t DROP CONSTRAINT k")["status"], "manual")

    def test_validate_constraint_is_noop(self):
        self.assertEqual(inv("ALTER TABLE t VALIDATE CONSTRAINT k")["status"], "noop")

    def test_rename_column(self):
        self.assertEqual(line("ALTER TABLE t RENAME COLUMN a TO b"),
                         ("ok", "ALTER TABLE t RENAME COLUMN b TO a;"))

    def test_rename_constraint(self):
        self.assertEqual(line("ALTER TABLE t RENAME CONSTRAINT a TO b"),
                         ("ok", "ALTER TABLE t RENAME CONSTRAINT b TO a;"))

    def test_rename_table(self):
        self.assertEqual(line("ALTER TABLE t RENAME TO t2"),
                         ("ok", "ALTER TABLE t2 RENAME TO t;"))

    def test_set_not_null(self):
        self.assertEqual(line("ALTER TABLE t ALTER COLUMN c SET NOT NULL"),
                         ("ok", "ALTER TABLE t ALTER COLUMN c DROP NOT NULL;"))

    def test_drop_not_null(self):
        self.assertEqual(line("ALTER TABLE t ALTER COLUMN c DROP NOT NULL"),
                         ("ok", "ALTER TABLE t ALTER COLUMN c SET NOT NULL;"))

    def test_set_default_is_manual(self):
        self.assertEqual(inv("ALTER TABLE t ALTER COLUMN c SET DEFAULT 0")["status"], "manual")

    def test_drop_default_is_manual(self):
        self.assertEqual(inv("ALTER TABLE t ALTER COLUMN c DROP DEFAULT")["status"], "manual")

    def test_type_change_is_manual(self):
        self.assertEqual(inv("ALTER TABLE t ALTER COLUMN c TYPE numeric(12,2)")["status"], "manual")


class SessionAndMulti(unittest.TestCase):
    def test_set_is_noop(self):
        self.assertEqual(inv("SET lock_timeout = '5s'")["status"], "noop")

    def test_begin_commit_noop(self):
        self.assertEqual(inv("BEGIN")["status"], "noop")
        self.assertEqual(inv("COMMIT")["status"], "noop")

    def test_multi_action_reverse_order_and_worst_status(self):
        r = inv("ALTER TABLE t ADD COLUMN a int, DROP COLUMN b")
        self.assertEqual(r["status"], "irreversible")          # DROP COLUMN dominates
        self.assertEqual(r["lines"][-1], "ALTER TABLE t DROP COLUMN IF EXISTS a;")  # ADD undone last
        self.assertTrue(any("DROP COLUMN t.b" in ln for ln in r["lines"]))


class QuotedIdentifiersIssue001(unittest.TestCase):
    def test_quoted_add_column(self):
        self.assertEqual(line('ALTER TABLE "My Orders" ADD COLUMN "user id" text'),
                         ("ok", 'ALTER TABLE "My Orders" DROP COLUMN IF EXISTS "user id";'))

    def test_quoted_unique_index(self):
        self.assertEqual(line('CREATE UNIQUE INDEX CONCURRENTLY "My Idx" ON "My Orders" (id)'),
                         ("ok", 'DROP INDEX CONCURRENTLY IF EXISTS "My Idx";'))

    def test_quoted_constraint(self):
        self.assertEqual(line('ALTER TABLE "My Orders" ADD CONSTRAINT "my chk" CHECK (id > 0) NOT VALID'),
                         ("ok", 'ALTER TABLE "My Orders" DROP CONSTRAINT IF EXISTS "my chk";'))

    def test_quoted_create_table(self):
        self.assertEqual(line('CREATE TABLE "My Orders" (id int)'),
                         ("ok", 'DROP TABLE IF EXISTS "My Orders";'))


class Helpers(unittest.TestCase):
    def test_bare(self):
        self.assertEqual(gr.bare("public.orders"), "orders")
        self.assertEqual(gr.bare('"My Orders"'), "My Orders")

    def test_split_top_level_commas(self):
        self.assertEqual(len(gr.split_top_level_commas("a, b(c, d), e")), 3)

    def test_today_matches(self):
        self.assertEqual(gr.TODAY, date.today().strftime("%Y%m%d"))


if __name__ == "__main__":
    unittest.main()
