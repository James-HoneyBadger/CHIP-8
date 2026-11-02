# User manual

How to drive the emulator: the frontends, the keys, the debugger, the library
browser and the scripting flags.

If you have not run it yet, start with [instructions](instructions.md).

---

## Contents

1. [Three ways to watch a program](#1-three-ways-to-watch-a-program)
2. [The keypad](#2-the-keypad)
3. [Controls](#3-controls)
4. [Speed](#4-speed)
5. [Rewind](#5-rewind)
6. [The inspector](#6-the-inspector)
7. [Running something you wrote](#7-running-something-you-wrote)
8. [The library](#8-the-library)
9. [Headless scripting](#9-headless-scripting)
10. [Colours and scale](#10-colours-and-scale)
11. [Files and formats](#11-files-and-formats)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Three ways to watch a program

`--frontend` picks one, and `auto` (the default) picks for you: `terminal` if
stdin and stdout are both terminals, `x11` if there is a display, `headless`
otherwise.

### terminal

A terminal cannot show 64 pixels across, so each character cell carries a
*pair* of vertically adjacent pixels using the upper half block `▀`: the
foreground colour is the top pixel and the background is the bottom one. That is
64 columns × 16 lines for the whole screen, and `--scale N` repeats each cell N
times horizontally to compensate for the tall shape of a text cell — `--scale 2`
gives 128 × 16 characters with pixels roughly square.

It uses the alternate screen, so your scrollback survives, and restores the
cursor and colours on the way out.

### x11

An 8×-scaled window (512 × 256 by default) with `--scale` to override. It talks
to libX11 directly through `ctypes` — no pip packages, no tkinter. The title bar
reads `CHIP-8 · snake · running · 60 opcodes/frame`, so you can see what is
loaded, whether it is running and how fast without opening the inspector.

### headless

No display at all. It still runs the machine — twenty-three programs, the test
suite and every `--show` invocation in this manual go through it — with optional
text output:

| flag | effect |
| --- | --- |
| `--frames N` | run this many frames then exit (default 600, i.e. ten seconds) |
| `--report N` | print a status line every N frames |
| `--show` | print the framebuffer after the last frame |
| `--paused` | load and stop before the first instruction |
| `--inspector`, `--memory` | print registers, listing and memory at the end |

```sh
python3 -m chip8 run hello --frontend headless --frames 30 --show --report 10
```

`--platform vip` runs the program on an emulated COSMAC VIP instead of on this
machine's own interpreter: the 1802 processor, the display in memory, the
row-scanned keypad, and CHIP-8 as firmware. Every flag in this manual works the
same way on it, `--speed` included, and `python3 -m chip8 vip` prints what the
board is running. `--screen vip` draws the board's own 64 × 128 display instead
of the CHIP-8 grid — four times as tall, so give it a window or a tall terminal. SuperChip programs need the default platform; the loader says
so when you try. See [the COSMAC VIP](vip.md).

---

## 2. The keypad

The 1977 keypad is a 4 × 4 grid. On a modern keyboard the mapping is a square
block on the left for the hex digits and a letter block on the right for the
letters, so the layout you see matches the layout the machine sees:

```text
     machine                     keyboard
 1  2  3  C                  1  2  3  4
 4  5  6  D                  Q  W  E  R
 7  8  9  E                  A  S  D  F
 A  0  B  F                  Z  X  C  V
```

That is, `1`→1, `4`→C, `Q`→4, `V`→F. The window and the terminal use the same
map. Keys are *held*, not tapped: a program that reads `EX9E` sees the key for
as long as you hold it down.

---

## 3. Controls

The transport keys are deliberately the same in both interactive frontends, with
a function-key alias in the window so you can drive a program that wants `R` or
`P` as keypad-adjacent input:

| action | terminal | window |
| --- | --- | --- |
| run / pause | `p` | `p` or `F5` |
| single step (and pause) | `o` or space | `o` or `F6` or space |
| reset to the start | `R` | `R` or `F7` |
| toggle the inspector | `i` | `F8` |
| dump memory | `m` | `F9` |
| rewind one snapshot | `[` | `F10` or `[` |
| forward one snapshot | `]` | `F11` or `]` |
| slower / faster | `-` `+` | `-` `+` |
| quit | `Esc` | `Esc` |

A program that is waiting on `FX0A` (key press) is woken by the first keypad key
you press, and the *transport* keys are not visible to it — `p` pauses the
machine, it does not tell the program that keypad `D` was pressed.

---

## 4. Speed

Speed is in CHIP-8 instructions per 60 Hz frame, which is the honest unit for
this machine: everything else in it — delay timer, sound timer, sprite drawing,
key polling — happens in frames.

| instruction rate | what it is |
| --- | --- |
| 10 opcodes/frame (600 Hz) | the default, and about what a real 1977 machine managed |
| 20–60 | comfortable on a modern screen; the bundled games use 20–60 |
| 200–2000 | stress testing, or a program that computes |

`+` and `-` change it while running: by one below ten, by ten above it, so the
keys stay useful at both ends of the range. A listing can set its own starting speed:

```text
; speed 60
```

and `run`/`dev` respect it unless you pass `--speed` yourself. `--speed` is
clamped to 1–2000; the delay timer still decrements once per frame no matter
what, so a program's sense of time does not change when you speed the machine
up — only the amount of work it does in each frame does.

The number means the same on both platforms. Under `--platform vip` the board
runs whole CHIP-8 instructions until it has retired that many, and stops if the
frame's 29,344 clocks run out first — which an instruction like `CLS`, a loop
over the whole display, can do on its own.

---

## 5. Rewind

The emulator keeps a snapshot every fifteen frames, 120 of them, which is thirty
seconds of history. `[` steps back through them one at a time; `]` forward.

A snapshot holds memory, all sixteen registers, `I`, the program counter, the
stack, both timers, the keypad and the screen. It does *not* hold the random
number generator's state, so if you rewind and replay, `CXNN` will produce
different numbers than the first time round. Nothing else diverges.

Rewinding far enough and then running again is the standard way to answer "how
did the screen get like that?" — step back until the moment before, then single
step forward.

---

## 6. The inspector

`i` (or `--inspector`) prints the machine:

```text
▶ running   PC 0x432 · 150 cycles · 30 opcodes/frame (1800 Hz)
Registers
V0 18  V1 1F  V2 28  V3 08
V4 20  V5 00  V6 00  V7 00
V8 00  V9 00  VA 00  VB 00
VC 00  VD 00  VE 00  VF 00
I   0x754   PC 0x432
DT  0    ST  0
cycles 150  hits 0
keys -
stack (3) 492 292 20A
Listing
  145     LD V0, 6
  146     LD V1, 27
  147     .print spec_4
> 148     RET
```

The listing is the *source* the program was assembled from, with `>` on the line
the program counter is on, so a bad jump is visible as "the wrong line in my own
file" rather than an address. `hits` counts `DRW` collisions, `stack` shows the
return addresses oldest first, and `keys -` means nothing is held.

`m` (or `--memory`) dumps memory in rows of eight bytes, starting at `I`:

```text
Memory
400 00 EE 00 E0 60 00 61 00
408 00 EE A7 53 D0 11 3F 00
```

In a window the inspector prints to stdout, so start the emulator from a
terminal to see it.

---

## 7. Running something you wrote

```sh
python3 -m chip8 new pong.ch8      # a listing that already runs
python3 -m chip8 dev pong.ch8      # run it, and reload on save
```

`new` refuses to overwrite; `--force` overrides. `--speed` sets the header it
writes. The template is a working program — a title, a drawn border, a bounce —
so you can change one thing at a time rather than filling in a blank page.

`dev` is the loop you want while writing:

- saves are picked up within 250 ms, with no keys to press;
- a listing that fails to assemble prints the reason *and where* on the status
  line and the last good build keeps running, so a half-typed line does not
  cost you your position;
- a listing that assembles but is too big, or has no entry point, is reported
  the same way.

Errors name the file and line, even when the mistake is inside an `INCLUDE`:

```text
▪ paused   PC 0x206 · … — chip8/lib/text.inc:41: 't_bdc' is not a number
```

Outside `dev`, `run` assembles once and reports the same messages:

```sh
python3 -m chip8 run pong.ch8
python3 -m chip8 asm pong.ch8 -o pong.bin --labels    # just the bytes
python3 -m chip8 info pong.ch8 --labels               # size, entry, labels
```

`run` takes either a `.ch8` listing (assembled on the way in) or a raw binary
`ROM`. Binaries load at `0x200` and start there; listings can start wherever
their first instruction lands.

---

## 8. The library

Twenty-three programs ship with it: five written for this emulator and eighteen
additional bundled examples.

```sh
python3 -m chip8 library                 # name, description, speed
python3 -m chip8 library --run snake     # run one
python3 -m chip8 library --run snake --frontend x11 --scale 6
```

| program | what it shows |
| --- | --- |
| `hello` | text: the string, the number and hex routines |
| `clock` | a live clock, sixty frames a second |
| `life` | Conway's life in 64 × 32 |
| `plot` | a plotting toy, drawn with `d_line` |
| `snake` | a complete game: input, state, score, game over |
| `bounce`, `maze`, `pong`, … | additional bundled examples |

`--speed` on `library` overrides the program's own, which is normally what you
want for the old examples: they were written for whatever machine their author
had.

---

## 9. Headless scripting

Everything about input can be scripted, which makes the whole system testable
and makes screenshots reproducible:

| flag | meaning |
| --- | --- |
| `--keys MASK` | hold these keys from the start. `1F` is "1 and F" |
| `--press KEY@FRAME` | press at that frame. `5@30`, or `1F@120` |
| `--release KEY@FRAME` | release at that frame |
| `--seed N` | make `RND` reproducible |
| `--frames N` | how long to run |

Some keys have names because `@` and `,` are awkward in a shell: `up`, `down`,
`left`, `right`, `fire` — a program that uses the arrow keys can be driven
without knowing which hex digit its author chose.

```sh
# start snake, turn right after a second, then stop and show the screen
python3 -m chip8 run snake --frontend headless --frames 120 \
    --press right@60 --show

# a reproducible random walk
python3 -m chip8 run plot --frontend headless --frames 200 --seed 7 --show

# the clock at a known time
python3 -m chip8 run clock --frontend headless --frames 61 --show
```

Combine `--seed` with `--report` for a run you can diff between versions.

---

## 10. Colours and scale

| frontend | control |
| --- | --- |
| terminal | `--color truecolor` (24-bit), `256`, `ascii` (no colour at all), `auto` |
| x11 | `--scale N` pixels per CHIP-8 pixel; default 8 |

`auto` for colour picks truecolor if `COLORTERM` says so, then 256, then ascii.
`ascii` renders lit pixels as `#` and dark as a space, which is the form that
survives copy-paste into anything.

The window draws the colours in `chip8/constants.py`: pale green
`(224, 248, 208)` on near-black `(10, 10, 20)`, the pair the LCD machines this
is modelled on used. Change `ON_COLOR` and `OFF_COLOR` there and both the window
and the terminal follow.

---

## 11. Files and formats

| path | what |
| --- | --- |
| `roms/*.ch8` | the five programs written for this emulator, as source |
| `examples/*.ch8` | the eighteen ported examples, as source |
| `chip8/lib/*.inc` | the standard library, included at assembly time |
| `chip8/lib/font.inc` | a generated 4 × 5 ASCII font — do not edit by hand |
| `tools/make_font.py` | what generates it |
| `tests/` | 226 tests; the reference for what is supposed to be true |

A listing is UTF-8 text. A binary ROM is the raw bytes the machine will execute,
and a program with the `.ch8` extension is assembled first; if you want a binary
from a listing, `asm -o` writes one.

---

## 12. Troubleshooting

| Symptom | What is happening |
| --- | --- |
| the terminal fills with `^[[?1049h` | something that is not a terminal is reading it; use `--frontend headless` |
| keys do nothing in the terminal | the terminal is not reporting them; check `--frontend terminal` was chosen, not `auto` in a pipe |
| `cannot open an X display` | no `DISPLAY`; use `--frontend terminal` |
| the window is enormous | `--scale 4` |
| the program hangs with no output | it is on `FX0A` waiting for a key, or its own key loop; `--keys 1F` proves it |
| the screen is blank but cycles are counting | the program is drawing off-screen, or to `PAGE`; press `i` and look at `I` |
| `unknown opcode 0x0000` | control ran into zeroed memory; a `RET` or a jump went to the wrong place |
| `'x' is not a number` | an undefined label: a typo, a missing `LIB`, or a label defined after a `.org` that moved past it |
| `'x' is not an instruction form this assembler knows` | a mnemonic that does not exist, or operands in a shape it will not take |
| `program is too big` | 3584 bytes is the whole address space; the library takes about 800 |
| a number prints as one digit or with the wrong digits | the routine in an *old* program may rely on `I` surviving a font draw — see [the programmer's reference](programmer-reference.md#2-the-two-rules) |

When in doubt, `python3 -m chip8 info program.ch8 --labels` shows the addresses
the assembler chose.
