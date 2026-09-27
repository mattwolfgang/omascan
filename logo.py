"""The OMASCAN title banner: a compact block-letter font in the style of the
Omarchy logo (thick strokes, rounded half-block corners, the bump on the M)."""

_GLYPHS = {
    "O": [
        "         ",
        " ▄█████▄ ",
        "███   ███",
        "███   ███",
        "███   ███",
        " ▀█████▀ ",
    ],
    "M": [
        "      ▄▄▄      ",
        " ▄███████████▄ ",
        "███   ███   ███",
        "███   ███   ███",
        "███   ███   ███",
        " ▀█   ███   █▀ ",
    ],
    "A": [
        "          ",
        "  ▄███████",
        " ███   ███",
        "▄███▄▄▄███",
        "▀███▀▀▀███",
        " ███   █▀ ",
    ],
    "S": [
        "          ",
        " ▄████████",
        "███▄▄▄▄   ",
        " ▀▀▀▀▀███▄",
        "▄█    ███ ",
        "▀██████▀  ",
    ],
    "C": [
        "          ",
        " ▄███████ ",
        "███   █▀  ",
        "███       ",
        "███   █▄  ",
        " ▀██████▀ ",
    ],
    "N": [
        "          ",
        "███▄▄▄▄   ",
        "███▀▀▀██▄ ",
        "███   ███ ",
        "███   ███ ",
        " ▀█   █▀  ",
    ],
}


def banner(text: str = "OMASCAN", gap: int = 1) -> str:
    glyphs = [_GLYPHS[ch] for ch in text]
    widths = [max(len(row) for row in g) for g in glyphs]
    rows = []
    for i in range(len(glyphs[0])):
        rows.append((" " * gap).join(g[i].ljust(w) for g, w in zip(glyphs, widths)).rstrip())
    # Pad every row to the same width so centering the block can't shift rows
    # relative to each other.
    width = max(len(row) for row in rows)
    return "\n".join(row.ljust(width) for row in rows)


BANNER = banner()
BANNER_WIDTH = max(len(row) for row in BANNER.splitlines())

if __name__ == "__main__":
    print(BANNER)
    print(BANNER_WIDTH)
