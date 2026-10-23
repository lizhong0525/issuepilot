"""Tests for the GitHub client. Fully offline: the transport is mocked."""

import json
import tempfile
import unittest
from pathlib import Path

from issuepilot.cache import HttpCache
from issuepilot.github_client import (
    GitHubApiError,
    GitHubClient,
    RateLimitExceeded,
    Response,
)

PAGE1 = [
    {"number": 1, "title": "Crash on startup", "body": "it crashes",
     "state": "open", "labels": [], "user": {"login": "a"}},
    {"number": 2, "title": "Add feature", "body": "", "state": "open",
     "labels": [{"name": "enhancement"}], "user": {"login": "b"},
     "pull_request": {"url": "https://api.github.com/..."}},  # PR, filtered out
]
PAGE2 = [
    {"number": 3, "title": "Typo in docs", "body": "readme typo",
     "state": "open", "labels": [], "user": {"login": "c"}},
]


class MockTransport:
    """Scriptable transport: queues (status, headers, body) responses."""

    def __init__(self):
        self.script = []
        self.calls = []  # (url, headers)

    def add(self, status, body="", headers=None):
        self.script.append(Response(status=status, headers=headers or {},
                                    body=body))
        return self

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if not self.script:
            raise AssertionError(f"unexpected request to {url}")
        if len(self.script) == 1:
            return self.script[0]  # repeat last response
        return self.script.pop(0)


class TestFetchIssues(unittest.TestCase):
    def test_pagination_follows_link_header(self):
        t = MockTransport()
        next_url = "https://api.github.com/repos/o/r/issues?state=open&per_page=100&page=2"
        t.add(200, json.dumps(PAGE1),
              headers={"Link": f'<{next_url}>; rel="next"'})
        t.add(200, json.dumps(PAGE2))
        client = GitHubClient(transport=t, sleep=lambda s: None)
        issues = client.fetch_issues("o", "r")
        self.assertEqual([i.number for i in issues], [1, 3])  # PR filtered
        self.assertEqual(t.calls[1][0], next_url)

    def test_max_pages_guard(self):
        t = MockTransport()
        t.add(200, json.dumps(PAGE1),
              headers={"Link": '<https://x/next>; rel="next"'})
        client = GitHubClient(transport=t, sleep=lambda s: None, max_pages=2)
        with self.assertRaises(GitHubApiError) as ctx:
            client.fetch_issues("o", "r")
        self.assertIn("max_pages", str(ctx.exception))

    def test_exactly_max_pages_without_next_link_is_not_an_error(self):
        next_url = "https://api.github.com/repos/o/r/issues?state=open&per_page=100&page=2"
        t = MockTransport()
        t.add(200, json.dumps(PAGE1),
              headers={"Link": f'<{next_url}>; rel="next"'})
        t.add(200, json.dumps(PAGE2))  # last page: no Link header
        client = GitHubClient(transport=t, sleep=lambda s: None, max_pages=2)
        issues = client.fetch_issues("o", "r")
        self.assertEqual([i.number for i in issues], [1, 3])

    def test_rate_limit_retry_then_success(self):
        t = MockTransport()
        t.add(403, '{"message": "API rate limit exceeded"}',
              headers={"X-RateLimit-Remaining": "0", "Retry-After": "1"})
        t.add(200, json.dumps(PAGE2))
        sleeps = []
        client = GitHubClient(transport=t, sleep=sleeps.append)
        issues = client.fetch_issues("o", "r")
        self.assertEqual([i.number for i in issues], [3])
        self.assertEqual(sleeps, [1.0])

    def test_rate_limit_exhausted_raises(self):
        t = MockTransport()
        t.add(429, "", headers={"Retry-After": "0"})
        client = GitHubClient(transport=t, sleep=lambda s: None, max_retries=2)
        with self.assertRaises(RateLimitExceeded):
            client.fetch_issues("o", "r")
        self.assertEqual(len(t.calls), 3)  # initial + 2 retries

    def test_404_message_mentions_repo(self):
        t = MockTransport()
        t.add(404, '{"message": "Not Found"}')
        client = GitHubClient(transport=t, sleep=lambda s: None)
        with self.assertRaises(GitHubApiError) as ctx:
            client.fetch_issues("o", "missing")
        self.assertIn("not found", str(ctx.exception))

    def test_malformed_json_raises(self):
        t = MockTransport()
        t.add(200, "<html>not json</html>")
        client = GitHubClient(transport=t, sleep=lambda s: None)
        with self.assertRaises(GitHubApiError) as ctx:
            client.fetch_issues("o", "r")
        self.assertIn("malformed JSON", str(ctx.exception))

    def test_empty_repo_returns_no_issues(self):
        t = MockTransport()
        t.add(200, "[]")
        client = GitHubClient(transport=t, sleep=lambda s: None)
        self.assertEqual(client.fetch_issues("o", "r"), [])

    def test_empty_owner_repo_rejected(self):
        client = GitHubClient(transport=MockTransport())
        with self.assertRaises(ValueError):
            client.fetch_issues("", "r")


class TestCaching(unittest.TestCase):
    def test_etag_sent_and_304_uses_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = HttpCache(Path(tmp) / "cache.json")
            t = MockTransport()
            t.add(200, json.dumps(PAGE2), headers={"ETag": '"abc123"'})
            client = GitHubClient(transport=t, cache=cache, sleep=lambda s: None)
            first = client.fetch_issues("o", "r")
            self.assertEqual(len(first), 1)
            cache.save()

            # Second run: server says not-modified, body comes from cache.
            t2 = MockTransport()
            t2.add(304, "", headers={"ETag": '"abc123"'})
            cache2 = HttpCache(Path(tmp) / "cache.json")
            client2 = GitHubClient(transport=t2, cache=cache2,
                                   sleep=lambda s: None)
            second = client2.fetch_issues("o", "r")
            self.assertEqual([i.number for i in second], [3])
            sent = t2.calls[0][1]
            self.assertEqual(sent.get("If-None-Match"), '"abc123"')

    def test_corrupt_cache_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache.json"
            path.write_text("garbage{{{", encoding="utf-8")
            cache = HttpCache(path)  # must not raise
            self.assertEqual(cache.validators("https://x"), {})


if __name__ == "__main__":
    unittest.main()
