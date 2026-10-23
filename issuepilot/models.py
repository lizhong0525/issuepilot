"""Data model for GitHub issues."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Issue:
    """A GitHub issue (read-only view)."""

    number: int
    title: str
    body: str
    state: str = "open"
    labels: list[str] = field(default_factory=list)
    author: str = ""
    created_at: str = ""
    html_url: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Issue":
        """Build an Issue from a GitHub API issue object.

        Raises ValueError on malformed input.
        """
        if not isinstance(data, dict):
            raise ValueError("issue entry is not an object")
        if "number" not in data or "title" not in data:
            raise ValueError("issue entry missing required 'number' or 'title' field")
        labels = data.get("labels") or []
        label_names = []
        for lab in labels:
            if isinstance(lab, dict):
                label_names.append(str(lab.get("name", "")))
            else:
                label_names.append(str(lab))
        user = data.get("user") or {}
        return cls(
            number=int(data["number"]),
            title=str(data["title"]),
            body=str(data.get("body") or ""),
            state=str(data.get("state", "open")),
            labels=[n for n in label_names if n],
            author=str(user.get("login", "")) if isinstance(user, dict) else "",
            created_at=str(data.get("created_at", "")),
            html_url=str(data.get("html_url", "")),
        )

    def display(self) -> str:
        return f"#{self.number} {self.title}"


def load_issues(data: Any) -> list[Issue]:
    """Parse a JSON-decoded list of API issue objects into Issues.

    Pull requests (which the issues endpoint also returns) are skipped.
    Raises ValueError on malformed input.
    """
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of issues")
    issues: list[Issue] = []
    for entry in data:
        if isinstance(entry, dict) and "pull_request" in entry:
            continue
        issues.append(Issue.from_api(entry))
    return issues
