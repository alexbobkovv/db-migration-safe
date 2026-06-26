"""A2 — squawk / eugene JSON normalization. run_tool is mocked, so no binary is needed.

Synthetic JSON exercises every parse branch now; B5 freezes real squawk 2.58 / eugene 0.8.3
output into tests/fixtures/ and the skipUnless tests below pin against it once present.
"""
import json
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


def _run(found, code, out, err):
    return mock.patch.object(analyze, "run_tool", return_value=(found, code, out, err))


class Squawk(unittest.TestCase):
    def test_not_installed(self):
        with _run(False, None, "", ""):
            findings, status = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertIsNone(findings)
        self.assertIn("not installed", status)

    def test_empty_stdout_nonzero_is_tool_error_never_silent_pass(self):
        with _run(True, 3, "", "boom"):
            findings, status = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["rule"], "tool-error")
        self.assertEqual(findings[0]["level"], "error")

    def test_empty_stdout_zero_is_clean(self):
        with _run(True, 0, "", ""):
            findings, status = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings, [])
        self.assertEqual(status, "ok")

    def test_lock_rule_with_warning_level_becomes_error(self):
        payload = json.dumps([{"rule_name": "require-concurrent-index-creation",
                               "level": "Warning", "line": 2,
                               "message": "build the index concurrently", "help": "use CONCURRENTLY"}])
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings[0]["level"], "error")
        self.assertEqual(findings[0]["line"], 2)

    def test_advisory_rule_stays_warning(self):
        payload = json.dumps([{"rule_name": "prefer-text-field", "level": "Warning",
                               "line": 1, "message": "m", "help": "h"}])
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings[0]["level"], "warning")

    def test_native_error_level_honored(self):
        payload = json.dumps([{"rule_name": "syntax-error", "level": "Error",
                               "line": 1, "message": "bad", "help": ""}])
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings[0]["level"], "error")

    def test_dict_with_violations_key(self):
        payload = json.dumps({"violations": [{"rule_name": "prefer-text-field",
                                              "level": "Warning", "line": 1,
                                              "message": "m", "help": "h"}]})
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings[0]["rule"], "prefer-text-field")

    def test_invalid_json_is_tool_error(self):
        with _run(True, 0, "not json", ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertEqual(findings[0]["rule"], "tool-error")


class Eugene(unittest.TestCase):
    def test_not_installed(self):
        with _run(False, None, "", ""):
            findings, status = analyze.analyze_eugene("m.sql", "eugene")
        self.assertIsNone(findings)
        self.assertIn("not installed", status)

    def test_empty_nonzero_is_tool_error(self):
        with _run(True, 2, "", "parse error"):
            findings, _ = analyze.analyze_eugene("m.sql", "eugene")
        self.assertEqual(findings[0]["rule"], "tool-error")

    def test_e_is_error_w_is_warning(self):
        payload = json.dumps({"statements": [
            {"line_number": 2, "triggered_rules": [
                {"id": "E6", "name": "index build blocks writes", "workaround": "concurrently"},
                {"id": "W12", "name": "advisory", "workaround": ""}]}]})
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_eugene("m.sql", "eugene")
        by_rule = {f["rule"]: f for f in findings}
        self.assertEqual(by_rule["E6"]["level"], "error")
        self.assertEqual(by_rule["E6"]["line"], 2)
        self.assertEqual(by_rule["W12"]["level"], "warning")

    def test_lints_key_variant_and_line_fallback(self):
        payload = json.dumps([{"statements": [
            {"line": 5, "lints": [{"id": "E9", "name": "n", "help": "h"}]}]}])
        with _run(True, 1, payload, ""):
            findings, _ = analyze.analyze_eugene("m.sql", "eugene")
        self.assertEqual(findings[0]["rule"], "E9")
        self.assertEqual(findings[0]["line"], 5)

    def test_invalid_json_is_tool_error(self):
        with _run(True, 0, "{not json", ""):
            findings, _ = analyze.analyze_eugene("m.sql", "eugene")
        self.assertEqual(findings[0]["rule"], "tool-error")


class FrozenFixtures(unittest.TestCase):
    """Pin merged output against real captured tool JSON (populated in Phase B5)."""

    def _load(self, name):
        path = os.path.join(FIXTURES, name)
        if not os.path.exists(path):
            self.skipTest(f"fixture not captured yet: {name} (see TEST-PLAN B5)")
        with open(path) as fh:
            return fh.read()

    def test_squawk_case02_fixture(self):
        out = self._load("squawk_case02.json")
        with _run(True, 1, out, ""):
            findings, _ = analyze.analyze_squawk("m.sql", None, "squawk")
        self.assertTrue(any(f["level"] == "error" for f in findings))

    def test_eugene_case02_fixture(self):
        out = self._load("eugene_case02.json")
        with _run(True, 1, out, ""):
            findings, _ = analyze.analyze_eugene("m.sql", "eugene")
        ids = {f["rule"] for f in findings}
        self.assertTrue({"E6", "E9"} & ids, ids)


if __name__ == "__main__":
    unittest.main()
