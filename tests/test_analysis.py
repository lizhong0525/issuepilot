import json
import unittest
from pathlib import Path

from issuepilot.analysis import (
    check_repro_details,
    find_duplicate_candidates,
    suggest_labels,
)
from issuepilot.models import Issue, load_issues

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "issues.json"


def fixture_issues():
    with open(FIXTURES, encoding="utf-8") as fh:
        return load_issues(json.load(fh))


def make_issue(number, title, body, state="open", labels=None):
    return Issue(number=number, title=title, body=body, state=state,
                 labels=labels or [])


class TestDuplicateDetection(unittest.TestCase):
    def test_fixture_cluster_detected(self):
        cands = find_duplicate_candidates(fixture_issues())
        pairs = {(c.issue_a, c.issue_b) for c in cands}
        # The three startup-crash issues report the same KeyError.
        self.assertIn((101, 102), pairs)
        self.assertIn((101, 103), pairs)
        self.assertIn((102, 103), pairs)
        for c in cands:
            self.assertIn(c.confidence, ("low", "medium", "high"))
            self.assertTrue(c.evidence())

    def test_similar_wording_alone_is_not_flagged(self):
        # Both issues are about "CSV export" but describe different problems.
        cands = find_duplicate_candidates(fixture_issues())
        pairs = {(c.issue_a, c.issue_b) for c in cands}
        self.assertNotIn((104, 105), pairs)
        self.assertNotIn((105, 104), pairs)

    def test_closed_issues_are_ignored(self):
        # #112 describes the same crash as #101-103 but is closed.
        cands = find_duplicate_candidates(fixture_issues())
        for c in cands:
            self.assertNotIn(112, (c.issue_a, c.issue_b))

    def test_identical_issues_flagged_high(self):
        body = ("Crash on startup with KeyError 'api_key' in config.yaml on "
                "version 2.3.1, Ubuntu 22.04, Python 3.11, traceback shown.")
        issues = [
            make_issue(1, "Startup crash KeyError api_key", body),
            make_issue(2, "Startup crash KeyError api_key", body),
        ]
        cands = find_duplicate_candidates(issues)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].confidence, "high")

    def test_unrelated_issues_not_flagged(self):
        issues = [
            make_issue(1, "Add dark mode", "Please add a dark theme option."),
            make_issue(2, "Typo in README", "The install section has a typo."),
        ]
        self.assertEqual(find_duplicate_candidates(issues), [])


class TestReproCheck(unittest.TestCase):
    def test_empty_bug_report_flags_everything(self):
        issues = {i.number: i for i in fixture_issues()}
        check = check_repro_details(issues[109])
        self.assertIsNotNone(check)
        self.assertEqual(check.confidence, "high")
        self.assertEqual(len(check.missing), 4)

    def test_well_formed_bug_report_passes(self):
        issues = {i.number: i for i in fixture_issues()}
        self.assertIsNone(check_repro_details(issues[101]))
        self.assertIsNone(check_repro_details(issues[105]))
        self.assertIsNone(check_repro_details(issues[111]))

    def test_partial_report(self):
        issues = {i.number: i for i in fixture_issues()}
        check = check_repro_details(issues[110])
        self.assertIsNotNone(check)
        self.assertEqual(check.confidence, "medium")
        self.assertIn("steps to reproduce", check.missing)
        self.assertIn("environment / version info", check.present)

    def test_non_bug_issues_skipped(self):
        issues = {i.number: i for i in fixture_issues()}
        self.assertIsNone(check_repro_details(issues[106]))  # feature request
        self.assertIsNone(check_repro_details(issues[108]))  # question

    def test_closed_issue_skipped(self):
        issue = make_issue(1, "crash bug", "it crashes", state="closed")
        self.assertIsNone(check_repro_details(issue))


class TestLabelSuggestions(unittest.TestCase):
    def test_fixture_labels(self):
        issues = {i.number: i for i in fixture_issues()}
        labels = {n: [l.label for l in suggest_labels(i)]
                  for n, i in issues.items()}
        self.assertIn("performance", labels[104])
        self.assertIn("enhancement", labels[106])
        self.assertIn("documentation", labels[107])
        self.assertIn("question", labels[108])
        self.assertIn("bug", labels[101])

    def test_camelcase_errors_match_bug(self):
        issue = make_issue(1, "ValueError when fetching", "I get a ValueError.")
        labels = [l.label for l in suggest_labels(issue)]
        self.assertIn("bug", labels)

    def test_existing_label_not_resuggested(self):
        issue = make_issue(1, "crash on startup", "it crashes with an error",
                           labels=["bug"])
        labels = [l.label for l in suggest_labels(issue)]
        self.assertNotIn("bug", labels)

    def test_closed_issue_gets_no_labels(self):
        issue = make_issue(1, "crash", "it crashes", state="closed")
        self.assertEqual(suggest_labels(issue), [])

    def test_confidence_scales_with_matches(self):
        weak = make_issue(1, "it crashes", "crash")
        strong = make_issue(2, "crash bug", "crash error traceback broken bug")
        weak_labels = {l.label: l.confidence for l in suggest_labels(weak)}
        strong_labels = {l.label: l.confidence for l in suggest_labels(strong)}
        self.assertEqual(weak_labels["bug"], "low")
        self.assertEqual(strong_labels["bug"], "high")


if __name__ == "__main__":
    unittest.main()
