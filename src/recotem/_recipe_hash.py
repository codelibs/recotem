"""Canonical recipe hashing, in a neutral home both sub-packages can import.

``training/`` writes ``recipe_hash`` into every artifact header and
``serving/`` compares it against the recipe it is about to serve under, so the
function has to be reachable from both. ``training/`` and ``serving/`` never
import each other (see CLAUDE.md), which is why this lives at the top level
alongside ``_idmap``, ``_features``, ``_irspack_compat`` and
``_artifact_identity``.

``training.pipeline`` re-exports the three names under their original private
spellings, so nothing that already imports them from there has to change.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from typing import Any

from recotem.recipe.models import Recipe


def normalize_paths_for_hash(obj: Any) -> Any:
    """Recursively convert Path-like objects to POSIX strings for stable hashing.

    ``pathlib.Path`` (and its subclasses such as ``PurePosixPath`` and
    ``PureWindowsPath``) serialise via ``str()`` to an OS-dependent
    representation: POSIX gives ``/data/foo`` while Windows gives
    ``\\data\\foo``.  Using ``Path.as_posix()`` normalises to the forward-
    slash form on every platform so the same recipe always produces the same
    hash regardless of where :func:`compute_recipe_hash` is called.
    """
    if isinstance(obj, pathlib.PurePath):
        return obj.as_posix()
    if isinstance(obj, dict):
        return {k: normalize_paths_for_hash(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_paths_for_hash(v) for v in obj]
    return obj


def json_default_for_hash(obj: Any) -> Any:
    """Custom JSON default serialiser for :func:`compute_recipe_hash`.

    Converts ``pathlib.PurePath`` to a POSIX string before falling back to
    ``str()`` for any other non-serialisable type.  This keeps the same
    safety net as a plain ``default=str`` while guaranteeing that Paths are
    never serialised with an OS-dependent separator.
    """
    if isinstance(obj, pathlib.PurePath):
        return obj.as_posix()
    return str(obj)


def compute_recipe_hash(recipe: Recipe) -> str:
    """Return a SHA-256 hex digest of the recipe's canonical YAML serialization.

    Uses pydantic's ``model_dump`` -> sorted JSON to get a stable canonical
    form.  No secrets are included (recipe YAML should never contain secrets).

    Path normalisation: any ``pathlib.PurePath`` (including ``PureWindowsPath``)
    found in the dump is converted to a POSIX forward-slash string via
    ``as_posix()`` so the hash is identical on POSIX and Windows hosts given
    the same recipe content.
    """
    raw = recipe.model_dump(mode="json", by_alias=False)
    normalised = normalize_paths_for_hash(raw)
    canonical = json.dumps(
        normalised,
        sort_keys=True,
        separators=(",", ":"),
        default=json_default_for_hash,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
