"""Shared pytest fixtures for the Recotem 2.0 test suite.

NOTE: This test suite tests a system whose payload format is pickle (required
by irspack for scipy sparse matrices). The pickle usage here is intentional
and required: we are testing the HMAC-signed artifact security layer, which
specifically tests that the SafeUnpickler allow-list blocks gadgets.

Provides:
- signing_key_bytes: deterministic 32-byte signing key
- key_ring: KeyRing with two kids ("active", "old")
- tmp_recipe_yaml: factory for writing recipe YAML files to tmp_path
- make_artifact: factory for building signed artifact bytes
- movielens_df: session-scoped MovieLens100K DataFrame (downloaded once)
"""
from __future__ import annotations

import hashlib
import json
import struct
import textwrap
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Signing key fixtures
# ---------------------------------------------------------------------------

ACTIVE_KEY_HEX: str = "aa" * 32  # 64 hex chars = 32 bytes, deterministic
OLD_KEY_HEX: str = "bb" * 32


@pytest.fixture(scope="session")
def signing_key_bytes() -> bytes:
    """Deterministic 32-byte signing key for 'active' kid."""
    return bytes.fromhex(ACTIVE_KEY_HEX)


@pytest.fixture(scope="session")
def key_ring():
    """KeyRing with two kids: 'active' and 'old'."""
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}", f"old:{OLD_KEY_HEX}")


@pytest.fixture(scope="session")
def single_key_ring():
    """KeyRing with just the 'active' kid."""
    from recotem.artifact.signing import KeyRing

    return KeyRing(f"active:{ACTIVE_KEY_HEX}")


# ---------------------------------------------------------------------------
# Artifact builder helper (used by multiple test modules)
# ---------------------------------------------------------------------------

def build_raw_artifact(
    kid: str,
    key_hex: str,
    header_dict: dict[str, Any] | None = None,
    payload_bytes: bytes | None = None,
) -> bytes:
    """Build a valid .recotem artifact byte string.

    Uses irspack-style pickle for the payload because the system under test
    requires it. The HMAC signs over kid || header || payload.
    """
    import hmac as _hmac
    import pickle  # noqa: S403

    if header_dict is None:
        header_dict = {
            "recipe_name": "test",
            "trained_at": "2026-01-01T00:00:00Z",
            "best_class": "TopPopRecommender",
            "best_score": 0.42,
        }
    if payload_bytes is None:
        # Use a simple builtin dict — allowed by SafeUnpickler allow-list
        payload_bytes = pickle.dumps({"key": "value"}, protocol=4)  # noqa: S301

    header_json: bytes = json.dumps(header_dict, separators=(",", ":")).encode("utf-8")
    kid_bytes: bytes = kid.encode("utf-8")
    key_bytes: bytes = bytes.fromhex(key_hex)

    h = _hmac.new(key_bytes, digestmod="sha256")
    h.update(kid_bytes)
    h.update(header_json)
    h.update(payload_bytes)
    digest = h.digest()

    from recotem.artifact.format import MAGIC, FORMAT_VERSION

    kid_len = len(kid_bytes)
    header_len = len(header_json)

    parts: list[bytes] = [
        MAGIC,
        struct.pack("<HH", FORMAT_VERSION, 0),
        bytes([kid_len]),
        kid_bytes,
        digest,
        struct.pack("<I", header_len),
        header_json,
        payload_bytes,
    ]
    return b"".join(parts)


@pytest.fixture()
def make_artifact(tmp_path: Path):
    """Factory that returns raw .recotem artifact bytes.

    Usage: make_artifact(payload_bytes=None, header_dict=None, kid='active',
                         key_hex=ACTIVE_KEY_HEX) -> bytes
    """
    def _factory(
        payload_bytes: bytes | None = None,
        header_dict: dict[str, Any] | None = None,
        kid: str = "active",
        key_hex: str = ACTIVE_KEY_HEX,
    ) -> bytes:
        return build_raw_artifact(
            kid=kid,
            key_hex=key_hex,
            header_dict=header_dict,
            payload_bytes=payload_bytes,
        )

    return _factory


@pytest.fixture()
def valid_artifact_path(tmp_path: Path, make_artifact):
    """Write a valid artifact to tmp_path/test.recotem and return its Path."""
    data = make_artifact()
    path = tmp_path / "test.recotem"
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------------
# Recipe YAML factory
# ---------------------------------------------------------------------------

_MINIMAL_RECIPE_TEMPLATE = """\
name: {name}
source:
  type: csv
  path: {csv_path}
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {output_path}
"""


@pytest.fixture()
def tmp_recipe_yaml(tmp_path: Path):
    """Factory: tmp_recipe_yaml(name, csv_path, output_path, extra_yaml) -> Path.

    Creates a minimal valid recipe YAML in tmp_path.
    """

    def _factory(
        name: str = "test_recipe",
        csv_path: str | None = None,
        output_path: str | None = None,
        extra_yaml: str = "",
    ) -> Path:
        if csv_path is None:
            csv_file = tmp_path / "data.csv"
            if not csv_file.exists():
                csv_file.write_text("user_id,item_id\nu1,i1\nu1,i2\nu2,i1\n")
            csv_path = str(csv_file)
        if output_path is None:
            output_path = str(tmp_path / f"{name}.recotem")

        content = _MINIMAL_RECIPE_TEMPLATE.format(
            name=name,
            csv_path=csv_path,
            output_path=output_path,
        )
        if extra_yaml:
            content += "\n" + textwrap.dedent(extra_yaml)

        yaml_path = tmp_path / f"{name}.yaml"
        yaml_path.write_text(content)
        return yaml_path

    return _factory


# ---------------------------------------------------------------------------
# MovieLens 100K fixture (session-scoped, downloaded once)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def movielens_df() -> pd.DataFrame:
    """Return the MovieLens100K interactions as a pandas DataFrame.

    Downloaded once per test session via irspack's MovieLens100KDataManager.
    Columns normalised to: user_id (str), item_id (str).
    """
    from irspack.dataset import MovieLens100KDataManager

    dm = MovieLens100KDataManager()
    df = dm.read_interaction()
    df = df.rename(
        columns={c: c.lower().replace(" ", "_") for c in df.columns}
    )
    for col in ("user_id", "item_id"):
        if col in df.columns:
            df[col] = df[col].astype(str)
    return df


@pytest.fixture(scope="session")
def movielens_small_df(movielens_df: pd.DataFrame) -> pd.DataFrame:
    """50 most-active users from MovieLens100K — fast training slice."""
    top_users = movielens_df["user_id"].value_counts().head(50).index
    return movielens_df[movielens_df["user_id"].isin(top_users)].reset_index(drop=True)
