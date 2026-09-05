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

# An ACTIONABLE step, not a mention. Either the command that produces the key,
# or an assignment/export of the variable it produces.
#
# Deliberately NOT a bare `RECOTEM_SIGNING_KEYS`: every fixed README names the
# variable in prose too ("without this it exits 8 with `RECOTEM_SIGNING_KEYS is
# not set`"), and the anchor is not unique in any of them -- 2 to 3 matches
# each. Anchoring on the first *mention* let a README pass with its `export`
# moved BELOW the train command, which still exits 8 for a reader working top
# to bottom. Verified by building exactly that file; see the test below.
_ACTIONABLE_KEY_STEP = re.compile(
    r"keygen\s+--type\s+signing"  # the command that generates one
    r"|export\s+RECOTEM_SIGNING_KEYS"  # an explicit export
    r"|^\s*RECOTEM_SIGNING_KEYS\s*=",  # or a bare assignment
    re.MULTILINE,
)


def _first_real_key_step(text: str) -> re.Match[str] | None:
    """First actionable key step that is not inside a shell comment.

    A commented *illustration* of the step is not the step. Without this
    filter, a README reading

        # 1. Train. You will need a signing key; it looks like this:
        #    export RECOTEM_SIGNING_KEYS="dev:<hex64>"
        uv run recotem train ...

    satisfies the anchor above its train command while the real `export` sits
    below it -- a file that still exits 8 on its first command. Verified by
    building exactly that; see the regression test at the bottom.

    ``_RUNS_CLI`` needs no equivalent filter: it is anchored to the start of a
    line and ``#`` is not in its prefix, so a commented-out command cannot
    match it.
    """
    for match in _ACTIONABLE_KEY_STEP.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        if not text[line_start : match.start()].lstrip().startswith("#"):
            return match
    return None


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
def test_readme_names_the_signing_key_before_it_runs_the_cli(readme: Path) -> None:
    """The key step must come BEFORE the first train/serve command, not anywhere.

    Ordering, not presence, is what makes the walkthrough work. A README that
    mentions RECOTEM_SIGNING_KEYS in a trailing footnote still exits 8 for a
    reader following it top to bottom -- and an earlier version of this guard
    passed that file, because every mutation used to check it had deleted the
    mention outright rather than moving it. Asserting the offset closes the gap
    between "the file says the words" and "the procedure runs".
    """
    text = readme.read_text(encoding="utf-8")
    first_cli = _RUNS_CLI.search(text)
    if not first_cli:
        pytest.skip("does not invoke `recotem train` / `recotem serve`")

    key = _first_real_key_step(text)
    rel = readme.relative_to(_REPO_ROOT)
    commented_only = key is None and _ACTIONABLE_KEY_STEP.search(text) is not None
    assert key, (
        f"{rel} tells the reader to run the recotem CLI but never gives them a "
        "step that produces a signing key -- no `recotem keygen --type "
        "signing`, no export or assignment of RECOTEM_SIGNING_KEYS. Without "
        "one, `recotem train` exits 8 (signing_key_missing) on the first "
        "command of the example."
        + (
            " (It shows one inside a comment, which the reader cannot run.)"
            if commented_only
            else ""
        )
    )
    assert key.start() < first_cli.start(), (
        f"{rel} gives the signing-key step at offset {key.start()}, AFTER its "
        f"first `recotem train`/`serve` command at offset {first_cli.start()}. "
        "A reader following the file top to bottom still hits exit 8. Move the "
        "keygen step above the first CLI invocation. (Naming the variable in "
        "prose earlier does not count -- the reader needs the command.)"
    )


def test_a_prose_mention_before_the_command_does_not_satisfy_the_guard() -> None:
    """An early mention of the variable must not stand in for the actual step.

    The first version of this guard asserted only that the file *mentions*
    ``RECOTEM_SIGNING_KEYS`` somewhere; the second asserted that the mention
    precedes the first CLI command. Both passed the README below, which still
    exits 8 on its first command -- the key anchor is not unique in any shipped
    README (2-3 matches each), so anchoring on the first *mention* anchors on
    prose. The assertion is now on the actionable step.
    """
    broken = (
        "# Run\n"
        "```bash\n"
        "# 1. Train. (`recotem train` refuses to write an unsigned artifact:\n"
        "#    without a signing key it exits 8 with `RECOTEM_SIGNING_KEYS is\n"
        "#    not set`.)\n"
        "uv run recotem train examples/demo/recipe.yaml\n"
        "\n"
        "# 2. If that failed, generate a signing key, then retry.\n"
        "export $(uv run recotem keygen --type signing | grep '^env_entry=')\n"
        "```\n"
    )
    first_cli = _RUNS_CLI.search(broken)
    assert first_cli, "fixture must contain a train command"

    # The weak anchor (any mention) is satisfied early -- this is the trap.
    assert re.search(r"RECOTEM_SIGNING_KEYS", broken).start() < first_cli.start()

    # The real one is not.
    step = _ACTIONABLE_KEY_STEP.search(broken)
    assert step is not None, "fixture must contain a keygen command"
    assert step.start() > first_cli.start(), (
        "the guard must reject a README whose actionable signing-key step "
        "comes after the command that needs it"
    )


def test_a_commented_illustration_does_not_satisfy_the_guard() -> None:
    """Showing the step in a comment is not giving the reader the step.

    Third revision of this guard, and the third anchor that admitted a README
    which exits 8 on its first command. Each was found by a probe the previous
    version's author would not have written:

    1. presence -- a trailing footnote naming the variable
    2. ordering of a *mention* -- prose above, the real `export` below
    3. ordering of an *action* -- a commented illustration above, the real
       `export` below

    Each fix moved the anchor one step closer to "what the reader can run".
    """
    broken = (
        "# Run\n"
        "```bash\n"
        "# 1. Train. You will need a signing key; it looks like this:\n"
        '#    export RECOTEM_SIGNING_KEYS="dev:<hex64>"\n'
        "uv run recotem train examples/demo/recipe.yaml\n"
        "\n"
        "# 2. Generate the key if the above failed.\n"
        "export $(uv run recotem keygen --type signing | grep '^env_entry=')\n"
        "```\n"
    )
    first_cli = _RUNS_CLI.search(broken)
    assert first_cli, "fixture must contain a train command"

    # The unfiltered anchor is satisfied early by the comment -- the trap.
    naive = _ACTIONABLE_KEY_STEP.search(broken)
    assert naive is not None and naive.start() < first_cli.start()

    # The filtered one finds only the real step, which is too late.
    real = _first_real_key_step(broken)
    assert real is not None, "fixture must contain a runnable keygen line"
    assert real.start() > first_cli.start(), (
        "the guard must reject a README whose only runnable signing-key step "
        "comes after the command that needs it, however clearly a comment "
        "above that command describes it"
    )


def test_every_shipped_readme_step_is_runnable_not_commented() -> None:
    """The anchor the guard uses must be a real line in every shipped README.

    Guards against the filter silently finding nothing: if a future edit moved
    every key step into a comment, `_first_real_key_step` would return None and
    the parametrised test would fail -- but this states the expectation
    directly, so the reason is legible.
    """
    checked = 0
    for readme in _example_readmes():
        text = readme.read_text(encoding="utf-8")
        if not _RUNS_CLI.search(text):
            continue
        checked += 1
        assert _first_real_key_step(text) is not None, (
            f"{readme.relative_to(_REPO_ROOT)} has no runnable signing-key step"
        )
    assert checked >= 7, f"expected >=7 READMEs invoking the CLI, saw {checked}"
