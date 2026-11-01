"""The text an inspector shows, shared by every frontend.

The window version of this is three tabs -- Registers, Memory and Listing. Here
they are three blocks of fixed-width text, so the terminal frontend can put them
beside the screen and ``--headless --inspect`` can print them after a run.
"""

from __future__ import annotations

from .assembler import Chip8Program
from .constants import MEMORY_SIZE
from .emulator import Chip8Snapshot

#: How wide an inspector block is, in characters.
INSPECTOR_WIDTH = 38


def status_text(snapshot: Chip8Snapshot, state_name: str) -> str:
    """The status line: state, program counter, cycles, speed."""

    glyph = {
        "running": "\u25b6",
        "paused": "\u25aa",
        "halted": "\u25a0",
        "waiting for a key": "\u2318",
        "error": "\u2716",
        "open a program": "\u25cb",
    }.get(state_name, "\u25cb")
    text = (
        f"{glyph} {state_name}   PC 0x{snapshot.pc:03X} \u00b7 "
        f"{snapshot.cycles} cycles \u00b7 {snapshot.speed} opcodes/frame "
        f"({snapshot.speed * 60} Hz)"
    )
    if snapshot.delay_timer or snapshot.sound_timer:
        text += f" \u00b7 DT {snapshot.delay_timer} ST {snapshot.sound_timer}"
    if snapshot.rewind_position >= 0:
        text += f" \u00b7 rewind {snapshot.rewind_position + 1}/{snapshot.rewind_count}"
    return text


def registers_text(snapshot: Chip8Snapshot) -> list[str]:
    """``V0``-``VF``, ``I``, ``PC``, the timers, the counters and the stack."""

    lines = ["Registers"]
    for row in range(4):
        cells = []
        for column in range(4):
            index = row * 4 + column
            cells.append(f"V{index:X} {snapshot.v[index]:02X}")
        lines.append("  ".join(cells))
    lines.append(f"I   0x{snapshot.i:03X}   PC 0x{snapshot.pc:03X}")
    lines.append(f"DT  {snapshot.delay_timer:<3d}  ST  {snapshot.sound_timer:<3d}")
    lines.append(f"cycles {snapshot.cycles}  hits {snapshot.collisions}")
    held = [f"{index:X}" for index in range(16) if snapshot.keys & (1 << index)]
    lines.append(f"keys {' '.join(held) if held else '-'}")
    if snapshot.stack:
        # Newest call first: the top of the stack is what RET goes back to.
        stack = " ".join(f"{address:03X}" for address in reversed(snapshot.stack[-4:]))
        depth = len(snapshot.stack)
        lines.append(f"stack ({depth}) {stack}")
    else:
        lines.append("stack (0)")
    return lines


def memory_text(
    snapshot: Chip8Snapshot, base: int | None = None, rows: int = 8
) -> list[str]:
    """The 4 KiB, eight bytes a row, around whichever page is asked for."""

    memory = snapshot.memory
    if base is None:
        # Open on the pager the program counter is in: that is the page with
        # the program on it, which is the one being debugged.
        base = max(0, (snapshot.pc // 0x80) * 0x80)
    base = max(0, min(base, MEMORY_SIZE - rows * 8))
    lines = ["Memory"]
    for row in range(rows):
        start = base + row * 8
        data = memory[start : start + 8]
        cells = " ".join(f"{byte:02X}" for byte in data)
        lines.append(f"{start:03X} {cells}")
    return lines


def listing_text(
    snapshot: Chip8Snapshot,
    program: Chip8Program | None,
    source: bytes,
    context: int = 3,
) -> list[str]:
    """The listing around the instruction the program counter is on."""

    if program is None or program.binary or not program.source_lines:
        return []
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        return []
    lines = text.split("\n")

    # The statement that owns this address: the highest one at or below the PC.
    owners = sorted(program.source_lines)
    current = None
    for address in owners:
        if address > snapshot.pc:
            break
        current = program.source_lines[address]
    if current is None:
        current = program.source_lines[owners[0]]

    first = max(1, current - context)
    last = min(len(lines), current + context)
    out = ["Listing"]
    for number in range(first, last + 1):
        pointer = ">" if number == current else " "
        out.append(f"{pointer}{number:>4} {lines[number - 1][: INSPECTOR_WIDTH - 6]}")
    return out


def hex_dump(data: bytes, base: int = 0, width: int = 16) -> list[str]:
    """Address, hex bytes and printable bytes, for ``chip8 asm`` and ``info``."""

    lines = []
    for offset in range(0, len(data), width):
        chunk = data[offset : offset + width]
        cells = " ".join(f"{byte:02X}" for byte in chunk)
        text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{base + offset:04X}  {cells:<{width * 3 - 1}}  |{text}|")
    return lines
