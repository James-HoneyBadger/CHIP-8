# The language

A program is a listing: readable text that becomes the bytes a 1977 machine would
have been given. The instructions are [the 1977 set](instruction-set.md); this
page is everything around them — data, includes, the standard library, and the
two idioms that catch everybody.

```sh
python3 -m chip8 new hello.ch8     # a program that runs
python3 -m chip8 dev hello.ch8     # run it, and reload when you save
python3 -m chip8 asm hello.ch8     # just the bytes
```

---

## A listing

```text
; HELLO -- a title card for the machine.
; speed 30

start:  CLS
        LD V1, 8
        .printc title
        HALT

.string title "CHIP-8"
.include text
```

A line is:

```text
[label:] [MNEMONIC operand[, operand ...]] [; comment]
```

Comments run from `;` to the end of the line, except inside a quoted string.
Labels are case-sensitive; mnemonics and registers are not. Operands separate on
commas, spaces, or both.

### The header

The first comment lines are the program's manual, and `chip8 library` reads them:

| Line | Means |
| --- | --- |
| first line | `TITLE -- one line of description` |
| `; speed 2000` | the opcodes a frame this program is meant to run at |
| `; keys 1F` | the keypad a headless run should hold |

---

## Numbers

| Spelling | Value |
| --- | --- |
| `255` | decimal |
| `0xFF`, `0XFF` | hexadecimal, either `x` |
| `FFh`, `0ffH` | hexadecimal, `h` suffix |

A byte operand must be 0–255 and a word operand 0–65535. Negative values are
refused rather than wrapped.

---

## The data section

Data is declared by name and placed **after the code**, so a listing never has
to jump over its own tables. A label is the address of the thing.

| Directive | Bytes | What it declares |
| --- | --- | --- |
| `.string NAME "text"` | length + 1 | ASCII, NUL-terminated, with `\n`, `\t`, `\0`, `\\`, `\"`, `\xNN` |
| `.bytes NAME …` | one a value | raw bytes, `1, 2, 0xFF` |
| `.words NAME …` | two a value | big-endian words; a value may be a label |
| `.art NAME … .end` | rows × width/8 | a sprite, `#` on and `.` off |
| `.zero NAME n` | n | n zeroed bytes, for a grid or a buffer |

Every blob also defines three names:

| Name | Is |
| --- | --- |
| `NAME` | its address |
| `NAME_len` | its length: characters for a string, bytes for the rest |
| `NAME_hi`, `NAME_lo` | the high and low halves of its address |

That last pair exists because the machine cannot read `I`: a routine that walks a
string has to be handed an address in two registers. `NAME_hi` is what makes that
possible without hard-coding an address.

```text
.art heart
  .##.##.
  #######
  #######
  .#####.
  ..###..
  ...#...
.end

.bytes sine
    16,15,13,12,10,9,8,6,5,4,4,3,2,2,1,1
.end

.zero grid 128
```

A block directive (`.bytes`, `.words`, `.art`, `.string`) continues until `.end`;
one with values on the same line is complete in itself.

---

## `.org`

```text
.org 0x300
```

sets the address of what follows. It only moves forward, because a listing that
overwrites itself is a listing with a mistake.

---

## Linking

```text
.include "helper.inc"    ; a file next to this one, or in chip8/lib
LIB text                 ; the same thing, spelled shorter
```

An include is assembled where it appears and **once**: the same file included
twice contributes one copy, so diamond includes are safe. Search order is the
including file's own directory, then `chip8/lib`. Cycles are refused.

Labels from an include are ordinary labels, so the standard library's are all
prefixed (`t_`, `d_`, `m_`, `i_`) to stay out of your way. An error inside an
include is reported against *that file's* line, not yours:

```text
lib/text.inc:23: CLS is not an instruction form this assembler knows
```

---

## Pseudo-instructions

Three spellings that stand for the instructions the library's calling convention
needs. The machine cannot read `I`, so a routine that walks a string has to be
given the address in V4 and V5 by hand; these write that for you.

| Write | Get |
| --- | --- |
| `.print NAME` | `LD V4, NAME_hi` · `LD V5, NAME_lo` · `CALL t_print` |
| `.printc NAME` | the same, plus `LD V2, NAME_len` and `CALL t_center` |
| `.num` | `CALL t_num` |

---

## Two idioms that catch everybody

CHIP-8 has no comparison instruction: it has `SUB`, which sets `VF` as a
by-product, and `SE`/`SNE`, which skip one instruction. Everything is built from
those, and the polarity is easy to get backwards.

`SUB Vx, Vy` sets **`VF = 1` when `Vx >= Vy`** (that is, when nothing was
borrowed), and puts `Vx - Vy` in `Vx`. So to compare two values without losing
one, subtract into a third register:

```text
        LD V3, V2       ; a copy, because SUB writes its result back
        LD V5, 4
        SUB V3, V5      ; VF = 1 when V2 >= 4
```

Then the branch, and this is the table to memorise:

| You wrote | It jumps when |
| --- | --- |
| `SE VF, 1` then `JP T` | `VF == 0` |
| `SE VF, 0` then `JP T` | `VF == 1` |
| `SNE VF, 0` then `RET` | never — the `RET` runs when `VF == 0` |
| `SNE Vx, 0` then `RET` | — the `RET` runs when `Vx == 0` |

So *"if `V2 >= 4` then store"* is:

```text
        LD V3, V2
        LD V5, 4
        SUB V3, V5      ; VF = 1 when V2 >= 4
        SE VF, 0        ; skip the jump when VF is 1
        JP  somewhere   ; so this runs when VF is 0
        ...             ; and control arrives here when V2 >= 4
```

Two more things worth knowing before they surprise you:

* **`LD Vx, [I]` loads V0 up to Vx**, not one byte, and moves `I` past them all.
  Reading a single byte with `LD V5, [I]` quietly overwrites V0–V4. Read into
  `V0` and copy, or expect the clobber.
* **`LD [I], Vx` stores V0 up to Vx** for the same reason. Writing one byte
  means putting it in V0 first.

---

## What is refused

| Message | Cause |
| --- | --- |
| `line N: MNEMONIC is not an instruction form this assembler knows` | unknown mnemonic |
| `line N: label NAME is defined twice` | duplicate |
| `NAME is not a byte` / `… not a 16-bit value` | out of range |
| `'text' is not a register (V0-VF)` | bad register |
| `line N: JP uses V0 only; Bnnn adds V0` | `JP V5, addr` |
| `NAME is not closed by .end` | an unterminated block |
| `cannot find 'NAME' to include` | a missing include |
| `the program needs N bytes and memory ends at 0x1000` | too large |

A listing that does not assemble leaves a running program alone: `chip8 dev`
puts the error on the status line and keeps the old program in the machine.

---

## Where next

* [The instruction set](instruction-set.md) — what each opcode does.
* [The standard library](library.md) — text, drawing, arithmetic, input.
* [`roms/`](../roms) — five programs written with all of this, from a title card
  to a game.
