# Documentation

For the COSMAC VIP emulator and its complete CHIP-8 system. Five documents, and
a reference shelf behind them. Start wherever you are:

| if you want to | read |
| --- | --- |
| get it running and write your first program | **[Instructions](instructions.md)** |
| use the emulator well | **[User manual](user-manual.md)** |
| write CHIP-8 programs | **[Programmer's reference](programmer-reference.md)** |
| understand or change the code | **[Technical reference](technical-reference.md)** |

The reference shelf:

| document | contents |
| --- | --- |
| [Instruction set](instruction-set.md) | every opcode, its operands, and what this machine does with it — including every place it disagrees with a CHIP-48 emulator, and why |
| [Language](language.md) | the assembly language: syntax, the data section, `.include`/`LIB`, the derived labels, the pseudo-instructions, and the branch-polarity table |
| [Library](library.md) | the standard library: each routine's registers in and out, what it destroys, and what it costs in bytes |
| [Assembler internals](assembly.md) | the two passes, the linker, the encoding table — for anyone changing `assembler.py` |
| [COSMAC VIP](vip.md) | the board CHIP-8 was written for: the 1802, the display in memory, the row-scanned keypad, and the interpreter as firmware you can read and change |

---

## The two pages people need most

**The branch-polarity table** — in [language.md](language.md#two-idioms-that-catch-everybody) and repeated
in the [programmer's reference](programmer-reference.md#3-branching-get-the-polarity-right),
because inverted branches caused more bugs in this project than everything else
combined. `SUB` sets `VF = 1` when there was *no* borrow.

**The two rules** — in the [programmer's reference](programmer-reference.md#2-the-two-rules):
a library call clobbers every register, and `I` survives nothing, not even a
font draw. Between them they account for every bug the review of this codebase
found.

---

## Reading order for a newcomer

1. [Instructions](instructions.md) — clone it, run it, write something.
2. [User manual](user-manual.md) sections 1–6 — the frontends, the keys, rewind.
3. [Programmer's reference](programmer-reference.md) sections 1–5 — the shape,
   the two rules, the idioms.
4. [Language](language.md) — when you need a syntax detail.
5. [Instruction set](instruction-set.md) — when you need an opcode.
6. `roms/snake.ch8` — the worked example that uses all of it.
