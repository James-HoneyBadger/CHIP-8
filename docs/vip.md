# The COSMAC VIP

The machine CHIP-8 was written for, built out of parts that are emulated one at
a time: a CDP1802 processor, four kilobytes of RAM, a display in that RAM, a
row-scanned keypad, and the 1977 interpreter itself — as 1802 firmware, not as
Python.

The native platform (`chip8/machine.py`) decodes a CHIP-8 opcode in Python. This
one runs a second, much older computer, and CHIP-8 is a program on it.

```sh
python3 -m chip8 run examples/bounce.ch8 --platform vip   # the same program, emulated twice over
python3 -m chip8 vip                                      # what the board is made of
python3 -m chip8 vip --labels --write vip-rom.bin          # the interpreter's symbols, and its ROM image
```

---

## 1. Why this exists

CHIP-8 has a shape that is easy to miss when the interpreter is a Python
`match` statement on a 16-bit word. The opcodes are what they are *because* of
the machine underneath:

- `FX1E` adds to `I` rather than setting it, because `I` is a register the
  interpreter moves by `GLO`/`PLO` and adding is how a 16-bit pointer walks a
  table;
- `FX55`/`FX65` walk memory one byte at a time for the same reason;
- `FX0A` blocks, because the interpreter's keypad scan is a loop with no way
  out except a key;
- the display is a 64 × 32 grid on a 64 × 128 screen, because the interpreter
  strobes four scan lines for every CHIP-8 row;
- `DRW` reports a collision in `VF`, because XOR into memory is what the
  hardware had.

Running the original arrangement — firmware and data — makes those decisions
legible, and it makes the interpreter itself testable: change a handler, and 23
programs tell you whether you broke a program or only the way one is run.

---

## 2. The board

| part | what it is |
| --- | --- |
| processor | `chip8/cosmac1802.py`: a CDP1802 — `D`, `DF`, `Q`, `T`, `IE`, sixteen 16-bit registers, `P` and `X`, and the two one-byte ports |
| clock | 1,760,640 Hz, so 29,344 clocks in a 60 Hz frame, spent in whole instructions |
| RAM | 4K, `0x0000`–`0x0FFF`. The CHIP-8 program is data at `0x0200` |
| ROM | the interpreter, at `0x1000` and up, write-protected: a program that runs off its end cannot erase the thing running it |
| display | 1,024 bytes at `0x0800`, 64 × 128 bits. Each CHIP-8 row owns four display lines |
| keypad | sixteen keys in a 4 × 4 matrix. Write a row mask to port 4, read the four columns of that row from port 1 |
| sound | the `Q` line, toggled by software; the board has no buzzer, so the frontends beep instead |

The RAM/ROM split is the one place this is not a CDP1802 board: on a real VIP
the interpreter lived in the first page of RAM. Putting it in ROM above the
four kilobytes keeps a CHIP-8 program from overwriting its interpreter, and it
is why `chip8 vip --write` produces an image from `0x0000`, not a RAM dump.

`VipMemory` is what the processor sees; `Vip` is the board — memory, clock,
display, keypad, noise byte, and the cassette bits that software writes and
nothing reads.

---

## 3. The firmware

`chip8/vipfirmware.py` is a 1802 listing: 1,569 bytes in three segments, and
every CHIP-8 opcode is a handler the processor really executes.

```
0x0000  LBR START        the three bytes a reset lands on
0x1000  START            clear the registers, the display and the timers
0x1056  FETCH            read two bytes of CHIP-8, dispatch on the high nibble
0x1075  DONE             restore the scratch pointers, then FETCH again
0x10A3  h0 ... hF        the sixteen handlers, and the tables they use
0x1F00  TABLE            sixteen handler addresses, then MASKS, ROWLOW,
                         FONTTAB, KEYMAP and the key's row and column masks
```

### The fetch loop

`FETCH` copies two bytes from the address in `R7` into `0x0117`/`0x0118` (the
byte after `RA`, so a handler can read the opcode's halves without another
pointer), advances `R7`, doubles the high nibble, indexes `TABLE`, and does
`SEP RF` — an indirect jump to the handler that costs five bytes and no page
arithmetic.

Handlers come back with `SEP R3`. `R3` is the program counter while `P = 3`, so
it is left pointing at `DONE`, the instruction after the `SEP RF`: the return
address is a consequence of the dispatch rather than something software saves.

### The one convention: DONE

Every handler returns to `DONE`, and `DONE` puts `R1`, `R2`, `R4`, `R5` and
`RE` back into page one before fetching again. Those six registers are the
firmware's scissors: a handler points one of them at the display, at a ROM
table, or at the CHIP-8 program's own memory, and the next handler must not
inherit it.

Without `DONE` the firmware was subtly wrong: `DRW` leaves `R4` and `R5`
pointing into the display and its tables, and the *next* `ADD Vx, nn` wrote to
display memory instead of a register. Six review bugs and a differential test
against the native machine came out of that one missing invariant, which is why
it is one `LDI` and five `PHI`s in one place rather than a rule each handler
has to remember.

### The interpreter's page

The interpreter keeps its machine in page `0x01`, which is also a page a CHIP-8
program can reach:

| address | holds |
| --- | --- |
| `0x0100`–`0x010F` | `V0`–`VF` |
| `0x0114` | the call-stack pointer |
| `0x0115`, `0x0116` | the delay and sound timers |
| `0x0117`, `0x0118` | the opcode being executed |
| `0x0120` | the call stack, sixteen entries of two bytes, counted from 1 so that `0x0120` can stay zero |
| `0x0150`–`0x015F` | the drawing work area |
| `0x0160`–`0x016F` | the keypad work area |
| `0x0170`–`0x0177` | the `FX33` digits |
| `0x0180`–`0x018F` | the `FX75`/`FX85` RPL flags |
| `0x01F0` | the noise byte the board writes once a frame |

`chip8.vip` reads exactly these addresses — `Vip.registers()`, `call_stack()`,
`sound_timer` — which is why the inspector and the frontends cannot tell the two
platforms apart.

### Group handlers

| group | opcodes | handler |
| --- | --- | --- |
| 0 | `CLS`, `RET`, halt | `h0` |
| 1, 2 | `JP nnn`, `CALL nnn` | `h1`, `h2` |
| 3, 4, 5, 9 | the four tests and skips | `h3`, `h4`, `h5`, `h9` |
| 6, 7 | `LD Vx, nn`, `ADD Vx, nn` | `h6`, `h7` |
| 8 | the register arithmetic, on the low nibble | `h8` |
| A, B | `LD I, nnn`, `JP V0 + nnn` | `hA`, `hB` |
| C | `RND Vx, nn`, masked with the board's byte | `hC` |
| D | `DRW Vx, Vy, n` | `hD` |
| E | `SKP`, `SKNP` | `hE` |
| F | timers, `FX0A`, `FX1E`, `FX29`, `FX33`, `FX55`, `FX65` | `hF` |

`hD` is the largest: it splits the sprite into two bytes when the x coordinate
falls inside one, shifts them with `SHR`/`SHRC`, XORs both into the display,
tests both for a collision, and copies the result to the four lines the row
owns. `hF_0A` is the only handler that does not return promptly: it scans the
keypad rows until a key answers, which is what blocking means.

---

## 4. Running a program on it

`--platform vip` on `run`, `dev` and `library` swaps the platform and nothing
else. The frontends, the inspector, `--keys`/`--press`/`--release`, `--speed`,
`--frames`, rewind and the status line all work exactly as they do natively.

```sh
python3 -m chip8 run roms/snake.ch8 --platform vip
python3 -m chip8 run examples/tour.ch8 --platform vip --frontend headless --frames 600 --show
python3 -m chip8 dev examples/maze.ch8 --platform vip
python3 -m chip8 library --run clock --platform vip
```

### The application

`chip8-vip` opens the desktop application, and `chip8 desktop install` puts it
in the application menu and gives `.ch8` files an icon. Its front panel is the
board's own hardware rather than a CHIP-8 abstraction: the 1802's D/DF/Q/IE/P/X
beside the CHIP-8 registers, the 4 × 4 keypad wired to the real row scan, and a
monitor that reads and writes RAM and sets the PC. A program can therefore be
typed in a byte at a time — address, byte, **Write** — pointed at `0x0200` with
**Set PC**, and run, with no host operating system in between. The
[user manual](user-manual.md#application) walks through it; `--frontend web`
serves the same screen to a browser when the machine has no display server.

`--speed N` still means *CHIP-8 opcodes per frame*, not 1802 instructions: the
board runs whole CHIP-8 instructions until it has retired `N` of them, and stops
at the frame's 29,344 clocks if it has not. Both limits are honest: a slowed
board really does idle between instructions, and `CLS` really does cost more
than a frame, because the clear loop has to store 1,024 bytes at four machine
cycles a byte. That floor is 32,768 clocks, more than a frame on its own; the
handler gets within a third of a frame of it by putting eight bytes down per
pass, and a clear costs 1.36 frames.

### Two screens

A CHIP-8 program draws on a 64 × 32 grid, and the board's display is 64 × 128:
four lines of one bit each for every CHIP-8 row. `--screen` chooses which of the
two a frontend is shown.

| `--screen` | what you see |
| --- | --- |
| `chip8` (default) | the 64 × 32 grid the program draws on, the same view the native platform shows |
| `vip` | the machine's own 64 × 128 display, with the pixels four times as tall as they are wide |

```sh
python3 -m chip8 run roms/snake.ch8 --platform vip --screen vip --frontend x11 --scale 4
python3 -m chip8 run examples/maze.ch8 --platform vip --screen vip --frontend headless --show
```

The view is nothing but a width, a height and a framebuffer, so every frontend
draws it without knowing which platform it came from; what changes is the
geometry. The full display is four times as tall as the grid, so a terminal
needs about seventy rows for it and a window needs a smaller `--scale` than the
one that suits 32 rows. `--screen vip` without `--platform vip` is refused
rather than ignored, because the native machine has no 128-line screen to show.

### What it will not run

Everything SuperChip added that needs hardware the VIP did not have: the scrolls
(`00Cn`, `00FB`, `00FC`), the screen modes (`00FE`, `00FF`), the 16-pixel
sprite (`Dxy0`) and the 8 × 10 font (`Fx30`). Loading such a program on the VIP
succeeds and says what it found:

```text
chip8: examples/hires.ch8: this program uses SuperChip opcodes (0x00FF, 0xD010,
0x00C2, 0x00FC); the VIP is the 1977 machine and has no handler for them
```

Three of SuperChip's additions *are* here, because they need nothing the board
lacks: `00FD` halts, and `Fx75`/`Fx85` store and load the sixteen bytes of RPL
flags at `0x0180`. Use the native platform for the rest. Everything else in
`examples/` and `roms/` runs on both.

---

## 5. The 1802 assembler

`chip8/vipasm.py` assembles the firmware. It is deliberately smaller than the
CHIP-8 assembler: one pass for constants and labels, one for bytes.

```asm
            .org 0x1000
FETCH:      GHI R7              ; a label is where the next byte goes
            PHI R9
            LDI high(V_BASE)    ; low() and high() split a sixteen-bit value
            PHI R4
LOOP:       LDN R4              ; the register is in the opcode
            STR R9
            INC R9
            XRI 0x10
            LBNZ LOOP           ; a long branch takes an address
            NOP
MASKS:      DB 0x01, 0x02, 0x04, 0x08
TABLE:      DW h0, h1, h2, h3   ; words go high byte first
```

- Mnemonics are the 1802's own: `LDI`, `PLO`, `PHI`, `GLO`, `GHI`, `LDN`, `STR`,
  `LDA`, `SEP`, `SEX`, `INC`, `DEC`, `BR`/`BZ`/`BNZ`/`BDF`/`BNF`, `LBR`/`LBZ`/
  `LBNZ`/`LBNF`, `LSKP`/`LSNZ`, `ADD`/`ADC`/`SD`/`SM`/`SHR`/`SHRC`/`SHL`, `OUT`/
  `INP`, `IDL`.
- `SEP R3` and the sixteen `SEP`/`SEX` forms take a register name; `OUT 4` takes
  a port number from 1 to 7.
- `.org` starts a section; the assembled program keeps its sections apart, so a
  listing can cover the boot stub at `0x0000` and the ROM at `0x1000`.
- `NAME = value` defines a constant, which `low(NAME)` and `high(NAME)` split.
- Short branches are relative and say so: a target further than ±127 bytes is an
  error naming both addresses.

The firmware is rebuilt from source on import — `chip8.vipfirmware.firmware()`
— so editing `FIRMWARE_SOURCE` is the whole edit-run loop:

```sh
python3 -m chip8 vip              # the sizes and the entry points
python3 -m unittest tests.test_vip -v
```

---

## 6. How it is tested

The firmware is checked by *being compared with the interpreter that is written
in Python*: the same program, instruction for instruction, on both platforms
must leave the same `V` registers, the same `I`, `PC`, timers and screen.
`tests/test_vip.py` does that for every opcode group, for wraparound drawing, for
the keypad skips with keys held, for a set of generated programs, and for 200
instructions of every bundled 1977 program.

Two comparisons are deliberately not made:

- **`RND`.** The native machine draws from Python's generator; the VIP draws a
  byte the board writes once a frame. Two random sources cannot agree, so a run
  is compared up to its first `RND`.
- **Memory below `0x0200`.** The VIP has a three-byte boot stub at `0x0000` and
  the interpreter's own page at `0x0100`; the native machine has zeros. Programs
  that read there are reading different boards, not different interpreters.

`tests/test_cosmac1802.py` checks the processor against the data sheet —
including the two instructions everyone gets backwards, `SD` (memory minus `D`)
and `SM` (`D` minus memory) — and `tests/test_vipasm.py` checks the assembler's
encodings, errors and section handling.

---

## 7. Where the parts are

| file | holds |
| --- | --- |
| [`chip8/cosmac1802.py`](../chip8/cosmac1802.py) | the processor: registers, flags, branches, ports, cycle counts |
| [`chip8/vipasm.py`](../chip8/vipasm.py) | the 1802 assembler and the `.org`-aware program image |
| [`chip8/vipfirmware.py`](../chip8/vipfirmware.py) | the CHIP-8 interpreter, as 1802 source |
| [`chip8/vip.py`](../chip8/vip.py) | the board, and `VipEmulator`, the adapter the frontends use |
