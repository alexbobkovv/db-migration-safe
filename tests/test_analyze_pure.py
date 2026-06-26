"""A1 — analyze.py pure helpers: comment stripping, statement split, severity, finding."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


class StripComments(unittest.TestCase):
    def test_line_comment(self):
        self.assertNotIn("secret", analyze.strip_sql_comments("SELECT 1; -- secret"))

    def test_block_comment_multiline(self):
        out = analyze.strip_sql_comments("A /* x\ny */ B")
        self.assertNotIn("x", out)
        self.assertIn("A", out)
        self.assertIn("B", out)

    def test_known_limitation_strips_dashes_in_string(self):
        # Documented limitation: -- inside a string literal is also stripped.
        # Pinned so a future change is a conscious one, not a silent regression.
        self.assertNotIn("b", analyze.strip_sql_comments("SELECT 'a -- b'"))


class SplitStatements(unittest.TestCase):
    def test_semicolon_in_single_quote_not_split(self):
        self.assertEqual(analyze.split_statements("INSERT INTO t VALUES ('a;b')"),
                         ["INSERT INTO t VALUES ('a;b')"])

    def test_semicolon_in_double_quote_not_split(self):
        self.assertEqual(analyze.split_statements('CREATE TABLE "a;b" (id int)'),
                         ['CREATE TABLE "a;b" (id int)'])

    def test_trailing_statement_without_semicolon(self):
        self.assertEqual(analyze.split_statements("A; B"), ["A", "B"])

    def test_empty_statements_dropped(self):
        self.assertEqual(analyze.split_statements("A;; ;B;"), ["A", "B"])

    def test_dollar_quote_known_limitation_splits(self):
        # Out of scope: dollar-quoted bodies are NOT treated as a single statement.
        self.assertGreater(len(analyze.split_statements("DO $$ BEGIN x; y; END $$")), 1)


class Severity(unittest.TestCase):
    def test_advisory_rules_are_warnings(self):
        for r in analyze.SQUAWK_ADVISORY:
            self.assertEqual(analyze.squawk_severity(r), "warning", r)

    def test_unknown_rule_is_error_fail_closed(self):
        self.assertEqual(analyze.squawk_severity("require-concurrent-index-creation"), "error")
        self.assertEqual(analyze.squawk_severity("some-new-lock-rule"), "error")


class FindingShape(unittest.TestCase):
    def test_finding_trims_and_keys(self):
        f = analyze.finding("squawk", "r", "error", 3, "  msg  ", "  help  ")
        self.assertEqual(set(f), {"source", "rule", "level", "line", "message", "help"})
        self.assertEqual(f["message"], "msg")
        self.assertEqual(f["help"], "help")

    def test_tool_error_is_error_level_and_truncates_help(self):
        f = analyze.tool_error("squawk", "x" * 1000)
        self.assertEqual(f["level"], "error")
        self.assertEqual(f["rule"], "tool-error")
        self.assertLessEqual(len(f["help"]), 500)


if __name__ == "__main__":
    unittest.main()
