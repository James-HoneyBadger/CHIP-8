"""The assembler and linker: listings become the bytes a 1977 machine was given.

The instruction set and the opcode encoding are Time Warp Studio's
(``native/src/lang/chip8.cpp``): the same mnemonics, the same two passes, the
same error text -- the messages are the text a learner reads, so they are part
of the interface.

On top of that this assembler links:

* a **data section** -- ``.string``, ``.bytes``, ``.words`` and ``.art`` declare
  data by name, and it is placed after the code, so a listing never has to jump
  over its own tables;
* ``.org`` to place code at an address;
* ``.include``/``LIB`` to pull another listing in, with the standard library
  (``chip8/lib``) on the search path.

A ``.ch8`` file is either of two things, and the NUL byte decides which:

* a **listing** -- text, assembled and linked here; or
* a **binary ROM** -- any file containing a NUL byte, copied to ``0x200``
  unchanged, up to the 3584 bytes that fit before the end of memory.

Both produce a :class:`Chip8Program`, so a caller that runs one does not care
which it was given.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .constants import MEMORY_SIZE, PROGRAM_ADDRESS, ROM_LIMIT
from .machine import Chip8Error, blank_memory

__all__ = [
    "Chip8Program",
    "assemble_chip8",
    "keypad_seed",
    "load_program",
    "load_program_file",
    "parse_key_mask",
]

_HEX_DIGITS = "0123456789abcdefABCDEF"
_DECIMAL = re.compile(r"[0-9]+")
_HEX = re.compile(r"[0-9a-fA-F]+")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

#: Where ``.include``/``LIB`` looks for a name that is not a path in the
#: including file's own directory: the standard library that ships with this
#: emulator.
LIBRARY_DIR = Path(__file__).resolve().parent / "lib"
#: How deep includes may nest before the assembler decides it is a mistake.
MAX_INCLUDE_DEPTH = 8

#: The directives that declare data, and so contribute to the data section
#: rather than to the code.
DATA_DIRECTIVES = (".STRING", ".TEXT", ".BYTES", ".WORDS", ".ART", ".ZERO")
_DATA_KINDS = {".STRING": "string", ".TEXT": "string", ".BYTES": "bytes",
               ".WORDS": "words", ".ART": "art", ".ZERO": "zero"}


@dataclass(slots=True)
class Chip8Program:
    """Assembled bytes, ready to be handed to a machine."""

    memory: bytearray = field(default_factory=blank_memory)
    labels: dict[str, int] = field(default_factory=dict)
    #: The 1-based listing line each opcode was assembled from, by memory
    #: address. Empty for a binary ROM, which has no lines to point at.
    source_lines: dict[int, int] = field(default_factory=dict)
    #: Where each included file's lines went, so the inspector can name them.
    sources: dict[int, tuple[str, int]] = field(default_factory=dict)
    entry: int = PROGRAM_ADDRESS
    #: How many bytes of code and data there are, from ``0x200``.
    size: int = 0
    #: Where the data section starts, or 0 when the listing declares no data.
    data_start: int = 0
    #: The files this program was linked from, in order, by name.
    includes: list[str] = field(default_factory=list)
    #: Whether the bytes came from a binary ROM rather than a listing.
    binary: bool = False


# ---------------------------------------------------------------------------
# Literals, registers and the small text helpers
# ---------------------------------------------------------------------------


def _trim(text: str) -> str:
    return text.strip()


def _upper(text: str) -> str:
    return text.upper()


def _is_space(character: str) -> bool:
    return character.isspace()


def _starts_with_ci(text: str, prefix: str) -> bool:
    return text[: len(prefix)].lower() == prefix.lower()


def parse_number(raw: str) -> int:
    """A literal: decimal unless it says otherwise, in either hex spelling."""

    text = _trim(raw)
    negative = False
    start = 0
    if start < len(text) and text[start] in "+-":
        negative = text[start] == "-"
        start += 1
    body = text[start:]
    base = 10
    if body.endswith(("h", "H")):
        body = body[:-1]
        base = 16
    elif _starts_with_ci(body, "0x"):
        body = body[2:]
        base = 16
    pattern = _HEX if base == 16 else _DECIMAL
    if not body or pattern.fullmatch(body) is None:
        raise Chip8Error(f"'{text}' is not a number")
    value = int(body, base)
    return -value if negative else value


def parse_byte(text: str) -> int:
    value = parse_number(text)
    if value < 0 or value > 0xFF:
        raise Chip8Error(f"{_trim(text)} is not a byte")
    return value


def parse_word(text: str) -> int:
    value = parse_number(text)
    if value < 0 or value > 0xFFFF:
        raise Chip8Error(f"{_trim(text)} is not a 16-bit value")
    return value


def parse_register(raw: str) -> int:
    """``V0``-``VF``, and nothing else.

    Two hex digits after the V is a named register, not a register and an
    operand.
    """

    text = _trim(raw)
    if len(text) != 2 or text[0] not in "Vv" or text[1] not in _HEX_DIGITS:
        raise Chip8Error(f"'{text}' is not a register (V0-VF)")
    return int(text[1], 16)


def is_register(text: str) -> bool:
    trimmed = _trim(text)
    return len(trimmed) == 2 and trimmed[0] in "Vv" and trimmed[1] in _HEX_DIGITS


def code_of(line: str, *, strip_comment: bool = True) -> str:
    """The line with its ``;`` comment removed and its ends trimmed.

    A ``;`` inside a quoted literal is part of the string, not a comment.
    """

    if not strip_comment:
        return _trim(line)
    out: list[str] = []
    quote = ""
    for character in line:
        if quote:
            out.append(character)
            if character == quote:
                quote = ""
        elif character in "\"'":
            out.append(character)
            quote = character
        elif character == ";":
            break
        else:
            out.append(character)
    return _trim("".join(out))


def label_prefix(text: str) -> tuple[str, int] | None:
    """The ``name:`` prefix of a statement, with the offset just past it."""

    if not text or not (text[0].isalpha() or text[0] == "_"):
        return None
    match = _IDENTIFIER.match(text)
    if match is None:  # pragma: no cover - the first character already decided
        return None
    index = match.end()
    after = index
    while after < len(text) and _is_space(text[after]):
        after += 1
    if after >= len(text) or text[after] != ":":
        return None
    return text[:index], after + 1


def split_operands(text: str) -> list[str]:
    """Operands split on commas or spaces, with quoted literals kept whole."""

    operands: list[str] = []
    current: list[str] = []
    quote = ""
    for character in text:
        if quote:
            current.append(character)
            if character == quote:
                quote = ""
            continue
        if character in "\"'":
            quote = character
            current.append(character)
            continue
        if character == "," or _is_space(character):
            if current:
                operands.append("".join(current))
                current.clear()
        else:
            current.append(character)
    if current:
        operands.append("".join(current))
    return operands


def size_of(text: str) -> int:
    end = 0
    while end < len(text) and not _is_space(text[end]):
        end += 1
    return 1 if _upper(text[:end]) == "DB" else 2


def parse_string_literal(raw: str) -> bytes:
    """A quoted literal, with the usual escapes.

    ``\\n``, ``\\t``, ``\\0``, ``\\\\``, ``\\"`` and ``\\xNN`` are understood;
    anything else after a backslash is itself.
    """

    text = _trim(raw)
    if len(text) < 2 or text[0] not in "\"'" or text[-1] != text[0]:
        raise Chip8Error(f'{text} is not a quoted string')
    body = text[1:-1]
    out = bytearray()
    index = 0
    escapes = {
        "n": 0x0A, "t": 0x09, "r": 0x0D, "0": 0x00, "\\": 0x5C,
        '"': 0x22, "'": 0x27, "e": 0x1B, "a": 0x07, "b": 0x08, "f": 0x0C,
        "v": 0x0B,
    }
    while index < len(body):
        character = body[index]
        if character != "\\":
            out.append(ord(character) & 0xFF)
            index += 1
            continue
        index += 1
        if index >= len(body):
            raise Chip8Error(f"{text} ends with a backslash")
        escape = body[index]
        if escape == "x":
            digits = body[index + 1 : index + 3]
            if len(digits) != 2 or any(d not in _HEX_DIGITS for d in digits):
                raise Chip8Error(f"{text} has a broken \\x escape")
            out.append(int(digits, 16))
            index += 3
        elif escape in escapes:
            out.append(escapes[escape])
            index += 1
        else:
            out.append(ord(escape) & 0xFF)
            index += 1
    return bytes(out)


def parse_key_mask(text: str) -> int:
    """The key mask a literal spells: every hex digit names one key."""

    keys = 0
    for character in text:
        if character in _HEX_DIGITS:
            keys |= 1 << int(character, 16)
    return keys


def keypad_seed(source: str) -> int:
    """The key mask a listing seeds with ``; keys`` comments.

    Any line whose first non-space text is ``; keys`` contributes every
    hexadecimal digit after it to the mask. It is a testing and demonstration
    aid, not an input mechanism for people.
    """

    keys = 0
    for raw in source.split("\n"):
        line = _trim(raw)
        if len(line) >= 7 and _starts_with_ci(line, "; keys"):
            keys |= parse_key_mask(line[6:])
    return keys


# ---------------------------------------------------------------------------
# Statements: the shape everything else works on
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Statement:
    """One thing to place: an instruction, a directive, or a data blob."""

    text: str
    #: ``line 7`` for the listing being assembled, ``text.inc:12`` for an
    #: included file. This is what an error message starts with.
    origin: str
    #: The listing line, for the inspector's map, or None in an included file.
    line: int | None = None
    #: Set for a data declaration: its name, kind and payload lines.
    name: str = ""
    kind: str = ""
    payload: list[str] = field(default_factory=list)
    inline: str = ""

    @property
    def is_data(self) -> bool:
        return bool(self.kind)

    @property
    def is_org(self) -> bool:
        return not self.kind and _upper(self.text.split(" ")[0]) == ".ORG"


def _resolve_include(name: str, base_dir: Path | None) -> Path:
    """Find an included listing: the local directory first, then the library."""

    cleaned = name.strip().strip("\"'")
    candidates: list[Path] = []
    raw = Path(cleaned)
    suffixes = [""] if raw.suffix else [".inc", ".ch8", ""]
    for suffix in suffixes:
        target = raw.with_name(raw.name + suffix) if suffix else raw
        if base_dir is not None:
            candidates.append(base_dir / target)
        candidates.append(LIBRARY_DIR / target)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    looked = ", ".join(str(c) for c in candidates[:2])
    raise Chip8Error(f"cannot find '{cleaned}' to include (looked in {looked}, ...)")


def _expand(
    source: str,
    origin_name: str,
    base_dir: Path | None,
    stack: list[Path],
    included: list[str],
    user: bool,
) -> list[Statement]:
    """Split a listing into statements, pulling includes in where they appear."""

    statements: list[Statement] = []
    lines = source.split("\n")
    if lines and lines[-1] == "" and source:
        lines.pop()

    for number, raw in enumerate(lines, start=1):
        text = code_of(raw)
        if not text:
            continue
        label = label_prefix(text)
        body = text if label is None else _trim(text[label[1] :])
        mnemonic = _upper(body.split(" ")[0]) if body else ""

        if mnemonic in ("LIB", ".INCLUDE", ".INC"):
            rest = body[len(body.split(" ")[0]) :]
            operands = split_operands(rest)
            if not operands:
                raise Chip8Error(f"line {number}: {mnemonic} takes a file name")
            if len(stack) >= MAX_INCLUDE_DEPTH:
                raise Chip8Error(
                    f"line {number}: includes are {MAX_INCLUDE_DEPTH} deep already; "
                    "there is probably a cycle"
                )
            path = _resolve_include(operands[0], base_dir)
            if path in stack:
                raise Chip8Error(f"line {number}: {path.name} includes itself")
            included.append(path.name)
            inner = path.read_text(encoding="utf-8", errors="replace")
            statements.extend(
                _expand(
                    inner,
                    path.name,
                    path.parent,
                    [*stack, path],
                    included,
                    user=False,
                )
            )
            if label is not None:
                raise Chip8Error(
                    f"line {number}: {label[0]}: labels an include, which has no "
                    "address of its own"
                )
            continue

        statement = Statement(
            text=text,
            origin=f"line {number}" if user else f"{origin_name}:{number}",
            line=number if user else None,
        )

        if mnemonic in _DATA_KINDS:
            kind = _DATA_KINDS[mnemonic]
            rest = _trim(body[len(body.split(" ")[0]) :])
            operands = split_operands(rest)
            if not operands:
                raise Chip8Error(
                    f"{statement.origin}: {mnemonic} takes a name, as "
                    f"{mnemonic} NAME"
                )
            name = operands[0]
            if _IDENTIFIER.fullmatch(name) is None:
                raise Chip8Error(
                    f"{statement.origin}: '{name}' is not a data name"
                )
            statement.name = name
            statement.kind = kind
            statement.inline = _trim(rest[len(name) :])
            if not statement.inline:
                statement.payload = []  # filled in by the block scan
                statement.kind = kind
                # Marked so the caller knows to scan for `.end`.
                statement.text = f".{kind.upper()} {name}"
            else:
                statement.text = f".{kind.upper()} {name}"

        statements.append(statement)

    return statements


def _scan_blocks(statements: list[Statement]) -> list[Statement]:
    """Fold ``.bytes NAME`` … ``.end`` blocks into the statements they follow."""

    result: list[Statement] = []
    index = 0
    while index < len(statements):
        statement = statements[index]
        index += 1
        if statement.is_data and not statement.inline:
            payload: list[str] = []
            closed = False
            while index < len(statements):
                candidate = statements[index]
                index += 1
                head = _upper(candidate.text.split(" ")[0]) if candidate.text else ""
                if head == ".END":
                    closed = True
                    break
                if candidate.is_data:
                    raise Chip8Error(
                        f"{candidate.origin}: {candidate.text} is inside a data block; "
                        "close it with .end"
                    )
                payload.append(candidate.text)
            if not closed:
                raise Chip8Error(
                    f"{statement.origin}: {statement.text} is not closed by .end"
                )
            statement.payload = payload
        elif statement.is_data and statement.inline:
            statement.payload = [statement.inline]
        else:
            head = _upper(statement.text.split(" ")[0]) if statement.text else ""
            if head == ".END":
                raise Chip8Error(f"{statement.origin}: .end without a block to close")
        result.append(statement)
    return result


def _data_size(statement: Statement) -> int:
    """How many bytes a data declaration takes, without encoding it twice."""

    if statement.kind == "alias":
        return 0
    if statement.kind == "string":
        return sum(len(parse_string_literal(line)) for line in statement.payload) + 1
    if statement.kind == "bytes":
        return sum(len(split_operands(line)) for line in statement.payload)
    if statement.kind == "words":
        return 2 * sum(len(split_operands(line)) for line in statement.payload)
    if statement.kind == "art":
        width, rows = _art_shape(statement)
        return len(rows) * _art_stride(width)
    if statement.kind == "zero":
        return parse_word(statement.inline)
    raise Chip8Error(f"{statement.origin}: unknown data kind {statement.kind}")


def _art_shape(statement: Statement) -> tuple[int, list[str]]:
    rows = [line for line in statement.payload if line.strip()]
    width = max((len(line) for line in rows), default=0)
    return width, rows


def _art_stride(width: int) -> int:
    return max(1, (width + 7) // 8)


_ART_ON = set("#*Xx1+@")


def _art_bytes(statement: Statement) -> bytes:
    width, rows = _art_shape(statement)
    if width == 0:
        return b""
    stride = _art_stride(width)
    out = bytearray()
    for row in rows:
        padded = row.ljust(width)
        for byte_index in range(stride):
            value = 0
            for bit in range(8):
                column = byte_index * 8 + bit
                if column < width and padded[column] in _ART_ON:
                    value |= 0x80 >> bit
            out.append(value)
    return bytes(out)


# ---------------------------------------------------------------------------
# Encoding one instruction
# ---------------------------------------------------------------------------


def lookup(label: str, labels: dict[str, int]) -> int | None:
    """A name's value, understanding the ``_hi``/``_lo`` suffixes.

    ``LD I, nnn`` takes twelve bits, so a name is always the whole address; but
    a routine that has to build one from a byte at a time -- which is every
    routine that walks a string -- needs the two halves separately, and there is
    no ``SUB`` immediate to shift one out.
    """

    if label in labels:
        return labels[label]
    for suffix, extract in (
        ("_hi", lambda value: (value >> 8) & 0xF),
        ("_lo", lambda value: value & 0xFF),
    ):
        if label.endswith(suffix):
            base = label[: -len(suffix)]
            if base in labels:
                return extract(labels[base])
    return None


def encode(text: str, labels: dict[str, int], origin: str) -> list[int]:
    """The bytes one statement assembles to."""

    split = text.find(" ")
    mnemonic = _upper(text if split < 0 else text[:split])
    operands = [] if split < 0 else split_operands(text[split + 1 :])

    def target(raw: str) -> int:
        operand = _trim(raw)
        found = lookup(operand, labels)
        if found is not None:
            return found
        return parse_word(operand)

    def byte_operand(raw: str) -> int:
        """A byte that may also be a name, such as `msg_len`."""

        operand = _trim(raw)
        found = lookup(operand, labels)
        if found is not None:
            if found > 0xFF:
                raise Chip8Error(f"{operand} is not a byte")
            return found
        return parse_byte(operand)

    def fail(expectation: str) -> Chip8Error:
        return Chip8Error(f"{origin}: {mnemonic} {expectation}")

    if mnemonic == "DB":
        if len(operands) != 1:
            raise fail("takes one byte")
        return [parse_byte(operands[0])]
    if mnemonic == "DW":
        if len(operands) != 1:
            raise fail("takes one word")
        value = parse_word(operands[0])
        return [value >> 8, value & 0xFF]

    if mnemonic == "CLS":
        return [0x00, 0xE0]
    if mnemonic == "RET":
        return [0x00, 0xEE]
    if mnemonic == "SYS":
        # 0NNN calls a routine on the RCA 1802 that hosted the original
        # interpreter. Nothing here can run it, so the machine ignores it, as
        # every interpreter since the original has; the assembler can still
        # spell it so a listing that contains one assembles.
        if len(operands) != 1:
            raise fail("takes an address")
        value = target(operands[0])
        return [value >> 8, value & 0xFF]
    if mnemonic == "HALT":
        # 0x0000 is unused by the original set, so a program can stop instead of
        # spinning on a jump-to-self to mark the end.
        return [0x00, 0x00]

    if mnemonic == "JP" and len(operands) == 1:
        value = target(operands[0])
        return [0x10 | (value >> 8), value & 0xFF]
    if mnemonic == "JP" and len(operands) == 2:
        # Bnnn carries the address in its twelve bits; V0 is added at run time
        # rather than encoded. Naming another register would assemble to an
        # instruction that ignores it, so that is refused rather than accepted.
        if parse_register(operands[0]) != 0:
            raise fail("uses V0 only; Bnnn adds V0")
        value = target(operands[1])
        return [0xB0 | (value >> 8), value & 0xFF]
    if mnemonic == "CALL":
        if len(operands) != 1:
            raise fail("takes an address")
        value = target(operands[0])
        return [0x20 | (value >> 8), value & 0xFF]

    if mnemonic in ("SE", "SNE") and len(operands) == 2:
        x = parse_register(operands[0])
        if is_register(operands[1]):
            y = parse_register(operands[1])
            base = 0x50 if mnemonic == "SE" else 0x90
            return [base | x, y << 4]
        base = 0x30 if mnemonic == "SE" else 0x40
        return [base | x, byte_operand(operands[1])]

    if mnemonic == "LD" and len(operands) == 2:
        left = _upper(operands[0])
        right = _upper(operands[1])
        if left == "I":
            value = target(operands[1])
            return [0xA0 | (value >> 8), value & 0xFF]
        # The conversions and the timers name the *destination* on the left, so
        # `LD F, V0` reads "point I at the glyph for V0".
        if left in ("DT", "ST"):
            x = parse_register(operands[1])
            return [0xF0 | x, 0x15 if left == "DT" else 0x18]
        if left == "F":
            x = parse_register(operands[1])
            return [0xF0 | x, 0x29]
        if left == "B":
            x = parse_register(operands[1])
            return [0xF0 | x, 0x33]
        if left == "[I]":
            x = parse_register(operands[1])
            return [0xF0 | x, 0x55]
        if right == "[I]":
            x = parse_register(operands[0])
            return [0xF0 | x, 0x65]
        if right == "DT":
            x = parse_register(operands[0])
            return [0xF0 | x, 0x07]
        if right == "K":
            x = parse_register(operands[0])
            return [0xF0 | x, 0x0A]
        x = parse_register(operands[0])
        if is_register(operands[1]):
            return [0x80 | x, parse_register(operands[1]) << 4]
        return [0x60 | x, byte_operand(operands[1])]

    if mnemonic == "ADD" and len(operands) == 2:
        if _upper(operands[0]) == "I":
            x = parse_register(operands[1])
            return [0xF0 | x, 0x1E]
        x = parse_register(operands[0])
        if is_register(operands[1]):
            return [0x80 | x, (parse_register(operands[1]) << 4) | 0x4]
        return [0x70 | x, byte_operand(operands[1])]

    if mnemonic in ("OR", "AND", "XOR", "SUB", "SUBN") and len(operands) == 2:
        x = parse_register(operands[0])
        y = parse_register(operands[1])
        low = {"OR": 0x1, "AND": 0x2, "XOR": 0x3, "SUB": 0x5, "SUBN": 0x7}[mnemonic]
        return [0x80 | x, (y << 4) | low]

    if mnemonic in ("SHR", "SHL") and operands:
        # The y operand is accepted and ignored: the 1977 machine shifts Vx in
        # place, and a listing written for a later emulator still assembles.
        x = parse_register(operands[0])
        return [0x80 | x, 0x06 if mnemonic == "SHR" else 0x0E]

    if mnemonic == "RND" and len(operands) == 2:
        x = parse_register(operands[0])
        return [0xC0 | x, byte_operand(operands[1])]

    if mnemonic == "DRW" and len(operands) == 3:
        x = parse_register(operands[0])
        y = parse_register(operands[1])
        rows = byte_operand(operands[2])
        if rows > 15:
            raise fail("draws at most 15 rows")
        return [0xD0 | x, (y << 4) | rows]

    if mnemonic in ("SKP", "SKNP") and len(operands) == 1:
        x = parse_register(operands[0])
        return [0xE0 | x, 0x9E if mnemonic == "SKP" else 0xA1]

    raise fail("is not an instruction form this assembler knows")


# ---------------------------------------------------------------------------
# The two passes
# ---------------------------------------------------------------------------


#: Pseudo-instructions that stand for a few real ones. They exist because the
#: machine cannot read `I`, so a routine that walks a string has to be handed
#: the address in two registers, and writing that out every time is noise.
PSEUDO = {
    ".PRINT": ("{name}_hi", "{name}_lo", 3),
    ".PRINTC": ("{name}_hi", "{name}_lo", 4),
    ".NUM": (None, None, 1),
}


def expand_pseudo(text: str) -> list[str] | None:
    """The instructions a pseudo-instruction stands for, or None."""

    split = text.find(" ")
    mnemonic = _upper(text if split < 0 else text[:split])
    if mnemonic not in PSEUDO:
        return None
    operands = [] if split < 0 else split_operands(text[split + 1 :])
    if mnemonic == ".NUM":
        # The value is in V2; a name here would be silently ignored, so it is
        # refused instead of assembling something that does not use it.
        if operands:
            raise Chip8Error(
                ".num takes no operand; put the value in V2 and write `.num`"
            )
        return ["CALL t_num"]
    if not operands:
        raise Chip8Error(f"{mnemonic} takes a string name")
    name = operands[0]
    if mnemonic == ".PRINT":
        return [f"LD V4, {name}_hi", f"LD V5, {name}_lo", "CALL t_print"]
    if mnemonic == ".PRINTC":
        return [
            f"LD V2, {name}_len",
            f"LD V4, {name}_hi",
            f"LD V5, {name}_lo",
            "CALL t_center",
        ]
    return None  # pragma: no cover


def _split_label(statement: Statement) -> tuple[str, str] | None:
    label = label_prefix(statement.text)
    if label is None:
        return None
    return label[0], _trim(statement.text[label[1] :])


def assemble_chip8(
    source: str | bytes, base_dir: str | Path | None = None
) -> Chip8Program:
    """Assemble and link *source*, or load it as a binary ROM when it has a NUL.

    *base_dir* is where ``.include`` looks first; it should be the directory of
    the file being assembled, so a listing's includes resolve next to itself.
    """

    text = source.decode("latin-1") if isinstance(source, bytes) else source
    program = Chip8Program()
    directory = Path(base_dir) if base_dir is not None else None

    # A NUL byte is not a thing a listing contains and is in every real ROM, so
    # it is the marker that decides which of the two loaders runs.
    if "\0" in text:
        rom = text.encode("latin-1")
        if len(rom) > ROM_LIMIT:
            raise Chip8Error(
                f"the ROM is {len(rom)} bytes; memory from 0x200 holds {ROM_LIMIT}"
            )
        program.memory[PROGRAM_ADDRESS : PROGRAM_ADDRESS + len(rom)] = rom
        program.size = len(rom)
        program.binary = True
        return program

    included: list[str] = []
    expanded = _expand(text, "listing", directory, [], included, user=True)
    statements = _scan_blocks(expanded)
    program.includes = included

    # -- pass 1a: layout the code ------------------------------------------
    labels = program.labels
    code: list[tuple[int, Statement, str]] = []
    pending_labels: list[tuple[str, Statement]] = []
    address = PROGRAM_ADDRESS
    highest = PROGRAM_ADDRESS

    for statement in statements:
        body = statement.text
        name = ""
        if not statement.is_data:
            split = _split_label(statement)
            if split is not None:
                name, body = split
        if statement.kind == "alias":
            name = statement.name
            pending_labels.append((name, statement))
            continue
        if name:
            if name in labels:
                raise Chip8Error(
                    f"{statement.origin}: label {name} is defined twice"
                )
            labels[name] = address
        if statement.is_data:
            pending_labels.append((statement.name, statement))
            continue
        if not body:
            # A label on a line of its own: it names the address of whatever
            # comes next, and costs nothing.
            continue
        mnemonic = _upper(body.split(" ")[0]) if body else ""
        if mnemonic == ".ORG":
            operands = split_operands(body[len(body.split(" ")[0]) :])
            if len(operands) != 1:
                raise Chip8Error(f"{statement.origin}: .org takes an address")
            target = parse_number(operands[0])
            if target < PROGRAM_ADDRESS or target >= MEMORY_SIZE:
                raise Chip8Error(
                    f"{statement.origin}: .org {target} is outside 0x200-0xFFF"
                )
            if target < address:
                raise Chip8Error(
                    f"{statement.origin}: .org {target:03X} moves backwards over code"
                )
            address = target
            highest = max(highest, address)
            continue
        if mnemonic == ".END":
            continue
        expansion = expand_pseudo(body)
        if expansion is not None:
            code.append((address, statement, body))
            if statement.line is not None:
                program.source_lines[address] = statement.line
            address += 2 * len(expansion)
            highest = max(highest, address)
            continue
        code.append((address, statement, body))
        if statement.line is not None:
            program.source_lines[address] = statement.line
        address += size_of(body)
        highest = max(highest, address)

    # -- pass 1b: place the data section -----------------------------------
    data: list[tuple[int, Statement]] = []
    data_start = highest
    if any(statement.is_data for statement in statements):
        program.data_start = highest
    address = highest
    for statement in statements:
        if not statement.is_data:
            continue
        labels[statement.name] = address
        # Storage size and logical length are different things for a string:
        # the NUL is stored but is not a character.
        storage = _data_size(statement)
        logical = storage
        if statement.kind == "string":
            logical = sum(len(parse_string_literal(line)) for line in statement.payload)
        # Every blob also names its own length: `LD V2, msg_len` is how a
        # routine that has to know how long a string is gets told.
        labels[f"{statement.name}_len"] = logical
        data.append((address, statement))
        address += storage

    if address > MEMORY_SIZE:
        raise Chip8Error(
            f"the program needs {address - PROGRAM_ADDRESS} bytes and memory ends at 0x1000"
        )
    program.size = address - PROGRAM_ADDRESS
    if not program.data_start:
        program.data_start = 0

    # -- pass 2: encode ----------------------------------------------------
    for at, statement, body in code:
        expansion = expand_pseudo(body)
        if expansion is not None:
            emitted = []
            for instruction in expansion:
                emitted.extend(encode(instruction, labels, statement.origin))
        else:
            emitted = encode(body, labels, statement.origin)
        program.memory[at : at + len(emitted)] = bytes(emitted)
        if statement.line is not None:
            program.sources[at] = ("listing", statement.line)

    for at, statement in data:
        if statement.kind == "string":
            body = b"".join(parse_string_literal(line) for line in statement.payload)
            body += b"\0"
        elif statement.kind == "bytes":
            values = [
                parse_byte(operand)
                for line in statement.payload
                for operand in split_operands(line)
            ]
            body = bytes(values)
        elif statement.kind == "words":
            body = bytearray()
            for line in statement.payload:
                for operand in split_operands(line):
                    value = lookup(operand, labels)
                    if value is None:
                        value = parse_word(operand)
                    body += bytes((value >> 8, value & 0xFF))
            body = bytes(body)
        elif statement.kind == "art":
            body = _art_bytes(statement)
        elif statement.kind == "zero":
            body = b"\x00" * _data_size(statement)
        else:  # pragma: no cover - guarded by _data_size
            raise Chip8Error(f"{statement.origin}: unknown data kind")
        program.memory[at : at + len(body)] = body

    return program


def load_program(data: bytes, base_dir: str | Path | None = None) -> Chip8Program:
    """A ``.ch8`` file's bytes, whichever of the two things it is."""

    return assemble_chip8(data, base_dir)


def load_program_file(path: str | Path) -> Chip8Program:
    """Read a ``.ch8`` file exactly as stored and load it.

    The file is not decoded as text on the way in, so no byte can be normalised
    before the NUL test decides what it is. Includes resolve next to the file.
    """

    target = Path(path)
    return load_program(target.read_bytes(), target.parent)
