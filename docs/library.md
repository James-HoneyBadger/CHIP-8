# The standard library

CHIP-8 has thirty-five instructions and no text, no multiply, no way to erase a
pixel and no comparison. The library is what makes the machine pleasant to write
for; it is written in CHIP-8 assembly, it is linked into the program, and it
costs about eight hundred bytes of the four thousand available.

```text
.include text
.include draw
.include math
.include input
```

| Include | Gives you |
| --- | --- |
| `text` | the ASCII font and five routines that draw with it (it includes `font`) |
| `draw` | points, lines, boxes and an eraser |
| `math` | multiply, divide, modulo, random, min, max |
| `input` | the keypad, asked three ways |

## The one rule

**Everything is clobbered.** Sixteen registers is not enough to save them, so
every routine takes its arguments in V0 onwards, returns its results in
registers, and is free to destroy anything else. A value that must survive a
call goes in memory:

```text
        LD I, scratch
        LD [I], V1          ; keep it
        CALL d_fill
        LD I, scratch
        LD V1, [I]          ; get it back
```

Two routines are exceptions, because they are called from inside loops:
`d_plot`, `d_unplot` and `i_poll` touch nothing above V1 except `VF`, and
`sn_cell` (in the snake program, not the library) is documented to leave V8
onwards alone.

---

## text

A character is a sprite: 4x5, five bytes, the same shape as the machine's own
font at `0x50`. Codes 32–95 have glyphs; lowercase is folded to capitals and
anything else is drawn as `?`.

| Routine | In | Out |
| --- | --- | --- |
| `t_char` | V0 = x, V1 = y, V2 = ASCII | V0 = x + 5 (wrapped past column 59), V1 = y |
| `t_print` | V0 = x, V1 = y, V4/V5 = string address | cursor after the text |
| `t_center` | V1 = y, V2 = length, V4/V5 = address | cursor after the text |
| `t_num` | V0 = x, V1 = y, V2 = value 0-255 | the number in decimal, no leading zeroes |
| `t_hex` | V0 = x, V1 = y, V2 = value | two hexadecimal digits |
| `t_clear` | — | the empty screen, cursor at 0,0 |

`t_char` uses the ASCII font; `t_hex` uses the machine's own hex font, because
`LD F, Vx` already points at exactly the right glyph.

The usual way to call one is not to. `.print`, `.printc` and `.num` write the
instructions for you:

```text
        LD V0, 4            ; x
        LD V1, 2            ; y
        .print msg          ; draws it, and V0 moves to the end of the line

        LD V1, 12
        .printc title       ; centred, because .printc knows the length

        LD V0, 4
        LD V1, 20
        LD V2, 205
        .num                ; 205
```

Strings are declared with `.string`, which puts them after the code and defines
`msg_hi`, `msg_lo` and `msg_len`:

```text
.string msg "HELLO, CHIP-8!"
```

### How the string walk works

The machine cannot read `I`, so after the first glyph is drawn `I` belongs to the
font and the string's position is lost. `t_print` therefore keeps the address in
V8/V9 and **patches the loader instruction** each iteration: `t_patch` is
rewritten with the current address and then executed. Programs on this machine
live in writable memory, which is how a 1977 interpreter would have done it too.

That is why the address arrives in V4 and V5 rather than in `I`.

---

## draw

`DRW` is XOR: it flips pixels. Everything here is built on two consequences —
setting a pixel is "flip it, and flip it back if it was already on", and erasing
is the same two draws in the other order.

| Routine | In | Does |
| --- | --- | --- |
| `d_plot` | V0 = x, V1 = y | sets one pixel |
| `d_unplot` | V0 = x, V1 = y | clears one pixel |
| `d_hline` | V0 = x, V1 = y, V2 = width | a horizontal run |
| `d_vline` | V0 = x, V1 = y, V2 = height | a vertical run |
| `d_rect` | V0, V1, V2 = w, V3 = h | the outline of a box |
| `d_fill` | V0, V1, V2 = w, V3 = h | a solid box |
| `d_xhline` / `d_xfill` | as above | the same, drawn with plain XOR — **drawing it again erases it** |
| `d_line` | V0,V1 to V2,V3 | Bresenham |

```text
        LD V0, 2            ; x
        LD V1, 2            ; y
        LD V2, 10           ; width
        LD V3, 6            ; height
        CALL d_rect
```

`d_line` keeps its error term under the longer axis, so the whole algorithm
fits in eight-bit arithmetic — the only kind this machine has.

**Primitives wrap**, exactly as `DRW` does: a box drawn at x = 60 has part of it
on the left edge. Nothing here clips, because on this machine nothing does.

Erasing a filled box means drawing it with `d_xfill`. That is how a moving
object is erased without an erase instruction, and it is what the snake's tail
does.

---

## math

| Routine | In | Out |
| --- | --- | --- |
| `m_mul` | V0 × V1 | V2 = low byte, V3 = high byte |
| `m_div` | V0 ÷ V1 | V2 = quotient, V3 = remainder; divisor 0 gives 0xFF |
| `m_mod` | V0 mod V1 | V2 = remainder |
| `m_rnd` | V0 = range | V0 = 0 … range-1 |
| `m_absdiff` | V0, V1 | V0 = abs(V0 − V1) |
| `m_min` / `m_max` | V0, V1 | V0 = the smaller / larger |

Division is repeated subtraction, because the shift-and-subtract form needs nine
bits of remainder. That makes `m_div` cost up to 255 iterations: a program that
divides inside a per-frame loop wants `--speed` turning up.

`m_rnd` is a modulo of the machine's uniform `RND`, so the low values are very
slightly likelier. For colour, motion and dice that is invisible.

---

## input

| Routine | Does |
| --- | --- |
| `i_poll` | V0 = the lowest key held, or `0xFF` when none is |
| `i_wait` | V0 = a key, waiting as long as it takes |
| `i_wait_release` | waits until the key in V0 is let go |

`i_wait_release` is what stops one press from being read as ten: a loop that
only waits for a press sees the same key every iteration at sixty frames a
second.

A whole-program run has no hands, so `i_wait` never returns there. Give the
machine a key with `--keys 1F`, or a `; keys 1F` comment in the listing.

---

## Cost

Roughly, and measured at the default speed of ten opcodes a frame:

| | Opcodes |
| --- | --- |
| one character | ~40 |
| one 4x4 filled cell | ~50 |
| `m_div` with a large dividend | up to ~800 |
| `i_poll` | 4 a key, 64 for none |

That is why the games in [`roms/`](../roms) say `; speed 2000` and the title
card says `; speed 30`: the machine runs at whatever pace you give it, and the
library is the reason a game needs a thousand opcodes a frame rather than ten.
