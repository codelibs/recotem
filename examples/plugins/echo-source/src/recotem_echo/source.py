"""EchoSource — a minimal Recotem DataSource plugin.

This module demonstrates the full DataSource contract that Recotem expects
from third-party plugins (spec Section 13).

Contract summary
----------------
1. The class must expose ``type_name: ClassVar[str]`` — the discriminator
   value used in recipe YAML (``source.type: echo``).
2. A pydantic ``Config`` inner class defines the recipe sub-schema.
3. ``extras_required: ClassVar[list[str]]`` names the pip extras that must be
   installed for this source to work.  Leave empty if base dependencies suffice.
4. ``__init__(self, config: Config)`` receives the validated config.
5. ``fetch(self, ctx: FetchContext) -> pd.DataFrame`` must return a DataFrame
   with at least the columns named in the recipe ``schema`` block.
6. Raise ``recotem.datasource.base.DataSourceError`` for any external/transient
   failure.  Other exceptions surface as exit 1.
7. Do NOT import optional dependencies at module top-level; defer imports to
   ``__init__`` so a missing extra yields a clear error message.

Allowed dependency direction (from spec Section 4):
  datasource/ → stdlib + pydantic + pandas only (no training/, serving/, etc.)
"""

from __future__ import annotations

import random
from typing import ClassVar

import pandas as pd
from pydantic import BaseModel, Field

# The FetchContext type is imported at function call time (deferred) to avoid
# importing optional extras at module load.
# In this example EchoSource has no optional dependencies, so we import
# DataSourceError eagerly for clarity.
from recotem.datasource.base import DataSourceError


class EchoSource:
    """DataSource plugin that returns a static synthetic DataFrame.

    Recipe YAML example::

        source:
          type: echo         # must match type_name below
          n_users: 20
          n_items: 50
          n_rows: 200
          seed: 42

    The returned DataFrame has columns ``user_id``, ``item_id``, and
    ``timestamp`` (integer epoch seconds, suitable for time-based splits).
    """

    # ── plugin contract class variables ───────────────────────────────────────

    type_name: ClassVar[str] = "echo"
    """Discriminator value for recipe YAML ``source.type``."""

    extras_required: ClassVar[list[str]] = []
    """No optional extras required — pandas is a core recotem dependency."""

    no_expand_fields: ClassVar[frozenset[str]] = frozenset()
    """No fields carry raw SQL or other content that must avoid env-var expansion."""

    # ── pydantic config schema ────────────────────────────────────────────────

    class Config(BaseModel):
        """Recipe sub-schema for EchoSource.

        All fields have defaults so the source can be used with minimal YAML.
        """

        n_users: int = Field(default=10, ge=1, description="Number of distinct users.")
        n_items: int = Field(default=20, ge=1, description="Number of distinct items.")
        n_rows: int = Field(
            default=100,
            ge=1,
            description="Total number of interaction rows to generate.",
        )
        seed: int = Field(
            default=42,
            description="Random seed for reproducibility.",
        )

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def __init__(self, config: EchoSource.Config) -> None:
        """Store validated config.

        Defer any import of optional dependencies to here (not module top-level)
        so that missing extras produce a clear DataSourceError with the extra
        name, not an ImportError.

        This plugin has no optional deps, so we just store the config.
        """
        self._config = config

    # ── DataSource protocol ───────────────────────────────────────────────────

    def fetch(self, ctx: object) -> pd.DataFrame:
        """Return a synthetic interactions DataFrame.

        Parameters
        ----------
        ctx:
            ``recotem.datasource.base.FetchContext`` instance carrying the
            recipe name, run_id, and logger.  Type-hinted as ``object`` to
            avoid importing recotem internals at module level.

        Returns
        -------
        pd.DataFrame
            Columns: ``user_id`` (str), ``item_id`` (str), ``timestamp`` (int).

        Raises
        ------
        DataSourceError
            Raised (for illustration) if n_rows > n_users * n_items, since
            we cannot produce meaningful unique interactions beyond that limit.
        """
        cfg = self._config

        max_possible = cfg.n_users * cfg.n_items
        if cfg.n_rows > max_possible:
            raise DataSourceError(
                f"EchoSource: n_rows ({cfg.n_rows}) exceeds n_users * n_items "
                f"({max_possible}).  Reduce n_rows or increase n_users/n_items."
            )

        rng = random.Random(cfg.seed)

        users = [f"user_{i}" for i in range(cfg.n_users)]
        items = [f"item_{j}" for j in range(cfg.n_items)]

        # Sample without replacement for clean interaction data.
        all_pairs = [(u, v) for u in users for v in items]
        sampled = rng.sample(all_pairs, cfg.n_rows)

        # Assign monotonically increasing timestamps (1-second steps).
        base_ts = 1_700_000_000  # 2023-11-14 UTC as a reasonable epoch
        rows = [
            {
                "user_id": u,
                "item_id": v,
                "timestamp": base_ts + idx,
            }
            for idx, (u, v) in enumerate(sampled)
        ]

        return pd.DataFrame(rows, columns=["user_id", "item_id", "timestamp"])

    # ── optional DataSource protocol ──────────────────────────────────────────

    def probe(self) -> dict:
        """Lightweight connectivity / auth check called by ``recotem validate``.

        EchoSource is entirely synthetic (no network, no filesystem), so
        probe() simply validates the config constraints without generating
        data.

        Returns
        -------
        dict
            A small status dict for logging by ``recotem validate``.

        Raises
        ------
        DataSourceError
            If the config is self-contradictory (n_rows > n_users * n_items).
        """
        cfg = self._config
        max_possible = cfg.n_users * cfg.n_items
        if cfg.n_rows > max_possible:
            raise DataSourceError(
                f"EchoSource: n_rows ({cfg.n_rows}) exceeds n_users * n_items "
                f"({max_possible}).  Reduce n_rows or increase n_users/n_items."
            )
        return {
            "status": "ok",
            "rows_to_emit": cfg.n_rows,
            "items": cfg.n_items,
        }
