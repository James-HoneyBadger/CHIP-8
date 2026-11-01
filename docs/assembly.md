# The CHIP-8 assembler

> This page is the instruction and encoding reference. The directives, the data
> section, linking and the standard library are in
> [the language](language.md); the library's routines are in
> [the standard library](library.md).

A listing is readable text that becomes the bytes a 1977 machine would have been
given. A `.ch8` file may be a listing or a binary ROM, and the NUL byte decides
which: no listing contains one, and every real ROM does.

```sh
python3 -m chip8 asm program.ch8              # print the bytes and the labels
python3 -m chip8 asm program.ch8 -o program.c8 # or write them
python3 -m chip8 run program.ch8              # assemble and run
python3 -m chip8 info program.ch8 --labels    # describe it without running
```

---

## A listing

```text
; A dot that walks right, one pixel a frame.
start:  LD V0, 0
loop:   LD I, dot
        DRW V0, V0, 1
        ADD V0, 1
        LD V1, 1
        LD DT, V1
wait:   LD V1, DT
        SE V1, 0
        JP wait
        JP loop
dot:    DB 0x80
```

One instruction or directive per line. A line is:

```text
[label:] [MNEMONIC operand[, operand ...]] [; comment]
```

### Comments

Everything from `;` to the end of the line is ignored.

One comment is not a comment: a line whose first non-space text is `; keys`
seeds the keypad for a whole-program run — see [seeding the
keypad](#seeding-the-keypad).

### Labels

A label is `name:` at the start of a line, optionally followed by an
instruction:

```text
loop:   DRW V0, V1, 1
target:
        JP target
```

- The name starts with a letter or `_`, and continues with letters, digits or
  `_`.
- Labels are **case-sensitive**; mnemonics and register names are not.
- A label is the address of whatever follows it, so a label on a line of its own
  costs nothing.
- Defining a name twice is an error naming the line and the name.

### Operands

Separate operands with commas, spaces, or both. `DRW V0, V1, 1`,
`DRW V0 V1 1` and `DRW V0,V1,1` are the same instruction. Case is free:
`ld v0, 5` is `LD V0, 5`.

### Numbers

| Spelling | Value |
| --- | --- |
| `255` | decimal |
| `0xFF`, `0XFF` | hexadecimal, either `x` |
| `FFh`, `0ffH` | hexadecimal, `h` suffix |
| `0x200`, `512`, `200h` | the address 512 |

A byte operand must be 0–255 and a word operand 0–65535. A negative value is
refused rather than wrapped, because a listing with a negative byte is a listing
with a mistake.

### Registers

`V0` through `VF`, in any case. Two hex digits after the `V` name a register and
nothing else: `V10` is not `V1` and `0`, it is an error.

---

## Directives and pseudo-instructions

| Form | Bytes | Meaning |
| --- | --- | --- |
| `DB value` | 1 | One byte of data, exactly as given |
| `DW value` | 2 | One 16-bit word, big-endian: high byte first |
| `HALT` | 2 | `0x0000` — stop the machine |
| `SYS address` | 2 | `0NNN` — an RCA 1802 call, which every interpreter since 1977 ignores |

`DB` is how sprites and tables are written. `DW` is the escape hatch for an
opcode the assembler has no mnemonic for; the SuperChip screen opcodes are the
usual case:

```text
        DW 0x00FF           ; hi-res: 128x64
        DW 0x00C2           ; scroll down 2
```

---

## Instruction forms

| Mnemonic | Forms | Encoding |
| --- | --- | --- |
| `CLS` | `CLS` | `00E0` |
| `RET` | `RET` | `00EE` |
| `JP` | `JP addr` | `1nnn` |
| `JP` | `JP V0, addr` | `Bnnn` (V0 is added at run time; another register is refused) |
| `CALL` | `CALL addr` | `2nnn` |
| `SE` | `SE Vx, byte` / `SE Vx, Vy` | `3xkk` / `5xy0` |
| `SNE` | `SNE Vx, byte` / `SNE Vx, Vy` | `4xkk` / `9xy0` |
| `LD` | `LD Vx, byte` / `LD Vx, Vy` | `6xkk` / `8xy0` |
| `LD` | `LD I, addr` | `Annn` |
| `LD` | `LD Vx, DT` | `Fx07` |
| `LD` | `LD Vx, K` | `Fx0A` |
| `LD` | `LD DT, Vx` / `LD ST, Vx` | `Fx15` / `Fx18` |
| `LD` | `LD F, Vx` | `Fx29` |
| `LD` | `LD B, Vx` | `Fx33` |
| `LD` | `LD [I], Vx` / `LD Vx, [I]` | `Fx55` / `Fx65` |
| `ADD` | `ADD Vx, byte` / `ADD Vx, Vy` | `7xkk` / `8xy4` |
| `ADD` | `ADD I, Vx` | `Fx1E` |
| `OR` | `OR Vx, Vy` | `8xy1` |
| `AND` | `AND Vx, Vy` | `8xy2` |
| `XOR` | `XOR Vx, Vy` | `8xy3` |
| `SUB` | `SUB Vx, Vy` | `8xy5` |
| `SUBN` | `SUBN Vx, Vy` | `8xy7` |
| `SHR` | `SHR Vx` (or `SHR Vx, Vy`) | `8xy6` |
| `SHL` | `SHL Vx` (or `SHL Vx, Vy`) | `8xyE` |
| `RND` | `RND Vx, byte` | `Cxkk` |
| `DRW` | `DRW Vx, Vy, rows` | `Dxyn`, with `rows` 0–15 |
| `SKP` | `SKP Vx` | `Ex9E` |
| `SKNP` | `SKNP Vx` | `ExA1` |

Two deliberate leniencies, for listings written for later machines:

- `SHR`/`SHL` accept a `Vy` operand and ignore it. This machine shifts `Vx` in
  place, as 1977 did; a listing that names the source register still assembles.
- `JP` with a register names `V0` only, and refuses anything else. `Bnnn` has no
  field for a register, so accepting `JP V5, addr` would silently ignore the
  `V5`.

---

## Seeding the keypad

An interactive machine has hands; a whole-program run does not, so a program
that waits for a key would wait forever. A listing can say what to hold:

```text
; keys 5
start:  LD V0, K
        ...
```

Any line whose first non-space text is `; keys` contributes every hexadecimal
digit after it to the initial mask, so `; keys 1F` holds keys `1` and `F`, and
several such lines combine. The scan is literal — every hex digit on the rest of
the line counts — so put the mask last: `; keys -- 1F` is fine, `; keys F and 1`
is not what it looks like.

`chip8 run --keys 1F` does the same thing from the command line, and combines
with whatever the listing asks for.

---

## What the assembler refuses

| Message | Cause |
| --- | --- |
| `line N: MNEMONIC takes …` | Wrong operand count or kind |
| `line N: MNEMONIC is not an instruction form this assembler knows` | Unknown mnemonic |
| `line N: JP uses V0 only; Bnnn adds V0` | `JP Vx, addr` with `x ≠ 0` |
| `line N: DRW draws at most 15 rows` | `DRW` with `rows` above 15 |
| `line N: label NAME is defined twice` | Duplicate label |
| `'text' is not a number` | Bad literal |
| `text is not a byte` / `… is not a 16-bit value` | Out of range |
| `'text' is not a register (V0-VF)` | Bad register |
| `the program needs N bytes and memory ends at 0x1000` | Listing too large |
| `the ROM is N bytes; memory from 0x200 holds 3584` | Binary ROM too large |

At run time the machine adds `RET with an empty call stack`, `unknown opcode
0xNNNN at 0xNNN`, `unknown arithmetic opcode 8xyN`, `unknown opcode FxNN` and
`the program counter ran past the end of memory`. The exact spelling of all of
these is asserted by the tests: they are the text a person reads, so they are
part of the interface.

A listing that fails to assemble does not unload the program that is running.

---

## A worked listing

```text
start:  LD V0, 5
        LD V1, 5
        LD I, sprite
        DRW V0, V1, 1
        HALT
sprite: DB 0x80
```

Assembled, byte by byte:

| Address | Bytes | From |
| --- | --- | --- |
| `0x200` | `60 05` | `LD V0, 5` |
| `0x202` | `61 05` | `LD V1, 5` |
| `0x204` | `A2 0A` | `LD I, sprite` — the label is at `0x20A` |
| `0x206` | `D0 11` | `DRW V0, V1, 1` |
| `0x208` | `00 00` | `HALT` |
| `0x20A` | `80` | `sprite: DB 0x80` |

That is a single lit pixel at column 5, row 5, and a clean halt: load registers,
point `I` at data, draw, stop.

---

## Assembly is two passes

1. **Layout.** Walk the lines, record every label's address, and size every
   statement (`DB` is one byte, everything else two). A label can therefore be
   used before it is defined.
2. **Encode.** Walk again and write the bytes, resolving labels.

That is why a label on its own line costs nothing, and why an instruction that
names a label defined further down still assembles. It is also why the listing's
size is known before any byte is written, so the memory-limit error is a layout
error rather than a surprise at run time.
