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
