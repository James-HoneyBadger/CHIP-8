# Technical reference

How the COSMAC VIP emulator is built: the modules, the data model, the execution
and timing models, the assembler's phases, the frontends, rewind, and the
testing strategy. Written for someone about to change the code.

Companion documents: [user manual](user-manual.md) ·
[programmer's reference](programmer-reference.md) ·
[instruction set](instruction-set.md) · [language](language.md) ·
[library](library.md).

---

## 1. Module map

About 9,000 lines of Python in `chip8/`, 170 in `tools/`, 4,300 in `tests/`.
The machine, assembler, terminal and web frontends are standard library only.
The desktop application is the exception: it uses PyGObject and GTK 4 from the
desktop, which are system packages rather than pip ones.

| module | lines | responsibility |
| --- | --- | --- |
| `chip8/constants.py` | 101 | every number the machine has: dimensions, addresses, speeds, rewind policy, the two fonts |
| `chip8/machine.py` | 492 | the 1977 machine. `Chip8Machine.step()` is the only place opcodes are decoded |
| `chip8/assembler.py` | 955 | assembler **and** linker: scanning, includes, data placement, labels, encoding, pseudo-instructions |
| `chip8/emulator.py` | 476 | the frame loop on top of the machine: speed, run/pause, rewind, hot reload, snapshots |
| `chip8/frontend_terminal.py` | 360 | half-block ANSI renderer, non-blocking key reading, transport keys |
| `chip8/frontend_x11.py` | 631 | the window, via `ctypes` and libX11 directly |
| `chip8/frontend_web.py` | 489 | a browser page and its HTTP protocol, for any Linux and any display |
| `chip8/gui.py` | 885 | the GTK 4 application: drawing area, keypad, monitor, registers, menus |
| `chip8/desktop.py` | 139 | launcher, icon and MIME file installation (`chip8 desktop`) |
| `chip8/cli.py` | 824 | argument parsing and the nine commands |
| `chip8/librarian.py` | 170 | the program catalogue, `new`, and the template |
| `chip8/screen.py` | 107 | framebuffer → text, with no I/O |
| `chip8/inspector.py` | 130 | registers, memory and disassembly as text, with no I/O |
| `chip8/lib/*.inc` | 839 | the standard library, in CHIP-8 assembly |
| `chip8/cosmac1802.py` | 524 | the CDP1802: registers, flags, branches, the two ports, cycle counts |
| `chip8/vipasm.py` | 452 | the 1802 assembler: sections, labels, the encoding table |
| `chip8/vipfirmware.py` | 1,381 | the CHIP-8 interpreter, as 1802 source assembled at import |
| `chip8/vip.py` | 772 | the VIP board — memory, display, keypad, clock — and its emulator adapter |
| `chip8/data/` | — | the desktop launcher, icon and MIME description |
| `tools/make_font.py` | 173 | generates `chip8/lib/font.inc` from 4 × 5 pictures |
| `tests/` | 4,329 | 372 tests |

The split that matters: `machine.py` knows nothing about time, `emulator.py`
knows nothing about pixels, and `screen.py`/`inspector.py` know nothing about
either. Everything above the machine is testable without a display, which is why
the test suite runs in 1.5 seconds.

Dependencies point one way only:

```text
constants → machine → emulator → frontends (terminal, x11, web, gui)
                   ↘ assembler ↗        ↘ cli
                     librarian
screen, inspector (pure functions, used by frontends and tests)
```

---

## 2. Data model

### `Chip8State` (`machine.py`)

A frozen snapshot of everything that decides what the machine does next.
Deliberately a value, not a live object, so that rewind is "keep a deque of
these" and a test can compare two of them field by field.

| field | type | notes |
| --- | --- | --- |
| `memory` | `bytes(4096)` | copied, never aliased |
| `v` | `bytes(16)` | `v[0xF]` is the flag |
| `rpl` | `bytes(16)` | SuperChip's flag registers, `Fx75`/`Fx85` |
| `i` | `int` | masked to 12 bits on every write |
| `pc` | `int` | `pc += 2`, masked to 12 bits, so it wraps at 0x1000 |
| `stack` | `tuple[int, ...]` | depth 16; overflow drops the oldest |
| `delay_timer`, `sound_timer` | `int` | decremented once per frame |
| `keys` | 16-bit mask | one bit per keypad key |
| `framebuffer` | `bytes(64 * 32)` | one byte per pixel. Not bit-packed: `DRW` reads and then writes each pixel, so unpacking costs more than the memory saves |
| `hi_res` | `bool` | SuperChip mode |
| `halted`, `waiting_for_key`, `waiting_register` | | the blocking `Fx0A` state, so a snapshot can be taken mid-wait |
| `cycles`, `collisions` | `int` | counters: useful in the inspector, and in tests |

The RNG is deliberately *not* here — see [rewind](#7-rewind).

`blank_memory()` returns memory with both fonts at their addresses, and
`Chip8Machine.from_program(memory, entry, keys, rng)` returns a machine ready to
run — that is the constructor nearly every test uses.

### `Chip8Program` (`assembler.py`)

The output of the linker: `memory`, `entry`, `labels`, `size`, `includes`,
`origin` (file and line of each emitted byte, for error messages and the
inspector's listing map), plus the provenance of each line so an error inside
`chip8/lib/text.inc` can say so.

### `Chip8Emulator` (`emulator.py`)

The only stateful object above the machine. Holds a program, a machine, the
speed, whether it is running, the rewind deque, an optional `SourceWatcher` and
an optional seeded `random.Random`. Both frontends and the CLI drive exactly
this and nothing else, so the three frontends cannot drift apart.

---

## 3. Execution model

### One opcode

`Chip8Machine.step()`:

1. fetch `memory[pc]`, `memory[pc+1]` (both masked, so a program counter at
   0xFFF reads a wrapped word);
2. `pc += 2`, masked to 12 bits;
3. decode `n1n2n3n4` into `x`, `y`, `n`, `nn`, `nnn` and dispatch on the high
   nibble, with a second dispatch on the low nibble for `8xyN` and `FxNN`;
4. anything unknown raises `Chip8Error` naming the opcode and the address.

`0NNN` is accepted and ignored, which is what the 1977 interpreter did with its
machine-code calls.

### One frame

`Chip8Emulator.advance_frame()`:

1. drop any snapshots *ahead* of the current position, because running on after
   a rewind branches the history rather than replaying it;
2. every `REWIND_STRIDE` frames, push a snapshot of the state *before* the
   work — so rewinding lands you at the start of a frame, never mid-frame;
3. run `speed` opcodes, stopping early if the machine is halted;
4. decrement the delay timer and the sound timer if non-zero.

That order is the machine's: timers tick between frames, not between
instructions, which is why `LD DT, V0` with 30 means half a second at any speed.

### Pacing

`FrameClock` / `frames_due()` compute how many frames *should* have happened
since the last call, from a monotonic clock, so the terminal renderer can repaint
at 30 Hz while the machine runs at 60 Hz and the window can run at 60 Hz without
either drifting. If the process is descheduled for a second, it catches up by
running several frames rather than by running the same frame six times faster.

A frame is bounded by construction: at most `MAX_SPEED` (2000) opcodes, and
`Chip8Error` from a bad opcode stops the machine rather than propagating into
the frontend. There is no separate step budget to tune.

Sound is the machine's bell: the frontends beep while `sound_timer` is non-zero,
and `--mute` silences it.

### The quirks, and why each one is here

This implementation models the 1977 interpreter plus the SuperChip additions.
Each quirk is a decision, and each decision makes some program work and some
program not:

| behaviour | choice | consequence |
| --- | --- | --- |
| `8xy6`/`8xyE` | shift `Vx` in place, ignore `Vy` | 1977 programs work; CHIP-48 programs that shift `Vy` into `Vx` do not |
| `VF` | written **before** the result | programs that read `VF` after an arithmetic op see the flag, as intended |
| `Fx55`/`Fx65` | `I` ends one past the block | the 1977 behaviour; CHIP-48 leaves `I` alone. Library routines depend on the advanced value *and* on re-setting `I` before a store |
| `Bnnn` | jumps to `nnn + V0` | the original; CHIP-48 uses `Vx` |
| sprites | wrap, never clip | snake crosses the screen with no edge code |
| `Dxy0` | 16 × 16 in hi-res, 8 × 16 doubled in lo-res | what SuperChip draws in both modes |
| `Fx30` | 8 × 10 digits, derived from the 4 × 5 set | the two faces cannot disagree |
| `Fx75`/`Fx85` | flags registers persist | a reload-and-restart keeps its flags |
| `Fx0A` | blocks only when the frontend is interactive | headless runs cannot hang on a keypress |
| collision | `VF = 1` if any pixel was erased | the standard, and what snake's death check uses |

The `Fx0A` quirk is the only one that exists for the implementation's benefit
rather than a program's, and it is documented on the option that controls it.

---

## 4. The assembler

`assemble_chip8(source, base_dir)` runs in four phases.

**1 — expand.** `_expand` splits the listing into statements, following
`.include`/`.inc`/`LIB` depth-first with cycle detection (a file that includes
itself is an error, not a hang) and a depth limit of 8. Each statement keeps the
file and line it came from, so a message can name `chip8/lib/text.inc:41`.

**2 — scan.** `_scan_blocks` walks the statements and records, for each one:
whether it is code or data, its `.org` if any, and its size in bytes. Data
sections have fixed sizes — `.string` is its length plus the terminator,
`.bytes`/`.words`/`.zero` are as many bytes as values given — so this phase
needs no knowledge of addresses.

**3 — place.** The linker lays code and data out from the current origin. Labels
resolve to addresses; a label defined after a `.org` that moved past it is an
error rather than a silent guess. Each data label also gets `NAME_len`, `NAME_hi`
and `NAME_lo`, computed here and stored in the label table like any other name,
which is why `lookup()` treats them as ordinary labels.

**4 — encode.** `encode` turns each code statement into two bytes, two passes
over the instruction table: exact-mnemonic forms first, then the forms with
operands. Errors carry the statement's origin.

`.print`/`.printc`/`.num` are expanded in pass 4 into the load-cursor-and-call
sequence the routines expect, so the pseudo-instructions cost the program nothing
at run time and nothing extra in the library.

The whole thing is 950 lines, of which about 400 are the instruction table (one
entry per form, with a docstring), which is also what `docs/instruction-set.md`
was written from.

---

## 5. The font, and why there are two blobs

`tools/make_font.py` holds `GLYPHS`: 64 glyphs, each a 4 × 5 picture of `#` and
`.`. It emits `chip8/lib/font.inc` containing:

- `font_a` — 51 glyphs for ASCII 32–82;
- `font_b` — 13 glyphs for ASCII 83–95;
- `font_split` — three bytes holding the base address of each blob and the
  boundary, which `t_char` reads.

Why two blobs: a glyph's offset is `(code - 32) * 5`, and that offset has to fit
in the 8-bit quantity the arithmetic has. 51 glyphs × 5 bytes = 255, so 51 is
the largest first blob that does not overflow. Splitting at `S` is the only
arbitrary thing in the design.

`tests/test_regressions.py` imports `GLYPHS` from the same generator and decodes
the framebuffer through it, so a test can assert "the screen shows a 1 here"
without a picture file.

---

## 6. Frontends

### terminal (`frontend_terminal.py`)

Each character cell carries two vertically adjacent pixels: the upper half block
`▀` with the foreground colour set to the top pixel's and the background to the
bottom one. Horizontally, each cell is two characters wide, so a pixel is
approximately square in a typical terminal's cell aspect ratio.

Rendering is a run-length pass: for each row pair, compare the pixels, emit the
character, and only emit an SGR sequence when the colour changes. A typical
screen changes colour a few dozen times, not 2048.

Input is the hard part, because a terminal reports key *presses* and never
releases. `interpret_input(data, more_waiting)` is therefore a pure function —
separated out precisely so it can be tested without a pty:

- bare bytes map through the keypad table;
- `ESC` followed by more bytes *in the same read* is a terminal escape sequence
  and is swallowed entire. Without that rule, F5 (`\x1b[15~`) presses keypad 1
  and 5;
- a lone `ESC` with nothing else waiting is the quit key;
- transport letters (`p`, `o`, `R`, `i`, `m`, `[`, `]`, `+`, `-`) are consumed
  and never reach the machine.

A key is held for 120 ms so a program polling the keypad sees it, which is the
only way to emulate a held key in a terminal.

The alternate screen is used, and colours, cursor and terminal state are restored
in a `finally`, so an exception does not leave the terminal unusable.

### x11 (`frontend_x11.py`)

No tkinter (absent on this system) and no pip packages, so libX11 is called
through `ctypes`. The window is created, mapped and kept; the framebuffer is
converted into an `XImage` per frame.

Three things this costs, each with a comment in the code:

1. **The `XImage`'s data is not ours by default.** `XCreateImage` may allocate
   nothing and leave `data` NULL, and if we hand it a Python buffer, `XDestroyImage`
   will `free()` a pointer Python still owns. So the buffer is allocated by us,
   the pointer is *unhooked* (`image.data = None`) before `XDestroyImage`, and
   only then is the image destroyed — otherwise every screen-mode change leaks
   an image.
2. **Pixel format is negotiated, not assumed.** `PixelFormat` asks the server for
   its masks and shifts (`red_mask`, `green_mask`, `blue_mask`) and packs pixels
   accordingly; 24-bit, 32-bit and byte-swapped visuals all work.
3. **Autorepeat is a lie.** X sends `KeyRelease`/`KeyPress` pairs for a held key.
   `PressTracker` ignores a release that has a matching press within the same
   event batch, and releases *everything* on focus loss — otherwise a program
   thinks a key is held forever after you alt-tab.

### web (`frontend_web.py`)

One page and a handful of JSON endpoints on `ThreadingHTTPServer`, both from the
standard library. The page polls `/state`, decodes the framebuffer from base64
and paints it on a canvas; `/key`, `/action` and `/platform` carry input back.
It exists because X11 is not universal: a bare Wayland session, a remote SSH
connection and a machine with no display all have a browser or can forward a
port. The platform switch rebuilds the emulator from the same program through a
factory the command line supplies, preserving the speed and the running state.
One lock guards the emulator: the frame loop runs on the main thread and the
requests arrive on server threads.

### application (`gui.py`)

GTK 4 through PyGObject, so the window uses the desktop's own toolkit and runs
under X11 or Wayland. The frame loop is a `GLib.timeout_add` at 60 Hz; the
drawing area paints the framebuffer as rectangles at an integer scale, which
keeps pixels square and crisp.

The right panel is the machine front panel: the CHIP-8 registers, the 1802's
`D`/`DF`/`Q`/`IE`/`P`/`X`, a 4 × 4 keypad whose buttons hold a key down for as
long as the mouse button is, and a monitor with address and byte fields.
`Write` pauses the machine, calls the emulator's `write_memory`, and advances the
address so a program can be typed in; `Set PC` writes the CHIP-8 program
counter. Keys typed into those fields are not stolen by the keypad because the
window's key controller checks the focused widget first.

`desktop.py` writes the launcher into `$XDG_DATA_HOME`, resolving `Exec` to the
installed `chip8-vip` command or to this interpreter with the checkout on
`PYTHONPATH`; the same install registers `.ch8` as a MIME type.

### headless

No frontend at all: `cli._headless` drives `advance_frame()` directly, applies
the `--press`/`--release` schedule by frame number, and optionally prints the
buffers at the end. This is the mode the tests use and the mode screenshots are
taken in, and it is the reason `--seed` exists.

---

## 7. Rewind

`REWIND_SNAPSHOTS = 120` states, one every `REWIND_STRIDE = 15` frames: thirty
seconds of history at 60 Hz. A state is about 6 KB — 4 KB of memory and 2 KB of
screen — so the whole buffer is about 750 KB — small enough that a `deque` with `maxlen` is the entire
implementation, oldest dropped automatically.

Rewinding is `rewind_to(index)`: take the stored `Chip8State`, hand it to a fresh
`Chip8Machine`, and continue from there. Because the state is a value, this is
exact — there is no "replay the frames" step and therefore no drift.

One thing is deliberately *not* in a snapshot: the RNG. A seeded run rewinds to
the same registers and the same screen, and the next `RND` gives a *different*
number than it did the first time. That is the honest choice — a rewind is a
branch, not a replay — but it means a bug that depends on a specific random
sequence cannot be reproduced by rewinding alone. Use `--seed` and start over.

---

## 8. Testing

372 tests in twelve files, about a dozen seconds, with no display and no
third-party packages needed:

| file | tests | what it pins |
| --- | --- | --- |
| `test_machine.py` | 42 | every instruction, both flag outcomes, the quirks, wraparound, stack depth, timers |
| `test_assembler.py` | 37 | labels, data placement, includes, cycle detection, error origins, derived labels |
| `test_language.py` | 45 | the syntax: data forms, pseudo-instructions, `.print`/`.string`/`.art`, every operand style |
| `test_emulator.py` | 27 | the frame loop: speed, timers per frame, rewind determinism, snapshots, state names, the memory monitor |
| `test_frontends.py` | 41 | terminal run-length output, `interpret_input`, X11 pixel packing, `PressTracker`, the web protocol |
| `test_gui.py` | 11 | the application's arguments, monitor hex fields, and the launcher, icon and MIME type it installs |
| `test_roms.py` | 13 | the five new programs: each runs and draws the expected pixels |
| `test_examples.py` | 6 | the ported programs: each assembles, loads and survives N frames |
| `test_regressions.py` | 22 | the five review bugs plus named keys, `.num`'s operand and the clock's refresh, asserted on *what the screen shows* |
| `test_cosmac1802.py` | 38 | the processor: `SD`/`SM` both ways, the carry and borrow shifts, short and long branches, `SKP`, the ports, cycle counts |
| `test_vipasm.py` | 26 | the 1802 assembler: encodings, `low()`/`high()`, `.org` sections, and every error it refuses |
| `test_vip.py` | 64 | the VIP: boot state, display mapping, the keypad matrix, the frame clock, the two screens, the monitor, the command line, and the firmware against the native machine instruction for instruction |

Two conventions worth keeping:

- **Assert on pixels, not registers.** The five bugs found in review were all
  "the right value went to the wrong place", which registers-level assertions do
  not catch, because the register usually still held the right number. The
  regression tests decode glyphs out of the framebuffer through the font's own
  bitmaps and assert on the picture.
- **Every library routine has a test that draws something.** A routine whose only
  test is "it returns" will regress the moment a caller's register expectations
  change.

`tests/support.py` provides `run_program(source_or_path, frames=…, seed=…)` and
the framebuffer helpers, so a test is three lines.

---

## 9. Performance

Measured on this machine, CPython 3.13, with `roms/snake.ch8`:

| | |
| --- | --- |
| one opcode | 0.79 µs → 1.27 M opcodes/s |
| one frame at speed 10 (the default) | 17 µs → 60,000 frames/s available for 60 needed |
| one frame at speed 60 | 75 µs → 13,300 frames/s available |
| one frame at speed 2000 (the maximum) | 1.2 ms → 825 frames/s available for 60 needed |
| one snapshot | 1.1 µs, taken four times a second → 0.004 % of a frame |

The headroom at the default speed is about a thousandfold, which is why the
emulator is written for clarity rather than speed: there is no dispatch table, no
pre-decoding and no cached sprite decode, and the 8.3 ms a 60 Hz frame allows is
never remotely at risk. Even the worst case — maximum speed with a full rewind
buffer — uses about 7 % of a frame's budget.

The renderer is the part that could be slow and is not: the terminal emits one
SGR sequence per colour change (a few dozen per frame), and X11 converts the
whole framebuffer with a single Python loop into a preallocated `bytes` buffer
(~2048 pixels, well under a millisecond).

---

## 10. Known limitations

These are decisions, not oversights, but they are the things to know before
reporting a bug:

- **`I + n` can wrap.** `Fx55`/`Fx65`/`Fx33` mask every address, so a block that
  runs off the end of memory wraps to 0 and overwrites the font. Real hardware
  did the same. A program that does it is broken; the machine will not tell you.
- **`Bnnn` past 0xFFF** wraps through the 12-bit mask, i.e. the jump lands
  somewhere valid but not where the author meant.
- **Stack overflow is silent.** The oldest return address is dropped, as on the
  original.
- **Sound is a bell.** The platforms this models had one bit of sound and this
  has one bit of sound.
- **No `.ch8` binary format metadata.** A binary ROM is raw bytes; it loads at
  0x200 and starts there. Everything else comes from the listing form.
- **Rewind does not restore the RNG** (section 7).
- **The terminal cannot report key releases**, so keys are held for a fixed
  120 ms. A program that requires a key to be held longer needs the window.
- **hi-res mode is one-way in practice.** `00FE`/`00FF` switch and the screen
  size changes, but the scroll and sprite semantics that go with it are the
  SuperChip ones and most 1977 programs assume neither.
