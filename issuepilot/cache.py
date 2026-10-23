"""Simple file-based HTTP cache keyed by URL, honoring ETag/Last-Modified."""

from __future__ import annotations

import json
from pathlib import Path


class HttpCache:
    """Stores response bodies with their validators in one JSON file.

    Everything stays on the local disk; nothing is sent anywhere except
    the conditional request headers to the API itself.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._entries = data
            except (json.JSONDecodeError, OSError):
                # A corrupt cache must never break the tool; start fresh.
                self._entries = {}

    def validators(self, url: str) -> dict[str, str]:
        """Conditional headers to send for a cached URL."""
        entry = self._entries.get(url)
        if not entry:
            return {}
        headers: dict[str, str] = {}
        if entry.get("etag"):
            headers["If-None-Match"] = entry["etag"]
        if entry.get("last_modified"):
            headers["If-Modified-Since"] = entry["last_modified"]
        return headers

    def get_body(self, url: str) -> str | None:
        entry = self._entries.get(url)
        return entry.get("body") if entry else None

    def store(
        self,
        url: str,
        body: str,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        self._entries[url] = {
            "body": body,
            "etag": etag,
            "last_modified": last_modified,
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._entries, ensure_ascii=False), encoding="utf-8"
        )
