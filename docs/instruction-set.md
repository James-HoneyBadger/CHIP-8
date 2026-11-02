# The CHIP-8 instruction set, as implemented

This is not a general CHIP-8 document. It is the reference for **this** machine:
the exact 1977 choices, the SuperChip additions it also accepts, and the places
where it deliberately refuses to be helpful. Where emulators disagree, this book
says what this one does.

- How to write these opcodes: [the language](language.md) and the
  [assembler reference](assembly.md).
- How to run them: the [README](../README.md).

---

## The machine

| Part | Size | Notes |
| --- | --- | --- |
| Memory | 4096 bytes | Programs at `0x200`; the screen is **not** memory-mapped |
| `V0`–`VF` | 16 × 8 bits | `VF` is the flag register |
| `I` | 12 bits | An address; increments wrap to `0` |
| `PC` | 12 bits, in effect | Points at the next instruction |
| Stack | Return addresses | The native platform grows it without a fixed depth and raises on an empty `RET`; the COSMAC VIP's interpreter uses sixteen entries in page one |
| Delay timer | 8 bits | Counts down at 60 Hz |
| Sound timer | 8 bits | Counts down at 60 Hz; the buzzer sounds while it is non-zero |
| Display | 64×32, or 128×64 hi-res | One bit per pixel, in its own buffer |
| Keypad | 16 keys | Read as a bit mask |

### Memory map

| Range | Holds |
| --- | --- |
| `0x000`–`0x04F` | Interpreter work area; nothing here is used |
| `0x050`–`0x09F` | The 4×5 font: sixteen glyphs (0–9, A–F), five bytes each |
| `0x0A0`–`0x103` | The 8×10 digits that `Fx30` selects: ten glyphs, ten bytes each |
| `0x104`–`0x1FF` | Unused |
| `0x200`–`0xFFF` | Programs |

The 4×5 glyphs are stored with each row in the high nibble of its byte, so `DRW`
reads them like any other 8-pixel-wide sprite. The 8×10 face doubles every row
and every column of the 4×5 one, and covers the digits 0–9 only: `Fx30` with
`Vx` above 9 points past the face, into unused memory, which is what the
reference machine does.

---

## Timing

One instruction is fetched, decoded and executed; `PC` then points at the next
one. Skip instructions advance `PC` by two extra bytes.

| Context | Clock |
| --- | --- |
| `chip8 run` | 1–2000 opcodes per frame at 60 Hz (default 10); timers tick once per frame |
| Headless (`--frames`) | The same, for a fixed number of frames |

Ten opcodes per frame is the original interpreter's pace — roughly 600
instructions a second, which is why 1977 programs look the way they do. The
timers are frame counters, not wall-clock stopwatches, so a program that changes
speed changes how fast its timers run, exactly as on the machines this imitates.

`LD Vx, K` (`Fx0A`) is the one instruction that can take longer than its frame.
On an interactive machine it waits, leaving `PC` after it, until a key is held;
the registers and the timers carry on. In a whole-program run, where no later
press can arrive, it reads the key mask once and moves on.

---

## Reading an opcode

An opcode is two bytes, read high first. Written as `NNNN`, its parts are:

```text
N N N N
│ │ │ └─ nnn / NNN: a 12-bit address, or an 8-bit constant
│ │ └─── y: a register number, 0–F
│ └───── x: a register number, 0–F
└─────── the family
```

So `Dxyn` is "family D, register x, register y, n rows", and `D0 11` is
`D011`: draw at `(V0, V1)`, one row.

---

## The instructions

| Opcode | Mnemonic | What it does |
| --- | --- | --- |
| `00E0` | `CLS` | Clear the screen at its current size |
| `00EE` | `RET` | Return from the subroutine at the top of the stack; on the native platform an empty stack is an error |
| `0NNN` | `SYS nnn` | An RCA 1802 call. **Ignored** |
| `0x00` | `HALT` | Halt (an addition: `0x0000` is unused by the original set) |
| `1nnn` | `JP nnn` | Jump |
| `2nnn` | `CALL nnn` | Push `PC`, then jump |
| `3xkk` | `SE Vx, kk` | Skip the next instruction if `Vx == kk` |
| `4xkk` | `SNE Vx, kk` | Skip if `Vx != kk` |
| `5xy0` | `SE Vx, Vy` | Skip if `Vx == Vy` (the low nibble is not a value and is not checked) |
| `6xkk` | `LD Vx, kk` | `Vx = kk` |
| `7xkk` | `ADD Vx, kk` | `Vx += kk`, wrapping; **`VF` is untouched** |
| `8xy0` | `LD Vx, Vy` | `Vx = Vy` |
| `8xy1` | `OR Vx, Vy` | `Vx \|= Vy` |
| `8xy2` | `AND Vx, Vy` | `Vx &= Vy` |
| `8xy3` | `XOR Vx, Vy` | `Vx ^= Vy` |
| `8xy4` | `ADD Vx, Vy` | `Vx += Vy`; `VF = 1` on carry |
| `8xy5` | `SUB Vx, Vy` | `Vx -= Vy`; `VF = 1` when there was **no** borrow |
| `8xy6` | `SHR Vx` | `VF = Vx & 1`, then `Vx >>= 1` — **in place**, `Vy` is ignored |
| `8xy7` | `SUBN Vx, Vy` | `Vx = Vy - Vx`; `VF = 1` when there was no borrow |
| `8xyE` | `SHL Vx` | `VF = Vx >> 7`, then `Vx <<= 1` — **in place**, `Vy` is ignored |
| `9xy0` | `SNE Vx, Vy` | Skip if `Vx != Vy` |
| `Annn` | `LD I, nnn` | `I = nnn` |
| `Bnnn` | `JP V0, nnn` | Jump to `nnn + V0` |
| `Cxkk` | `RND Vx, kk` | `Vx = random_byte & kk` |
| `Dxyn` | `DRW Vx, Vy, n` | Draw `n` rows of 8 bits from `I` at `(Vx, Vy)`, XOR, wrapping at the edges; `VF = 1` on a collision |
| `Ex9E` | `SKP Vx` | Skip if key `Vx` is held |
| `ExA1` | `SKNP Vx` | Skip if key `Vx` is not held |
| `Fx07` | `LD Vx, DT` | `Vx = delay timer` |
| `Fx0A` | `LD Vx, K` | Wait for a key, then `Vx = key` |
| `Fx15` | `LD DT, Vx` | Delay timer = `Vx` |
| `Fx18` | `LD ST, Vx` | Sound timer = `Vx` |
| `Fx1E` | `ADD I, Vx` | `I += Vx`, wrapping at `0xFFF`; `VF` is untouched |
| `Fx29` | `LD F, Vx` | `I = 0x50 + (Vx & 0xF) * 5`: the 4×5 glyph |
| `Fx33` | `LD B, Vx` | Write the three decimal digits of `Vx` at `I`, `I+1`, `I+2` |
| `Fx55` | `LD [I], Vx` | Write `V0`–`Vx` to memory from `I`; **`I += x + 1`** |
| `Fx65` | `LD Vx, [I]` | Read `V0`–`Vx` from memory at `I`; **`I += x + 1`** |

### SuperChip additions

| Opcode | Does |
| --- | --- |
| `00Cn` | Scroll the display down `n` pixels; the top is cleared |
| `00FB` | Scroll four pixels right; the gap is cleared |
| `00FC` | Scroll four pixels left; the gap is cleared |
| `00FD` | Halt — SuperChip's "exit interpreter" |
| `00FE` | Select 64×32 lo-res and clear |
| `00FF` | Select 128×64 hi-res and clear |
| `Dxy0` | In hi-res, a 16×16 sprite (two bytes per row); in lo-res, an 8×16 sprite with each column doubled |
| `Fx30` | `I = 0xA0 + (Vx & 0xF) * 10`: the 8×10 digit |
| `Fx75` | Store `V0`–`Vx` in the RPL flags |
| `Fx85` | Load `V0`–`Vx` from the RPL flags |

Selecting the mode the machine is already in still clears the screen, because a
ROM that does that expects a blank screen.

---

## The choices this machine makes

These are the 1977 answers, kept rather than corrected:

**Shifts are in place.** `8xy6` and `8xyE` shift `Vx` and ignore `Vy`. CHIP-48
and SCHIP shift `Vy` into `Vx` instead. A listing that names `Vy` still
assembles — the assembler accepts the operand and says nothing — but the machine
will shift `Vx`.

**`VF` is written before the result.** The flag an arithmetic instruction
produces is stored before the result, so naming `VF` as the destination leaves
the result there, not the flag. `8xy4` is the exception in the other direction:
it computes the sum, stores it, then stores the carry, so `VF` there ends up
holding the flag.

**Sprites wrap.** A sprite drawn at column 63 wraps to column 0 rather than being
clipped. The start position is `Vx % width` and `Vy % height`, so a start
position beyond the screen wraps too.

**`Fx55`/`Fx65` move `I`.** The 1977 interpreter leaves `I` one past the block;
CHIP-48 and SCHIP leave it where it was. Programs written for the later behaviour
that rely on `I` staying put will be wrong here, which is what the reference
machine does too.

**`Bnnn` adds `V0`.** The later `Bxnn` form, which adds `Vx`, is not implemented;
the assembler refuses `JP V5, addr` rather than silently producing an instruction
that ignores the register.

**A key register above `F` holds no key.** `Ex9E` with `Vx = 0xFF` shifts a bit
past the mask, finds nothing, and does not skip. This is the sane reading of a
case the original never faced.

**Unknown opcodes are errors.** `0xF0FF` stops the machine with
`unknown opcode 0xF0FF at 0x200` rather than being skipped; the error names the
address. `0NNN` is the one family that is ignored, because every interpreter
since the original ignores it.

---

## Limits, stated plainly

| Limit | Value |
| --- | --- |
| Program size | 3584 bytes, `0x200`–`0xFFF` |
| Opcodes per frame | 1 to 2000 |
| Frames in a headless run | 600 by default (`--frames`) |
| Stack depth | 16 on the COSMAC VIP; unbounded on the native platform, which grows a list instead of using a fixed page |
| Rewind | 120 snapshots, one every 15 frames: thirty seconds |

The machine reports what it cannot do instead of doing something else:

| Message | Cause |
| --- | --- |
| `RET with an empty call stack` | `RET` without a `CALL` (native platform) |
| `unknown opcode 0xNNNN at 0xNNN` | A `DW` or a binary byte that is not an instruction |
| `unknown arithmetic opcode 8xyN` / `unknown opcode FxNN` | Same, inside those families |
| `the program counter ran past the end of memory` | Fell off the end of the listing |
