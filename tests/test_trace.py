"""A7 — trace.py lock parsing + exit-code branches. subprocess.run is mocked: no eugene,
no Postgres needed."""
import contextlib
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MIG = os.path.join(REPO, "evals", "cases", "02_bare_create_index.sql")
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import trace  # noqa: E402


def run_main(argv, stdout="", stderr="", returncode=0, side_effect=None):
    fake = types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)
    patch = (mock.patch.object(trace.subprocess, "run", side_effect=side_effect)
             if side_effect else
             mock.patch.object(trace.subprocess, "run", return_value=fake))
    with patch, mock.patch.object(sys, "argv", argv), \
            contextlib.redirect_stdout(io.StringIO()) as out, \
            contextlib.redirect_stderr(io.StringIO()):
        rc = trace.main()
    return rc, out.getvalue()


class LocksOf(unittest.TestCase):
    def test_key_fallback_chain(self):
        self.assertEqual(trace.locks_of({"locks_taken": [1]}), [1])
        self.assertEqual(trace.locks_of({"locks": [2]}), [2])
        self.assertEqual(trace.locks_of({"new_locks_taken": [3]}), [3])
        self.assertEqual(trace.locks_of({}), [])


class ExitCodes(unittest.TestCase):
    def test_passed_true_no_dangerous_exit_0(self):
        body = json.dumps({"passed_all_checks": True, "dangerous_locks_count": 0,
                           "statements": []})
        rc, _ = run_main(["trace.py", MIG], stdout=body)
        self.assertEqual(rc, 0)

    def test_failed_checks_exit_1(self):
        body = json.dumps({"passed_all_checks": False, "dangerous_locks_count": 0,
                           "statements": []})
        rc, _ = run_main(["trace.py", MIG], stdout=body)
        self.assertEqual(rc, 1)

    def test_dangerous_locks_exit_1(self):
        body = json.dumps({"passed_all_checks": True, "dangerous_locks_count": 2,
                           "statements": []})
        rc, _ = run_main(["trace.py", MIG], stdout=body)
        self.assertEqual(rc, 1)

    def test_empty_stdout_exit_2(self):
        rc, _ = run_main(["trace.py", MIG], stdout="", stderr="initdb: not found",
                         returncode=1)
        self.assertEqual(rc, 2)

    def test_invalid_json_exit_2(self):
        rc, _ = run_main(["trace.py", MIG], stdout="not json")
        self.assertEqual(rc, 2)

    def test_eugene_missing_exit_2(self):
        rc, _ = run_main(["trace.py", MIG], side_effect=FileNotFoundError())
        self.assertEqual(rc, 2)

    def test_missing_file_exit_2(self):
        rc, _ = run_main(["trace.py", "nope.sql"])
        self.assertEqual(rc, 2)

    def test_json_passthrough(self):
        body = json.dumps({"passed_all_checks": True, "dangerous_locks_count": 0,
                           "statements": []})
        rc, out = run_main(["trace.py", MIG, "--json"], stdout=body)
        self.assertEqual(json.loads(out)["passed_all_checks"], True)


class PrintHuman(unittest.TestCase):
    def test_renders_dangerous_lock(self):
        data = {"statements": [{"sql": "CREATE INDEX i ON t (c)", "duration_millis": 12,
                                "locks_taken": [{"mode": "ShareLock", "object_name": "t",
                                                 "maybe_dangerous": True,
                                                 "blocked_queries": ["INSERT"]}]}],
                "dangerous_locks_count": 1, "passed_all_checks": False}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            trace.print_human(data, "m.sql")
        self.assertIn("DANGEROUS", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
