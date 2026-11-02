"""A COSMAC VIP: the 1802, four kilobytes, the display, the keypad and its bell.

The VIP is the machine CHIP-8 was written for, and this module is that machine
rather than an emulator of CHIP-8. What is here is the board:

* **memory** -- four kilobytes of RAM from 0x0000 to 0x0FFF, with the display
  itself at 0x0800 as it was, and the interpreter's ROM above it;
* **the display** -- 64 columns by 128 lines, one bit per pixel, read straight
  out of the display bytes, so a program that writes a byte writes pixels;
* **the keypad** -- sixteen keys in a four-by-four matrix. A row is strobed by
  writing its mask to output port 4, and input port 1 answers with the four
  columns of that row. That is how VIP software reads a key, and it is how the
  CHIP-8 interpreter in ``chip8.vipfirmware`` reads one;
* **the sound** -- the Q line, which software toggles; and
* **the clock** -- the 1802's 1.76 MHz, budgeted in whole instructions so a
  frame never ends halfway through one.

Nothing here knows what CHIP-8 is. The interpreter is firmware: 1802 code,
loaded into ROM at :func:`chip8.vipfirmware.build_firmware`, and CHIP-8 programs
are data at 0x0200 that it runs. :class:`VipEmulator` is the adapter that lets
the frontends, the inspector and the command line treat the VIP as they treat
the native machine.
"""

from __future__ import annotations

import random
from collections import deque
from pathlib import Path

from . import vipfirmware
from .assembler import Chip8Program, assemble_chip8
from .constants import (
    CYCLES_PER_FRAME,
    FONT,
    FONT_ADDRESS,
    FRAMES_PER_SECOND,
    MAX_SPEED,
    MIN_SPEED,
    PROGRAM_ADDRESS,
    REWIND_SNAPSHOTS,
    REWIND_STRIDE,
    SCREEN_HEIGHT,
)
from .cosmac1802 import Cdp1802
from .emulator import Chip8Snapshot
from .machine import Chip8Error

__all__ = [
    "CLOCK",
    "SCREEN_VIEWS",
    "DISPLAY_ADDRESS",
    "DISPLAY_HEIGHT",
    "DISPLAY_WIDTH",
    "KEYMAP",
    "Vip",
    "VipEmulator",
    "VipMemory",
]

#: The VIP's crystal: 1.76 MHz, which is 1,760,640 clock cycles a second.
CLOCK = 1_760_640

#: Where the display lives and how big it is: 64 x 128 pixels, one bit each.
DISPLAY_ADDRESS = 0x0800
DISPLAY_WIDTH = 64
DISPLAY_HEIGHT = 128

#: The keys, by the row the hardware strobes and the column it reads back. The
#: layout is the one on the plastic: 1 2 3 C across the top.
KEYMAP = (
    (0x1, 0x2, 0x3, 0xC),
    (0x4, 0x5, 0x6, 0xD),
    (0x7, 0x8, 0x9, 0xE),
    (0xA, 0x0, 0xB, 0xF),
)

#: The port numbers the board answers on.
KEYPAD_ROW_PORT = 4
KEYPAD_COLUMN_PORT = 1

#: The two ways to look at the board. ``chip8`` is the 64 x 32 grid a program
#: draws on; ``vip`` is the machine's own 64 x 128 display, where a CHIP-8 row
#: is four lines of one bit each.
SCREEN_VIEWS = ("chip8", "vip")

#: The ROM's label table. Two addresses in it matter to the board: where the
#: fetch loop starts, and the span the ``FX0A`` scan loop occupies.
_FIRMWARE_LABELS = vipfirmware.firmware().labels


class VipMemory:
    """Four kilobytes of RAM, and read-only firmware above it.

    A write to the ROM is ignored rather than raising: that is what the board
    does, and a program that runs off its own end should not take the emulator
    with it.
    """

    RAM_TOP = 0x1000
    ROM_TOP = 0x3000

    __slots__ = ("bytes",)

    def __init__(self) -> None:
        self.bytes = bytearray(self.ROM_TOP)

    def read(self, address: int) -> int:
        address &= 0xFFFF
        return self.bytes[address] if address < len(self.bytes) else 0

    def write(self, address: int, value: int) -> None:
        address &= 0xFFFF
        if address < self.RAM_TOP:
            self.bytes[address] = value & 0xFF

    def is_ram(self, address: int) -> bool:
        return address < self.RAM_TOP

    def load(self, address: int, data: bytes) -> None:
        end = min(address + len(data), self.RAM_TOP)
        if end > address:
            self.bytes[address:end] = data[: end - address]


class Vip:
    """The board: processor, memory, display, keypad, bell and clock."""

    __slots__ = (
        "memory",
        "cpu",
        "clock",
        "keys",
        "row_latch",
        "frames",
        "instructions",
        "noise",
        "tape",
        "tape_input",
        "seed",
        "_random",
        "_fetch",
    )

    def __init__(self, seed: int | None = None, clock: int = CLOCK) -> None:
        self.memory = VipMemory()
        self.clock = clock
        self.cpu = Cdp1802(
            self.memory, in_port=self._read_port, out_port=self._write_port
        )
        #: Where the interpreter's fetch loop begins. Arriving here is what
        #: "one CHIP-8 instruction has been executed" means.
        self._fetch = _FIRMWARE_LABELS["FETCH"]
        #: The CHIP-8 keypad, as sixteen hex values, which is how a frontend
        #: thinks of the keys even though the row latch is a row.
        self.keys = 0
        self.row_latch = 0
        self.frames = 0
        #: CHIP-8 instructions retired, which is the count the native platform
        #: calls cycles. The 1802's own count is ``cpu.instructions``.
        self.instructions = 0
        self.noise = 0
        #: The bits written to output ports 1 and 2: the cassette the VIP could
        #: save a program on. Nothing is done with them beyond being recorded.
        self.tape = bytearray()
        self.tape_input = 0
        self.seed = seed
        self._random = random.Random(seed)
        self.boot()

    # -- power on ---------------------------------------------------------
    def boot(self) -> None:
        """Load the interpreter and the font, then run its own start-up."""

        self.memory.bytes = bytearray(VipMemory.ROM_TOP)
        # The firmware owns the ROM and the three bytes of boot stub in RAM, so
        # it is placed directly: write protection is for programs, not for the
        # image the board is soldered with.
        for address, data in vipfirmware.firmware().segments:
            self.memory.bytes[address : address + len(data)] = data
        self.memory.load(FONT_ADDRESS, FONT)
        self.cpu.reset()
        self.keys = 0
        self.row_latch = 0
        self.frames = 0
        self.instructions = 0
        self.noise = 0
        self._random = random.Random(self.seed)
        # A reset leaves the 1802 at 0x0000 with no idea what CHIP-8 is; the
        # interpreter clears its registers and the display from there. Running
        # that start-up now means the board is found waiting in its fetch loop,
        # ready for the first CHIP-8 instruction, which is what power-on looked
        # like on the real thing. The bound is a guard against a broken firmware
        # image, not something a working one reaches: start-up is ~7,000
        # instructions, almost all of them the loop that clears 1,024 bytes of
        # display.
        for _ in range(1_000_000):
            if self.cpu.p == 3 and self.cpu.pc == self._fetch:
                break
            if self.cpu.halted:  # pragma: no cover - a broken firmware image
                break
            self.cpu.step()

    def load_chip8(self, program: Chip8Program) -> None:
        """Place a CHIP-8 program where the interpreter expects to find it."""

        image = bytes(program.memory)
        self.memory.load(PROGRAM_ADDRESS, image[PROGRAM_ADDRESS:])
        self.cpu.r[7] = program.entry & 0xFFFF

    # -- the ports --------------------------------------------------------
    def _read_port(self, port: int) -> int:
        if port != KEYPAD_COLUMN_PORT:
            return 0
        row = self.row_latch & 0x03
        value = 0
        for column, key in enumerate(KEYMAP[row]):
            if self.keys & (1 << key):
                value |= 1 << column
        return value

    def _write_port(self, port: int, value: int) -> None:
        if port == KEYPAD_ROW_PORT:
            mask = value & 0x0F
            self.row_latch = (mask.bit_length() - 1) if mask else 0
        elif port in (1, 2):
            self.tape.append(value & 0x01)

    # -- the keypad -------------------------------------------------------
    def set_key(self, key: int, down: bool) -> None:
        if not 0 <= key <= 15:
            return
        bit = 1 << key
        self.keys = (self.keys | bit) if down else (self.keys & ~bit)

    def set_keys(self, keys: int) -> None:
        self.keys = keys & 0xFFFF

    def first_key(self) -> int:
        for index in range(16):
            if self.keys & (1 << index):
                return index
        return -1

    @property
    def delay_timer(self) -> int:
        return self.memory.read(vipfirmware.DELAY_TIMER)

    @property
    def sound_timer(self) -> int:
        """The bell's counter: what the frontends watch to decide to beep."""

        return self.memory.read(vipfirmware.SOUND_TIMER)

    @property
    def waiting_for_key(self) -> bool:
        """True while the interpreter is inside ``FX0A``'s keypad scan.

        It is a busy loop in firmware rather than a flag, so the only way to
        tell is where the program counter is: between the start of the FX0A
        handler and the next handler in the listing.
        """

        cpu = self.cpu
        labels = _FIRMWARE_LABELS
        return cpu.p != 3 and labels["hF_0A"] <= cpu.pc < labels["hF_33"]

    # -- running ----------------------------------------------------------
    @property
    def cycles_per_frame(self) -> int:
        """The 1802 clocks one frame is worth: 1.76 MHz over sixty frames."""

        return self.clock // FRAMES_PER_SECOND

    def advance_frame(self, opcodes: int = CYCLES_PER_FRAME) -> None:
        """Run one sixtieth of a second: up to *opcodes* CHIP-8 instructions.

        The budget is spent in 1802 clocks as well, so a program that sits
        waiting for a key cannot hold the frame open past its 29,344 clocks --
        on the real board the processor really was spinning for that frame.
        """

        deadline = self.cpu.cycles + self.cycles_per_frame
        done = 0
        while done < opcodes and not self.cpu.halted:
            if not self.step_instruction(deadline - self.cpu.cycles):
                break
            done += 1
        self.frames += 1
        self.tick_timers()
        self.noise = self._random.randrange(256)
        self.memory.write(vipfirmware.NOISE, self.noise)

    def tick_timers(self) -> None:
        """The CHIP-8 timers count frames, so the board counts them here.

        Real interpreters decremented them from the display interrupt; a
        frame-boundary tick is the same arithmetic without pretending to model
        the interrupt.
        """

        for address in (vipfirmware.DELAY_TIMER, vipfirmware.SOUND_TIMER):
            value = self.memory.read(address)
            if value:
                self.memory.write(address, value - 1)

    def step_instruction(self, clocks: int | None = None) -> bool:
        """Run CHIP-8 instructions until one retires; True if one did.

        A CHIP-8 instruction here is the 1802 running the interpreter's handler
        for it, so the boundary is the top of the fetch loop. *clocks* caps the
        1802 clocks spent, to the next instruction boundary -- instructions are
        whole, so the cap is a floor on when to stop, not a hard limit. A
        blocking ``FX0A`` needs the cap: its scan loop only ends on a key, and
        without one the caller would never see its own keypad.
        """

        cpu = self.cpu
        if cpu.halted:
            return False
        deadline = None if clocks is None else cpu.cycles + max(0, clocks)
        while deadline is None or cpu.cycles < deadline:
            cpu.step()
            if cpu.halted:
                return True
            if cpu.p == 3 and cpu.pc == self._fetch:
                self.instructions += 1
                return True
        return False

    # -- state ------------------------------------------------------------
    def registers(self) -> tuple[tuple[int, ...], int, int, int, int]:
        memory = self.memory.bytes
        v = tuple(memory[vipfirmware.V_REGISTERS : vipfirmware.V_REGISTERS + 16])
        i = self.cpu.r[6]
        pc = self.cpu.r[7]
        delay = memory[vipfirmware.DELAY_TIMER]
        sound = memory[vipfirmware.SOUND_TIMER]
        return v, i, pc, delay, sound

    def call_stack(self) -> tuple[int, ...]:
        depth = self.memory.read(vipfirmware.STACK_POINTER)
        entries = []
        for index in range(1, min(depth, 16) + 1):
            high = self.memory.read(vipfirmware.CALL_STACK + index * 2)
            low = self.memory.read(vipfirmware.CALL_STACK + index * 2 + 1)
            entries.append((high << 8) | low)
        return tuple(entries)

    def physical_screen(self) -> bytes:
        """The 64 x 128 display, as one byte per pixel, straight from memory."""

        pixels = bytearray(DISPLAY_WIDTH * DISPLAY_HEIGHT)
        memory = self.memory.bytes
        for row in range(DISPLAY_HEIGHT):
            base = DISPLAY_ADDRESS + row * 8
            out = row * DISPLAY_WIDTH
            for index in range(8):
                byte = memory[base + index]
                if not byte:
                    continue
                for bit in range(8):
                    if byte & (0x80 >> bit):
                        pixels[out + index * 8 + bit] = 1
        return bytes(pixels)

    def screen(self) -> bytes:
        """The 64 x 32 screen CHIP-8 draws on.

        The interpreter writes each CHIP-8 row to four display lines, so a row
        of the grid is four lines of the board ORed together -- which is what
        the program drew, and also what anything that wrote the display by hand
        left behind.
        """

        pixels = bytearray(DISPLAY_WIDTH * SCREEN_HEIGHT)
        memory = self.memory.bytes
        for row in range(SCREEN_HEIGHT):
            base = DISPLAY_ADDRESS + row * 4 * 8
            out = row * DISPLAY_WIDTH
            for index in range(8):
                byte = memory[base + index]
                byte |= memory[base + 8 + index]
                byte |= memory[base + 16 + index]
                byte |= memory[base + 24 + index]
                if not byte:
                    continue
                for bit in range(8):
                    if byte & (0x80 >> bit):
                        pixels[out + index * 8 + bit] = 1
        return bytes(pixels)

    def save_state(self) -> dict:
        cpu = self.cpu
        return {
            "r": tuple(cpu.r),
            "d": cpu.d,
            "df": cpu.df,
            "q": cpu.q,
            "t": cpu.t,
            "ie": cpu.ie,
            "p": cpu.p,
            "x": cpu.x,
            "halted": cpu.halted,
            "instructions": cpu.instructions,
            "ram": bytes(self.memory.bytes[: VipMemory.RAM_TOP]),
            "keys": self.keys,
            "frames": self.frames,
            "retired": self.instructions,
        }

    def restore_state(self, state: dict) -> None:
        cpu = self.cpu
        cpu.r = list(state["r"])
        cpu.d = state["d"]
        cpu.df = state["df"]
        cpu.q = state["q"]
        cpu.t = state["t"]
        cpu.ie = state["ie"]
        cpu.p = state["p"]
        cpu.x = state["x"]
        cpu.halted = state["halted"]
        cpu.instructions = state["instructions"]
        self.memory.bytes[: VipMemory.RAM_TOP] = state["ram"]
        self.keys = state["keys"]
        self.frames = state["frames"]
        self.instructions = state["retired"]


#: The opcodes a program can be full of that the VIP has no handler for: the
#: scrolls, the screen modes, sixteen-pixel sprites and the 8x10 font. A program
#: that uses one needs the native platform, which models SuperChip as well as
#: the 1977 machine. Halt (00FD), Fx75 and Fx85 are deliberately absent: they
#: need nothing the VIP did not have, and the firmware implements them.
_UNSUPPORTED = {
    0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5, 0x00C6, 0x00C7,
    0x00C8, 0x00C9, 0x00CA, 0x00CB, 0x00CC, 0x00CD, 0x00CE, 0x00CF,
    0x00FB, 0x00FC, 0x00FE, 0x00FF,
}


def superchip_opcodes(program: Chip8Program) -> list[int]:
    """The SuperChip opcodes a program seems to contain that the VIP cannot run.

    A listing's data section can look like an opcode, so this is a note for the
    person running the program rather than a refusal.
    """

    found = []
    image = bytes(program.memory)
    for address in range(program.entry, len(image) - 1, 2):
        opcode = (image[address] << 8) | image[address + 1]
        unsupported = opcode in _UNSUPPORTED
        if (opcode & 0xF00F) == 0xD000 and (opcode & 0x000F) == 0:
            unsupported = True  # Dxy0, the sixteen-pixel sprite
        elif (opcode & 0xF0FF) == 0xF030:
            unsupported = True  # the 8x10 font, which the VIP has not got
        if unsupported and opcode not in found:
            found.append(opcode)
    return found


class VipEmulator:
    """The VIP, wearing the same coat as :class:`~chip8.emulator.Chip8Emulator`.

    The frontends, the inspector and the command line were written against a
    machine that steps CHIP-8 opcodes. The VIP steps 1802 instructions, so this
    adapter owns the frame loop and reports the CHIP-8 state the board ends up
    in: the registers are the interpreter's, the screen is the display memory's
    and the timers are the two bytes the interpreter keeps.
    """

    def __init__(self, seed: int | None = None, clock: int = CLOCK) -> None:
        self.vip = Vip(seed=seed, clock=clock)
        self.program: Chip8Program | None = None
        self.source = b""
        self.name = ""
        self.path = ""
        self.error = ""
        self.notes: list[str] = []
        self.running = False
        self.speed = 10
        #: Which of the board's two screens a frontend is shown.
        self.screen_view = "chip8"
        self._states: deque[dict] = deque(maxlen=REWIND_SNAPSHOTS)
        self._rewind_position = -1
        self._frames_since_snapshot = 0

    # -- loading ----------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.program is not None

    @property
    def binary(self) -> bool:
        return bool(self.program is not None and self.program.binary)

    def load_file(self, path: str | Path) -> bool:
        try:
            data = Path(path).read_bytes()
        except OSError as failure:
            self.error = str(failure)
            return False
        if not self.load_bytes(data, Path(path).name):
            return False
        self.path = str(Path(path).absolute())
        return True

    def load_bytes(self, data: bytes, name: str = "program") -> bool:
        try:
            program = assemble_chip8(data)
        except Chip8Error as failure:
            self.error = str(failure)
            return False
        self.running = False
        self.program = program
        self.source = data
        self.name = name
        self.path = ""
        self.error = ""
        self.notes = []
        extra = superchip_opcodes(program)
        if extra:
            self.notes.append(
                "this program uses SuperChip opcodes ("
                + ", ".join(f"0x{opcode:04X}" for opcode in extra[:4])
                + "); the VIP is the 1977 machine and has no handler for them"
            )
        self._make_machine()
        return True

    def load_source(self, source: str, name: str = "document") -> bool:
        return self.load_bytes(source.encode("utf-8"), name)

    def _make_machine(self) -> None:
        assert self.program is not None
        self.vip.boot()
        self.vip.load_chip8(self.program)
        self._states.clear()
        self._rewind_position = -1
        self._frames_since_snapshot = 0
        self._remember()

    # -- transport --------------------------------------------------------
    @property
    def cycles(self) -> int:
        """CHIP-8 instructions retired, the count the inspector calls cycles."""

        return self.vip.instructions

    @property
    def machine(self) -> Vip:
        """The board, for the frontends' sound check: ``machine.sound_timer``."""

        return self.vip

    def start(self) -> None:
        if self.program is None or self.running or self.vip.cpu.halted:
            return
        self._forget_future()
        self.error = ""
        self.running = True
        self._frames_since_snapshot = 0

    def pause(self) -> None:
        if not self.running:
            return
        self.running = False
        self._remember()

    def toggle(self) -> None:
        if self.running:
            self.pause()
        else:
            self.start()

    def step(self) -> None:
        """One CHIP-8 instruction, while paused.

        The clock cap is one frame's worth of 1802 clocks: a blocking ``FX0A``
        would otherwise never return, and the frontend would stop reading keys.
        """

        if self.program is None or self.running or self.vip.cpu.halted:
            return
        self._forget_future()
        self.vip.step_instruction(self.vip.cycles_per_frame)

    def reset(self) -> None:
        self.running = False
        if self.program is None:
            return
        self._make_machine()
        self.error = ""

    def tick(self) -> None:
        if self.running:
            self.advance_frame()

    def advance_frame(self) -> None:
        if self.program is None:
            return
        self._forget_future()
        self._frames_since_snapshot += 1
        if self._frames_since_snapshot >= REWIND_STRIDE:
            self._frames_since_snapshot = 0
            self._remember()
        self.vip.advance_frame(self.speed)
        if self.vip.cpu.halted:
            self.running = False

    # -- speed and keys ---------------------------------------------------
    def set_speed(self, cycles_per_frame: int) -> None:
        self.speed = max(MIN_SPEED, min(MAX_SPEED, int(cycles_per_frame)))

    def nudge_speed(self, delta: int) -> None:
        if delta > 0:
            step_size = 10 if self.speed >= 10 else 1
        else:
            step_size = 10 if self.speed > 10 else 1
        self.set_speed(self.speed + (step_size if delta > 0 else -step_size))

    def set_screen_view(self, view: str) -> None:
        """Draw the CHIP-8 grid, or the board's own display, from now on."""

        if view not in SCREEN_VIEWS:
            raise ValueError(f"there is no {view!r} screen; try {SCREEN_VIEWS}")
        self.screen_view = view

    def set_key(self, key: int, down: bool) -> None:
        self.vip.set_key(key, down)

    def release_all_keys(self) -> None:
        self.vip.set_keys(0)

    @property
    def keys(self) -> int:
        return self.vip.keys

    # -- rewind -----------------------------------------------------------
    @property
    def rewind_count(self) -> int:
        return len(self._states)

    @property
    def rewind_position(self) -> int:
        return self._rewind_position

    def _remember(self) -> None:
        self._frames_since_snapshot = 0
        cpu = self.vip.cpu
        if self._states:
            last = self._states[-1]
            # Nothing has happened since the last moment; a second copy would
            # make the newest rewind step one that does nothing.
            if (
                last["instructions"] == cpu.instructions
                and last["r"][7] == cpu.r[7]
            ):
                return
        self._states.append(self.vip.save_state())
        self._rewind_position = -1

    def _forget_future(self) -> None:
        if self._rewind_position < 0:
            return
        while len(self._states) > self._rewind_position + 1:
            self._states.pop()
        self._rewind_position = -1

    def begin_rewind(self) -> int:
        """Pause, keep the live state as the newest moment, and return the count."""

        self.running = False
        if self._rewind_position < 0:
            self._remember()
        return len(self._states)

    def rewind_to(self, index: int) -> None:
        if not self._states:
            return
        wanted = max(0, min(index, len(self._states) - 1))
        self.running = False
        self.vip.restore_state(self._states[wanted])
        self._rewind_position = wanted
        self._frames_since_snapshot = 0

    def clear_rewind(self) -> None:
        self._states.clear()
        self._rewind_position = -1
        self._frames_since_snapshot = 0

    def rewind_by(self, delta: int) -> None:
        """Move through the history, entering it at the live moment first."""

        if not self._states:
            return
        position = self._rewind_position
        if position < 0:
            self.begin_rewind()
            position = len(self._states) - 1
        self.rewind_to(position + delta)

    # -- what a frontend reads --------------------------------------------
    @property
    def state_name(self) -> str:
        if not self.loaded:
            return "empty"
        if self.error:
            return "error"
        if self.vip.cpu.halted:
            return "halted"
        if self.vip.waiting_for_key:
            return "waiting for a key"
        return "running" if self.running else "paused"

    def snapshot(self) -> Chip8Snapshot:
        v, i, pc, delay, sound = self.vip.registers()
        if self.screen_view == "vip":
            width, height = DISPLAY_WIDTH, DISPLAY_HEIGHT
            framebuffer = self.vip.physical_screen()
        else:
            width, height = DISPLAY_WIDTH, SCREEN_HEIGHT
            framebuffer = self.vip.screen()
        return Chip8Snapshot(
            v=v,
            i=i,
            pc=pc,
            stack=self.vip.call_stack(),
            delay_timer=delay,
            sound_timer=sound,
            cycles=self.vip.instructions,
            loaded=self.loaded,
            running=self.running,
            halted=self.vip.cpu.halted,
            waiting_for_key=self.vip.waiting_for_key,
            speed=self.speed,
            screen_width=width,
            screen_height=height,
            framebuffer=framebuffer,
            memory=bytes(self.vip.memory.bytes[: VipMemory.RAM_TOP]),
            name=self.name,
            binary=self.binary,
            error=self.error,
            rewind_count=len(self._states),
            rewind_position=self._rewind_position,
            keys=self.vip.keys,
        )
