"""Triage analysis: duplicate candidates, reproduction-detail checks,
label suggestions.

Every finding carries evidence and a confidence level. Nothing here is
treated as proof -- duplicates are always "possible duplicates" for a
human to confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .models import Issue
from . import textsim

CONFIDENCE_LEVELS = ("low", "medium", "high")

# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

DUPLICATE_MIN_SCORE = 0.55
DUPLICATE_MIN_SHARED_DISTINCTIVE = 3
# A pair sharing this many error/identifier tokens is surfaced even with a
# lower overall score: that overlap is very unlikely to happen by chance.
DUPLICATE_STRONG_ERROR_OVERLAP = 4


def _corpus_common_tokens(issues: list[Issue], max_df_ratio: float = 0.6) -> set[str]:
    """Tokens appearing in more than ``max_df_ratio`` of the issues.

    These carry no discriminating signal (project name, boilerplate) and
    are excluded from distinctive/error overlap.
    """
    df: dict[str, int] = {}
    for issue in issues:
        for tok in set(textsim.tokenize(f"{issue.title}\n{issue.body}")):
            df[tok] = df.get(tok, 0) + 1
    if not issues:
        return set()
    # Small corpora must not filter everything out; require both a high
    # document-frequency ratio and an absolute minimum count.
    threshold = max(3, max_df_ratio * len(issues))
    return {tok for tok, n in df.items() if n > threshold}


@dataclass
class DuplicateCandidate:
    issue_a: int
    issue_b: int
    score: float
    confidence: str
    shared_distinctive: list[str] = field(default_factory=list)
    shared_errors: list[str] = field(default_factory=list)
    title_sim: float = 0.0
    body_sim: float = 0.0

    def evidence(self) -> list[str]:
        ev = [
            f"combined similarity score {self.score:.2f} "
            f"(title {self.title_sim:.2f}, body {self.body_sim:.2f})",
            f"shared distinctive terms: {', '.join(self.shared_distinctive)}",
        ]
        if self.shared_errors:
            ev.append(
                f"shared error/identifier tokens: {', '.join(self.shared_errors)}"
            )
        else:
            ev.append(
                "no shared error/identifier tokens -- wording similarity only, "
                "treat with extra skepticism"
            )
        return ev


def _duplicate_confidence(
    score: float, shared_errors: int, title_sim: float, strong_error_rule: bool
) -> str:
    # Wording alone can never produce high confidence.
    if (score >= 0.80 and shared_errors >= 2) or (
        shared_errors >= DUPLICATE_STRONG_ERROR_OVERLAP + 1 and score >= 0.55
    ):
        return "high"
    if strong_error_rule or (score >= 0.70 and (shared_errors >= 1 or title_sim >= 0.85)):
        return "medium"
    return "low"


def find_duplicate_candidates(issues: list[Issue]) -> list[DuplicateCandidate]:
    """Pairwise scan for possible duplicates among open issues.

    A pair is only surfaced when the combined score clears the threshold
    AND the pair shares enough distinctive terms, or when it shares a
    strong overlap of error/identifier tokens. Similar phrasing of
    unrelated problems is not reported.
    """
    open_issues = [i for i in issues if i.state == "open"]
    common = _corpus_common_tokens(open_issues)
    candidates: list[DuplicateCandidate] = []
    for idx in range(len(open_issues)):
        for j in range(idx + 1, len(open_issues)):
            a, b = open_issues[idx], open_issues[j]
            text_a = f"{a.title}\n{a.body}"
            text_b = f"{b.title}\n{b.body}"
            t_sim = textsim.title_similarity(a.title, b.title)
            b_sim = textsim.body_similarity(text_a, text_b)
            shared_dist = sorted(
                (textsim.distinctive_tokens(text_a) & textsim.distinctive_tokens(text_b))
                - common
            )
            shared_err = sorted(
                (textsim.error_tokens(text_a) & textsim.error_tokens(text_b)) - common
            )
            score = (
                0.35 * t_sim
                + 0.45 * b_sim
                + 0.20 * min(1.0, len(shared_err) / 3)
            )
            strong_error_rule = (
                len(shared_err) >= DUPLICATE_STRONG_ERROR_OVERLAP and b_sim >= 0.30
            )
            if strong_error_rule or (
                score >= DUPLICATE_MIN_SCORE
                and len(shared_dist) >= DUPLICATE_MIN_SHARED_DISTINCTIVE
            ):
                candidates.append(
                    DuplicateCandidate(
                        issue_a=a.number,
                        issue_b=b.number,
                        score=round(score, 3),
                        confidence=_duplicate_confidence(
                            score, len(shared_err), t_sim, strong_error_rule
                        ),
                        shared_distinctive=shared_dist[:10],
                        shared_errors=shared_err[:10],
                        title_sim=round(t_sim, 3),
                        body_sim=round(b_sim, 3),
                    )
                )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


# ---------------------------------------------------------------------------
# Missing reproduction details
# ---------------------------------------------------------------------------

_REPRO_ASPECTS: list[tuple[str, list[str]]] = [
    (
        "steps to reproduce",
        [
            r"steps?\s+to\s+reproduce",
            r"repro(duce|duction)?\s*steps?",
            r"^\s*1[.)]\s",  # numbered list, a common steps format
            r"how\s+to\s+reproduce",
        ],
    ),
    (
        "expected behavior",
        [r"expect(ed|ation)?\b", r"should\s+(have\s+)?(be|show|return|work)"],
    ),
    (
        "actual behavior",
        [r"actual(ly)?\b", r"instead\b", r"what\s+happened", r"but\s+(it|I)\s+got"],
    ),
    (
        "environment / version info",
        [
            r"\bv?\d+\.\d+(\.\d+)?\b",  # version number
            r"\b(windows|macos|mac\s*os|linux|ubuntu|debian|fedora|android|ios)\b",
            r"\b(python|node|chrome|firefox|safari)\s*\d",
            r"environment\s*:",
        ],
    ),
]


@dataclass
class ReproCheck:
    issue: int
    missing: list[str]
    present: list[str]
    confidence: str

    def evidence(self) -> list[str]:
        ev = [f"missing: {', '.join(self.missing)}"]
        if self.present:
            ev.append(f"present: {', '.join(self.present)}")
        return ev


def check_repro_details(issue: Issue) -> ReproCheck | None:
    """Check whether a bug-looking issue lacks reproduction details.

    Returns None when nothing is missing or the issue does not look like
    a bug report at all.
    """
    if issue.state != "open":
        return None
    text = f"{issue.title}\n{issue.body}".lower()
    looks_like_bug = bool(
        re.search(r"bug|crash|error|fail|broken|exception|traceback|wrong", text)
    ) or "bug" in issue.labels
    if not looks_like_bug:
        return None
    missing: list[str] = []
    present: list[str] = []
    for aspect, patterns in _REPRO_ASPECTS:
        if any(re.search(p, text, re.MULTILINE) for p in patterns):
            present.append(aspect)
        else:
            missing.append(aspect)
    if not missing:
        return None
    confidence = "high" if len(missing) >= 3 else "medium" if len(missing) == 2 else "low"
    return ReproCheck(issue=issue.number, missing=missing, present=present,
                      confidence=confidence)


# ---------------------------------------------------------------------------
# Label suggestions
# ---------------------------------------------------------------------------

_LABEL_RULES: list[tuple[str, list[str]]] = [
    ("bug", [r"\bbug\b", r"\bcrash", r"\w*error\w*", r"\bfail",
             r"\bbroken\b", r"\btraceback\b", r"\w*exception\w*", r"\bregression\b",
             r"\bwrong\b"]),
    ("enhancement", [r"\bfeature\s+request\b", r"\bwould\s+be\s+nice\b",
                     r"\bplease\s+(add|support)\b", r"\bsupport\s+for\b",
                     r"\benhancement\b", r"\bcould\s+you\s+add\b"]),
    ("documentation", [r"\bdocs?\b", r"\bdocumentation\b", r"\breadme\b",
                       r"\btypo\b", r"\btutorial\b", r"\bexample\s+is\s+wrong\b"]),
    ("performance", [r"\bslow\b", r"\bperformance\b", r"\bmemory\s+(usage|leak)\b",
                     r"\bcpu\b", r"\blatency\b", r"\boptimi[sz]e\b"]),
    ("question", [r"\bhow\s+(do|can|should)\s+i\b", r"\bquestion\b",
                  r"\bis\s+it\s+possible\b", r"\bhelp\s+(me\s+)?understand\b"]),
]


@dataclass
class LabelSuggestion:
    issue: int
    label: str
    confidence: str
    matched: list[str]  # regex patterns that matched
    already_present: bool = False

    def evidence(self) -> list[str]:
        return [f"matched keywords/patterns: {', '.join(self.matched)}"]


def suggest_labels(issue: Issue) -> list[LabelSuggestion]:
    """Suggest labels from keyword rules. Issues that already carry the
    label are skipped."""
    if issue.state != "open":
        return []
    text = f"{issue.title}\n{issue.body}".lower()
    out: list[LabelSuggestion] = []
    for label, patterns in _LABEL_RULES:
        if label in issue.labels:
            continue
        matched = [p for p in patterns if re.search(p, text)]
        if not matched:
            continue
        if len(matched) >= 3:
            confidence = "high"
        elif len(matched) == 2:
            confidence = "medium"
        else:
            confidence = "low"
        out.append(
            LabelSuggestion(issue=issue.number, label=label,
                            confidence=confidence, matched=matched[:5])
        )
    return out
