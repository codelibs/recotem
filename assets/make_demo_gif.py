#!/usr/bin/env python3
"""Render the README terminal demo GIF (assets/demo.gif).

Self-contained, no system deps beyond Pillow and a monospace TTF. The
content mirrors a real `examples/quickstart/` run (60 users / 20 items /
849 rows, TopPop, score 0.691) and the real recommend response, so the
GIF stays truthful to what the quickstart actually prints.

Regenerate:
    uv run --with pillow python assets/make_demo_gif.py
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

# --- geometry -------------------------------------------------------------
SCALE = 2
FONT_SIZE = 15 * SCALE
LINE_H = 23 * SCALE
PAD_X = 22 * SCALE
PAD_TOP = 16 * SCALE
PAD_BOTTOM = 16 * SCALE
TITLE_H = 34 * SCALE
COLS = 64  # visible columns; width is derived from this

# --- palette (GitHub dark) ------------------------------------------------
BG = (13, 17, 23)
TITLEBAR = (22, 27, 34)
BORDER = (48, 54, 61)
FG = (230, 237, 243)
PROMPT = (63, 185, 80)
GREEN = (63, 185, 80)
CYAN = (121, 192, 255)
BLUE = (88, 166, 255)
GRAY = (139, 148, 158)
YELLOW = (242, 204, 96)
STRING = (165, 214, 255)
KEY = (121, 192, 255)
PUNCT = (139, 148, 158)
DOT_R = (255, 95, 86)
DOT_Y = (255, 189, 46)
DOT_G = (39, 201, 63)
CURSOR = (88, 166, 255)

FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Monaco.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def load_font() -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, FONT_SIZE)
    return ImageFont.load_default()


FONT = load_font()
CHAR_W = int(round(FONT.getlength("M")))
WIDTH = PAD_X * 2 + CHAR_W * COLS


# --- demo script ----------------------------------------------------------
# Each "out" line is a list of (text, color) segments.
def seg(*pairs):
    return list(pairs)


PROMPT_SEG = ("$ ", PROMPT)

SCRIPT = [
    ("cmd", "recotem train examples/quickstart/recipe.yaml"),
    (
        "out",
        seg(
            ("  ✓ ", GREEN),
            ("csv source", FG),
            ("  60 users · 20 items · 849 rows", GRAY),
        ),
    ),
    (
        "out",
        seg(
            ("  ✓ ", GREEN),
            ("tuned ", FG),
            ("TopPop", CYAN),
            (" · 40 trials · score ", FG),
            ("0.691", YELLOW),
        ),
    ),
    (
        "out",
        seg(
            ("  ✓ ", GREEN),
            ("artifact → ", FG),
            ("top_picks.recotem", CYAN),
            ("  (HMAC-signed)", GRAY),
        ),
    ),
    ("blank",),
    ("hold", 500),
    ("cmd", "recotem serve --recipes examples/quickstart/"),
    (
        "out",
        seg(
            ("  ✓ ", GREEN),
            ("http://localhost:8080", BLUE),
            ("  ·  1 recipe · hot-swap on", GRAY),
        ),
    ),
    ("blank",),
    ("hold", 500),
    ("cmd", "curl -sX POST localhost:8080/v1/recipes/top_picks:recommend \\"),
    ("cmd_cont", '     -H "X-API-Key: ***" -d \'{"user_id":"u01","limit":3}\''),
    ("out", seg(("{", PUNCT))),
    (
        "out",
        seg(('  "recipe"', KEY), (": ", PUNCT), ('"top_picks"', STRING), (",", PUNCT)),
    ),
    ("out", seg(('  "items"', KEY), (": [", PUNCT))),
    (
        "out",
        seg(
            ('    { "item_id"', KEY),
            (": ", PUNCT),
            ('"i10"', STRING),
            (', "score"', KEY),
            (": ", PUNCT),
            ("50.0", YELLOW),
            (" },", PUNCT),
        ),
    ),
    (
        "out",
        seg(
            ('    { "item_id"', KEY),
            (": ", PUNCT),
            ('"i03"', STRING),
            (', "score"', KEY),
            (": ", PUNCT),
            ("50.0", YELLOW),
            (" },", PUNCT),
        ),
    ),
    (
        "out",
        seg(
            ('    { "item_id"', KEY),
            (": ", PUNCT),
            ('"i06"', STRING),
            (', "score"', KEY),
            (": ", PUNCT),
            ("48.0", YELLOW),
            (" }", PUNCT),
        ),
    ),
    ("out", seg(("  ]", PUNCT))),
    ("out", seg(("}", PUNCT))),
    ("hold", 2200),
]

# rows shown = number of content lines the terminal displays (fixed height)
ROWS = 20
CANVAS_H = TITLE_H + PAD_TOP + ROWS * LINE_H + PAD_BOTTOM


def base_image() -> Image.Image:
    img = Image.new("RGB", (WIDTH, CANVAS_H), BG)
    d = ImageDraw.Draw(img)
    # title bar
    d.rectangle([0, 0, WIDTH, TITLE_H], fill=TITLEBAR)
    d.line([0, TITLE_H, WIDTH, TITLE_H], fill=BORDER, width=1)
    cy = TITLE_H // 2
    r = 6 * SCALE // 2 + 2
    x0 = PAD_X
    for i, c in enumerate((DOT_R, DOT_Y, DOT_G)):
        cx = x0 + i * (r * 2 + 6 * SCALE // 2 + 4)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
    title = "recotem — quickstart"
    tw = FONT.getlength(title)
    d.text(((WIDTH - tw) / 2, cy - FONT_SIZE / 2 - SCALE), title, font=FONT, fill=GRAY)
    return img


def draw_lines(lines, typing=None, cursor=False) -> Image.Image:
    """lines: list of segment-lists. typing: (segments, ncols_shown) for the
    in-progress last line. cursor: draw a block cursor after content."""
    img = base_image()
    d = ImageDraw.Draw(img)
    y = TITLE_H + PAD_TOP
    render = list(lines)
    if typing is not None:
        render = render + [typing]
    # keep only the last ROWS lines (scroll)
    render = render[-ROWS:]
    last_x = PAD_X
    last_y = y
    for segs in render:
        x = PAD_X
        for text, color in segs:
            if text:
                d.text((x, y), text, font=FONT, fill=color)
                x += CHAR_W * len(text)
        last_x, last_y = x, y
        y += LINE_H
    if cursor:
        cy = last_y
        d.rectangle(
            [last_x + 1, cy + 2, last_x + CHAR_W - 1, cy + FONT_SIZE + 2], fill=CURSOR
        )
    return img


def main() -> int:
    frames: list[Image.Image] = []
    durations: list[int] = []
    lines: list[list] = []

    def push(img, ms):
        frames.append(img)
        durations.append(ms)

    for step in SCRIPT:
        kind = step[0]
        if kind in ("cmd", "cmd_cont"):
            text = step[1]
            prefix = PROMPT_SEG if kind == "cmd" else ("  ", FG)
            # typing animation, ~3 chars/frame
            chunk = 3
            n = len(text)
            i = 0
            while i < n:
                i = min(n, i + chunk)
                typing = [prefix, (text[:i], FG)]
                push(draw_lines(lines, typing=typing, cursor=True), 45)
            # hold the completed command briefly
            push(draw_lines(lines, typing=[prefix, (text, FG)], cursor=True), 350)
            lines.append([prefix, (text, FG)])
        elif kind == "out":
            lines.append(step[1])
            push(draw_lines(lines), 110)
        elif kind == "blank":
            lines.append([])
            push(draw_lines(lines), 90)
        elif kind == "hold":
            if frames:
                push(frames[-1].copy(), step[1])

    # scale down to final display size (crisper text via supersample)
    final_w = WIDTH // SCALE
    final_h = CANVAS_H // SCALE
    frames = [f.resize((final_w, final_h), Image.LANCZOS) for f in frames]

    # quantize to a shared adaptive palette for small, clean GIF
    pal = frames[0].convert("P", palette=Image.ADAPTIVE, colors=96)
    q = [f.convert("RGB").quantize(palette=pal, dither=Image.NONE) for f in frames]

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.gif")
    # disposal=1 (leave prior frame in place) lets Pillow's optimizer store
    # only the changed bounding box per frame. Safe here because the content
    # never scrolls or erases — it only fills in downward and to the right.
    q[0].save(
        out,
        save_all=True,
        append_images=q[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    size = os.path.getsize(out)
    print(f"wrote {out}")
    print(
        f"frames={len(q)} size={final_w}x{final_h} bytes={size} ({size / 1024:.0f} KiB)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
