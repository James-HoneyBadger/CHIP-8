# Programmer's reference

Everything you need in order to write a CHIP-8 program for this machine: the
shape you are programming, the two rules that catch everybody, the idioms that
work, the library's contracts, and five worked examples.

This page is the hub. The detail lives in three companions:

| for | read |
| --- | --- |
| every opcode, with this machine's behaviour | [instruction-set.md](instruction-set.md) |
| the assembler's syntax, data, includes, pseudo-instructions | [language.md](language.md) |
| each library routine's registers | [library.md](library.md) |

---

## 1. The shape you are programming

```text
0x000  ┌────────────────────────────┐
       │ 4x5 font, 16 glyphs        │  LD F, Vx points here
0x050  ├────────────────────────────┤
       │ 8x10 font, 10 digits       │  SuperChip's LD F, Vx
0x0A0  ├────────────────────────────┤
       │                            │
       │ (free: scratch, stack)     │
0x200  ├────────────────────────────┤
       │ your program: code + data  │
0xFFF  └────────────────────────────┘
```

Sixteen 8-bit registers `V0`–`VF`, a 12-bit address register `I`, a 16-deep
stack, a program counter, two 8-bit timers and a 64 × 32 monochrome screen.
There is no memory protection, no multiply, no divide, no indirect register
access, and no way to read `I`.

That last one shapes everything. There is no `LD Vx, I`; a routine that needs to
compute an address must either keep it in a register and patch an instruction
(`FX55`/`FX65` cannot take a register operand either — see `t_print`) or store
it in memory and re-load it with `LD I, label`.

`VF` is not a general register. `ADD` (`8xy4`), `SUB`, `SUBN`, `SHR`, `SHL`
and `DRW` all write it, as a carry, borrow or collision flag — and `DRW` clears
it at the start of every sprite. Treat it as the flag register and nothing else;
a value you put there will not survive the next arithmetic instruction or the
next draw.

---

## 2. The two rules

Almost every bug in this repository's history is one of these two. They are
worth reading twice.

### Rule 1: a library call clobbers every register

Sixteen registers is not enough to save and restore them, so nothing does. Each
routine documents exactly what it destroys, and *everything you want to survive
a call has to be in memory* — or in a register the routine promises not to
touch, which is usually none of them.

| routine | destroys |
| --- | --- |
| `t_char`, `t_print`, `t_center`, `t_num`, `t_hex`, `t_clear` | `V0`–`V9`, `I` |
| `d_plot` … `d_line` | `V0`–`V4`, `I` |
| `m_mul`, `m_div`, `m_mod`, `m_rnd`, … | `V0`–`V7`, `I` |
| `i_poll` | `V0`, `VF` |
| `i_wait`, `i_wait_release` | `V0`, `VF` |

The cursor `V0`/`V1` is the exception that makes text usable: the text routines
read it and leave the advanced position in it.

```text
    LD V2, 205          ; the number you want to print
    CALL t_num          ; V2, V0, V1 and the other seven are gone afterwards
```

Keep the value in memory and load it immediately before the call:

```text
    LD I, p_score
    LD V2, [I]          ; reads one byte into V2, advancing I past it
    LD V0, 4
    LD V1, 20
    CALL t_num
```

### Rule 2: `I` does not survive anything, including a font draw

`LD F, Vx` sets `I` to the glyph's address. `FX1E` adds to it. `FX65`/`FX55`
walk it. Any routine that draws a character leaves `I` pointing at a font row.
So a loop that draws digits must re-derive its pointer every iteration — not
compute it once before the loop.

This is exactly what went wrong in `t_num`: it held its BCD digits' address in
`I` and drew through `t_char`, which overwrote `I` with the font address. The
second digit read from the font instead of from the digits. The fixed version
re-loads `LD I, t_bcd` and adds the digit index each time round:

```text
t_num_show:
    LD I, t_bcd         ; re-derived every digit, because t_char moves I
    ADD I, V9
    LD V3, [I]          ; this digit
    ...
    CALL t_char         ; destroys I, and V0-V9
```

**Corollary for `LD Vx, [I]`:** `FX65` loads `V0` *through* `Vx` and advances
`I` by `x+1`, so "load one byte into `V3`" is a four-byte load that also wipes
`V0`–`V2`. To read a single byte, read it into `V0` — that costs only `V0` — and
copy it where you want it. And `FX55`/`FX65`
leave `I` advanced, so a store that follows a load needs `LD I, label` again —
forgetting it writes *past* your state block and into whatever is next, which is
how snake once corrupted its own food position.

---

## 3. Branching: get the polarity right

`SUB Vx, Vy` and `SUBN` write `VF = 1` when *no borrow* occurred. That inverts
the way most people read `SE VF, 1`. The table below is the one to keep by your
elbow — it is in [language.md](language.md) too, because it causes so many
bugs:

| you want to jump when | test | then |
| --- | --- | --- |
| `Vx >= Vy` | `SUB Vx, Vy` | `SE VF, 0` + `JP there` |
| `Vx < Vy` | `SUB Vx, Vy` | `SE VF, 1` + `JP there` |
| `Vx == 0` | — | `SNE Vx, 0` + `JP there` |
| `Vx != 0` | — | `SE Vx, 0` + `JP there` |
| a flag in `V8` is set | — | `SE V8, 0` + `JP there` |
| a flag is clear | — | `SNE V8, 0` + `JP there` |

The general rule: `SE` skips when equal, so the instruction *after* it runs when
they are **not** equal. Every branch in assembly is written as "skip, jump" or
"skip, return", and it is the skip that is easy to invert.

A loop that counts down is the same idea:

```text
loop:
    ...
    ADD V6, 0xFF        ; -1
    SE V6, 0            ; if it did not reach zero,
    JP loop             ; go round again
```

---

## 4. Idioms that fit the machine

**Drawing is XOR.** `DRW` erases what it draws over and sets `VF = 1` when
anything was erased. Drawing something twice removes it; that is how you move a
sprite (draw, move, draw again) and how you detect a collision.

**A sprite is a bit pattern, not an image.** Eight pixels a byte row, one byte
per row, and `DxyN` reads `N` bytes from `I`. To draw a rectangle, hoist the same
byte row.

**Use the data section for state.** `.bytes name` reserves and zeroes; `LD [I], Vx`
and `LD Vx, [I]` move bytes in and out. State in registers is state you will
lose; state in memory is state you can reload after a call.

**Keep a scratch area.** `text.inc` uses `t_bcd`, three bytes, for `LD B, V2`.
If you need a temporary, declare one in the data section rather than reusing a
register across a call.

**16-bit arithmetic is two registers.** `m_mul` takes `V2:V3` and returns
`V6:V7`; the high byte is what you want for a "divide by 256". For screen
coordinates a single byte is almost always enough — the screen is 64 × 32.

**Timing is frames.** The delay timer decrements once per frame, so
`LD V0, 30; LD DT, V0` is half a second, whatever the instruction rate. Write
games to that, not to a loop of instructions.

**Randomness:** `RND Vx, NN` is uniform in `0..NN`. To get a value in
`0..width-1`, mask with `width-1` — which only works for powers of two, so
`0x3F` for an x coordinate and `0x1F` for a y coordinate. `m_rnd` does the
modulo for other ranges.

---

## 5. The standard library

Include what you need; unused modules cost nothing because the linker only
places what is referenced.

| listing | entry points | linked size |
| --- | --- | --- |
| `LIB text` | `t_char`, `t_print`, `t_center`, `t_num`, `t_hex`, `t_clear` | 598 bytes (with the font) |
| `LIB draw` | `d_plot`, `d_unplot`, `d_hline`, `d_vline`, `d_rect`, `d_fill`, `d_xhline`, `d_xfill`, `d_line` | 430 bytes |
| `LIB math` | `m_mul`, `m_div`, `m_mod`, `m_rnd`, `m_absdiff`, `m_min`, `m_max` | 136 bytes |
| `LIB input` | `i_poll`, `i_wait`, `i_wait_release` | 34 bytes |

The five bundled programs are 1068–1841 bytes linked, which leaves most of the
3.5 KiB for your own code. Full contracts are in [library.md](library.md).

**Two things to know before you use it in a loop.** First, includes go at the
**end** of the listing: execution starts at `0x200`, and whatever you include is
laid down where it appears, so an `LIB text` at the top puts the library at
`0x200` and your program is never reached.

Second, **text is expensive.** `t_print` costs about 50 opcodes per character and
`t_num` about 52 per digit, because every character is a font lookup and a
`DRW`. A screenful of text is not affordable; a line of it is:

| | opcodes |
| --- | --- |
| `t_print` a 10-character string | 514 (≈ 51 each) |
| `t_num` a three-digit number | 155 |
| `t_hex` a byte | 22 — it can use the machine's own font |
| a whole clock face (heading, 2 colons, 3 numbers) | 1661 |

At 60 opcodes a frame that clock face takes 28 frames to come back, which is why
`roms/clock.ch8` runs at 1700 and redraws once a second rather than every frame.
Draw what changed, not what is on the screen.

The pseudo-instructions hide the arithmetic:

```text
    LD V0, 8
    LD V1, 3
    .print hello        ; x, y, draw the string
    .printc title       ; centred: works out x from the length
    LD V2, score        ; .num prints what is in V2, so load it first
    .num                ; decimal, no leading zeroes
```

`.string name "TEXT"` declares the bytes and also `name_hi`, `name_lo` and
`name_len`, so a routine that wants an address as two bytes can be handed one.

---

## 6. Five worked examples

Each shows one thing. Read the one you need.

### `roms/hello.ch8` — the smallest complete program

Text, a number, a hex value, a border: the whole library in twenty lines. Start
here to see how the cursor and the `.print` pseudo-instruction fit together.

### `roms/clock.ch8` — state that outlives a call

Keeps hours, minutes and seconds in three memory bytes, increments once a second
using the delay timer as the pacing, and re-reads each field *immediately* before
drawing it. That is Rule 1 in practice: an earlier version parked all three in
registers, called `t_num`, and printed garbage because `t_num` had taken those
registers.

It also shows the leading-zero rule: minutes and seconds print as two digits by
printing a `0` first when the value is under ten.

### `roms/life.ch8` — a grid with no bounds checks

Conway's life on 16 × 8 cells, each cell drawn as a 4 × 4 block. Two grids of
180 bytes (`ga` and `gb`) are 18 wide and 10 tall: the extra row and column on
each side is *dead padding*, so a cell's eight neighbours are at the fixed
offsets `-19 -18 -17 / -1 +1 / +17 +18 +19` and the inner loop needs no bounds
check at all. That is the trick worth stealing.

It also shows the machine's one real structural limit. There is no way to keep a
pointer in a register, so the two grids cannot be swapped by exchanging
addresses: `ga` is read, `gb` is written, and then `gb` is copied back over `ga`
before the next generation. Plan for a copy when you plan a double buffer.

Run it at `--speed 2000`; it computes a generation per frame.

### `roms/plot.ch8` — a function is data

A graph plotter: axes, two curves, a label. Nothing in it computes a sine — it
carries two `.bytes` tables of sixty-four values and draws one pixel per entry
with `d_plot`, joining the long axis with `d_line`. On a machine with no
floating point and 3.5 KiB of program space, that is not a shortcut, it is the
only way, and it is worth internalising before you reach for `m_mul` to compute
a coordinate.

### `roms/snake.ch8` — a complete game

The one to read if you are writing a game. The grid is 16 × 8 cells of 4 × 4
pixels, one cell is one byte (`y * 16 + x`), and the body is a 64-byte *ring
buffer* with head and tail indices that walk round it — a step writes one cell
and erases one, so the snake never moves its own bytes. It has:

- a state block in memory, `sn_state` = `[head, tail, len, dir]`, reloaded after
  every library call because nothing survives one;
- erasing as drawing: `d_xfill` is XOR, so drawing the tail a second time is
  what removes it;
- input through `i_poll`, with a direction register that refuses a 180°
  reversal (a difference of two mod four) or the snake would eat itself;
- food placed with a check against the body, because a meal inside the snake is
  a game that can never end;
- a score drawn with `t_num`, and a game-over screen — which is where one of the
  bugs was: the score was read into `V8` before printing, and `t_print` keeps its
  string pointer in `V8`, so the digits went nowhere. Read the score *after*
  drawing the text, not before.

It is 1841 bytes — the largest program here — and about 200 lines of assembly.

---

## 7. Debugging

The loop that works:

```sh
python3 -m chip8 dev roms/snake.ch8      # edit, save, it reloads
```

then, when something is wrong:

1. **Look at the screen.** `--show` in headless mode prints the framebuffer as
   characters; it is faster than squinting at a window and it can be diffed.
2. **Open the inspector** (`i`, or `--inspector`) and compare `PC` with the
   listing's labels. `info --labels` prints every label's address.
3. **Rewind.** `[` steps back fifteen frames at a time; the snapshot has the
   screen, so you can see the moment it went wrong without reproducing it.
4. **Seed it.** `--seed 7` makes `RND` repeat exactly, so a failure is
   reproducible and a fix is verifiable.
5. **Prove it in a test.** `tests/test_regressions.py` decodes the framebuffer
   through the font's own bitmaps and asserts on the *picture*, not on register
   values. That is the level at which these bugs actually appear:

```python
frame = run_program(SNAKE, frames=90, seed=1).screen  # after eating
assert glyph_at(frame, x, y) == "1"                   # score reads 1
```

If you change a library routine, add a test that draws and checks a pixel. Bugs
2 and 5 above were invisible to register-level assertions.

---

## 8. Limits worth knowing before you hit them

| limit | value | what happens past it |
| --- | --- | --- |
| memory | 0xFFF | the linker says the program is too big |
| call depth | 16 | the oldest return address is dropped |
| timer | byte | `LD DT, Vx` above 255 is not expressible |
| register | byte | no 16-bit add except `m_mul`/`m_div` |
| screen | 64 × 32 (or 128 × 64 in hi-res) | sprites **wrap**, they do not clip |
| instruction rate | 1–2000 per frame | clamped |
| `RND` | 0–255 | mask to the range you want |
| program size | 3584 bytes | the whole address space above 0x200 |

Sprites wrapping (rather than clipping at the edge) is the 1977 behaviour and is
worth exploiting: a sprite drawn partly off the left edge reappears on the right,
which is how the snake crosses the screen without any edge code.
