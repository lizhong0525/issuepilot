"""End-to-end CLI tests over the bundled fixtures. No network involved."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from issuepilot.cli import main

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "issues.json"


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class TestAnalyzeAndReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = str(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_analyze_then_review_shows_evidence_and_previews(self):
        code, out, _ = run_cli(
            ["analyze", "--issues", str(FIXTURES), "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Analyzed 12 issues", out)

        code, out, _ = run_cli(["review", "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("review queue", out)
        self.assertIn("confidence:", out)
        self.assertIn("evidence:", out)
        self.assertIn("PREVIEW ONLY", out)
        self.assertIn("Nothing has been posted", out)

    def test_approve_and_ignore_flow(self):
        run_cli(["analyze", "--issues", str(FIXTURES), "--state", self.state])
        _, out, _ = run_cli(["review", "--state", self.state, "--brief"])
        first_id = out.split("[")[1].split("]")[0]

        code, out, _ = run_cli(["approve", first_id, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Manual steps", out)
        self.assertIn("never posts", out)

        code, out, _ = run_cli(["ignore", first_id, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Ignored", out)

        # Re-analysis must not resurface the ignored suggestion.
        run_cli(["analyze", "--issues", str(FIXTURES), "--state", self.state])
        _, out, _ = run_cli(["review", "--state", self.state, "--brief"])
        self.assertNotIn(first_id, out)

    def test_unknown_id_is_a_clean_error(self):
        run_cli(["analyze", "--issues", str(FIXTURES), "--state", self.state])
        code, _, err = run_cli(["approve", "nope-0000", "--state", self.state])
        self.assertEqual(code, 2)
        self.assertIn("no suggestion with id", err)

    def test_missing_issues_file(self):
        code, _, err = run_cli(
            ["analyze", "--issues", "does_not_exist.json", "--state", self.state])
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_malformed_issues_file(self):
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        code, _, err = run_cli(
            ["analyze", "--issues", str(bad), "--state", self.state])
        self.assertEqual(code, 2)
        self.assertIn("malformed JSON", err)

    def test_wrong_shape_issues_file(self):
        bad = Path(self.tmp.name) / "shape.json"
        bad.write_text('{"number": 1}', encoding="utf-8")
        code, _, err = run_cli(
            ["analyze", "--issues", str(bad), "--state", self.state])
        self.assertEqual(code, 2)
        self.assertIn("invalid issues file", err)

    def test_empty_issues_file(self):
        empty = Path(self.tmp.name) / "empty.json"
        empty.write_text("[]", encoding="utf-8")
        code, out, _ = run_cli(
            ["analyze", "--issues", str(empty), "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("nothing to analyze", out)

    def test_review_with_empty_queue(self):
        code, out, _ = run_cli(["review", "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("empty", out)

    def test_corrupt_state_file_is_a_clean_error(self):
        bad = Path(self.tmp.name) / "state.json"
        bad.write_text("garbage{", encoding="utf-8")
        code, _, err = run_cli(["review", "--state", str(bad)])
        self.assertEqual(code, 2)
        self.assertIn("cannot read state file", err)

    def test_malformed_state_entries_are_a_clean_error(self):
        bad = Path(self.tmp.name) / "state.json"
        bad.write_text('[{"foo": 1}]', encoding="utf-8")
        code, _, err = run_cli(["approve", "label-1a2b3c4d", "--state", str(bad)])
        self.assertEqual(code, 2)
        self.assertIn("malformed entries", err)


class TestDemo(unittest.TestCase):
    def test_demo_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "demo_state.json")
            code, out, err = run_cli(["demo", "--state", state])
            self.assertEqual(code, 0, err)
            self.assertIn("possible duplicates", out)
            self.assertIn("PREVIEW ONLY", out)
            self.assertIn("nothing was posted", out.lower())

    def test_demo_survives_non_utf8_console(self):
        # On a Windows console with a legacy code page (e.g. cp1252), printing
        # a non-ASCII repo path must not crash with UnicodeEncodeError.
        import os
        import subprocess
        import sys

        repo_root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as tmp:
            state = str(Path(tmp) / "state.json")
            env = dict(os.environ, PYTHONIOENCODING="cp1252")
            proc = subprocess.run(
                [sys.executable, "-m", "issuepilot", "demo", "--state", state],
                capture_output=True,
                cwd=repo_root,
                env=env,
            )
        self.assertEqual(
            proc.returncode, 0, proc.stderr.decode("utf-8", "replace")
        )
        self.assertIn(
            "possible duplicates",
            proc.stdout.decode("utf-8", "replace"),
        )


if __name__ == "__main__":
    unittest.main()
