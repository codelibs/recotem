"""`docs/getting-started.md` must declare every tool its own commands invoke.

The page is the adoption front door, and its Prerequisites used to read
"Either Docker (with the Compose plugin) **or** Python 3.12+" -- while every
verification step in both paths runs `curl`, and three of them pipe into `jq`.
Neither is present in a bare `python:3.12-slim`, which is exactly the
environment Path B describes ("everything in your venv", no checkout).  Run
there verbatim, the page trains a model and then dies at::

    $ curl -sX POST http://127.0.0.1:8080/v1/recipes/purchase_log:recommend ...
    bash: curl: command not found

with nothing on the page to explain why.  A missing prerequisite is invisible
until the reader hits it, so the check is that the page's *own* command blocks
cannot name a tool the page does not list.

Scope: shell builtins and coreutils are assumed (a reader who lacks `cd` cannot
run the page at all), and `recotem` itself is installed by the walkthrough.
Everything else has to be declared.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
GETTING_STARTED = REPO_ROOT / "docs" / "getting-started.md"

# POSIX shell builtins and coreutils.  A reader without these cannot run any
# shell walkthrough, so listing them would be noise rather than information.
_SHELL_BASELINE = frozenset({"cat", "cd", "export", "grep", "mkdir", "sed"})

# Installed by the page's own steps, so it cannot be a prerequisite for them.
_INSTALLED_BY_THE_PAGE = frozenset({"recotem"})

_BASH_BLOCK = re.compile(r"^```bash\n(.*?)^```", re.DOTALL | re.MULTILINE)
_HEREDOC_OPEN = re.compile(r"<<'?(\w+)'?")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]*\Z")


def _prerequisites_section() -> str:
    text = GETTING_STARTED.read_text(encoding="utf-8")
    match = re.search(r"^## Prerequisites\n(.*?)^## ", text, re.DOTALL | re.MULTILINE)
    assert match is not None, (
        "docs/getting-started.md has no '## Prerequisites' section. It is the "
        "only place the page states what a reader needs before starting."
    )
    return match.group(1)


def _invoked_tools() -> set[str]:
    """Every command name the page's ```bash blocks invoke.

    Heredoc bodies are skipped: the recipe the page writes with
    `cat > recipes/purchase_log.yaml <<'EOF'` is YAML, and its keys would
    otherwise be read as command names.
    """
    tools: set[str] = set()
    for block in _BASH_BLOCK.findall(GETTING_STARTED.read_text(encoding="utf-8")):
        lines = block.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            index += 1
            opened = _HEREDOC_OPEN.search(line)
            if opened is not None:
                terminator = opened.group(1)
                while index < len(lines) and lines[index].strip() != terminator:
                    index += 1
                index += 1
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            for segment in re.split(r"\|\||&&|[|;]", stripped):
                segment = segment.strip().lstrip("$( ")
                if not segment:
                    continue
                word = segment.split()[0]
                if _WORD.match(word):
                    tools.add(word)
    return tools


def test_the_command_scan_still_finds_commands() -> None:
    """An empty scan is a failure, not a pass.

    If the fenced-block or heredoc parsing above stops matching, every
    assertion below passes vacuously while the page is free to invoke anything.
    `curl` is the sentinel because it is what the page uses to show the reader
    that each step worked -- if the page ever stops using it, this test should
    be revisited deliberately rather than quietly going green.
    """
    tools = _invoked_tools()
    assert len(tools) >= 5, (
        f"only {sorted(tools)} were parsed out of docs/getting-started.md's "
        "```bash blocks. The parser has stopped matching the page, which would "
        "make the prerequisite check below vacuous."
    )
    assert "curl" in tools, (
        "docs/getting-started.md no longer invokes `curl` in any command block. "
        "Either the parser broke or the page changed shape; check which before "
        "relaxing this."
    )


def test_every_invoked_tool_is_a_stated_prerequisite() -> None:
    prerequisites = _prerequisites_section().lower()
    undeclared = sorted(
        tool
        for tool in _invoked_tools()
        if tool not in _SHELL_BASELINE
        and tool not in _INSTALLED_BY_THE_PAGE
        and tool.lower() not in prerequisites
    )
    assert not undeclared, (
        f"docs/getting-started.md runs {undeclared} but its Prerequisites "
        "section does not name them. A reader on a minimal box (the bare "
        "`python:3.12-slim` Path B describes) hits `command not found` partway "
        "through with nothing on the page to explain it. Add the tool to "
        "Prerequisites, or to _SHELL_BASELINE if it really is universal."
    )


def test_the_baseline_exemption_does_not_cover_the_verification_tools() -> None:
    """`curl` and `jq` must never be waved through as "universal".

    They are the reason this file exists: they are what the page uses to check
    its own results, and neither ships in the container Path B describes.
    Adding them to the exemption list would turn the test above green while
    restoring exactly the gap it was written for.
    """
    waved_through = _SHELL_BASELINE & {"curl", "jq", "docker"}
    assert not waved_through, (
        f"{sorted(waved_through)} is exempted as a shell baseline. It is not: "
        "a bare python:3.12-slim has none of them, and they are how the page "
        "verifies every step."
    )


def test_path_b_says_serve_blocks_the_terminal() -> None:
    """Path B step 5 needs a shell that step 4 has not taken over.

    Path A detaches explicitly (`docker compose up -d serve`); Path B's
    `recotem serve --recipes recipes/` runs in the foreground and said nothing,
    so a reader following it literally has no prompt left to run step 5 from.
    """
    text = GETTING_STARTED.read_text(encoding="utf-8")
    match = re.search(
        r"^### 4\. Serve\n(.*?)^### 5\. Recommend", text, re.DOTALL | re.MULTILINE
    )
    assert match is not None, (
        "docs/getting-started.md Path B no longer has a '### 4. Serve' step "
        "followed by '### 5. Recommend'."
    )
    step = match.group(1).lower()
    assert "second terminal" in step or "foreground" in step, (
        "Path B's Serve step does not say that `recotem serve` holds the "
        "terminal. Step 5 is a curl that needs another shell, and Path A says "
        "`up -d` where this says nothing."
    )
