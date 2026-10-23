import json
import tempfile
import unittest
from pathlib import Path

from issuepilot.models import Issue
from issuepilot.suggestions import (
    STATUS_APPROVED,
    STATUS_IGNORED,
    STATUS_PENDING,
    SuggestionStore,
    build_suggestions,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "issues.json"


def fixture_issues():
    from issuepilot.models import load_issues
    with open(FIXTURES, encoding="utf-8") as fh:
        return load_issues(json.load(fh))


class TestBuildSuggestions(unittest.TestCase):
    def test_every_suggestion_has_evidence_confidence_and_preview(self):
        suggestions = build_suggestions(fixture_issues())
        self.assertTrue(suggestions)
        for s in suggestions:
            self.assertTrue(s.id)
            self.assertIn(s.kind, ("duplicate", "missing-repro", "label"))
            self.assertTrue(s.evidence, f"{s.id} has no evidence")
            self.assertIn(s.confidence, ("low", "medium", "high"))
            self.assertIn("PREVIEW ONLY", s.proposed_action)
            self.assertEqual(s.status, STATUS_PENDING)

    def test_deterministic_ids(self):
        a = build_suggestions(fixture_issues())
        b = build_suggestions(fixture_issues())
        self.assertEqual([s.id for s in a], [s.id for s in b])

    def test_empty_issue_list(self):
        self.assertEqual(build_suggestions([]), [])


class TestSuggestionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_persists_and_preserves_status(self):
        suggestions = build_suggestions(fixture_issues())
        store = SuggestionStore(self.state)
        added, _ = store.merge(suggestions)
        self.assertEqual(added, len(suggestions))
        target = suggestions[0]
        store.ignore(target.id)
        store.save()

        # Re-analyze: the ignored suggestion must not resurface as pending.
        store2 = SuggestionStore(self.state)
        added2, _ = store2.merge(build_suggestions(fixture_issues()))
        self.assertEqual(added2, 0)
        self.assertEqual(store2.get(target.id).status, STATUS_IGNORED)
        self.assertNotIn(target.id, [s.id for s in store2.pending()])

    def test_approve_and_ignore(self):
        store = SuggestionStore(self.state)
        store.merge(build_suggestions(fixture_issues()))
        s = store.pending()[0]
        store.approve(s.id)
        self.assertEqual(store.get(s.id).status, STATUS_APPROVED)
        other = store.pending()[0]
        store.ignore(other.id)
        self.assertEqual(store.get(other.id).status, STATUS_IGNORED)

    def test_unknown_id_raises_with_known_ids(self):
        store = SuggestionStore(self.state)
        store.merge(build_suggestions(fixture_issues()))
        with self.assertRaises(KeyError) as ctx:
            store.approve("nope-0000")
        self.assertIn("no suggestion with id", str(ctx.exception))

    def test_corrupt_state_file_raises_meaningful_error(self):
        self.state.write_text("not json at all", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            SuggestionStore(self.state)
        self.assertIn("cannot read state file", str(ctx.exception))

    def test_state_file_not_a_list(self):
        self.state.write_text('{"oops": true}', encoding="utf-8")
        with self.assertRaises(ValueError):
            SuggestionStore(self.state)


if __name__ == "__main__":
    unittest.main()
