"""The 1977 machine: registers, memory, timers and the framebuffer.

This implements the original CHIP-8 machine and the SuperChip additions
described below. Where emulators disagree, this one uses these behaviors:

* ``8xy5``/``8xy7``/``8xy6``/``8xyE`` shift and subtract ``Vx`` **in place** and
  write ``VF`` before the result, so ``x == F`` ends up holding the result;
* sprites **wrap** around the edges instead of clipping;
* ``Fx55``/``Fx65`` leave ``I`` **one past** the block they touched.

The SuperChip additions are here too: ``00Cn`` scroll, ``00FB``/``00FC`` scroll
a half screen, ``00FD`` halt, ``00FE``/``00FF`` screen
modes, ``Dxy0``'s 16x16 sprite, ``Fx30``'s large font and ``Fx75``/``Fx85``'s
RPL flags.

The machine is deliberately synchronous: :meth:`Chip8Machine.step` executes one
instruction and nothing else, so a test can assert on the byte it produced and a
frontend can pause between two of them.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass, field

from .constants import (
    FONT,
    FONT_ADDRESS,
    HI_RES_HEIGHT,
    HI_RES_WIDTH,
    LARGE_FONT,
    LARGE_FONT_ADDRESS,
    MEMORY_SIZE,
    PROGRAM_ADDRESS,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
)


class Chip8Error(RuntimeError):
    """A malformed program, or one the machine cannot carry on running."""


@dataclass(slots=True)
class Chip8State:
    """Everything that decides what the machine does next, in one value.

    A CHIP-8 program is four kilobytes, so a state is small enough to keep a few
    seconds of -- which is what makes rewind possible. The random generator is
    deliberately *not* in here: restoring replays the drawing, not the luck, and
    a rewind that re-rolled the dice would not show what happened.
    """

    v: bytes = bytes(16)
    rpl: bytes = bytes(16)
    framebuffer: bytes = b""
    memory: bytes = b""
    stack: tuple[int, ...] = ()
    i: int = 0
    pc: int = PROGRAM_ADDRESS
    delay_timer: int = 0
    sound_timer: int = 0
    keys: int = 0
    hi_res: bool = False
    halted: bool = False
    waiting_for_key: bool = False
    waiting_register: int = 0
    cycles: int = 0
    collisions: int = 0


@dataclass(slots=True)
class Chip8Machine:
    """Registers, memory, timers and framebuffer, with one instruction at a time."""

    memory: bytearray = field(default_factory=lambda: bytearray(MEMORY_SIZE))
    pc: int = PROGRAM_ADDRESS
    v: bytearray = field(default_factory=lambda: bytearray(16))
    i: int = 0
    stack: list[int] = field(default_factory=list)
    delay_timer: int = 0
    sound_timer: int = 0
    keys: int = 0
    interactive: bool = False
    waiting_for_key: bool = False
    waiting_register: int = 0
    hi_res: bool = False
    rpl: bytearray = field(default_factory=lambda: bytearray(16))
    framebuffer: bytearray = field(
        default_factory=lambda: bytearray(SCREEN_WIDTH * SCREEN_HEIGHT)
    )
    halted: bool = False
    cycles: int = 0
    collisions: int = 0
    rng: _random.Random = field(default_factory=_random.Random, repr=False)

    # -- lifetime ---------------------------------------------------------
    @classmethod
    def from_program(
        cls,
        memory: bytes | bytearray,
        entry: int = PROGRAM_ADDRESS,
        keys: int = 0,
        rng: _random.Random | None = None,
    ) -> "Chip8Machine":
        """A machine holding *memory*, ready to run from *entry*.

        The generator is seeded from the OS on construction, as Python's
        ``random`` module is: a fixed seed would make every run of a ``RND``
        program identical.
        """

        if len(memory) > MEMORY_SIZE:
            raise Chip8Error(
                f"the program needs {len(memory)} bytes and memory ends at 0x1000"
            )
        machine = cls(
            memory=bytearray(memory) + bytes(MEMORY_SIZE - len(memory)),
            pc=entry,
            keys=keys & 0xFFFF,
            rng=rng if rng is not None else _random.Random(),
        )
        return machine

    # -- the keypad -------------------------------------------------------
    def set_keys(self, keys: int) -> None:
        self.keys = keys & 0xFFFF

    def set_key(self, key: int, down: bool) -> None:
        if key < 0 or key > 15:
            return
        bit = 1 << key
        self.keys = (self.keys | bit) if down else (self.keys & ~bit)

    def first_key(self) -> int:
        """The lowest held key, or -1 when none is held."""

        for index in range(16):
            if self.keys & (1 << index):
                return index
        return -1

    # -- the screen -------------------------------------------------------
    @property
    def screen_width(self) -> int:
        return HI_RES_WIDTH if self.hi_res else SCREEN_WIDTH

    @property
    def screen_height(self) -> int:
        return HI_RES_HEIGHT if self.hi_res else SCREEN_HEIGHT

    def pixel(self, x: int, y: int) -> bool:
        return (
            0 <= x < self.screen_width
            and 0 <= y < self.screen_height
            and self.framebuffer[y * self.screen_width + x] != 0
        )

    def set_pixel(self, x: int, y: int, on: bool) -> None:
        if 0 <= x < self.screen_width and 0 <= y < self.screen_height:
            self.framebuffer[y * self.screen_width + x] = 1 if on else 0

    # -- state ------------------------------------------------------------
    def save_state(self) -> Chip8State:
        return Chip8State(
            v=bytes(self.v),
            rpl=bytes(self.rpl),
            framebuffer=bytes(self.framebuffer),
            memory=bytes(self.memory),
            stack=tuple(self.stack),
            i=self.i,
            pc=self.pc,
            delay_timer=self.delay_timer,
            sound_timer=self.sound_timer,
            keys=self.keys,
            hi_res=self.hi_res,
            halted=self.halted,
            waiting_for_key=self.waiting_for_key,
            waiting_register=self.waiting_register,
            cycles=self.cycles,
            collisions=self.collisions,
        )

    def restore_state(self, state: Chip8State) -> None:
        self.v = bytearray(state.v)
        self.rpl = bytearray(state.rpl)
        self.framebuffer = bytearray(state.framebuffer)
        self.memory = bytearray(state.memory)
        self.stack = list(state.stack)
        self.i = state.i
        self.pc = state.pc
        self.delay_timer = state.delay_timer
        self.sound_timer = state.sound_timer
        self.keys = state.keys
        self.hi_res = state.hi_res
        self.halted = state.halted
        self.waiting_for_key = state.waiting_for_key
        self.waiting_register = state.waiting_register
        self.cycles = state.cycles
        self.collisions = state.collisions

    # -- execution --------------------------------------------------------
    def fetch(self) -> int:
        if self.pc + 1 >= MEMORY_SIZE:
            raise Chip8Error("the program counter ran past the end of memory")
        opcode = (self.memory[self.pc] << 8) | self.memory[self.pc + 1]
        self.pc += 2
        return opcode

    def skip(self) -> None:
        self.pc += 2

    def step(self) -> None:
        """Execute one instruction.

        A waiting ``LD Vx, K`` is not re-fetched: its PC already points at the
        next instruction. Completing it here, when a key is held, is what makes
        the wait interruptible by a frontend without rewinding anything.
        """

        if self.waiting_for_key:
            key = self.first_key()
            if key < 0:
                return
            self.v[self.waiting_register] = key
            self.waiting_for_key = False
            self.cycles += 1
            return

        self.cycles += 1
        opcode = self.fetch()
        high = opcode >> 12
        x = (opcode >> 8) & 0xF
        y = (opcode >> 4) & 0xF
        low_byte = opcode & 0xFF
        low_nibble = opcode & 0xF

        if opcode == 0x0000:
            self.halted = True
            return
        if opcode == 0x00E0:
            # The grid's own size, not the original one: clearing in hi-res must
            # not shrink the framebuffer the next DRAW indexes.
            self.framebuffer = bytearray(self.screen_width * self.screen_height)
            return
        if opcode == 0x00EE:
            if not self.stack:
                raise Chip8Error("RET with an empty call stack")
            self.pc = self.stack.pop()
            return
        if opcode == 0x00FD:
            # SuperChip's "exit interpreter": HALT in one opcode.
            self.halted = True
            return
        if opcode in (0x00FE, 0x00FF):
            self._set_mode(opcode == 0x00FF)
            return
        if (opcode & 0xFFF0) == 0x00C0:
            self._scroll_down(opcode & 0xF)
            return
        if opcode == 0x00FB:
            self._scroll_columns(-4)
            return
        if opcode == 0x00FC:
            self._scroll_columns(4)
            return
        if high == 0x0:
            # 0NNN (SYS): an RCA 1802 routine, which this machine cannot
            # execute. Ignoring it is what every interpreter since the original
            # does, and it keeps a ROM that contains one runnable.
            return

        if high == 0x1:
            self.pc = opcode & 0xFFF
        elif high == 0x2:
            self.stack.append(self.pc)
            self.pc = opcode & 0xFFF
        elif high == 0x3:
            if self.v[x] == low_byte:
                self.skip()
        elif high == 0x4:
            if self.v[x] != low_byte:
                self.skip()
        elif high == 0x5:
            if self.v[x] == self.v[y]:
                self.skip()
        elif high == 0x6:
            self.v[x] = low_byte
        elif high == 0x7:
            self.v[x] = (self.v[x] + low_byte) & 0xFF
        elif high == 0x8:
            self._arithmetic(x, y, low_nibble)
        elif high == 0x9:
            if self.v[x] != self.v[y]:
                self.skip()
        elif high == 0xA:
            self.i = opcode & 0xFFF
        elif high == 0xB:
            self.pc = (opcode & 0xFFF) + self.v[0]
        elif high == 0xC:
            self.v[x] = self.rng.randint(0, 255) & low_byte
        elif high == 0xD:
            self._draw(x, y, low_nibble)
        elif high == 0xE:
            # A register above 0xF names no key; the mask has sixteen bits, so
            # the shift simply misses.
            bit = 1 << self.v[x]
            if low_byte == 0x9E and (self.keys & bit) != 0:
                self.skip()
            elif low_byte == 0xA1 and (self.keys & bit) == 0:
                self.skip()
        elif high == 0xF:
            self._misc(x, low_byte)
        else:  # pragma: no cover - every high nibble is handled above
            raise Chip8Error(
                f"unknown opcode 0x{opcode:04X} at 0x{self.pc - 2:03X}"
            )

    def tick_timers(self) -> None:
        if self.delay_timer > 0:
            self.delay_timer -= 1
        if self.sound_timer > 0:
            self.sound_timer -= 1

    # -- opcode families --------------------------------------------------
    def _arithmetic(self, x: int, y: int, low: int) -> None:
        vy = self.v[y]
        vx = self.v[x]
        if low == 0x0:
            self.v[x] = vy
        elif low == 0x1:
            self.v[x] = vx | vy
        elif low == 0x2:
            self.v[x] = vx & vy
        elif low == 0x3:
            self.v[x] = vx ^ vy
        elif low == 0x4:
            total = vx + vy
            self.v[x] = total & 0xFF
            self.v[0xF] = 1 if total > 0xFF else 0
        elif low == 0x5:
            self.v[0xF] = 1 if vx >= vy else 0
            self.v[x] = (vx - vy) & 0xFF
        elif low == 0x6:
            # Vx shifted in place, as the 1977 interpreter does.
            self.v[0xF] = vx & 0x1
            self.v[x] = vx >> 1
        elif low == 0x7:
            self.v[0xF] = 1 if vy >= vx else 0
            self.v[x] = (vy - vx) & 0xFF
        elif low == 0xE:
            self.v[0xF] = 1 if vx & 0x80 else 0
            self.v[x] = (vx << 1) & 0xFF
        else:
            raise Chip8Error(f"unknown arithmetic opcode 8xy{low:X}")

    def _draw(self, x: int, y: int, rows: int) -> None:
        width = self.screen_width
        height = self.screen_height
        start_x = self.v[x] % width
        start_y = self.v[y] % height

        # `rows == 0` is SuperChip's large sprite: 16 rows of 16 bits in hi-res.
        # In the original mode the same opcode is an 8x16 sprite with each
        # column doubled, which is what SCHIP draws there and what a lores ROM
        # expects.
        wide = 8
        doubled = False
        if rows == 0:
            rows = 16
            if self.hi_res:
                wide = 16
            else:
                doubled = True

        self.v[0xF] = 0
        memory = self.memory
        collisions = self.collisions
        for row in range(rows):
            if wide == 16:
                bits = (
                    memory[(self.i + row * 2) % MEMORY_SIZE] << 8
                ) | memory[(self.i + row * 2 + 1) % MEMORY_SIZE]
            else:
                bits = memory[(self.i + row) % MEMORY_SIZE]
            pixel_y = (start_y + row) % height
            for source_column in range(wide):
                mask = (0x8000 >> source_column) if wide == 16 else (0x80 >> source_column)
                if not bits & mask:
                    continue
                for repeat in range(2 if doubled else 1):
                    column = source_column * (2 if doubled else 1) + repeat
                    pixel_x = (start_x + column) % width
                    lit = self.pixel(pixel_x, pixel_y)
                    if lit:
                        self.v[0xF] = 1
                        collisions += 1
                    self.set_pixel(pixel_x, pixel_y, not lit)
        self.collisions = collisions

    def _set_mode(self, hi_res: bool) -> None:
        # The opcode clears either way: a ROM that selects the mode it is
        # already in expects the same blank screen as one that switches.
        self.hi_res = hi_res
        self.framebuffer = bytearray(self.screen_width * self.screen_height)

    def _scroll_down(self, rows: int) -> None:
        if rows <= 0:
            return
        width = self.screen_width
        height = self.screen_height
        shift = min(rows, height)
        framebuffer = self.framebuffer
        framebuffer[shift * width :] = framebuffer[: (height - shift) * width]
        framebuffer[: shift * width] = bytes(shift * width)

    def _scroll_columns(self, columns: int) -> None:
        """Positive columns scroll left, negative right; the gap is cleared."""

        width = self.screen_width
        height = self.screen_height
        shift = min(abs(columns), width)
        if shift == 0:
            return
        framebuffer = self.framebuffer
        for y in range(height):
            row = y * width
            if columns > 0:
                framebuffer[row : row + width - shift] = framebuffer[row + shift : row + width]
                framebuffer[row + width - shift : row + width] = bytes(shift)
            else:
                framebuffer[row + shift : row + width] = framebuffer[row : row + width - shift]
                framebuffer[row : row + shift] = bytes(shift)

    def _misc(self, x: int, low: int) -> None:
        vx = self.v[x]
        if low == 0x07:
            self.v[x] = self.delay_timer & 0xFF
        elif low == 0x0A:
            # A whole-program run has no way to deliver a later press, so it
            # reads the mask once. An interactive machine waits, which is what
            # the instruction means on the real keypad.
            key = self.first_key()
            if not self.interactive:
                self.v[x] = 0 if key < 0 else key
            elif key < 0:
                self.waiting_for_key = True
                self.waiting_register = x
            else:
                self.v[x] = key
        elif low == 0x15:
            self.delay_timer = vx
        elif low == 0x18:
            self.sound_timer = vx
        elif low == 0x1E:
            self.i = (self.i + vx) & 0xFFF
        elif low == 0x29:
            self.i = FONT_ADDRESS + (vx & 0xF) * 5
        elif low == 0x30:
            self.i = LARGE_FONT_ADDRESS + (vx & 0xF) * 10
        elif low == 0x33:
            self.memory[self.i % MEMORY_SIZE] = vx // 100
            self.memory[(self.i + 1) % MEMORY_SIZE] = (vx // 10) % 10
            self.memory[(self.i + 2) % MEMORY_SIZE] = vx % 10
        elif low == 0x55:
            for index in range(x + 1):
                self.memory[(self.i + index) % MEMORY_SIZE] = self.v[index]
            # The 1977 interpreter leaves I one past the block; CHIP-48 and
            # SCHIP leave it where it was. This machine is the 1977 one, as the
            # shifts and the sprite wrap are.
            self.i = (self.i + x + 1) & 0xFFF
        elif low == 0x65:
            for index in range(x + 1):
                self.v[index] = self.memory[(self.i + index) % MEMORY_SIZE]
            self.i = (self.i + x + 1) & 0xFFF
        elif low == 0x75:
            for index in range(min(x + 1, 16)):
                self.rpl[index] = self.v[index]
        elif low == 0x85:
            for index in range(min(x + 1, 16)):
                self.v[index] = self.rpl[index]
        else:
            raise Chip8Error(f"unknown opcode Fx{low:02X}")


def blank_memory() -> bytearray:
    """Four kilobytes with the two fonts where the machine expects them."""

    memory = bytearray(MEMORY_SIZE)
    memory[FONT_ADDRESS : FONT_ADDRESS + len(FONT)] = FONT
    memory[LARGE_FONT_ADDRESS : LARGE_FONT_ADDRESS + len(LARGE_FONT)] = LARGE_FONT
    return memory
