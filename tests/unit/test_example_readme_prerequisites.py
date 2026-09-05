"""Every example README that tells the reader to run `recotem train` must first
tell them to generate a signing key.

`recotem train` refuses to write an unsigned artifact: with no
``RECOTEM_SIGNING_KEYS`` in the environment it exits 8 (``_EXIT_CONFIG``,
``code=signing_key_missing``) before the first Optuna trial.  Four of the nine
example READMEs presented a self-contained "Run" block that omitted the step,
so a reader who copy-pasted it hit exit 8 on the very first command of the
example.  The message names the fix, but the documented procedure was still one
that cannot work as printed.

The scan is unconditional -- there is no exemption list -- so a *new* example
README that forgets the step fails this test the day it lands, which is the
regression this guards against as much as the four that were fixed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLES = _REPO_ROOT / "examples"

# A shell invocation of the CLI: bare, under `uv run`, or under `docker run ...`.
_RUNS_CLI = re.compile(r"(?m)^\s*(?:\$\s*)?(?:uv run\s+)?recotem\s+(?:train|serve)\b")

# Either half of the fix is acceptable: the keygen command itself, or a
# reference to the variable it produces (some READMEs export it from a Secret).
_NAMES_SIGNING_KEY = re.compile(r"keygen\s+--type\s+signing|RECOTEM_SIGNING_KEYS")


def _example_readmes() -> list[Path]:
    return sorted(_EXAMPLES.rglob("README.md"))


def test_the_scan_actually_finds_the_example_readmes() -> None:
    """Fail loudly if the corpus is empty or has moved.

    Without this, renaming or relocating `examples/` would leave the two tests
    below passing vacuously over zero files -- a guard watching nothing.
    """
    readmes = _example_readmes()
    assert _EXAMPLES.is_dir(), f"examples/ not found at {_EXAMPLES}"
    assert len(readmes) >= 8, (
        f"expected at least 8 example READMEs, found {len(readmes)}: "
        f"{[str(p.relative_to(_REPO_ROOT)) for p in readmes]}"
    )


def test_the_scan_recognises_a_train_command() -> None:
    """Pin the detector itself, so a regex drift cannot silently match nothing."""
    assert _RUNS_CLI.search("uv run recotem train examples/csv-local/recipe.yaml")
    assert _RUNS_CLI.search("recotem serve --recipes ./recipes/")
    assert _RUNS_CLI.search("  recotem train recipe.yaml")
    assert not _RUNS_CLI.search("recotem inspect ./artifacts/x.recotem")
    assert not _RUNS_CLI.search("see `recotem train` for details")


@pytest.mark.parametrize(
    "readme", _example_readmes(), ids=lambda p: str(p.relative_to(_EXAMPLES))
)
def test_readme_that_runs_train_names_the_signing_key(readme: Path) -> None:
    text = readme.read_text(encoding="utf-8")
    if not _RUNS_CLI.search(text):
        pytest.skip("does not invoke `recotem train` / `recotem serve`")
    assert _NAMES_SIGNING_KEY.search(text), (
        f"{readme.relative_to(_REPO_ROOT)} tells the reader to run the recotem "
        "CLI but never mentions `recotem keygen --type signing` or "
        "RECOTEM_SIGNING_KEYS. Without a signing key `recotem train` exits 8 "
        "(signing_key_missing) on the first command of the example."
    )
