"""A COSMAC VIP emulator with a complete CHIP-8 system.

The 1977 virtual machine and its assembler, on the board it was written for:
an emulated CDP1802 running the CHIP-8 interpreter as firmware. The machine
implements the original behavior and SuperChip extensions; see ``docs/`` for
what it does where emulators disagree.

    from chip8 import Chip8Emulator

    emulator = Chip8Emulator()
    emulator.load_file("examples/bounce.ch8")
    emulator.start()
    for _ in range(120):
        emulator.advance_frame()

``VipEmulator`` is the same program on the computer CHIP-8 was written for: an
emulated COSMAC VIP, running the 1977 interpreter as CDP1802 firmware. It
answers the same interface, so a frontend written for one runs the other.

    from chip8 import VipEmulator

    emulator = VipEmulator()
    emulator.load_file("examples/bounce.ch8")
    emulator.start()
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

#: The VIP is imported on first use: importing it assembles its firmware, which
#: nobody who only wants the native machine should pay for.
_LAZY = {
    "Cdp1802": "cosmac1802",
    "Vip": "vip",
    "VipEmulator": "vip",
    "VipMemory": "vip",
    "VipProgram": "vipasm",
    "assemble_vip": "vipasm",
}


def __getattr__(name: str):
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{module}", __name__), name)

__all__ = [
    "Cdp1802",
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
    "Vip",
    "VipEmulator",
    "VipMemory",
    "VipProgram",
    "assemble_chip8",
    "assemble_vip",
    "load_program",
    "load_program_file",
]
