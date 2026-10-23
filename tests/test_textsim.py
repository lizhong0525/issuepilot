import unittest

from issuepilot import textsim


class TestTokenize(unittest.TestCase):
    def test_lowercases_and_splits(self):
        self.assertEqual(textsim.tokenize("Hello, World!"), ["hello", "world"])

    def test_keeps_identifiers_and_strips_punctuation_edges(self):
        toks = textsim.tokenize("KeyError: 'api_key' in config.yaml.")
        self.assertIn("api_key", toks)
        self.assertIn("config.yaml", toks)
        self.assertIn("keyerror", toks)

    def test_empty(self):
        self.assertEqual(textsim.tokenize(""), [])


class TestTokenSets(unittest.TestCase):
    def test_distinctive_requires_length_or_special_chars(self):
        dist = textsim.distinctive_tokens("the cat sat on ubuntu 22.04")
        self.assertIn("ubuntu", dist)
        self.assertIn("22.04", dist)
        self.assertNotIn("cat", dist)
        self.assertNotIn("the", dist)

    def test_error_tokens_catch_identifiers(self):
        err = textsim.error_tokens("KeyError api_key config.yaml version 2.3.1 slow")
        self.assertIn("keyerror", err)
        self.assertIn("api_key", err)
        self.assertIn("config.yaml", err)
        self.assertIn("2.3.1", err)
        self.assertNotIn("slow", err)

    def test_jaccard(self):
        self.assertAlmostEqual(textsim.jaccard({"a", "b"}, {"b", "c"}), 1 / 3)
        self.assertEqual(textsim.jaccard(set(), {"a"}), 0.0)

    def test_cosine(self):
        self.assertAlmostEqual(textsim.cosine(["a", "b"], ["a", "b"]), 1.0)
        self.assertEqual(textsim.cosine(["a"], ["b"]), 0.0)
        self.assertEqual(textsim.cosine([], ["a"]), 0.0)

    def test_title_similarity_identical(self):
        self.assertAlmostEqual(
            textsim.title_similarity("Crash on startup", "Crash on startup"), 1.0
        )

    def test_title_similarity_different(self):
        self.assertLess(
            textsim.title_similarity("Crash on startup", "Add dark mode"), 0.4
        )


if __name__ == "__main__":
    unittest.main()
