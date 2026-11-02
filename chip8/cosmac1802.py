"""The RCA COSMAC CDP1802, the eight-bit processor the VIP was built around.

The 1802 is not like the processors that came after it, and the differences are
not decoration -- they decide how software for it is written:

* it has **sixteen 16-bit registers** and no stack pointer. R(P) is the program
  counter, R(X) is the data pointer, and software chooses which register each
  is with ``SEP`` and ``SEX``;
* most instructions have no operand. ``ADD`` adds *the byte at M(R(X))* to D,
  and the "immediate" forms read the byte at M(R(P)) -- the instruction stream
  itself -- and advance the program counter past it;
* it is built for one-byte-per-cycle memory, and every instruction is a whole
  number of eight-clock machine cycles.

This module models the processor and nothing else: it reads and writes through
:class:`Memory`, and reports the ports and flags to whoever owns the board. It
is 2 machine cycles per instruction, 3 for a long branch or a long skip, which
is what the cycle counts in the RCA data sheet add up to for the instructions
this emulator runs; bus timing within an instruction is not modelled.

Timing is reported by :meth:`Cdp1802.step` in *clock cycles*, eight to a
machine cycle, so a caller can hold the processor to a 1.76 MHz budget.
"""

from __future__ import annotations

from typing import Protocol

__all__ = ["Cdp1802", "Memory", "Ram"]


class Memory(Protocol):
    """What the processor needs from the board it is soldered to."""

    def read(self, address: int) -> int:
        """The byte at *address*."""

    def write(self, address: int, value: int) -> None:
        """Store *value* at *address*. ROM is expected to ignore it."""


class Ram:
    """64K of plain memory, for tests and for boards with no ROM on them."""

    __slots__ = ("bytes",)

    def __init__(self, size: int = 0x10000) -> None:
        self.bytes = bytearray(size)

    def read(self, address: int) -> int:
        return self.bytes[address & 0xFFFF]

    def write(self, address: int, value: int) -> None:
        self.bytes[address & 0xFFFF] = value & 0xFF

    def load(self, address: int, data: bytes) -> None:
        self.bytes[address : address + len(data)] = data


#: Clock cycles in one machine cycle. The 1802 does one memory access per eight
#: clock periods, and every instruction is 2 or 3 of them.
CLOCKS_PER_MACHINE_CYCLE = 8

#: Where the sixteen short branches and skips go, by their low nibble.
_SHORT = ("BR", "BQ", "BZ", "BDF", "B1", "B2", "B3", "B4",
          "SKP", "BNQ", "BNZ", "BNF", "BN1", "BN2", "BN3", "BN4")

#: The long branches, by their low nibble: a target address follows in the code.
_LONG = ("LBR", "LBQ", "LBZ", "LBDF", None, None, None, None,
         None, "LBNQ", "LBNZ", "LBNF", None, None, None, None)

#: The long skips. These skip the *next instruction* (two bytes of it) rather
#: than branch, and their low nibble is the condition.
_SKIP = (None, None, None, None, None, "LSNQ", "LSNZ", "LSNF",
         "LSKP", None, None, None, "LSIE", "LSQ", "LSZ", "LSDF")


class Cdp1802:
    """One CDP1802, ready to be clocked.

    A caller drives it with :meth:`step` and gives it the two things the chip
    cannot supply itself: the four external flags it tests, and the two
    one-byte ports it reads and writes.
    """

    __slots__ = (
        "memory",
        "r",
        "d",
        "df",
        "q",
        "t",
        "ie",
        "p",
        "x",
        "cycles",
        "instructions",
        "halted",
        "ef",
        "in_port",
        "out_port",
    )

    def __init__(
        self,
        memory: Memory,
        *,
        in_port=None,
        out_port=None,
        ef: tuple[int, int, int, int] | list[int] = (0, 0, 0, 0),
    ) -> None:
        self.memory = memory
        #: The sixteen 16-bit registers. R(P) is the program counter and R(X) is
        #: the data pointer; which register is which is software's business.
        self.r = [0] * 16
        self.d = 0
        self.df = 0
        self.q = 0
        self.t = 0
        self.ie = 1
        self.p = 0
        self.x = 0
        self.cycles = 0
        self.instructions = 0
        #: Set by IDL: the processor is waiting for an interrupt this model does
        #: not raise, so the board can decide what waiting means.
        self.halted = False
        self.ef = list(ef)
        self.in_port = in_port if in_port is not None else (lambda n: 0)
        self.out_port = out_port if out_port is not None else (lambda n, v: None)

    # -- helpers ----------------------------------------------------------
    def reset(self) -> None:
        """The state a power-on reset leaves: P = X = 0, R0 = 0, IE = 1."""

        self.r = [0] * 16
        self.d = self.df = self.q = self.t = 0
        self.ie = 1
        self.p = self.x = 0
        self.cycles = 0
        self.instructions = 0
        self.halted = False

    @property
    def pc(self) -> int:
        return self.r[self.p]

    @pc.setter
    def pc(self, value: int) -> None:
        self.r[self.p] = value & 0xFFFF

    def _fetch(self) -> int:
        """One instruction byte, with the program counter stepped over it."""

        address = self.r[self.p]
        self.r[self.p] = (address + 1) & 0xFFFF
        return self.memory.read(address)

    def _immediate(self) -> int:
        """The operand of an immediate instruction: the next byte of code."""

        return self._fetch()

    def _mem(self, register: int) -> int:
        return self.memory.read(self.r[register])

    def _store(self, register: int, value: int) -> None:
        self.memory.write(self.r[register], value)

    # -- the flag arithmetic ----------------------------------------------
    def _add(self, value: int) -> None:
        total = self.d + value
        self.df = 1 if total > 0xFF else 0
        self.d = total & 0xFF

    def _add_carry(self, value: int) -> None:
        total = self.d + value + self.df
        self.df = 1 if total > 0xFF else 0
        self.d = total & 0xFF

    # The two families point opposite ways, and the mnemonics are named for what
    # they subtract: SD subtracts D (`M - D`), SM subtracts memory (`D - M`).
    # DF is the *no-borrow* flag in both, set when the subtraction did not need
    # to borrow, which is what CHIP-8's VF means when FX1E's neighbours use it.
    def _sub_d(self, value: int) -> None:
        """``value - D``: the SD family, where the memory byte is the minuend."""

        result = value - self.d
        self.df = 1 if result >= 0 else 0
        self.d = result & 0xFF

    def _sub_d_borrow(self, value: int) -> None:
        """SDB: ``value - D - (1 - DF)``, the same with a borrow coming in."""

        result = value - self.d - (1 - self.df)
        self.df = 1 if result >= 0 else 0
        self.d = result & 0xFF

    def _sub_memory(self, value: int) -> None:
        """``D - value``: the SM family, where the accumulator is the minuend."""

        result = self.d - value
        self.df = 1 if result >= 0 else 0
        self.d = result & 0xFF

    def _sub_memory_borrow(self, value: int) -> None:
        """SMB: ``D - value - (1 - DF)``, the same with a borrow coming in."""

        result = self.d - value - (1 - self.df)
        self.df = 1 if result >= 0 else 0
        self.d = result & 0xFF

    # -- shifts -----------------------------------------------------------
    def _shr(self) -> None:
        """SHR: a plain shift right. Zero enters the top, the old bit 0 to DF."""

        self.df = self.d & 1
        self.d >>= 1

    def _shrc(self) -> None:
        """SHRC: a ring shift. DF enters the top, the old bit 0 goes to DF."""

        low = self.d & 1
        self.d = (self.d >> 1) | (self.df << 7)
        self.df = low

    def _shl(self) -> None:
        self.df = (self.d >> 7) & 1
        self.d = (self.d << 1) & 0xFF

    def _shlc(self) -> None:
        high = (self.d >> 7) & 1
        self.d = ((self.d << 1) & 0xFF) | self.df
        self.df = high

    # -- branches ---------------------------------------------------------
    def _short_branch(self, low: int) -> int:
        """A 2-byte short branch, returning the machine cycles it took."""

        offset = self._immediate()
        condition = _SHORT[low]
        if condition == "SKP":
            # SKP's operand is the byte it skips: reading it above is the whole
            # instruction, and there is no displacement to add.
            return 2
        if self._short_taken(condition):
            self.r[self.p] = (self.r[self.p] + _signed(offset)) & 0xFFFF
        return 2

    def _short_taken(self, condition: str) -> bool:
        if condition == "BR":
            return True
        if condition == "BQ":
            return self.q == 1
        if condition == "BZ":
            return self.d == 0
        if condition == "BDF":
            return self.df == 1
        if condition == "B1":
            return self.ef[0] == 1
        if condition == "B2":
            return self.ef[1] == 1
        if condition == "B3":
            return self.ef[2] == 1
        if condition == "B4":
            return self.ef[3] == 1
        if condition == "SKP":
            return True
        if condition == "BNQ":
            return self.q == 0
        if condition == "BNZ":
            return self.d != 0
        if condition == "BNF":
            return self.df == 0
        if condition == "BN1":
            return self.ef[0] == 0
        if condition == "BN2":
            return self.ef[1] == 0
        if condition == "BN3":
            return self.ef[2] == 0
        if condition == "BN4":
            return self.ef[3] == 0
        raise AssertionError(f"no such short branch: {condition}")

    def _long_branch(self, low: int) -> int:
        """A 3-byte long branch: an address follows, used only if taken."""

        high_byte = self._immediate()
        low_byte = self._immediate()
        name = _LONG[low]
        taken = self._long_taken(name)
        if taken:
            self.r[self.p] = (high_byte << 8) | low_byte
        return 3

    def _long_taken(self, name: str) -> bool:
        if name == "LBR":
            return True
        if name == "LBQ":
            return self.q == 1
        if name == "LBZ":
            return self.d == 0
        if name == "LBDF":
            return self.df == 1
        if name == "LBNQ":
            return self.q == 0
        if name == "LBNZ":
            return self.d != 0
        if name == "LBNF":
            return self.df == 0
        raise AssertionError(f"no such long branch: {name}")

    def _long_skip(self, low: int) -> int:
        """LSKP and friends: step the program counter over the next instruction."""

        name = _SKIP[low]
        taken = False
        if name == "LSKP":
            taken = True
        elif name == "LSNQ":
            taken = self.q == 0
        elif name == "LSNZ":
            taken = self.d != 0
        elif name == "LSNF":
            taken = self.df == 0
        elif name == "LSIE":
            taken = self.ie == 1
        elif name == "LSQ":
            taken = self.q == 1
        elif name == "LSZ":
            taken = self.d == 0
        elif name == "LSDF":
            taken = self.df == 1
        else:
            raise AssertionError(f"no such long skip: {name}")
        if taken:
            # The skipped instruction is two bytes: that is the most any 1802
            # instruction occupies after its opcode.
            self.r[self.p] = (self.r[self.p] + 2) & 0xFFFF
        return 3

    # -- execution --------------------------------------------------------
    def step(self) -> int:
        """Execute one instruction, returning the clock cycles it took."""

        opcode = self._fetch()
        self.instructions += 1
        family = opcode >> 4
        low = opcode & 0x0F
        machines = 2

        if family == 0x0:
            if opcode == 0x00:
                # IDL: the real chip stops here until an interrupt. This model
                # records the wait and lets the board decide when to wake it.
                self.halted = True
            else:
                self.d = self._mem(low)  # LDN Rn
        elif family == 0x1:
            self.r[low] = (self.r[low] + 1) & 0xFFFF  # INC
        elif family == 0x2:
            self.r[low] = (self.r[low] - 1) & 0xFFFF  # DEC
        elif family == 0x3:
            machines = self._short_branch(low)
        elif family == 0x4:
            self.d = self._mem(low)  # LDA
            self.r[low] = (self.r[low] + 1) & 0xFFFF
        elif family == 0x5:
            self._store(low, self.d)  # STR
        elif family == 0x6:
            machines = self._io(opcode, low)
        elif family == 0x7:
            machines = self._seventy(opcode, low)
        elif family == 0x8:
            self.d = self.r[low] & 0xFF  # GLO
        elif family == 0x9:
            self.d = (self.r[low] >> 8) & 0xFF  # GHI
        elif family == 0xA:
            self.r[low] = (self.r[low] & 0xFF00) | self.d  # PLO
        elif family == 0xB:
            self.r[low] = (self.r[low] & 0x00FF) | (self.d << 8)  # PHI
        elif family == 0xC:
            if opcode == 0xC4:  # NOP: one byte, and nothing happens
                machines = 2
            elif _SKIP[low] is not None:
                machines = self._long_skip(low)
            else:
                machines = self._long_branch(low)
        elif family == 0xD:
            self.p = low  # SEP
        elif family == 0xE:
            self.x = low  # SEX
        else:
            machines = self._alu(opcode, low)

        clocks = machines * CLOCKS_PER_MACHINE_CYCLE
        self.cycles += clocks
        return clocks

    def _io(self, opcode: int, low: int) -> int:
        if opcode == 0x60:  # IRX
            self.r[self.x] = (self.r[self.x] + 1) & 0xFFFF
            return 2
        if opcode == 0x68:
            return 2  # unused; takes the two cycles a real chip spends on it
        address = self.r[self.x]
        self.r[self.x] = (address + 1) & 0xFFFF
        # The port number is 1 to 7 in both directions: OUT is 0x61 to 0x67 and
        # INP is 0x69 to 0x6F, so the three low bits are the port in each case.
        port = low & 0x07
        if low < 8:
            self.out_port(port, self.memory.read(address))  # OUT n
        else:
            # INP n: the port's byte lands in D and in the memory the data
            # pointer had reached, which is how 1802 software reads a port.
            value = self.in_port(port) & 0xFF
            self.memory.write(address, value)
            self.d = value
        return 2

    def _seventy(self, opcode: int, low: int) -> int:
        if opcode == 0x70:  # RET
            return self._return(True)
        if opcode == 0x71:  # DIS
            return self._return(False)
        if opcode == 0x72:  # LDXA
            self.d = self._mem(self.x)
            self.r[self.x] = (self.r[self.x] + 1) & 0xFFFF
            return 2
        if opcode == 0x73:  # STXD
            self.memory.write(self.r[self.x], self.d)
            self.r[self.x] = (self.r[self.x] - 1) & 0xFFFF
            return 2
        if opcode == 0x74:
            self._add_carry(self._mem(self.x))
        elif opcode == 0x75:
            self._sub_d_borrow(self._mem(self.x))
        elif opcode == 0x76:
            self._shrc()
        elif opcode == 0x77:
            self._sub_memory_borrow(self._mem(self.x))
        elif opcode == 0x78:  # SAV
            self.memory.write(self.r[self.x], self.t)
        elif opcode == 0x79:  # MARK
            self.t = (self.x << 4) | self.p
            self.memory.write(self.r[2], self.t)
            self.r[2] = (self.r[2] - 1) & 0xFFFF
            self.x = self.p
        elif opcode == 0x7A:  # REQ
            self.q = 0
        elif opcode == 0x7B:  # SEQ
            self.q = 1
        elif opcode == 0x7C:
            self._add_carry(self._immediate())
        elif opcode == 0x7D:
            self._sub_d_borrow(self._immediate())
        elif opcode == 0x7E:
            self._shlc()
        elif opcode == 0x7F:
            self._sub_memory_borrow(self._immediate())
        return 2

    def _return(self, enable: bool) -> int:
        saved = self._mem(self.x)
        self.r[self.x] = (self.r[self.x] + 1) & 0xFFFF
        self.x = (saved >> 4) & 0x0F
        self.p = saved & 0x0F
        self.ie = 1 if enable else 0
        return 2

    def _alu(self, opcode: int, low: int) -> int:
        if low == 0x0:
            self.d = self._mem(self.x)  # LDX
        elif low == 0x1:
            self.d |= self._mem(self.x)  # OR
        elif low == 0x2:
            self.d &= self._mem(self.x)  # AND
        elif low == 0x3:
            self.d ^= self._mem(self.x)  # XOR
        elif low == 0x4:
            self._add(self._mem(self.x))  # ADD
        elif low == 0x5:
            self._sub_d(self._mem(self.x))  # SD
        elif low == 0x6:
            self._shr()  # SHR
        elif low == 0x7:
            self._sub_memory(self._mem(self.x))  # SM
        elif low == 0x8:
            self.d = self._immediate()  # LDI
        elif low == 0x9:
            self.d |= self._immediate()  # ORI
        elif low == 0xA:
            self.d &= self._immediate()  # ANI
        elif low == 0xB:
            self.d ^= self._immediate()  # XRI
        elif low == 0xC:
            self._add(self._immediate())  # ADI
        elif low == 0xD:
            self._sub_d(self._immediate())  # SDI
        elif low == 0xE:
            self._shl()  # SHL
        else:
            self._sub_memory(self._immediate())  # SMI
        return 2

    # -- whole runs -------------------------------------------------------
    def run(self, clocks: int) -> int:
        """Run for at most *clocks* clock cycles; returns the cycles used.

        Stopping on an instruction boundary is the point: a caller that budgets
        a frame gets whole instructions, never half of one.
        """

        spent = 0
        while spent < clocks and not self.halted:
            spent += self.step()
        return spent


def _signed(byte: int) -> int:
    """A short branch's displacement: two's complement in one byte."""

    return byte - 256 if byte >= 128 else byte
