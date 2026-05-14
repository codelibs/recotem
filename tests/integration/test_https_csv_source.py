"""Integration tests: HTTP-fetched CSV => recotem train end-to-end.

Uses pytest-httpserver to stand up an HTTP server in-process. The same
urllib code path serves HTTP and HTTPS, so HTTP coverage is sufficient at
the unit/integration layer. TLS handshake is covered (optionally) by the
e2e tutorial mode.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ("pytest_httpserver",)

# Derive the venv bin directory from the current interpreter so we can invoke
# the ``recotem`` entry-point script even when ``recotem`` is not on the outer
# PATH.  ``recotem.__main__`` does not exist; the package is exposed only as an
# installed console-script entry point.
_VENV_BIN = Path(sys.executable).parent
_RECOTEM_BIN = str(_VENV_BIN / "recotem")


def _csv_body() -> bytes:
    rows = ["user_id,item_id"]
    # 200 users x 5 items each = 1000 interactions (above min_rows default).
    for u in range(1, 201):
        for i in range(1, 6):
            rows.append(f"{u},item_{i}")
    return ("\n".join(rows) + "\n").encode("utf-8")


@pytest.fixture
def signing_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Generate a signing key and export it via env."""
    kid = "it"
    plaintext = "ab" * 32
    monkeypatch.setenv("RECOTEM_SIGNING_KEYS", f"{kid}:{plaintext}")
    return kid


def test_http_csv_train_end_to_end(
    httpserver,
    tmp_path: Path,
    signing_env: str,  # noqa: ARG001
) -> None:
    body = _csv_body()
    digest = hashlib.sha256(body).hexdigest()
    httpserver.expect_request("/data.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/data.csv")

    out_path = tmp_path / "artifacts" / "it.recotem"
    out_path.parent.mkdir()
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(
        f"""\
name: it_recipe

source:
  type: csv
  path: {url}
  sha256: {digest}
  dtype:
    user_id: str
    item_id: str

schema:
  user_column: user_id
  item_column: item_id

cleansing:
  drop_null_ids: true
  min_rows: 100

training:
  algorithms: [TopPop]
  metric: ndcg
  cutoff: 5
  n_trials: 1
  parallelism: 1
  split:
    scheme: random
    heldout_ratio: 0.2
    seed: 42

output:
  path: {out_path}
  versioning: always_overwrite
"""
    )

    proc = subprocess.run(
        [_RECOTEM_BIN, "train", str(recipe)],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    assert proc.returncode == 0, (
        f"train failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_http_csv_sha256_mismatch_train_exits_7(
    httpserver,
    tmp_path: Path,
    signing_env: str,  # noqa: ARG001
) -> None:
    body = _csv_body()
    httpserver.expect_request("/bad.csv").respond_with_data(
        body, content_type="text/csv"
    )
    url = httpserver.url_for("/bad.csv")

    recipe = tmp_path / "recipe.yaml"
    bad_sha256 = "0" * 64
    recipe.write_text(
        f"""\
name: it_bad_sha
source:
  type: csv
  path: {url}
  sha256: "{bad_sha256}"
schema:
  user_column: user_id
  item_column: item_id
training:
  algorithms: [TopPop]
  n_trials: 1
output:
  path: {tmp_path / "out.recotem"}
  versioning: always_overwrite
"""
    )

    proc = subprocess.run(
        [_RECOTEM_BIN, "train", str(recipe)],
        capture_output=True,
        text=True,
        env=os.environ,
    )
    # sha256 mismatch on a network-fetched source raises HttpFetchError
    # (verify_sha256 lives in _http_fetch.py).  Even though the CSV source
    # wraps it as DataSourceError, _map_exception_to_exit walks the
    # __cause__ chain so the canonical exit code 7 is preserved for
    # CronJob retry semantics.  See docs/operations.md exit-code table.
    assert proc.returncode == 7, (
        f"expected exit 7 (HttpFetchError, sha256 mismatch on network fetch), "
        f"got {proc.returncode}:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
