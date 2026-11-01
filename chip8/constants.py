"""The machine's dimensions, addresses and its two fonts.

Everything here is a number the 1977 machine has and the SuperChip additions
that this implementation also accepts. The values are the ones Time Warp
Studio's CHIP-8 machine uses, so a program assembled for either runs the same
way on the other.
"""

from __future__ import annotations

SCREEN_WIDTH = 64
SCREEN_HEIGHT = 32
#: SuperChip hi-res: exactly twice the original in each direction, so the drawn
#: canvas keeps its 2:1 shape and a hi-res pixel is half the size.
HI_RES_WIDTH = 128
HI_RES_HEIGHT = 64
#: How many host pixels one CHIP-8 pixel becomes when nothing else is asked for.
PIXEL_SCALE = 8

MEMORY_SIZE = 4096
FONT_ADDRESS = 0x50
#: Ten bytes per digit: the 8x10 face ``LD F, Vx`` points at in SuperChip,
#: right after the 4x5 set.
LARGE_FONT_ADDRESS = FONT_ADDRESS + 16 * 5
PROGRAM_ADDRESS = 0x200
#: How many bytes a binary ROM may occupy: memory from 0x200 to 0xFFF.
ROM_LIMIT = MEMORY_SIZE - PROGRAM_ADDRESS

FRAMES_PER_SECOND = 60
#: Ten opcodes a frame is the original interpreter's pace, and the pace at
#: which 1977 programs were written to look right.
CYCLES_PER_FRAME = 10
MIN_SPEED = 1
#: Fast enough for a listing that computes, still bounded so one frame cannot
#: monopolise the frontend.
MAX_SPEED = 2000
#: The whole-program run's limits, used by ``--headless``.
MAX_FRAMES = 600
MAX_STEPS = 1_000_000

#: One rewind snapshot every quarter of a second: fine enough to step through
#: the moment something went wrong, coarse enough that a minute of play stays
#: small.
REWIND_STRIDE = 15
#: How many are kept: thirty seconds of history, oldest dropped first.
REWIND_SNAPSHOTS = 120

#: The 4x5 font, sixteen glyphs of five bytes. Each row is held in the high
#: nibble so that ``DRW`` reads a glyph as an ordinary 8-pixel-wide sprite.
FONT = bytes(
    (
        0xF0, 0x90, 0x90, 0x90, 0xF0,  # 0
        0x20, 0x60, 0x20, 0x20, 0x70,  # 1
        0xF0, 0x10, 0xF0, 0x80, 0xF0,  # 2
        0xF0, 0x10, 0xF0, 0x10, 0xF0,  # 3
        0x90, 0x90, 0xF0, 0x10, 0x10,  # 4
        0xF0, 0x80, 0xF0, 0x10, 0xF0,  # 5
        0xF0, 0x80, 0xF0, 0x90, 0xF0,  # 6
        0xF0, 0x10, 0x20, 0x40, 0x40,  # 7
        0xF0, 0x90, 0xF0, 0x90, 0xF0,  # 8
        0xF0, 0x90, 0xF0, 0x10, 0xF0,  # 9
        0xF0, 0x90, 0xF0, 0x90, 0x90,  # A
        0xE0, 0x90, 0xE0, 0x90, 0xE0,  # B
        0xF0, 0x80, 0x80, 0x80, 0xF0,  # C
        0xE0, 0x90, 0x90, 0x90, 0xE0,  # D
        0xF0, 0x80, 0xF0, 0x80, 0xF0,  # E
        0xF0, 0x80, 0xF0, 0x80, 0x80,  # F
    )
)


def _build_large_font() -> bytes:
    """The 8x10 digits that SuperChip's ``LD F, Vx`` points at.

    Derived from the 4x5 set by doubling every row and every column rather than
    embedded, so the two faces cannot disagree about a digit. Only 0-9 exist:
    ten glyphs of ten bytes, which is what the reference machine fills.
    """

    font = bytearray()
    for digit in range(10):
        for row in range(5):
            bits = FONT[digit * 5 + row]
            doubled = 0
            for column in range(4):
                if bits & (0x80 >> column):
                    doubled |= 0xC0 >> (column * 2)
            font += bytes((doubled, doubled))
    return bytes(font)


LARGE_FONT = _build_large_font()

#: The two colours, as the CHIP-8 window draws them.
ON_COLOR = (224, 248, 208)
OFF_COLOR = (10, 10, 20)

#: The keypad, in key order 0-F, laid out as the original had it.
KEY_LABELS = (
    "1", "2", "3", "C",
    "4", "5", "6", "D",
    "7", "8", "9", "E",
    "A", "0", "B", "F",
)
