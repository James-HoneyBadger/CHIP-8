"""Turning a framebuffer into characters, with no terminal involved.

Two CHIP-8 pixels share one character cell -- the upper half block carries the
top pixel and the lower half block the bottom -- so the 64x32 screen is 64
columns by 16 rows and the 128x64 hi-res screen is 128 by 32. That is small
enough to print, which is what ``chip8 run --show`` does, and it is how a ROM
can be looked at in a test.

Nothing here touches a terminal, so it works the same in a pipe, a test and the
frontend that draws it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .constants import OFF_COLOR, ON_COLOR
from .emulator import Chip8Snapshot

#: The glyph for each (top lit, bottom lit) pair.
_ASCII_GLYPHS = {
    (True, True): "\u2588",
    (True, False): "\u2580",
    (False, True): "\u2584",
    (False, False): " ",
}
_COLOURED_GLYPHS = {
    (True, True): " ",
    (True, False): "\u2580",
    (False, True): "\u2584",
    (False, False): " ",
}


@dataclass(frozen=True)
class Palette:
    """The two colours, as the escape sequences one character cell needs."""

    mode: str = "truecolor"

    def glyph(self, key: tuple[bool, bool]) -> str:
        if self.mode == "ascii":
            return _ASCII_GLYPHS[key]
        return _COLOURED_GLYPHS[key]

    def prefix(self, key: tuple[bool, bool]) -> str:
        """The colours a cell sets before its glyph. In ascii mode, none."""

        if self.mode == "ascii":
            return ""
        top, bottom = key
        if self.mode == "256":
            foreground = "\x1b[38;5;194m" if top else "\x1b[38;5;234m"
            background = "\x1b[48;5;194m" if bottom else "\x1b[48;5;234m"
            return foreground + background
        on, off = ON_COLOR, OFF_COLOR
        foreground = on if top else off
        background = on if bottom else off
        return (
            f"\x1b[38;2;{foreground[0]};{foreground[1]};{foreground[2]}m"
            f"\x1b[48;2;{background[0]};{background[1]};{background[2]}m"
        )

    @property
    def reset(self) -> str:
        return "" if self.mode == "ascii" else "\x1b[0m"


def render_display(
    snapshot: Chip8Snapshot, scale: int = 1, palette: Palette | None = None
) -> list[str]:
    """The framebuffer as text lines: one cell per two pixel rows.

    A run of cells of the same two colours shares one escape sequence, so a
    frame that changed a corner does not rewrite the colour of all sixteen rows.
    """

    width = snapshot.screen_width
    height = snapshot.screen_height
    framebuffer = snapshot.framebuffer
    palette = palette or Palette()
    scale = max(1, scale)
    lines: list[str] = []
    for cell_row in range((height + 1) // 2):
        top_row = cell_row * 2
        bottom_row = min(top_row + 1, height - 1)
        parts: list[str] = []
        previous: tuple[bool, bool] | None = None
        for column in range(width):
            key = (
                framebuffer[top_row * width + column] != 0,
                framebuffer[bottom_row * width + column] != 0,
            )
            if key != previous:
                parts.append(palette.prefix(key))
                previous = key
            parts.append(palette.glyph(key) * scale)
        parts.append(palette.reset)
        line = "".join(parts)
        lines.extend([line] * scale)
    return lines


def ascii_screen(snapshot: Chip8Snapshot) -> str:
    """The screen as plain half-block characters, ready to print."""

    return "\n".join(render_display(snapshot, 1, Palette("ascii")))
