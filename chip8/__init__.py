"""A CHIP-8 emulator: the 1977 virtual machine and its assembler.

The machine is a port of Time Warp Studio's CHIP-8
(``native/src/lang/chip8.cpp``), quirks included; see ``docs/`` for what it does
where emulators disagree.

    from chip8 import Chip8Emulator

    emulator = Chip8Emulator()
    emulator.load_file("examples/bounce.ch8")
    emulator.start()
    for _ in range(120):
        emulator.advance_frame()
"""

from __future__ import annotations

from .assembler import Chip8Program, assemble_chip8, load_program, load_program_file
from .constants import (
    CYCLES_PER_FRAME,
    FRAMES_PER_SECOND,
    HI_RES_HEIGHT,
    HI_RES_WIDTH,
    MEMORY_SIZE,
    PROGRAM_ADDRESS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
)
from .emulator import Chip8Emulator, Chip8Snapshot, FrameClock
from .machine import Chip8Error, Chip8Machine, Chip8State

__version__ = "1.0.0"

__all__ = [
    "Chip8Emulator",
    "Chip8Error",
    "Chip8Machine",
    "Chip8Program",
    "Chip8Snapshot",
    "Chip8State",
    "CYCLES_PER_FRAME",
    "FRAMES_PER_SECOND",
    "FrameClock",
    "HI_RES_HEIGHT",
    "HI_RES_WIDTH",
    "MEMORY_SIZE",
    "PROGRAM_ADDRESS",
    "SCREEN_HEIGHT",
    "SCREEN_WIDTH",
    "assemble_chip8",
    "load_program",
    "load_program_file",
]
