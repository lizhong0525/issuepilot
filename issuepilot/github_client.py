"""Read-only GitHub REST client with pagination, rate-limit handling and
ETag caching. Standard library only.

The transport is injectable so tests run fully offline with a mocked
transport -- no network, no tokens.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .cache import HttpCache
from .models import Issue, load_issues

API_BASE = "https://api.github.com"
USER_AGENT = "issuepilot/0.1 (+https://github.com/local/issuepilot)"


class GitHubApiError(RuntimeError):
    """Raised for non-retryable API failures."""


class RateLimitExceeded(GitHubApiError):
    """Raised when retries against the rate limit are exhausted."""


@dataclass
class Response:
    status: int
    headers: dict[str, str]
    body: str

    def header(self, name: str) -> str | None:
        lname = name.lower()
        for k, v in self.headers.items():
            if k.lower() == lname:
                return v
        return None


# Transport: (url, headers) -> Response
Transport = Callable[[str, dict[str, str]], Response]


def urllib_transport(url: str, headers: dict[str, str]) -> Response:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return Response(
                status=resp.status,
                headers=dict(resp.headers.items()),
                body=resp.read().decode("utf-8", errors="replace"),
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return Response(
            status=exc.code, headers=dict(exc.headers.items()) if exc.headers else {},
            body=body,
        )
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"network error contacting GitHub: {exc.reason}") from exc


_LINK_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="next"')


class GitHubClient:
    """Read-only client for the issues endpoint."""

    def __init__(
        self,
        token: str | None = None,
        transport: Transport | None = None,
        cache: HttpCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_pages: int = 50,
        max_retries: int = 3,
        max_sleep: float = 60.0,
        base_url: str = API_BASE,
    ):
        self.token = token
        self.transport = transport or urllib_transport
        self.cache = cache
        self.sleep = sleep
        self.max_pages = max_pages
        self.max_retries = max_retries
        self.max_sleep = max_sleep
        self.base_url = base_url.rstrip("/")
        self._last_next_link: str | None = None

    # -- low level ---------------------------------------------------------

    def _headers(self, url: str) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.cache:
            headers.update(self.cache.validators(url))
        return headers

    def _is_rate_limited(self, resp: Response) -> bool:
        if resp.status == 429:
            return True
        if resp.status == 403:
            remaining = resp.header("X-RateLimit-Remaining")
            if remaining == "0":
                return True
            if "rate limit" in resp.body.lower():
                return True
        return False

    def _retry_delay(self, resp: Response, attempt: int) -> float:
        retry_after = resp.header("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), self.max_sleep)
        reset = resp.header("X-RateLimit-Reset")
        if reset and reset.isdigit():
            delay = int(reset) - int(time.time())
            if 0 < delay <= self.max_sleep:
                return float(delay)
        return min(2.0 ** attempt, self.max_sleep)

    def get(self, url: str) -> tuple[int, str]:
        """GET a single URL with rate-limit retries and ETag caching.

        Returns (status, body). A 304 returns the cached body with status 200.
        """
        last_error: str = "unknown error"
        for attempt in range(self.max_retries + 1):
            resp = self.transport(url, self._headers(url))
            if self._is_rate_limited(resp):
                if attempt >= self.max_retries:
                    raise RateLimitExceeded(
                        f"GitHub rate limit exceeded after {self.max_retries} "
                        f"retries for {url}. Wait for the reset window or pass a "
                        "token with --token."
                    )
                self.sleep(self._retry_delay(resp, attempt))
                continue
            if resp.status == 304 and self.cache is not None:
                cached = self.cache.get_body(url)
                if cached is not None:
                    return 200, cached
                # No cached body despite 304: fall through and refetch without
                # conditional headers.
                resp = self.transport(
                    url, {k: v for k, v in self._headers(url).items()
                          if not k.lower().startswith("if-")}
                )
            if resp.status == 404:
                raise GitHubApiError(
                    f"not found: {url} (check owner/repo spelling and that the "
                    "repository is public)"
                )
            if resp.status >= 400:
                last_error = f"HTTP {resp.status}: {resp.body[:200]}"
                if resp.status in (500, 502, 503) and attempt < self.max_retries:
                    self.sleep(min(2.0 ** attempt, self.max_sleep))
                    continue
                raise GitHubApiError(f"GitHub API error for {url}: {last_error}")
            self._capture_link(resp.headers)
            if self.cache is not None:
                self.cache.store(
                    url,
                    resp.body,
                    etag=resp.header("ETag"),
                    last_modified=resp.header("Last-Modified"),
                )
            return resp.status, resp.body
        raise GitHubApiError(f"request failed for {url}: {last_error}")

    # -- high level --------------------------------------------------------

    def fetch_issues(self, owner: str, repo: str, state: str = "open") -> list[Issue]:
        """Fetch all issues for a public repo, following pagination.

        Pull requests returned by the issues endpoint are filtered out.
        """
        if not owner or not repo:
            raise ValueError("owner and repo must be non-empty")
        url = (
            f"{self.base_url}/repos/{owner}/{repo}/issues"
            f"?state={state}&per_page=100"
        )
        raw: list[dict] = []
        pages = 0
        while url and pages < self.max_pages:
            self._last_next_link = None
            _, body = self.get(url)
            try:
                page = json.loads(body)
            except json.JSONDecodeError as exc:
                raise GitHubApiError(
                    f"malformed JSON from GitHub for {url}: {exc}"
                ) from exc
            if not isinstance(page, list):
                raise GitHubApiError(f"unexpected response shape for {url}")
            raw.extend(page)
            pages += 1
            url = self._last_next_link or ""
        if url:
            raise GitHubApiError(
                f"hit max_pages={self.max_pages} while paginating issues for "
                f"{owner}/{repo}; raise --max-pages if the repo is larger"
            )
        try:
            return load_issues(raw)
        except ValueError as exc:
            raise GitHubApiError(f"unexpected issue payload from GitHub: {exc}") from exc

    # The Link header needs to survive get(); kept on the instance.
    def _capture_link(self, headers: dict[str, str]) -> None:
        self._last_next_link = None
        link = None
        for k, v in headers.items():
            if k.lower() == "link":
                link = v
                break
        if link:
            m = _LINK_NEXT_RE.search(link)
            if m:
                self._last_next_link = m.group(1)
