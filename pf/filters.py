"""Keyword filtering.

Terms are matched against *whole tokens* of normalised text, so short
acronyms such as ``ito`` or ``azo`` never match inside longer words like
``composition``. A trailing ``*`` turns a term into a prefix match.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Groups: a sequence of groups, each group a sequence of terms. Groups are
# ANDed, terms inside a group are ORed.
Groups = Sequence[Sequence[str]]


def normalize(text: str) -> str:
    """Lowercase and fold separators so ``SnO2:F`` becomes ``sno2 f``."""
    if not text:
        return ""
    lowered = text.lower()
    folded = _NON_ALNUM.sub(" ", lowered)
    return " ".join(folded.split())


def _prepare(text: str) -> tuple[str, frozenset[str]]:
    normalized = normalize(text)
    padded = f" {normalized} "
    return padded, frozenset(normalized.split())


def term_matches(term: str, padded: str, tokens: frozenset[str]) -> bool:
    term = term.strip().lower()
    if not term:
        return False
    if term.endswith("*"):
        prefix = normalize(term[:-1])
        if not prefix:
            return False
        if " " in prefix:
            return f" {prefix}" in padded
        return any(token.startswith(prefix) for token in tokens)
    normalized = normalize(term)
    if not normalized:
        return False
    if " " in normalized:
        return f" {normalized} " in padded
    return normalized in tokens


def group_matches(group: Iterable[str], padded: str, tokens: frozenset[str]) -> bool:
    return any(term_matches(term, padded, tokens) for term in group)


def matches_groups(text: str, groups: Groups) -> bool:
    """True when every group is satisfied by at least one of its terms."""
    if not groups:
        return True
    padded, tokens = _prepare(text)
    return all(group_matches(group, padded, tokens) for group in groups)


def matches_any(text: str, terms: Iterable[str]) -> bool:
    padded, tokens = _prepare(text)
    return any(term_matches(term, padded, tokens) for term in terms)


def is_pv_related(title: str, abstract: str, pv_keywords: Iterable[str]) -> bool:
    """Flag papers that mention photovoltaics; never used to drop rows."""
    return matches_any(f"{title} {abstract}", list(pv_keywords))


def auto_groups(query: str, stopwords: Iterable[str] | None = None) -> List[List[str]]:
    """Split a free-text query into single-word AND groups.

    Used when the caller supplies ``--must`` implicitly: every word of the
    query must appear in the matched field.
    """
    if stopwords is None:
        stopwords = {
            "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
            "of", "on", "or", "the", "to", "vs", "via", "with",
        }
    drop = {w.lower() for w in stopwords}
    words = [w for w in normalize(query).split() if w not in drop]
    return [[word] for word in words]