"""A two-pass assembler for COSMAC 1802 listings.

The 1802 has no immediate instruction that carries its operand in the opcode:
``LDI`` reads the byte at M(R(P)) and steps the program counter over it. A
listing therefore looks like annotated machine code, and this assembler keeps it
that way -- mnemonic, optional register, optional value, on one line:

    FETCH:  LDN  RA          ; D = the opcode's high byte
            ANI  0xF0        ; the operand is the next byte of code
            SHR
            SHR              ; D = the opcode group, 0 to 15

Labels, ``.org`` to place code, ``DB``/``DW`` for data and ``label = value`` for
constants are enough to write the firmware in ``chip8.vipfirmware`` and any VIP
program a person wants to try. ``low(...)`` and ``high(...)`` are there because
the 1802 needs both halves of an address, one byte at a time.

Two passes, because a branch can go backwards: the first pass works out where
every label is, the second writes the bytes. A branch that cannot reach its
target is an error rather than a surprise at run time.

The 1802's arithmetic is not the arithmetic of the processors that followed it,
and the immediate forms are named for their destination, not their source:
``SDI`` is *memory minus D*, ``SMI`` is *D minus memory*. Both are in the table;
neither is a typo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["VipAsmError", "VipProgram", "assemble_vip"]


class VipAsmError(ValueError):
    """A listing that cannot be assembled, with the line it failed on."""


#: Short branches and skips: mnemonic -> the low nibble of 0x3n.
SHORT_BRANCHES = {
    "BR": 0x0,
    "BQ": 0x1,
    "BZ": 0x2,
    "BDF": 0x3,
    "BGE": 0x3,
    "BPZ": 0x3,
    "B1": 0x4,
    "B2": 0x5,
    "B3": 0x6,
    "B4": 0x7,
    "SKP": 0x8,
    "NBR": 0x8,
    "BNQ": 0x9,
    "BNZ": 0xA,
    "BNF": 0xB,
    "BL": 0xB,
    "BM": 0xB,
    "BN1": 0xC,
    "BN2": 0xD,
    "BN3": 0xE,
    "BN4": 0xF,
}

#: Long branches: mnemonic -> the low nibble of 0xCn. A 2-byte address follows.
LONG_BRANCHES = {
    "LBR": 0x0,
    "LBQ": 0x1,
    "LBZ": 0x2,
    "LBDF": 0x3,
    "LBNQ": 0x9,
    "LBNZ": 0xA,
    "LBNF": 0xB,
}

#: Long skips: mnemonic -> the low nibble of 0xCn. These have no operand.
LONG_SKIPS = {
    "LSNQ": 0x5,
    "LSNZ": 0x6,
    "LSNF": 0x7,
    "LSKP": 0x8,
    "NLBR": 0x8,
    "LSIE": 0xC,
    "LSQ": 0xD,
    "LSZ": 0xE,
    "LSDF": 0xF,
}

#: Register-only mnemonics: name -> (opcode base, has a register).
REGISTER_OPS = {
    "LDN": 0x00,
    "INC": 0x10,
    "DEC": 0x20,
    "LDA": 0x40,
    "STR": 0x50,
    "GLO": 0x80,
    "GHI": 0x90,
    "PLO": 0xA0,
    "PHI": 0xB0,
    "SEP": 0xD0,
    "SEX": 0xE0,
}

#: Mnemonics with no operand at all.
PLAIN_OPS = {
    "IDL": 0x00,
    "IRX": 0x60,
    "RET": 0x70,
    "DIS": 0x71,
    "LDXA": 0x72,
    "STXD": 0x73,
    "ADC": 0x74,
    "SDB": 0x75,
    "SHRC": 0x76,
    "RSHR": 0x76,
    "SMB": 0x77,
    "SAV": 0x78,
    "MARK": 0x79,
    "REQ": 0x7A,
    "SEQ": 0x7B,
    "SHLC": 0x7E,
    "RSHL": 0x7E,
    "NOP": 0xC4,
    "LDX": 0xF0,
    "OR": 0xF1,
    "AND": 0xF2,
    "XOR": 0xF3,
    "ADD": 0xF4,
    "SD": 0xF5,
    "SHR": 0xF6,
    "SM": 0xF7,
    "SHL": 0xFE,
}

#: Immediate mnemonics: name -> opcode. One value byte follows in the code.
IMMEDIATE_OPS = {
    "ADCI": 0x7C,
    "SDBI": 0x7D,
    "SMBI": 0x7F,
    "LDI": 0xF8,
    "ORI": 0xF9,
    "ANI": 0xFA,
    "XRI": 0xFB,
    "ADI": 0xFC,
    "SDI": 0xFD,
    "SMI": 0xFF,
}

#: The two port instructions: "OUT 4" is 0x64, "INP 1" is 0x69.
PORT_OPS = {"OUT": 0x60, "INP": 0x68}

_LABEL = re.compile(r"^[A-Za-z_.][A-Za-z0-9_.]*$")
_CONSTANT = re.compile(r"^([A-Za-z_.][A-Za-z0-9_.]*)\s*=\s*(.+)$")


@dataclass
class _Line:
    number: int
    label: str | None
    mnemonic: str
    operand: str


@dataclass
class VipProgram:
    """Assembled code, as the pieces of memory it occupies.

    A 1802 program is not one block: the boot stub sits at 0x0000 so the chip
    finds it after a reset, and the firmware lives in ROM at 0x1000. Keeping the
    segments apart is what lets one listing cover both.
    """

    segments: tuple[tuple[int, bytes], ...] = ()
    labels: dict[str, int] = field(default_factory=dict)
    entry: int = 0

    def load_into(self, memory, *, writable=None) -> None:
        """Copy every segment into *memory*, skipping read-only addresses."""

        for address, data in self.segments:
            for offset, value in enumerate(data):
                where = address + offset
                if writable is not None and not writable(where):
                    continue
                memory.write(where, value)

    @property
    def size(self) -> int:
        return sum(len(data) for _, data in self.segments)

    def bytes(self, size: int = 0x10000) -> bytes:
        """The whole image, with the gaps between segments left as zeros."""

        image = bytearray(size)
        for address, data in self.segments:
            image[address : address + len(data)] = data
        return bytes(image)


def assemble_vip(source: str, *, origin: int = 0x1000) -> VipProgram:
    """Assemble a listing. *origin* is where code starts before any ``.org``."""

    lines = _parse(source)
    labels: dict[str, int] = {}

    # Constants first: a label may be used as the origin of a section, and a
    # constant may be used anywhere, so both are known before any code is
    # placed. A constant may refer to the constants above it.
    for line in lines:
        if line.mnemonic != ".EQU":
            continue
        match = _CONSTANT.match(line.operand)
        if not match:
            raise VipAsmError(
                f"line {line.number}: a constant needs a name and a value"
            )
        labels[match.group(1)] = _value(match.group(2), labels, line)

    address = origin
    #: Where each line's instruction begins, so the second pass can encode
    #: branches relative to the right place.
    places: list[int] = []
    for line in lines:
        places.append(address)
        if line.mnemonic == ".ORG":
            address = _value(line.operand, labels, line)
            continue
        if line.mnemonic == ".EQU":
            continue
        if line.label is not None:
            if line.label in labels:
                raise VipAsmError(
                    f"line {line.number}: the label '{line.label}' is used twice"
                )
            labels[line.label] = address
        address += _size(line, labels)

    emitted: dict[int, bytearray] = {}
    for line, where in zip(lines, places):
        if line.mnemonic in (".ORG", ".EQU"):
            continue
        data = _encode(line, where, labels)
        emitted.setdefault(where, bytearray()).extend(data)

    segments: list[tuple[int, bytes]] = []
    for where in sorted(emitted):
        data = emitted[where]
        if segments and segments[-1][0] + len(segments[-1][1]) == where:
            segments[-1] = (segments[-1][0], segments[-1][1] + bytes(data))
        else:
            segments.append((where, bytes(data)))
    return VipProgram(segments=tuple(segments), labels=dict(labels), entry=origin)


def _parse(source: str) -> list[_Line]:
    lines: list[_Line] = []
    for number, raw in enumerate(source.splitlines(), start=1):
        text = raw.split(";", 1)[0].rstrip()
        if not text.strip():
            continue
        # A constant definition is reported as a .equ line so both passes see
        # it the same way.
        constant = _CONSTANT.match(text.strip())
        if constant:
            lines.append(_Line(number, None, ".EQU", text.strip()))
            continue
        label = None
        stripped = text.lstrip()
        if ":" in stripped:
            candidate, _, rest = stripped.partition(":")
            if _LABEL.match(candidate.strip()):
                label = candidate.strip()
                text = rest
        parts = text.split(None, 1)
        if not parts:
            lines.append(_Line(number, label, "", ""))
            continue
        mnemonic = parts[0].upper()
        operand = parts[1].strip() if len(parts) > 1 else ""
        lines.append(_Line(number, label, mnemonic, operand))
    return lines


def _size(line: _Line, labels: dict[str, int]) -> int:
    mnemonic = line.mnemonic
    if not mnemonic:
        return 0
    if mnemonic in ("DB", ".BYTE", "DW", ".WORD"):
        width = 2 if mnemonic in ("DW", ".WORD") else 1
        return len(_operands(line.operand, line)) * width
    if mnemonic in SHORT_BRANCHES:
        return 2
    if mnemonic in LONG_BRANCHES:
        return 3
    if mnemonic in IMMEDIATE_OPS:
        return 2
    # A register or a port names itself inside the opcode, so one byte is all
    # the line occupies whether or not it has an operand.
    return 1


def _operands(text: str, line: _Line) -> list[str]:
    if not text.strip():
        raise VipAsmError(f"line {line.number}: this directive needs a value")
    parts = [part.strip() for part in text.split(",")]
    if any(not part for part in parts):
        raise VipAsmError(f"line {line.number}: an empty value in '{text}'")
    return parts


def _encode(line: _Line, where: int, labels: dict[str, int]) -> bytes:
    mnemonic = line.mnemonic
    operand = line.operand
    number = line.number

    if mnemonic in ("DB", ".BYTE"):
        values = (_value(part, labels, line) for part in _operands(operand, line))
        return bytes(_byte(value, line) for value in values)
    if mnemonic in ("DW", ".WORD"):
        data = bytearray()
        for part in _operands(operand, line):
            value = _value(part, labels, line) & 0xFFFF
            data += bytes((value >> 8, value & 0xFF))
        return bytes(data)
    if not mnemonic:
        return b""

    if mnemonic in SHORT_BRANCHES:
        target = _value(operand, labels, line)
        displacement = target - (where + 2)
        if not -128 <= displacement <= 127:
            raise VipAsmError(
                f"line {number}: {mnemonic} cannot reach {_hex(target)} from {_hex(where)}"
            )
        return bytes((0x30 | SHORT_BRANCHES[mnemonic], displacement & 0xFF))

    if mnemonic in LONG_BRANCHES:
        target = _value(operand, labels, line) & 0xFFFF
        return bytes((0xC0 | LONG_BRANCHES[mnemonic], target >> 8, target & 0xFF))

    if mnemonic in LONG_SKIPS:
        if operand:
            raise VipAsmError(f"line {number}: {mnemonic} takes no operand")
        return bytes((0xC0 | LONG_SKIPS[mnemonic],))

    if mnemonic in IMMEDIATE_OPS:
        value = _byte(_value(operand, labels, line), line)
        return bytes((IMMEDIATE_OPS[mnemonic], value))

    if mnemonic in PORT_OPS:
        port = _value(operand, labels, line)
        if not 1 <= port <= 7:
            raise VipAsmError(f"line {number}: there is no port {port}")
        return bytes((PORT_OPS[mnemonic] | port,))

    if mnemonic in PLAIN_OPS:
        if operand:
            raise VipAsmError(f"line {number}: {mnemonic} takes no operand")
        return bytes((PLAIN_OPS[mnemonic],))

    if mnemonic in REGISTER_OPS:
        register = _register(operand, line)
        return bytes((REGISTER_OPS[mnemonic] | register,))

    raise VipAsmError(f"line {number}: there is no {mnemonic} instruction")


def _register(text: str, line: _Line) -> int:
    name = text.strip().upper()
    if not name:
        raise VipAsmError(f"line {line.number}: {line.mnemonic} needs a register")
    if name.startswith("R"):
        name = name[1:]
    try:
        register = int(name, 16)
    except ValueError as failure:
        raise VipAsmError(
            f"line {line.number}: '{text}' is not a register"
        ) from failure
    if not 0 <= register <= 15:
        raise VipAsmError(f"line {line.number}: there is no register R{text}")
    return register


def _byte(value: int, line: _Line) -> int:
    if not 0 <= value <= 0xFF:
        raise VipAsmError(f"line {line.number}: {value} does not fit in a byte")
    return value


def _value(text: str, labels: dict[str, int], line: _Line) -> int:
    """A number, a label, or either of those plus or minus a number."""

    expression = text.strip()
    if not expression:
        raise VipAsmError(f"line {line.number}: a value is missing")
    for name, function in (("low", _low), ("high", _high)):
        prefix = f"{name}("
        if expression.lower().startswith(prefix) and expression.endswith(")"):
            inner = expression[len(prefix) : -1]
            return function(_value(inner, labels, line))
    total = 0
    sign = 1
    token = ""
    for character in expression.replace(" ", "") + "+":
        if character in "+-":
            if token:
                total += sign * _term(token, labels, line)
                token = ""
            sign = 1 if character == "+" else -1
        else:
            token += character
    if token:  # pragma: no cover - the sentinel above always flushes the token
        total += sign * _term(token, labels, line)
    return total


def _term(token: str, labels: dict[str, int], line: _Line) -> int:
    if token in labels:
        return labels[token]
    text = token.replace("_", "")
    try:
        if text.lower().startswith("0x"):
            return int(text, 16)
        if text.lower().startswith("0b"):
            return int(text, 2)
        return int(text, 10)
    except ValueError as failure:
        raise VipAsmError(
            f"line {line.number}: '{token}' is not a number or a label"
        ) from failure


def _low(value: int) -> int:
    return value & 0xFF


def _high(value: int) -> int:
    return (value >> 8) & 0xFF


def _hex(value: int) -> str:
    return f"0x{value:04X}"
