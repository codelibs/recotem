"""Log-safety helpers shared across recotem subpackages.

Lives at the top level so neither ``recotem.artifact`` nor ``recotem.serving``
needs to import the other.  Centralises kid sanitisation so escape sequences,
ANSI codes, or other ambiguous characters that happen to survive UTF-8 decode
do not reach an operator's terminal when log lines are tailed live.
"""

from __future__ import annotations

import string

_SAFE_KID_CHARS = frozenset(string.ascii_letters + string.digits + "_-.:")
_MAX_KID_LOG_LEN = 64


def format_kid_for_log(kid: str | bytes | bytearray) -> str:
    """Return a representation of ``kid`` safe to embed in log fields.

    Accepts ``str``, ``bytes``, or ``bytearray`` input (bytes are decoded
    with ``errors="replace"``).  Replaces any character outside
    ``[A-Za-z0-9_-.:] with its ``\\xHH`` escape and truncates to 64 chars.
    Empty / null input renders as ``"<empty>"``.

    Two defences applied (S-4):

    1. **Non-safe-char scrub** — replaces characters outside the safe set with
       their ``\\xHH`` hex escape to prevent terminal ANSI injection and log
       aggregation confusion.  More informative than simple ``?``-replacement
       because the original bytes remain recoverable.
    2. **Length cap** — truncates to ``_MAX_KID_LOG_LEN`` characters and
       appends ``...`` so log lines stay bounded regardless of what a
       malicious artifact embeds in the kid field.
    """
    if isinstance(kid, (bytes, bytearray)):
        kid_str = kid.decode("utf-8", errors="replace")
    else:
        kid_str = str(kid) if kid is not None else ""

    if not kid_str:
        return "<empty>"

    out = []
    for ch in kid_str[:_MAX_KID_LOG_LEN]:
        if ch in _SAFE_KID_CHARS:
            out.append(ch)
        else:
            out.append(f"\\x{ord(ch):02x}")
    s = "".join(out)
    if len(kid_str) > _MAX_KID_LOG_LEN:
        s += "..."
    return s
