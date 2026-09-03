"""Integration test: `recotem train` must not write progress bars to a pipe.

``recotem train recipe.yaml > train.log`` — the idiom ``docs/deployment/cron.md``
is built on — used to capture nothing but carriage returns and block-drawing
characters, while all 47 structured log events went to stderr and escaped to
the operator's terminal.  The bars come from fastprogress (transitively via
irspack) and were emitted for a redirected stdout because fastprogress's own
``printing()`` gate reads ``getattr(stdout, 'isatty', False)``, which yields
the bound method rather than calling it.

Only a real subprocess can catch a regression here: the bars are written by a
C-adjacent third-party library to whatever ``sys.stdout`` is at call time, and
an in-process capture cannot distinguish "not a terminal" from "captured".
The interactive case is exercised over a real pty for the same reason.

IALS is used deliberately — it is the one algorithm in the shipped set that
subclasses irspack's ``BaseEarlyStopRecommender``, which is what constructs
the fastprogress bar.
"""

from __future__ import annotations

import os
import pty
import selectors
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# Generous enough for interpreter start + irspack import on a loaded CI box,
# short enough that a hang fails the suite rather than stalling it.
_SUBPROCESS_TIMEOUT = 180.0

# Deterministic signing key — same format used in tests/conftest.py.
_SIGNING_KEYS = "active:" + ("ab" * 32)

# The two byte classes that made the old redirected log useless.
_CARRIAGE_RETURN = "\r"
_BAR_FILL = "█"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trainable_recipe(tmp_path: Path) -> Path:
    """A recipe small enough to train in about a second, with real epochs."""
    csv_path = tmp_path / "interactions.csv"
    lines = ["user_id,item_id"]
    for user in range(40):
        for item in range(12):
            lines.append(f"u{user},i{(user * 7 + item * 3) % 25}")
    csv_path.write_text("\n".join(lines) + "\n")

    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        textwrap.dedent(f"""\
            name: progress_bar_probe
            source:
              type: csv
              path: {csv_path}
            schema:
              user_column: user_id
              item_column: item_id
            training:
              algorithms: [IALS]
              n_trials: 1
              cutoff: 5
              parallelism: 1
              split:
                scheme: random
                heldout_ratio: 0.2
                seed: 42
            output:
              path: {tmp_path / "probe.recotem"}
              versioning: always_overwrite
            """)
    )
    return recipe_path


def _train_env() -> dict[str, str]:
    """Environment for a train subprocess.

    ``RECOTEM_LOG_FORMAT`` is cleared rather than pinned so the pty case
    exercises the stdout-TTY branch of the policy on its own merits — with
    ``json`` set, suppression would kick in for a different reason and the
    interactive assertion would prove nothing.
    """
    env = dict(os.environ)
    env["RECOTEM_SIGNING_KEYS"] = _SIGNING_KEYS
    env.pop("RECOTEM_LOG_FORMAT", None)
    return env


def _train_argv(recipe: Path, *extra: str) -> list[str]:
    """Invoke via ``python -m recotem.cli`` so PATH need not hold the script."""
    return [sys.executable, "-m", "recotem.cli", "train", str(recipe), *extra]


def _run_train_on_a_pty(recipe: Path, *extra: str) -> tuple[int, str]:
    """Run train with stdout attached to a pty; return (exit code, output).

    The master side is drained while the child runs — a pty buffer that fills
    would block the writer and turn this test into a hang.
    """
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        _train_argv(recipe, *extra),
        stdout=slave_fd,
        stderr=subprocess.DEVNULL,
        env=_train_env(),
        close_fds=True,
    )
    os.close(slave_fd)

    chunks = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(master_fd, selectors.EVENT_READ)
    try:
        while True:
            if not selector.select(timeout=1.0):
                if proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(master_fd, 65536)
            except OSError:
                # The slave side closed: EIO on Linux, empty read on macOS.
                break
            if not data:
                break
            chunks += data
    finally:
        selector.close()
        os.close(master_fd)
        returncode = proc.wait(timeout=_SUBPROCESS_TIMEOUT)

    return returncode, chunks.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Redirected: stdout must stay clean, stderr keeps the structured log
# ---------------------------------------------------------------------------


def test_redirected_train_writes_no_progress_bars_to_stdout(
    trainable_recipe: Path, tmp_path: Path
) -> None:
    """`recotem train recipe.yaml > log` must not capture bar frames."""
    stdout_path = tmp_path / "train.log"
    stderr_path = tmp_path / "train.err"

    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        proc = subprocess.run(
            _train_argv(trainable_recipe),
            stdout=out,
            stderr=err,
            env=_train_env(),
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )

    captured = stdout_path.read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (
        f"train failed: {stderr_path.read_text(errors='replace')[-2000:]}"
    )
    assert _CARRIAGE_RETURN not in captured, (
        f"progress bar frames reached a redirected stdout: {captured[:400]!r}"
    )
    assert _BAR_FILL not in captured, (
        f"progress bar fill reached a redirected stdout: {captured[:400]!r}"
    )

    # The structured log is the useful half; it must be intact and unmoved.
    stderr_lines = stderr_path.read_text(encoding="utf-8", errors="replace")
    assert '"event": "train_done"' in stderr_lines


# ---------------------------------------------------------------------------
# Interactive: the bars must survive
# ---------------------------------------------------------------------------


def test_interactive_train_still_draws_progress_bars(trainable_recipe: Path) -> None:
    """A terminal run keeps the bars — the fix must not degrade it."""
    returncode, output = _run_train_on_a_pty(trainable_recipe)

    assert returncode == 0
    assert _CARRIAGE_RETURN in output, "progress bars vanished from an interactive run"
    assert _BAR_FILL in output


def test_quiet_suppresses_progress_bars_even_on_a_terminal(
    trainable_recipe: Path,
) -> None:
    """--quiet promises to suppress per-trial output; the bar is per-trial."""
    returncode, output = _run_train_on_a_pty(trainable_recipe, "--quiet")

    assert returncode == 0
    assert output == "", f"--quiet still wrote to stdout: {output[:400]!r}"
