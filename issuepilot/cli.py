"""Command-line interface for IssuePilot.

Commands:
  fetch    Download issues from a public GitHub repo (read-only) to JSON.
  analyze  Run triage analysis over a saved issues JSON file.
  review   Print the pending review queue with evidence and action previews.
  approve  Mark a suggestion approved (prints manual steps; acts on nothing).
  ignore   Mark a suggestion ignored so it leaves the queue for good.
  demo     End-to-end demo over the bundled fixtures.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .cache import HttpCache
from .github_client import GitHubApiError, GitHubClient, RateLimitExceeded
from .models import Issue, load_issues
from .suggestions import (
    STATUS_APPROVED,
    STATUS_IGNORED,
    STATUS_PENDING,
    Suggestion,
    SuggestionStore,
    build_suggestions,
)

DEFAULT_STATE = "issuepilot_state.json"
DEFAULT_CACHE = "issuepilot_cache.json"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "issues.json"


class CliError(Exception):
    pass


def _read_issues_file(path: str | Path) -> list[Issue]:
    p = Path(path)
    if not p.exists():
        raise CliError(f"issues file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CliError(f"malformed JSON in {p}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise CliError(f"file {p} is not valid UTF-8 text: {exc}") from exc
    try:
        return load_issues(data)
    except ValueError as exc:
        raise CliError(f"invalid issues file {p}: {exc}") from exc


def _open_store(path: str | Path) -> SuggestionStore:
    try:
        return SuggestionStore(path)
    except ValueError as exc:
        raise CliError(str(exc)) from exc


def _format_suggestion(s: Suggestion, verbose: bool = True) -> str:
    lines = [
        f"[{s.id}] ({s.kind}, confidence: {s.confidence}) {s.summary}",
    ]
    if verbose:
        for ev in s.evidence:
            lines.append(f"    evidence: {ev}")
        lines.append(f"    proposed action: {s.proposed_action}")
        lines.append(
            f"    next: `python -m issuepilot approve {s.id}` or "
            f"`python -m issuepilot ignore {s.id}`"
        )
    return "\n".join(lines)


def cmd_fetch(args: argparse.Namespace) -> int:
    cache = HttpCache(args.cache)
    client = GitHubClient(
        token=args.token, cache=cache, max_pages=args.max_pages
    )
    try:
        issues = client.fetch_issues(args.owner, args.repo, state=args.state)
    except RateLimitExceeded as exc:
        raise CliError(str(exc)) from exc
    except GitHubApiError as exc:
        raise CliError(str(exc)) from exc
    finally:
        cache.save()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "number": i.number,
            "title": i.title,
            "body": i.body,
            "state": i.state,
            "labels": [{"name": n} for n in i.labels],
            "user": {"login": i.author},
            "created_at": i.created_at,
            "html_url": i.html_url,
        }
        for i in issues
    ]
    out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Fetched {len(issues)} issues from {args.owner}/{args.repo} -> {out}")
    return 0


def _run_analysis(issues_path: str | Path, state_path: str | Path) -> tuple[int, int, int]:
    issues = _read_issues_file(issues_path)
    if not issues:
        print(f"No issues found in {issues_path}; nothing to analyze.")
        return 0, 0, 0
    store = _open_store(state_path)
    added, revived = store.merge(build_suggestions(issues))
    store.save()
    return len(issues), added, len(store.pending())


def cmd_analyze(args: argparse.Namespace) -> int:
    n_issues, added, pending = _run_analysis(args.issues, args.state)
    if n_issues == 0:
        return 0
    print(
        f"Analyzed {n_issues} issues from {args.issues}: "
        f"{added} new suggestions, {pending} pending in queue "
        f"(state: {args.state})."
    )
    print("Run `python -m issuepilot review` to inspect the queue.")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    store = _open_store(args.state)
    pending = store.pending()
    if not pending:
        print("Review queue is empty. Run `analyze` first, or everything is handled.")
        return 0
    counts = {"high": 0, "medium": 0, "low": 0}
    for s in pending:
        counts[s.confidence] = counts.get(s.confidence, 0) + 1
    print(
        f"IssuePilot review queue: {len(pending)} pending suggestion(s) "
        f"(high: {counts.get('high', 0)}, medium: {counts.get('medium', 0)}, "
        f"low: {counts.get('low', 0)})"
    )
    print("=" * 72)
    for s in pending:
        print(_format_suggestion(s, verbose=not args.brief))
        print("-" * 72)
    print(
        "Nothing has been posted or changed on GitHub. Approving a suggestion "
        "only prints the manual steps."
    )
    return 0


def _cmd_set_status(args: argparse.Namespace, status: str) -> int:
    store = _open_store(args.state)
    try:
        if status == STATUS_APPROVED:
            s = store.approve(args.id)
        else:
            s = store.ignore(args.id)
    except KeyError as exc:
        raise CliError(str(exc)) from exc
    store.save()
    if status == STATUS_APPROVED:
        print(f"Approved {s.id}: {s.summary}")
        print(f"Manual steps: {s.proposed_action}")
        print("Reminder: IssuePilot v1 never posts, closes, or labels anything.")
    else:
        print(
            f"Ignored {s.id}: {s.summary}\n"
            "It will stay out of the review queue on future runs."
        )
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    return _cmd_set_status(args, STATUS_APPROVED)


def cmd_ignore(args: argparse.Namespace) -> int:
    return _cmd_set_status(args, STATUS_IGNORED)


def cmd_demo(args: argparse.Namespace) -> int:
    fixtures = FIXTURES
    if not fixtures.exists():
        raise CliError(f"demo fixtures missing: {fixtures}")
    state = args.state
    print(f"Demo: analyzing bundled fixtures ({fixtures})")
    n_issues, added, pending = _run_analysis(fixtures, state)
    if n_issues == 0:
        return 0
    print(
        f"Analyzed {n_issues} issues: {added} new suggestion(s), "
        f"{pending} pending (state: {state}).\n"
    )
    store = _open_store(state)
    for s in store.pending():
        print(_format_suggestion(s, verbose=True))
        print("-" * 72)
    print(
        "End of demo. Every item above is a preview with evidence and "
        "confidence; nothing was posted to GitHub. Use `approve`/`ignore` with "
        f"--state {state} to triage the queue."
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m issuepilot",
        description=(
            "Maintainer-assist triage for small public GitHub repos. "
            "Read-only: suggests, never posts."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="download issues from a public repo")
    p_fetch.add_argument("owner")
    p_fetch.add_argument("repo")
    p_fetch.add_argument("--state", default="open",
                         choices=["open", "closed", "all"],
                         help="issue state filter (default: open)")
    p_fetch.add_argument("--out", default="issues.json",
                         help="output JSON file (default: issues.json)")
    p_fetch.add_argument("--token", default=None,
                         help="optional read-only token; never stored")
    p_fetch.add_argument("--cache", default=DEFAULT_CACHE,
                         help="HTTP cache file (default: issuepilot_cache.json)")
    p_fetch.add_argument("--max-pages", type=int, default=50)
    p_fetch.set_defaults(func=cmd_fetch)

    p_analyze = sub.add_parser("analyze", help="analyze a saved issues JSON file")
    p_analyze.add_argument("--issues", required=True, help="issues JSON file")
    p_analyze.add_argument("--state", default=DEFAULT_STATE,
                           help="review queue state file")
    p_analyze.set_defaults(func=cmd_analyze)

    p_review = sub.add_parser("review", help="show the pending review queue")
    p_review.add_argument("--state", default=DEFAULT_STATE)
    p_review.add_argument("--brief", action="store_true",
                          help="summaries only, no evidence")
    p_review.set_defaults(func=cmd_review)

    for name, func, helptext in (
        ("approve", cmd_approve, "approve a suggestion (prints manual steps)"),
        ("ignore", cmd_ignore, "ignore a suggestion permanently"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("id", help="suggestion id, e.g. label-1a2b3c4d")
        p.add_argument("--state", default=DEFAULT_STATE)
        p.set_defaults(func=func)

    p_demo = sub.add_parser("demo", help="run the end-to-end demo on fixtures")
    p_demo.add_argument("--state", default="demo_state.json",
                        help="state file for the demo queue")
    p_demo.set_defaults(func=cmd_demo)

    return parser


def _enable_utf8_stdio() -> None:
    # Windows consoles often default to a legacy code page (cp1252, GBK),
    # which crashes or mojibakes non-ASCII paths/titles. Emit UTF-8 instead.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = (getattr(stream, "encoding", None) or "").lower()
        if reconfigure is not None and encoding.replace("-", "") != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _enable_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
