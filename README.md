# CHIP-8

*A complete CHIP-8 computer: the machine, a linker, a standard library, text and
graphics, twenty-three programs, and a toolkit for writing your own — all in
Python with no dependencies.*

CHIP-8 is the 1977 virtual machine that made a computer out of four kilobytes:
sixty-four by thirty-two pixels, sixteen keys, sixteen registers, two timers and
thirty-five instructions. It was never hardware. It ran on a COSMAC VIP, a
Telmac, an ETI-660 and everything after them, which is why the same small
program still runs here.

This is the CHIP-8 machine from [Time Warp Studio](https://github.com/James-HoneyBadger/Time_Warp_Studio),
lifted out of that environment and grown into a whole system: the 1977 machine
with its quirks, SuperChip's extensions, a two-pass assembler that links, a
standard library of text, drawing, arithmetic and input, five new programs and
five tools for writing and running your own.

```sh
python3 -m chip8 library              # what is in the box
python3 -m chip8 library --run snake  # play one
python3 -m chip8 new mygame.ch8       # start one
python3 -m chip8 dev mygame.ch8       # write it: save, and it reloads
```

```text
▄▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▄
█                                                              █
█   ▀                                                          █
█       ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀                                       █
█                       █               ▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀       █
█                       █                                      █
▀▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▀
▶ running   PC 0x2BA · 400 cycles · 10 opcodes/frame (600 Hz) · DT 1
```

---

## Quick start

No installation, no dependencies — Python 3.10 or newer:

```sh
python3 -m chip8 run examples/bounce.ch8
```

Where the screen goes is decided for you: the terminal when you have one, an X11
window when there is a display but no terminal, and a headless run otherwise. To
be explicit:

```sh
python3 -m chip8 run examples/maze.ch8 --frontend terminal --scale 2   # 128x16 characters
python3 -m chip8 run examples/hires.ch8 --frontend x11 --scale 8       # a 1024x512 window
python3 -m chip8 run examples/tour.ch8 --frontend headless --frames 600
```

Installing it puts a `chip8` command on the path, which needs a virtual
environment on systems that mark their Python as externally managed:

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
chip8 run examples/countdown.ch8
```

---

## Writing a program

```sh
python3 -m chip8 new pong.ch8      # a listing that already runs
python3 -m chip8 dev pong.ch8      # run it, and reload whenever you save
```

`dev` assembles the file on every save and hot-swaps the machine; a listing that
does not assemble leaves the running program alone and puts the error on the
status line, with its line number.

A program says what it is in its first comments, and the library does the work:

```text
; PONG -- two bats, one ball.
; speed 200

start:  CLS
        LD V1, 20
        .printc title        ; centred, and .printc works out the column
        LD V0, 4
        LD V1, 2
        LD V2, 3
        .num                 ; draws "3"
        LD V0, 40
        LD V1, 10
        LD V2, 20
        LD V3, 12
        CALL d_rect          ; an outlined box
        HALT

.string title "PONG"
.include text
.include draw
```

Text is real text — a 4x5 ASCII font, strings, numbers, centring — and graphics
are real primitives — points, lines, boxes, XOR-erase. [The language](docs/language.md)
covers the data section, linking and the two idioms that catch everybody; [the
library](docs/library.md) is the API.

---

## The programs

| Program | What it shows |
| --- | --- |
| [`hello`](roms/hello.ch8) | a title card: centred text, borders, a scanner drawn with XOR |
| [`clock`](roms/clock.ch8) | a clock built from the delay timer — no real-time clock exists |
| [`life`](roms/life.ch8) | Conway's Life on a padded grid, so counting neighbours needs no bounds check |
| [`plot`](roms/plot.ch8) | a graph plotter: axes, two curves, and functions as data tables |
| [`snake`](roms/snake.ch8) | a game: a ring buffer of cells, XOR-erase, walls, self-collision, score |

and in [`examples/`](examples) the eighteen that came from Time Warp Studio.

```sh
python3 -m chip8 library              # all twenty-three, with their speeds
python3 -m chip8 library --run life   # runs at the speed its header asks for
```

---

## The keypad

The pads are mirrored, so the finger positions the books show still work. The
same keys are used in both frontends:

| PC keys | `1 2 3 4` | `Q W E R` | `A S D F` | `Z X C V` |
| --- | --- | --- | --- | --- |
| CHIP-8 keys | `1 2 3 C` | `4 5 6 D` | `7 8 9 E` | `A 0 B F` |

The letters are the keypad, so the transport and the debugger sit on keys that
are not:

| Key | Does | X11 alternative |
| --- | --- | --- |
| `p` | run / pause | `F5` |
| `o` or space | step one instruction | `F6` |
| `R` (shift) | reset | `F7` |
| `i` | registers + listing | `F8` (prints to the terminal) |
| `m` | memory page | `F9` (prints to the terminal) |
| `[` / `]` | rewind back / forward | `F10` / `F11` |
| `+` / `-` | faster / slower | `+` / `-` |
| `Esc` | quit | `Esc` |

A terminal reports presses but not releases, so a key is held for 120 ms and
then let go — long enough for a game to notice, short enough that nothing
sticks. The X11 window gets real press and release events, so held keys behave
as they do on a real keypad.

---

## What the machine does

The whole instruction set is implemented, including the parts emulators disagree
about. This machine is the 1977 one, and says so:

| Choice | What this machine does |
| --- | --- |
| `8xy6` / `8xyE` | Shift `Vx` **in place**; `Vy` is ignored. `VF` is written first, so naming `VF` as the destination leaves the result there |
| Sprites | **Wrap** around the screen edges instead of clipping |
| `Fx55` / `Fx65` | Leave `I` **one past** the block they touched |
| `Bnnn` | Jumps to `nnn + V0` |
| `0NNN` | Ignored, as every interpreter since the original has |
| `Fx0A` | **Waits** for a key while the machine is interactive; a whole-program run reads the keypad once |

The SuperChip additions are here too:

| Opcode | Does |
| --- | --- |
| `00Cn` | Scroll the display down *n* rows |
| `00FB` / `00FC` | Scroll four pixels right / left |
| `00FD` | Halt (SuperChip's "exit interpreter") |
| `00FE` / `00FF` | 64x32 lo-res / 128x64 hi-res, clearing the screen |
| `Dxy0` | 16x16 sprite in hi-res, 8x16 with doubled columns in lo-res |
| `Fx30` | Point `I` at the 8x10 digits (0-9) after the 4x5 font |
| `Fx75` / `Fx85` | Store / load the RPL flags |

[`docs/instruction-set.md`](docs/instruction-set.md) is the full table, with the
timing model and the limits.

### Timing

The clock is the frame, not the instruction: 60 frames a second and *N* opcodes
per frame, ten being the original pace (`--speed`). The delay and sound timers
tick once per frame whether the program executed ten opcodes or none while
waiting for a key, exactly as they did when they were frame counters.

---

## Speed, pause, step and rewind

`--speed N` runs *N* opcodes per frame, from 1 to 2000. Ten is the 1977 pace and
looks right for 1977 programs; 2000 is as fast as one frame may run without
monopolising the frontend. A program that carries `; speed N` in its header is
run at that speed without being asked.

Rewind keeps a snapshot of the whole machine — registers, memory, pixels, timers
— every quarter of a second, for thirty seconds. `[` steps back through them and
`]` steps forward, and running from a moment in the past drops the future that
came after it. It is a debugger, not a replay: the random generator is not in the
snapshot, so a program that rolls dice rolls them again.

The inspector shows the registers, the call stack, the listing line the program
counter is on, and the memory page it lives in. It sits beside the screen when
the terminal is wide enough, under it when the terminal is tall enough, and in
place of it when it is neither — asking for the registers is asking to look at
them.

---

## Headless runs

`--frontend headless` is for scripts and tests: it runs a fixed number of frames
with no display, then prints the final state and exits 0, or exits 1 if the
program could not be loaded or stopped with an error.

```sh
$ python3 -m chip8 run examples/countdown.ch8 --frontend headless --frames 120 --inspector
halted   PC 0x236 · 1210 cycles · 10 opcodes/frame (600 Hz)
Registers
V0 00  V1 00  V2 00  V3 00
...
```

`--report N` prints a status line every *N* frames while it runs, and `--show`
prints the screen as characters when the run ends, which is a cheap way to look
at a program you are writing:

```sh
python3 -m chip8 run mygame.ch8 --frontend headless --frames 60 --show
```

Input can be scripted too — `--keys 1F` holds keys, and `--press 5@30 --release
5@40` presses one at a frame — so a game can be driven and checked without a
pair of hands. `--seed N` makes `RND` repeat, so a run of a program that rolls
dice can be reproduced.

A program that waits for a key has no hands in a headless run, so the keypad can
be held for it: `--keys 1F` holds keys `1` and `F`. A listing can also say so
itself with a `; keys 1F` comment, which is what the bundled examples use when
they want to be runnable unattended.

---

## The assembler

A `.ch8` file is a **listing** — text — or a **binary ROM**; the NUL byte
decides, because no listing contains one and every real ROM does. Listings look
like this:

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

`python3 -m chip8 asm examples/bounce.ch8` prints the bytes with their addresses,
`--labels` adds the label table, and `-o out.c8` writes them. `chip8 info` describes a program without
running it. [`docs/assembly.md`](docs/assembly.md) is the language reference:
labels, numbers in both hex spellings, `DB`/`DW`/`HALT`/`SYS`, and the errors
the assembler refuses with.

Errors are reported with the line they came from, and an assembly error never
unloads the program that is already loaded — a typo in a listing is not a reason
to lose the game that is running.

---

## Documentation

| | |
| --- | --- |
| **[Instructions](docs/instructions.md)** | from nothing to a program you wrote, in ten minutes |
| **[User manual](docs/user-manual.md)** | the frontends, keys, speed, rewind, the inspector, headless scripting, troubleshooting |
| **[Programmer's reference](docs/programmer-reference.md)** | how to write programs: the two rules that catch everybody, the idioms that work, the library's contracts, five worked examples |
| **[Technical reference](docs/technical-reference.md)** | how it is built: the modules, the execution and timing models, the linker, the frontends, rewind, testing, performance |
| [Instruction set](docs/instruction-set.md) | every opcode, with this machine's behaviour for each quirk |
| [Language](docs/language.md) | the assembler: data, includes, pseudo-instructions, labels |
| [Library](docs/library.md) | each library routine's registers and contract |
| [Assembler internals](docs/assembly.md) | the two passes, the linker, the encoding table |

---

## What is in the box

| Path | Holds |
| --- | --- |
| [`chip8/machine.py`](chip8/machine.py) | The 1977 machine: opcodes, quirks, screen, timers |
| [`chip8/assembler.py`](chip8/assembler.py) | The two-pass assembler and the ROM loader |
| [`chip8/emulator.py`](chip8/emulator.py) | The frame loop, speed, keypad, rewind |
| [`chip8/frontend_terminal.py`](chip8/frontend_terminal.py) | The screen as half-block characters |
| [`chip8/frontend_x11.py`](chip8/frontend_x11.py) | A real window, through libX11 and `ctypes` |
| [`chip8/inspector.py`](chip8/inspector.py) | Registers, memory, listing and status text |
| [`chip8/screen.py`](chip8/screen.py) | The framebuffer as characters, with no terminal involved |
| [`chip8/librarian.py`](chip8/librarian.py) | Finding programs and reading their headers |
| [`chip8/cli.py`](chip8/cli.py) | `run`, `library`, `new`, `dev`, `asm`, `info` |
| [`chip8/lib/`](chip8/lib) | The standard library, in CHIP-8 assembly: text, draw, math, input |
| [`roms/`](roms) | Five programs written for this emulator |
| [`examples/`](examples) | Eighteen more, from Time Warp Studio |
| [`docs/`](docs) | The whole manual set: [index](docs/README.md) |
| [`tools/make_font.py`](tools/make_font.py) | Regenerates the ASCII font from its glyph pictures |

The frontends draw the same snapshot of the same machine, so nothing they show
can disagree about what the machine is doing: a frontend is a view, and the
machine has no idea which one is watching.

---

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

226 tests: every opcode and every quirk, every assembler encoding and refusal,
the linker and its data section, each library routine against the pixels it
should set, the frame loop and its rewind, the renderers, the command line, and
all twenty-three programs run for several seconds of emulated time — including
the clock ticking, the Life grid evolving, the snake moving and turning, and the
game-over screen appearing. The X11 window test skips itself when there is no
display.

---

## Provenance and licence

MIT. The machine, the assembler, the timing model and the `examples/` listings
are ported from the CHIP-8 in
[Time Warp Studio](https://github.com/James-HoneyBadger/Time_Warp_Studio); see
[NOTICE](NOTICE) for the file-level mapping. The linker, the standard library,
the five programs in `roms/`, the frontends, the tools and the tests are new
here.
