"""The release notes must describe the probe topology the chart actually ships.

The 2.1.0 notes were drafted while all three Helm probes still polled
``/v1/health``, and said so.  The release then split liveness and readiness onto
``/v1/health/live`` and ``/v1/health/ready`` and rewired the chart, but the notes
kept the old sentence and never mentioned the two new endpoints.  A reader
planning a Kubernetes upgrade from those notes -- the one audience the
"Upgrading from 2.0.0" section is written for -- would copy the 2.0.0 probe block
into their own manifests and reproduce exactly the CrashLoop the split removed.

So the claim is checked against ``helm/recotem/templates/deployment.yaml`` rather
than trusted.  The chart is the authority: if a future release really does point
every probe at one path, both assertions relax on their own.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_CHANGELOG = _ROOT / "CHANGELOG.md"
_CHART_DEPLOYMENT = _ROOT / "helm" / "recotem" / "templates" / "deployment.yaml"
_EXAMPLE_DEPLOYMENT = _ROOT / "examples" / "k8s" / "serve-deployment.yaml"

# The manifests are Helm templates, so they are not parseable YAML.  Both files
# spell a probe the same way: `<kind>Probe:` then `httpGet:` then `path:`.
_PROBE = re.compile(
    r"^\s*(startup|readiness|liveness)Probe:\s*$\n"
    r"\s*httpGet:\s*$\n"
    r"\s*path:\s*(\S+)\s*$",
    re.MULTILINE,
)

# The sentence this guard exists to keep out of the notes while it is false.
_SINGLE_PATH_CLAIM = "points all three probes (startup, readiness, liveness) at"


def _probe_paths(manifest: Path) -> dict[str, str]:
    paths = {
        m.group(1): m.group(2) for m in _PROBE.finditer(manifest.read_text("utf-8"))
    }
    assert paths, f"no httpGet probes found in {manifest.relative_to(_ROOT)}"
    return paths


def _unreleased_section() -> str:
    """The CHANGELOG section for the version being prepared (the first one)."""
    text = _CHANGELOG.read_text(encoding="utf-8")
    headings = [m.start() for m in re.finditer(r"^## \[", text, re.MULTILINE)]
    assert len(headings) >= 2, "CHANGELOG has fewer than two version sections"
    return text[headings[0] : headings[1]]


def test_release_notes_name_every_probe_path_the_chart_polls() -> None:
    section = _unreleased_section()
    for manifest in (_CHART_DEPLOYMENT, _EXAMPLE_DEPLOYMENT):
        for probe, path in sorted(_probe_paths(manifest).items()):
            assert path in section, (
                f"{manifest.relative_to(_ROOT)} points its {probe}Probe at "
                f"{path}, but the release notes never mention that endpoint. "
                "An operator upgrading their own manifests reads the notes, not "
                "the chart."
            )


def test_release_notes_do_not_claim_one_probe_path_for_the_shipped_chart() -> None:
    chart = _probe_paths(_CHART_DEPLOYMENT)
    if len(set(chart.values())) == 1:
        return  # The claim would be true; nothing to guard.
    section = _unreleased_section()
    assert _SINGLE_PATH_CLAIM not in section, (
        "The release notes say the shipped chart "
        f"{_SINGLE_PATH_CLAIM} one endpoint, but "
        f"helm/recotem/templates/deployment.yaml points them at "
        f"{sorted(set(chart.values()))}."
    )


def test_release_notes_do_not_attribute_a_wrong_path_to_a_chart_probe() -> None:
    """Naming the right endpoints somewhere is not enough.

    The two checks above both passed while the notes said "The shipped Helm
    chart points its **startupProbe** at `/v1/health`" -- every path the chart
    polls was mentioned elsewhere in the section, and the guarded sentence uses
    different words.  A whole upgrade-planning paragraph was built on that
    claim.  So each probe is also checked against what the notes attribute to
    it by name.
    """
    section = _unreleased_section()
    for probe, path in sorted(_probe_paths(_CHART_DEPLOYMENT).items()):
        matches = list(
            re.finditer(
                rf"{probe}Probe`?\**[^.\n]{{0,80}}?(?:at|to|onto|on)\s+"
                rf"`?(/v1/[A-Za-z0-9/_-]+)`?",
                section,
                re.IGNORECASE,
            )
        )
        # Without this the loop below is a no-op whenever the notes are
        # reworded, and the test goes green over an empty scan -- which is what
        # it did for every probe from #263 until this assertion was added.
        assert matches, (
            f"the release notes never say where the chart points its "
            f"{probe}Probe, so this guard inspected nothing for it. Write the "
            f"attribution as '{probe}Probe at `{path}`' (the phrasing an "
            "operator copying probes into their own manifests reads), or "
            "delete this test rather than leaving it green over an empty scan."
        )
        for m in matches:
            assert m.group(1) == path, (
                f"the release notes say the chart points its {probe}Probe at "
                f"{m.group(1)}; helm/recotem/templates/deployment.yaml points "
                f"it at {path}."
            )


# The count-based endpoint.  Matched exactly -- `/v1/health/ready` and
# `/v1/health/live` must not be read as mentions of it.
_COUNT_BASED = re.compile(r"`/v1/health`")
_PROBE_TOKEN = re.compile(r"\b(?:startup|readiness|liveness)Probe\b", re.IGNORECASE)
_UPGRADING_HEADING = "### Upgrading from 2.0.0"


def _upgrading_section() -> str:
    """The "Upgrading from 2.0.0" subsection of the release notes."""
    section = _unreleased_section()
    start = section.index(_UPGRADING_HEADING)
    after = section[start + len(_UPGRADING_HEADING) :]
    nxt = re.search(r"^### ", after, re.MULTILINE)
    return section[
        start : start + len(_UPGRADING_HEADING) + (nxt.start() if nxt else len(after))
    ]


def test_the_upgrading_section_still_discusses_the_probes() -> None:
    """A `must` companion for the pairing guard, which is `mustNot`-shaped.

    R9-P8's shape 2: a `mustNot` can only see the old wording COMING BACK, and a
    wholesale deletion is the one thing that does not produce. Measured on this
    guard before this test existed -- excising the whole "On Kubernetes the blast
    radius ... CrashLoop them." discussion removes 1,721 characters of
    upgrade-planning prose and **all four tests passed**, because the `Added`
    section further down carries its own probe names and paths and satisfied
    every needle. Shapes 1 and 2 compounding: blind to deletion, and the needles
    were findable elsewhere in the same file.

    A botched merge or a careless trim produces exactly that, and the operator
    it strands is the one this section is written for -- someone planning a
    Kubernetes upgrade, who then gets no probe guidance at all. The fix is the
    one P8 applied to their own pins: anchor the `must` on the same passage the
    `mustNot` guards, not on the file.
    """
    upgrading = _upgrading_section()

    assert _PROBE_TOKEN.search(upgrading), (
        f"{_UPGRADING_HEADING!r} no longer names any <kind>Probe. The pairing "
        "guard below is mustNot-shaped and cannot see this: it only fires when "
        "a WRONG attribution appears, not when the discussion is deleted."
    )
    assert _COUNT_BASED.search(upgrading), (
        f"{_UPGRADING_HEADING!r} no longer mentions `/v1/health`, so a reader "
        "upgrading from 2.0.0 -- where all three probes polled it -- is not "
        "told to move them."
    )
    for path in sorted(set(_probe_paths(_CHART_DEPLOYMENT).values())):
        assert path in upgrading, (
            f"the chart polls {path} but {_UPGRADING_HEADING!r} never mentions "
            "it. An operator rewriting their own manifests reads this section, "
            "not the Added section and not the chart."
        )


def _sentences(text: str) -> list[str]:
    """Split on '.' with newlines flattened.

    These are wrapped Markdown paragraphs, so a claim routinely spans two
    lines; the 80-character same-line window the check above uses cannot see
    those, which is half of why the third stale claim survived #263.
    """
    return [s for s in " ".join(text.split()).split(".") if s]


def _flat_section() -> str:
    """The release section with whitespace flattened.

    Flattened because a claim routinely spans two wrapped lines, and R9-P5
    measured a guard whose outcome depended on where a paragraph happened to
    wrap. A prose reflow must not decide whether a claim is pinned.
    """
    return " ".join(_unreleased_section().split())


def test_the_notes_assert_the_topology_rather_than_naming_it() -> None:
    """R9-P8's negation probe: pin the verdict, not the topic.

    Both guards below survive the section being rewritten to state the reverse.
    Measured -- "**No probe in the 2.1.0 chart reads it.**" flipped to "**Every
    probe in the 2.1.0 chart reads it.**" left `5 passed`, and inverting the
    consequence ("a failing startupProbe restarts the container instead of
    withholding traffic" -> "withholds traffic instead of restarting the
    container") also left `5 passed`. Neither touches a `<kind>Probe` token or a
    `/v1/...` path, so every needle survived and the direction of the claim was
    not pinned by anything.

    The second inversion is the dangerous one: "a failing startupProbe restarts
    the container" is the entire reason `/v1/health` is the wrong endpoint for
    a startupProbe. Reversed, the notes state the rationale for the design #241
    reversed, and an operator who believes it puts the probe back where it
    CrashLoops.

    Both assertions are conditioned on the chart, so if a future chart really
    does poll `/v1/health`, the first relaxes on its own.
    """
    chart_paths = set(_probe_paths(_CHART_DEPLOYMENT).values())
    section = _flat_section()

    if "/v1/health" not in chart_paths:
        assert "No probe in the 2.1.0 chart reads it" in section, (
            "no probe in the shipped chart reads `/v1/health` "
            f"(it polls {sorted(chart_paths)}), but the release notes no longer "
            "say so. Naming the endpoint is not asserting who reads it -- every "
            "other check here passes with this sentence reversed."
        )

    assert "a failing startupProbe restarts the container" in section, (
        "the notes no longer state that a failing startupProbe RESTARTS the "
        "container rather than withholding traffic. That is the whole reason "
        "the count-based endpoint is wrong for a startupProbe; without it the "
        "section names a topology without saying why it matters, and reads "
        "equally well reversed."
    )


def test_release_notes_never_tie_a_probe_to_the_count_based_endpoint() -> None:
    """No sentence may name a probe and `/v1/health` together.

    #241 moved the startupProbe onto `/v1/health/ready`; #263 then corrected
    two places that still described the old topology and missed a third, in
    **Added**::

        `/v1/health` itself is unchanged -- still `503` whenever
        `loaded < total` -- and stays the startupProbe path, where "every
        recipe present" is the right gate for a *new* pod.

    Both guards above were blind to it: the path comes *before* the probe name
    there, so no "<probe>Probe ... at <path>" attribution exists to check, and
    every path the chart polls is named elsewhere in the section.  The claim
    also contradicted the corrected paragraph 250 lines earlier ("No probe in
    the 2.1.0 chart reads it") and carried the rationale for the design #241
    reversed, so a reader writing manifests from **Added** would put their
    startupProbe back on the count-based endpoint and get the CrashLoop the
    release removed.

    Order-independent by construction, and derived from the chart: if a future
    chart really does poll `/v1/health` with a probe, this relaxes on its own.
    Sentences that say "probes" rather than a `<kind>Probe` token are untouched,
    which is what keeps the legitimate 2.0.0 paragraphs ("all three of your
    probes are still on `/v1/health`") passing.
    """
    chart = _probe_paths(_CHART_DEPLOYMENT)
    if any(p == "/v1/health" for p in chart.values()):
        return  # A probe really does read it; the pairing would be true.

    section = _unreleased_section()
    assert _COUNT_BASED.search(section) and _PROBE_TOKEN.search(section), (
        "the release section no longer mentions both `/v1/health` and a "
        "<kind>Probe token, so this guard is watching nothing. Delete it "
        "rather than leaving it green over an empty scan."
    )

    offenders = [
        s.strip()
        for s in _sentences(section)
        if _COUNT_BASED.search(s) and _PROBE_TOKEN.search(s)
    ]
    assert not offenders, (
        "these sentences tie a named probe to `/v1/health`, which no probe in "
        f"the shipped chart reads (it polls {sorted(set(chart.values()))}). A "
        "reader copying probes out of the notes would put one back on the "
        "count-based endpoint, where a single unloadable recipe stops every "
        "new pod from starting:\n  " + "\n  ".join(offenders)
    )
