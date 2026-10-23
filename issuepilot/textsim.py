"""Text similarity helpers for duplicate detection.

Pure stdlib. Similarity here is a *hint* for a human reviewer, never proof:
the scoring deliberately requires agreement between several independent
signals before a pair is even surfaced, and shared error/identifier tokens
are what push confidence up -- not merely similar phrasing.
"""

from __future__ import annotations

import difflib
import math
import re

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.\-/]*")

STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of at by for with about into
    through during to from in out on off over under again further once here
    there all any both each few more most other some such no nor not only own
    same so than too very can will just should now is are was were be been
    being have has had having do does did doing would could ought i you he she
    it we they them his her its our their this that these those am get got
    getting my me what which who whom as us
    """.split()
)


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, including identifiers like ``config.yaml``."""
    out = []
    for tok in _WORD_RE.findall(text.lower()):
        tok = tok.strip(".-/")
        if tok:
            out.append(tok)
    return out


def content_tokens(text: str) -> list[str]:
    """Tokens with stopwords removed."""
    return [t for t in tokenize(text) if t not in STOPWORDS]


def distinctive_tokens(text: str) -> set[str]:
    """Tokens likely to carry signal: long words, or words containing
    digits, dots, slashes or dashes (versions, paths, identifiers)."""
    out = set()
    for tok in content_tokens(text):
        if len(tok) >= 5 or re.search(r"[0-9._/\-]", tok):
            out.add(tok)
    return out


_ERROR_WORD_RE = re.compile(r"\w*(error|exception|traceback)\w*")


def error_tokens(text: str) -> set[str]:
    """Error-like tokens: identifiers with digits/dots/underscores/slashes
    (versions, paths, ``api_key``), and error/exception/traceback words --
    the strongest shared signal between duplicates."""
    out = set()
    for tok in tokenize(text):
        if len(tok) < 3:
            continue
        if re.search(r"[0-9_./]", tok) or _ERROR_WORD_RE.match(tok):
            out.add(tok)
    return out - STOPWORDS


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(a: list[str], b: list[str]) -> float:
    """Cosine similarity over term-frequency vectors."""
    if not a or not b:
        return 0.0
    fa: dict[str, int] = {}
    fb: dict[str, int] = {}
    for t in a:
        fa[t] = fa.get(t, 0) + 1
    for t in b:
        fb[t] = fb.get(t, 0) + 1
    dot = sum(fa.get(t, 0) * fb.get(t, 0) for t in fa.keys() & fb.keys())
    na = math.sqrt(sum(v * v for v in fa.values()))
    nb = math.sqrt(sum(v * v for v in fb.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def title_similarity(a: str, b: str) -> float:
    """Blend of token overlap and sequence ratio for short titles."""
    jac = jaccard(set(content_tokens(a)), set(content_tokens(b)))
    seq = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return 0.5 * jac + 0.5 * seq


def body_similarity(a: str, b: str, limit: int = 4000) -> float:
    return cosine(content_tokens(a[:limit]), content_tokens(b[:limit]))
