"""The serve-memory sizing prose must not go back to counting an artifact at 1x.

Loading an artifact costs about 4.8x its size on disk: ``read_artifact`` holds
the whole file and the payload slice of it at the same time, and the
deserialized model lands on top of both.  Measured on a real 644.5 MiB IALS
artifact (10M interactions, 500k users, 50k items), ``recotem serve`` sat at
3,292 MiB resident.  The page previously said

    RAM per pod = (avg_artifact_size_GiB x n_recipes) + ... + 1 GiB OS overhead

which predicted 1,668 MiB for that model -- half the truth.  The flat 1 GiB
constant hides the missing multiplier while the models are small, so the error
only appears at the sizes where it OOMKills a pod.

These are prose guards in the same spirit as
``test_no_shipped_prose_still_calls_the_feature_cost_cubic``: the numbers live
in shipped Markdown, nothing executes them, and the way they go wrong is by a
later edit quietly restoring the simpler-looking version.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted((ROOT / "docs").rglob("*.md"))

# The artifact term of the pod-sizing formula, with no multiplier in front.
_UNMULTIPLIED_ARTIFACT_TERM = re.compile(
    r"\(\s*avg_artifact_size_GiB\s*[x×*]\s*n_recipes\s*\)"
)


def test_pod_sizing_formula_multiplies_the_artifact_term() -> None:
    """The artifact term must carry its measured multiplier, not stand alone."""
    offenders = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in DOCS
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _UNMULTIPLIED_ARTIFACT_TERM.search(line)
    ]
    assert not offenders, (
        "the pod-sizing formula counts a loaded artifact at 1x its on-disk "
        "size; measured cost is ~4.8x (644.5 MiB artifact -> 3,292 MiB "
        f"resident), so this under-estimates a large model by 2x: {offenders}"
    )


def test_replica_sizing_note_does_not_budget_one_times_the_artifact_cap() -> None:
    """k8s.md's replica note carried the same 1x arithmetic (2 GiB x 10 = 20 GiB)."""
    k8s = ROOT / "docs" / "deployment" / "k8s.md"
    text = k8s.read_text(encoding="utf-8")
    assert "plan for up to 20 GiB per pod" not in text, (
        "k8s.md budgets 10 recipes at the 2 GiB artifact cap as 20 GiB per pod; "
        "at the measured ~4.8x load multiplier that is on the order of 96 GiB"
    )


def test_csv_error_table_covers_the_download_cap_on_a_local_path() -> None:
    """The cap an operator actually hits must be in the exit-code table.

    The table distinguishes local from HTTP for a ``sha256`` mismatch but not
    for the byte cap, though the byte cap is the one a 10M-row CSV reaches --
    255,567,608 bytes for three short columns, against a 268,435,456-byte
    default -- and it reports exit 3, not the exit 7 the table's only cap row
    shows.  Retry logic keyed on exit 7 would never see it.
    """
    csv_doc = (ROOT / "docs" / "data-sources" / "csv.md").read_text(encoding="utf-8")
    cap_rows = [
        line
        for line in csv_doc.splitlines()
        if line.startswith("|") and "Download cap exceeded" in line
    ]
    assert len(cap_rows) == 2, cap_rows
    assert any("| 3 |" in row and "DataSourceError" in row for row in cap_rows), (
        "no row for the download cap on a local or object-store path, which "
        f"raises DataSourceError (exit 3), not HttpFetchError (exit 7): {cap_rows}"
    )
