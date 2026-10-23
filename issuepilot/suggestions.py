"""Suggestion objects and the persistent review queue.

Every suggestion carries evidence and a confidence level, plus a preview
of the action it proposes. v1 never acts on the repository: approving a
suggestion only marks it and prints the exact manual steps.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .analysis import (
    DuplicateCandidate,
    LabelSuggestion,
    ReproCheck,
    check_repro_details,
    find_duplicate_candidates,
    suggest_labels,
)
from .models import Issue

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_IGNORED = "ignored"


@dataclass
class Suggestion:
    id: str
    kind: str  # "duplicate" | "missing-repro" | "label"
    issues: list[int]
    summary: str
    evidence: list[str]
    confidence: str
    proposed_action: str
    status: str = STATUS_PENDING
    score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Suggestion":
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            issues=[int(n) for n in data.get("issues", [])],
            summary=str(data.get("summary", "")),
            evidence=[str(e) for e in data.get("evidence", [])],
            confidence=str(data.get("confidence", "low")),
            proposed_action=str(data.get("proposed_action", "")),
            status=str(data.get("status", STATUS_PENDING)),
            score=data.get("score"),
        )


def _suggestion_id(kind: str, *parts: object) -> str:
    raw = "|".join([kind] + [str(p) for p in parts])
    return f"{kind}-{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def build_suggestions(issues: list[Issue]) -> list[Suggestion]:
    """Run all analyzers over the issues and build suggestion objects."""
    suggestions: list[Suggestion] = []

    for cand in find_duplicate_candidates(issues):
        suggestions.append(
            Suggestion(
                id=_suggestion_id("duplicate", cand.issue_a, cand.issue_b),
                kind="duplicate",
                issues=[cand.issue_a, cand.issue_b],
                summary=(
                    f"#{cand.issue_a} and #{cand.issue_b} look like possible "
                    f"duplicates (score {cand.score:.2f})"
                ),
                evidence=cand.evidence(),
                confidence=cand.confidence,
                proposed_action=(
                    "PREVIEW ONLY -- nothing will be posted. If you confirm the "
                    f"duplicate, manually comment on #{cand.issue_b} linking to "
                    f"#{cand.issue_a} and consider closing #{cand.issue_b} as a "
                    "duplicate. Similar wording is not proof; read both issues "
                    "first."
                ),
                score=cand.score,
            )
        )

    by_number = {i.number: i for i in issues}
    for issue in sorted(issues, key=lambda i: i.number):
        check = check_repro_details(issue)
        if check is not None:
            suggestions.append(
                Suggestion(
                    id=_suggestion_id("missing-repro", check.issue),
                    kind="missing-repro",
                    issues=[check.issue],
                    summary=(
                        f"#{check.issue} looks like a bug report missing: "
                        f"{', '.join(check.missing)}"
                    ),
                    evidence=check.evidence(),
                    confidence=check.confidence,
                    proposed_action=(
                        "PREVIEW ONLY -- nothing will be posted. If you agree, "
                        f"manually ask the reporter of #{check.issue} to add: "
                        f"{', '.join(check.missing)}."
                    ),
                )
            )
        for ls in suggest_labels(issue):
            suggestions.append(
                Suggestion(
                    id=_suggestion_id("label", ls.issue, ls.label),
                    kind="label",
                    issues=[ls.issue],
                    summary=f"#{ls.issue}: suggest label '{ls.label}'",
                    evidence=ls.evidence(),
                    confidence=ls.confidence,
                    proposed_action=(
                        "PREVIEW ONLY -- labels are never applied automatically. "
                        f"If you agree, manually add the '{ls.label}' label to "
                        f"#{ls.issue}."
                    ),
                )
            )

    order = {"high": 0, "medium": 1, "low": 2}
    suggestions.sort(key=lambda s: (order.get(s.confidence, 3), s.id))
    return suggestions


class SuggestionStore:
    """JSON-backed review queue. Statuses survive re-analysis."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._suggestions: dict[str, Suggestion] = {}
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(
                f"cannot read state file {self.path}: {exc}"
            ) from exc
        if not isinstance(data, list):
            raise ValueError(f"state file {self.path} is not a JSON array")
        try:
            self._suggestions = {
                s.id: s for s in (Suggestion.from_dict(d) for d in data)
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"state file {self.path} contains malformed entries: {exc}"
            ) from exc

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = [s.to_dict() for s in self.all()]
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def merge(self, new_suggestions: list[Suggestion]) -> tuple[int, int]:
        """Merge freshly built suggestions, preserving existing statuses.

        Returns (added, revived) counts. Suggestions previously ignored or
        approved keep their status and are not re-surfaced as pending.
        """
        added = revived = 0
        fresh_ids = {s.id for s in new_suggestions}
        for s in new_suggestions:
            existing = self._suggestions.get(s.id)
            if existing is None:
                self._suggestions[s.id] = s
                added += 1
            else:
                # Refresh content (evidence/summary) but keep the status.
                status = existing.status
                self._suggestions[s.id] = s
                self._suggestions[s.id].status = status
                if status == STATUS_PENDING:
                    revived += 1
        # Suggestions whose issues vanished are left untouched; the queue is
        # append-only so maintainers keep an audit trail.
        _ = fresh_ids
        return added, revived

    def all(self) -> list[Suggestion]:
        order = {"high": 0, "medium": 1, "low": 2}
        return sorted(
            self._suggestions.values(),
            key=lambda s: (s.status != STATUS_PENDING, order.get(s.confidence, 3), s.id),
        )

    def pending(self) -> list[Suggestion]:
        return [s for s in self.all() if s.status == STATUS_PENDING]

    def get(self, suggestion_id: str) -> Suggestion:
        try:
            return self._suggestions[suggestion_id]
        except KeyError:
            raise KeyError(
                f"no suggestion with id '{suggestion_id}' "
                f"(known ids: {', '.join(sorted(self._suggestions)) or 'none'})"
            ) from None

    def approve(self, suggestion_id: str) -> Suggestion:
        s = self.get(suggestion_id)
        s.status = STATUS_APPROVED
        return s

    def ignore(self, suggestion_id: str) -> Suggestion:
        s = self.get(suggestion_id)
        s.status = STATUS_IGNORED
        return s
