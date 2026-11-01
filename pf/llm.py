"""Reserved hook for an optional LLM pass.

Nothing in the pipeline calls a model today. The interface exists so a
relevance score or a summary can be bolted on later without touching the
fetch/enrich/export code paths.

Wire a provider up like::

    from pf.llm import get_provider

    provider = get_provider("noop")
    for paper in papers:
        paper.pv_related = bool(provider.score(paper, topic="photovoltaics"))

The default provider is a no-op that returns ``None``, meaning "no opinion",
so existing behaviour is unchanged.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def score(self, text: str, topic: str) -> Optional[float]:
        """Return a 0-1 relevance score, or None when no judgement is made."""
        ...

    def summarize(self, text: str, max_words: int = 80) -> Optional[str]:
        """Return a short summary, or None when unsupported."""
        ...


class NoopProvider:
    """Default provider: never calls a network service."""

    name = "noop"

    def available(self) -> bool:
        return False

    def score(self, text: str, topic: str) -> Optional[float]:
        return None

    def summarize(self, text: str, max_words: int = 80) -> Optional[str]:
        return None


_PROVIDERS = {"noop": NoopProvider}


def register_provider(name: str, factory) -> None:
    _PROVIDERS[name] = factory


def get_provider(name: str = "noop") -> LLMProvider:
    try:
        factory = _PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown LLM provider {name!r}; known: {sorted(_PROVIDERS)}"
        ) from exc
    return factory()