# Documentation

The manual set for the COSMAC VIP emulator: four guides to doing something, and
five reference documents to look something up in. Start with the guide that
matches what you are doing.

## Guides

| if you want to | read |
| --- | --- |
| get it running and write your first program | **[Instructions](instructions.md)** |
| use the emulator well | **[User manual](user-manual.md)** |
| write CHIP-8 programs | **[Programmer's reference](programmer-reference.md)** |
| understand or change the code | **[Technical reference](technical-reference.md)** |

## Reference

| document | contents |
| --- | --- |
| [Instruction set](instruction-set.md) | every opcode, its operands, and what this machine does with it, including every place it disagrees with a CHIP-48 emulator |
| [Language](language.md) | the assembly language: syntax, the data section, `.include`/`LIB`, the derived labels, the pseudo-instructions, and the branch-polarity table |
| [Library](library.md) | the standard library: each routine's registers in and out, what it destroys, and what it costs in bytes |
| [Assembler reference](assembly.md) | the instruction forms and encodings, `; keys` seeding, every error the assembler refuses, and the two passes |
| [COSMAC VIP](vip.md) | the board CHIP-8 was written for: the 1802, the display in memory, the row-scanned keypad, and the interpreter as firmware you can read and change |

## Two things worth reading before you write a program

**Branch polarity.** `SE` skips when the operands are equal, so the instruction
after it runs when they are not; `SUB` reports *no borrow* in `VF`. The table is
in [language.md](language.md#two-idioms-that-catch-everybody) and repeated in
the [programmer's reference](programmer-reference.md#3-branching-get-the-polarity-right),
because inverted conditionals are the easiest mistake to make in this language
and the hardest to see on screen.

**The two rules.** A library call clobbers every register, and `I` survives
nothing — not even a font draw. They are in the
[programmer's reference](programmer-reference.md#2-the-two-rules); many of the
bugs found while building this emulator were a broken version of one of them.

## Reading order for a newcomer

1. [Instructions](instructions.md) — clone it, run it, write something.
2. [User manual](user-manual.md) sections 1–6 — the frontends, the keys, rewind.
3. [Programmer's reference](programmer-reference.md) sections 1–5 — the shape,
   the two rules, the idioms.
4. [Language](language.md) — when you need a syntax detail.
5. [Instruction set](instruction-set.md) — when you need an opcode.
6. `roms/snake.ch8` — the worked example that uses all of it.
