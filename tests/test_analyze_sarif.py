"""A7 — SARIF 2.1.0 output for GitHub code scanning. Pure (to_sarif) + CLI; the CLI cases
force --no-external so they hold regardless of whether squawk/eugene are installed."""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
ANALYZE = os.path.join(REPO, "scripts", "analyze.py")
CASE01 = os.path.join(REPO, "evals", "cases", "01_add_column_not_null_default.sql")
CASE02 = os.path.join(REPO, "evals", "cases", "02_bare_create_index.sql")
CASE04B = os.path.join(REPO, "evals", "cases", "04b_add_column_nullable.sql")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import analyze  # noqa: E402


def verdict(findings, path="m.sql"):
    return analyze.build_verdict(findings, {}, "postgres", path)


def run(*args):
    p = subprocess.run([sys.executable, ANALYZE, *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


class ToSarif(unittest.TestCase):
    def test_schema_version_and_driver(self):
        s = analyze.to_sarif([verdict([analyze.finding("squawk", "r1", "error", 3, "m", "h")])])
        self.assertEqual(s["version"], "2.1.0")
        self.assertIn("$schema", s)
        self.assertEqual(s["runs"][0]["tool"]["driver"]["name"], "db-migration-safe")

    def test_levels_mapped(self):
        s = analyze.to_sarif([verdict([
            analyze.finding("squawk", "e", "error", 1, "m", ""),
            analyze.finding("eugene", "W12", "warning", 2, "m", ""),
            analyze.finding("x", "i", "info", 3, "m", ""),
        ])])
        levels = {r["ruleId"]: r["level"] for r in s["runs"][0]["results"]}
        self.assertEqual(levels["squawk:e"], "error")
        self.assertEqual(levels["eugene:W12"], "warning")
        self.assertEqual(levels["x:i"], "note")

    def test_ruleid_is_source_colon_rule(self):
        s = analyze.to_sarif([verdict([analyze.finding("heuristic-pg", "pg-set-not-null", "error", 1, "m", "h")])])
        self.assertEqual(s["runs"][0]["results"][0]["ruleId"], "heuristic-pg:pg-set-not-null")

    def test_missing_line_anchored_to_1(self):
        s = analyze.to_sarif([verdict([analyze.finding("heuristic", "r", "warning", None, "m", "h")])])
        region = s["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["region"]
        self.assertEqual(region["startLine"], 1)

    def test_rules_deduped(self):
        s = analyze.to_sarif([verdict([
            analyze.finding("squawk", "r", "error", 1, "m", "h"),
            analyze.finding("squawk", "r", "error", 9, "m", "h"),
        ])])
        ids = [r["id"] for r in s["runs"][0]["tool"]["driver"]["rules"]]
        self.assertEqual(ids, ["squawk:r"])
        self.assertEqual(len(s["runs"][0]["results"]), 2)  # results not deduped, rules are

    def test_empty_is_valid_sarif(self):
        s = analyze.to_sarif([])
        self.assertEqual(s["version"], "2.1.0")
        self.assertEqual(s["runs"][0]["results"], [])

    def test_per_file_uri_preserved(self):
        s = analyze.to_sarif([
            verdict([analyze.finding("squawk", "a", "error", 1, "m", "")], "x/one.sql"),
            verdict([analyze.finding("squawk", "b", "error", 1, "m", "")], "y/two.sql"),
        ])
        uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
                for r in s["runs"][0]["results"]}
        self.assertEqual(uris, {"x/one.sql", "y/two.sql"})


class SarifCli(unittest.TestCase):
    def test_single_file_sarif_parses_and_gates(self):
        rc, out, _ = run(CASE02, "--no-external", "--sarif")
        data = json.loads(out)
        self.assertEqual(data["version"], "2.1.0")
        rule_ids = [r["ruleId"] for r in data["runs"][0]["results"]]
        self.assertIn("heuristic-pg:pg-create-index-not-concurrent", rule_ids)
        self.assertEqual(rc, 1)  # --sarif still returns the gate exit code

    def test_multi_file_merged_single_run(self):
        rc, out, _ = run(CASE02, CASE01, CASE04B, "--no-external", "--sarif")
        data = json.loads(out)
        self.assertEqual(len(data["runs"]), 1)
        uris = {r["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
                for r in data["runs"][0]["results"]}
        self.assertEqual(uris, {CASE02, CASE01})  # 04b is clean → no results
        self.assertEqual(rc, 1)

    def test_all_clean_sarif_empty_results_exit_0(self):
        rc, out, _ = run(CASE04B, "--no-external", "--sarif")
        data = json.loads(out)
        self.assertEqual(data["runs"][0]["results"], [])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
