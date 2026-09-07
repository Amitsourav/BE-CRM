"""Normalising repeatable query filters.

Every multi-select filter in the API is a repeatable query param —
`?bank_name=PNB&bank_name=Axis` — following the convention `tags` on
/leads established. This module is the one place that cleans them, so a
cleared dropdown behaves the same way on every endpoint.
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence, TypeVar

T = TypeVar("T")


def clean_multi(values: Iterable[T] | None) -> list[T] | None:
    """A repeatable filter's values, or None when it isn't filtering.

    A multi-select that has just been cleared commonly still sends the
    param with an empty value (`?bank_name=`), which arrives as `[""]`.
    Left alone that filters on a bank literally named "" and returns an
    empty page, which reads as a broken screen rather than as no filter.

    Blanks are dropped, strings are stripped, duplicates removed with
    order preserved, and an empty result becomes None so callers can keep
    using a plain `if value:` test.
    """
    if not values:
        return None
    out: list[Any] = []
    seen: set[Any] = set()
    for v in values:
        if v is None:
            continue
        if isinstance(v, str):
            v = v.strip()
            if not v:
                continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out or None


def as_key(values: Sequence[Any] | None) -> tuple | None:
    """A hashable form of a cleaned filter, for cache keys.

    The Kanban caches its payload against the filter set it was built
    from. A list is unhashable, so every repeatable filter has to be
    frozen before it can go in that key — and it must be frozen in a
    STABLE order, or the same selection ticked in a different sequence
    would miss the cache and, worse, two different selections could
    collide if order were ignored entirely.
    """
    if not values:
        return None
    try:
        return tuple(sorted(values))
    except TypeError:
        # Mixed types don't sort; fall back to insertion order, which is
        # still stable for a given caller.
        return tuple(values)
