"""Shared naming helpers for the Recotem serving layer."""

from __future__ import annotations

from collections.abc import Callable


def dedup_stub_name(base: str, taken: Callable[[str], bool]) -> str:
    """Return a name derived from *base* that is not already taken.

    Appends ``_1``, ``_2``, … until ``taken(candidate)`` returns ``False``.
    If ``taken(base)`` is already ``False`` the base name is returned as-is.

    Parameters
    ----------
    base:
        The preferred name (typically a YAML file stem).
    taken:
        A callable that returns ``True`` when *candidate* is already in use.
    """
    candidate = base
    suffix = 0
    while taken(candidate):
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate
