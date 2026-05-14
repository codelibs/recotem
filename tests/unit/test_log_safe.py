"""Unit tests for recotem._log_safe.format_kid_for_log.

N-11 / OBS-3: format_kid_for_log is directly importable from recotem._log_safe
and satisfies the following contract:
- Empty input renders as '<empty>'
- Safe characters (A-Za-z0-9_-.:) are preserved as-is
- Control characters and non-safe characters are replaced with \\xHH escapes
- Output is truncated at 64 characters + '...' suffix for long inputs
- bytes / bytearray inputs are accepted (decoded with errors='replace')
"""

from __future__ import annotations

from recotem._log_safe import format_kid_for_log

# ---------------------------------------------------------------------------
# Basic contract tests
# ---------------------------------------------------------------------------


def test_format_kid_for_log_empty_string_renders_placeholder() -> None:
    """An empty string must render as '<empty>' (not an empty string)."""
    assert format_kid_for_log("") == "<empty>"


def test_format_kid_for_log_none_like_string_is_empty() -> None:
    """str(None) is 'None', not '' — but a genuinely empty string is '<empty>'."""
    result = format_kid_for_log("")
    assert result == "<empty>"


def test_format_kid_for_log_safe_chars_preserved() -> None:
    """Characters in [A-Za-z0-9_-.:]  must not be modified."""
    safe = "kid-1_v2.prod:active"
    assert format_kid_for_log(safe) == safe


def test_format_kid_for_log_preserves_alphanumeric() -> None:
    """Pure alphanumeric kid is returned unchanged."""
    kid = "myRecipeKey123"
    assert format_kid_for_log(kid) == kid


def test_format_kid_for_log_escapes_control_chars() -> None:
    """Control characters must be replaced with \\xHH escapes, not passed through."""
    s = format_kid_for_log("a\x1bb\x00c")
    assert "\x1b" not in s, "Raw ESC must not appear in output"
    assert "\x00" not in s, "Raw NUL must not appear in output"
    assert r"\x1b" in s, "ESC must appear as \\x1b escape"
    assert r"\x00" in s, "NUL must appear as \\x00 escape"


def test_format_kid_for_log_escapes_tab() -> None:
    """Tab (\\x09) is outside the safe set and must be escaped."""
    s = format_kid_for_log("a\tb")
    assert "\t" not in s
    assert r"\x09" in s


def test_format_kid_for_log_escapes_ansi_sequence() -> None:
    """An ANSI colour escape sequence must be fully neutralised."""
    ansi_kid = "\x1b[31mred\x1b[0m"
    result = format_kid_for_log(ansi_kid)
    assert "\x1b" not in result
    assert r"\x1b" in result
    # Printable letters in the sequence that are in the safe set are kept
    assert "red" in result


def test_format_kid_for_log_truncates_long_input() -> None:
    """Inputs longer than 64 characters must be truncated and suffixed with '...'."""
    long_kid = "a" * 200
    result = format_kid_for_log(long_kid)
    assert result.endswith("..."), f"Long input must end with '...'; got {result!r}"
    # Total length must be at most 64 + len('...')
    assert len(result) <= 64 + len("..."), (
        f"Truncated result must not exceed 64+3 chars; got len={len(result)}"
    )


def test_format_kid_for_log_exactly_64_chars_not_truncated() -> None:
    """An input of exactly 64 safe characters must not gain a '...' suffix."""
    kid = "a" * 64
    result = format_kid_for_log(kid)
    assert result == kid, f"64-char input must not be truncated; got {result!r}"


def test_format_kid_for_log_65_chars_truncated() -> None:
    """An input of 65 safe characters must be truncated to 64 + '...'."""
    kid = "b" * 65
    result = format_kid_for_log(kid)
    assert result.endswith("..."), "65-char input must be truncated"
    assert result == "b" * 64 + "..."


# ---------------------------------------------------------------------------
# bytes / bytearray input
# ---------------------------------------------------------------------------


def test_format_kid_for_log_accepts_bytes_input() -> None:
    """bytes input must be decoded and returned as a str."""
    result = format_kid_for_log(b"abc-123")
    assert isinstance(result, str)
    assert result == "abc-123"


def test_format_kid_for_log_accepts_bytearray_input() -> None:
    """bytearray input must be decoded and returned as a str."""
    result = format_kid_for_log(bytearray(b"key_v1"))
    assert isinstance(result, str)
    assert result == "key_v1"


def test_format_kid_for_log_bytes_with_invalid_utf8_uses_replacement() -> None:
    """Invalid UTF-8 bytes must use 'replace' error handling, not raise."""
    invalid_utf8 = b"\xff\xfe"
    result = format_kid_for_log(invalid_utf8)
    # Must not raise; result is a str (replacement chars are in the output)
    assert isinstance(result, str)
    # Must not be empty (unless replacement chars happen to be outside safe set
    # and the truncation left nothing — but replacement chars are U+FFFD which
    # is outside the safe set so they get hex-escaped)
    assert len(result) > 0


def test_format_kid_for_log_empty_bytes_renders_placeholder() -> None:
    """Empty bytes must render as '<empty>'."""
    assert format_kid_for_log(b"") == "<empty>"
