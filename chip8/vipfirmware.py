"""The CHIP-8 interpreter as 1802 firmware: the VIP's resident application.

On a real VIP, CHIP-8 was not built into anything. It was a 512-byte 1802
program in the first page of RAM, and the games were data it ran. This is the
same arrangement: the listing below is assembled by :mod:`chip8.vipasm` and
loaded into the VIP's ROM, and every CHIP-8 opcode is a piece of code the
COSMAC processor actually executes.

The interpreter keeps its machine in one page of RAM, which is also the page a
CHIP-8 program could reach, exactly as the original did:

===== ============================================================
0x100 V0-VF, the eight-bit registers
0x114 the call stack pointer
0x115 the delay timer        0x116 the sound timer
0x117 the last opcode, high  0x118 and low
0x120 the call stack, sixteen entries of two bytes
0x140 the RPL flags
0x150 the drawing work area  0x160 the keypad work area
0x170 the decimal work area  0x1F0 the noise byte, written by the board
===== ============================================================

The ROM holds the fetch loop, the sixteen opcode groups and their tables. The
dispatch is a table of handler addresses, so a handler may be any size and no
group has to fit in a page: the fetch loop doubles the group number, indexes the
table, loads the address into R(F) and does ``SEP RF`` -- an indirect jump that
costs five bytes.

Every handler comes back with ``SEP R3`` to the instruction after the ``SEP RF``,
which is ``DONE``: one place that puts the six registers a handler borrows as
pointers back into page one. A handler may repoint R1, R2, R4, R5 or RE at the
display, at a ROM table or at the program's memory, and the next handler starts
from a clean page whatever it did, which is the invariant the firmware is built
around.

Only the sixteen registered opcodes of the 1977 machine are here, plus the three
of SuperChip's additions that need no hardware the VIP did not have: 00FD halts,
and FX75/FX85 store and load the sixteen bytes at 0x0140. The scrolls, the
screen modes, the 16-pixel sprite and the 8x10 font all need something the VIP
was not built with, and a program that uses them needs the native platform.
"""

from __future__ import annotations

from .vipasm import VipProgram, assemble_vip

__all__ = ["FIRMWARE_SOURCE", "build_firmware", "firmware"]

#: Machine addresses the firmware and the board agree on.
V_REGISTERS = 0x0100
STACK_POINTER = 0x0114
DELAY_TIMER = 0x0115
SOUND_TIMER = 0x0116
CALL_STACK = 0x0120
RPL_FLAGS = 0x0140
NOISE = 0x01F0
DISPLAY = 0x0800
DISPLAY_END = 0x0C00
PROGRAM = 0x0200

FIRMWARE_SOURCE = r"""
; ---------------------------------------------------------------------------
; CHIP-8 for the COSMAC VIP, in CDP1802 assembly.
; ---------------------------------------------------------------------------

.org 0x0000
BOOT:       LBR START           ; where a reset finds the interpreter

; ---------------------------------------------------------------------------
; Where the interpreter keeps things. The page is the CHIP-8 program's as well:
; a program that writes 0x0100 is writing the registers, as it always could.
; ---------------------------------------------------------------------------
V_BASE       = 0x0100
VF           = 0x010F
DISPLAY      = 0x0800
DISPLAY_END  = 0x0C00
PROGRAM      = 0x0200
SP_ADDR      = 0x0114
DT_ADDR      = 0x0115
ST_ADDR      = 0x0116
OPHI         = 0x0117
OPLO         = 0x0118
SCR0         = 0x0119
CALL_STACK   = 0x0120
RPL          = 0x0140
NOISE        = 0x01F0

W_X          = 0x0150          ; drawing: x, y, rows, sprite byte
W_Y          = 0x0151
W_N          = 0x0152
W_SPRITE     = 0x0153
W_COL        = 0x0154
W_B0         = 0x0155          ; the sprite shifted into two bytes
W_B1         = 0x0156
W_SHIFT      = 0x0157
W_COL1       = 0x0158
W_OLD0       = 0x0159
W_NEW0       = 0x0159
W_NEW1       = 0x015A
W_COLL       = 0x015B
W_COUNT      = 0x015C

K_MASK       = 0x0160          ; keypad: the row being strobed
K_COLS       = 0x0161          ; and the columns it answered with
K_ROW        = 0x0162
K_COL        = 0x0163
K_COLMASK    = 0x0164
K_KEY        = 0x0165

B_REM        = 0x0170          ; FX33: what is left, then the digits
B_HUN        = 0x0171
B_TEN        = 0x0172
C_COUNT      = 0x0178

.org 0x1000
; ---------------------------------------------------------------------------
; START: the state the interpreter needs before it can run a byte of CHIP-8.
; ---------------------------------------------------------------------------
START:
            LDI 0x00
            PLO RF                ; a handler page is then one PHI away
            LDI high(V_BASE)
            PHI R8                ; R8 -> V0, the low byte never changes
            PHI R4
            PHI R5
            PHI R1
            PHI R2
            PHI RA
            PHI RB
            PHI RC
            PHI RD
            PHI RE
            LDI low(SP_ADDR)
            PLO RC                ; RC -> the stack pointer
            LDI low(DT_ADDR)
            PLO RD                ; RD -> the delay timer
            LDI low(OPHI)
            PLO RA                ; RA -> the opcode, high byte
            LDI low(OPLO)
            PLO RB                ; RB -> the opcode, low byte
            LDI high(DONE)
            PHI R3
            LDI low(DONE)
            PLO R3                ; SEP R3 is how a handler comes back
            LDI high(PROGRAM)
            PHI R7
            LDI low(PROGRAM)
            PLO R7                ; the CHIP-8 program counter starts at 0x200
            LDI 0x00
            PHI R6
            PLO R6                ; I = 0

CLRV:       LDI 0x00
            STR R8
            INC R8
            GLO R8
            XRI 0x10
            LBNZ CLRV             ; V0..VF, sixteen bytes
            LDI low(V_BASE)
            PLO R8                ; the loop left R8 one past VF

            LDI 0x00
            STR RC                ; no calls are in progress
            STR RD                ; and both timers are zero
            LDI low(ST_ADDR)
            PLO RE
            LDI 0x00
            STR RE
            LDI low(SCR0)
            PLO RE

            LDI high(DISPLAY)
            PHI R9
            LDI low(DISPLAY)
            PLO R9
CLRS:       LDI 0x00
            STR R9
            INC R9
            GHI R9
            XRI high(DISPLAY_END)
            LBNZ CLRS             ; 1024 bytes of display
            SEP R3

; ---------------------------------------------------------------------------
; FETCH: two bytes of CHIP-8, then an indirect jump on the high nibble.
; ---------------------------------------------------------------------------
FETCH:
            GHI R7
            PHI R9
            GLO R7
            PLO R9
            LDA R9
            STR RA                ; the opcode's high byte
            LDA R9
            STR RB                ; and its low byte
            GHI R9
            PHI R7
            GLO R9
            PLO R7                ; PC += 2
            LDN RA
            ANI 0xF0
            SHR
            SHR
            SHR
            SHR                   ; D = the opcode group, 0 to 15
            SHL                   ; two bytes per table entry
            ADI low(TABLE)
            PLO R9
            LDI high(TABLE)
            PHI R9
            LDA R9
            PHI RF
            LDA R9
            PLO RF
            SEP RF                ; into the handler; R3 now points at DONE,
                                  ; which is where every handler comes back to

; ---------------------------------------------------------------------------
; DONE: the return from a handler, and the one place that puts the scratch
; pointers back in page one. A handler is free to repoint R1, R2, R4, R5 or RE
; at the display, at a ROM table or at the program's own memory; nothing it
; leaves behind can reach the handler that runs next. R3 is the PC while P = 3,
; so the handlers land here and are carried on by the LBR.
; ---------------------------------------------------------------------------
DONE:
            LDI high(V_BASE)
            PHI R1
            PHI R2
            PHI R4
            PHI R5
            PHI RE
            LBR FETCH

; ---------------------------------------------------------------------------
; SKIP2: step over the next CHIP-8 instruction. A shared leaf: the handler
; jumps here, and SEP R3 carries it back through DONE like any other return.
; ---------------------------------------------------------------------------
SKIP2:
            GLO R7
            ADI 0x02
            PLO R7
            GHI R7
            ADCI 0x00
            PHI R7
            SEP R3

; ---------------------------------------------------------------------------
; SETVF: VF = DF. The arithmetic handlers leave their flag in DF, as the 1802
; wants, and this is where CHIP-8 gets to see it.
; ---------------------------------------------------------------------------
SETVF:
            LDI low(VF)
            PLO R4
            LDI 0x00
            ADCI 0x00
            STR R4
            SEP R3

; ---------------------------------------------------------------------------
; Group 0: CLS, RET, halt; anything else is SYS, which every interpreter since
; the original has ignored.
; ---------------------------------------------------------------------------
h0:
            LDN RA
            XRI 0x00
            LBNZ h0_done
            LDN RB
            XRI 0x00
            LBZ h0_halt
            LDN RB
            XRI 0xE0
            LBZ h0_cls
            LDN RB
            XRI 0xEE
            LBZ h0_ret
            LDN RB
            XRI 0xFD
            LBZ h0_halt
h0_done:    SEP R3

h0_halt:    IDL                   ; 00FD, and the all-zero opcode a run falls into

; CLS clears 1,024 bytes. A store and an increment are four machine cycles a
; byte however the loop is written, so 4,096 cycles -- 32,768 clocks -- is the
; floor, and that is already more than the 29,344 clocks in a frame: no clear
; can finish inside one. What is left to save is the loop overhead, so eight
; bytes go down per pass. D has to be reloaded with zero each pass because GLO
; borrows it to test the counter; that is 2 + 8 x 4 + 2 + 2 = 38 cycles a pass,
; 38,912 clocks for the whole display, 1.33 frames.
h0_cls:
            LDI high(DISPLAY)
            PHI R9
            LDI low(DISPLAY)
            PLO R9
h0_cls_l:   LDI 0x00
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            STR R9
            INC R9
            GLO R9                ; the low byte wraps every 256 bytes, so the
            BNZ h0_cls_l          ; page is what says the 1,024 are done
            GHI R9
            XRI high(DISPLAY_END)
            BNZ h0_cls_l
            SEP R3

h0_ret:
            LDN RC
            SEX RC
            ADD                   ; two bytes per entry, counted from the top
            ADI low(CALL_STACK)
            PLO R9
            LDI high(CALL_STACK)
            PHI R9
            LDA R9
            PHI R7
            LDA R9
            PLO R7
            LDN RC
            SMI 0x01
            STR RC                ; and only now is the entry gone
            SEP R3

; ---------------------------------------------------------------------------
; Group 1: JP nnn
; ---------------------------------------------------------------------------
h1:
            LDN RA
            ANI 0x0F
            PHI R7
            LDN RB
            PLO R7
            SEP R3

; ---------------------------------------------------------------------------
; Group 2: CALL nnn
; ---------------------------------------------------------------------------
h2:
            LDN RC
            ADI 0x01
            STR RC
            SEX RC
            ADD
            ADI low(CALL_STACK)
            PLO R9
            LDI high(CALL_STACK)
            PHI R9
            GHI R7
            STR R9
            INC R9
            GLO R7
            STR R9
            LDN RA
            ANI 0x0F
            PHI R7
            LDN RB
            PLO R7
            SEP R3

; ---------------------------------------------------------------------------
; Group 3: SE Vx, nn
; ---------------------------------------------------------------------------
h3:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            SEX R4
            XOR
            LBNZ h3_no
            LBR SKIP2
h3_no:      SEP R3

; ---------------------------------------------------------------------------
; Group 4: SNE Vx, nn
; ---------------------------------------------------------------------------
h4:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            SEX R4
            XOR
            LBZ h4_no
            LBR SKIP2
h4_no:      SEP R3

; ---------------------------------------------------------------------------
; Group 5: SE Vx, Vy
; ---------------------------------------------------------------------------
h5:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            SHR
            SHR
            SHR
            SHR
            PLO R5
            LDN R4
            SEX R5
            XOR
            LBNZ h5_no
            LBR SKIP2
h5_no:      SEP R3

; ---------------------------------------------------------------------------
; Group 6: LD Vx, nn
; ---------------------------------------------------------------------------
h6:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            STR R4
            SEP R3

; ---------------------------------------------------------------------------
; Group 7: ADD Vx, nn
; ---------------------------------------------------------------------------
h7:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN R4
            SEX RB
            ADD
            STR R4
            SEP R3

; ---------------------------------------------------------------------------
; Group 8: the register arithmetic, chosen by the low nibble.
; ---------------------------------------------------------------------------
h8:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            SHR
            SHR
            SHR
            SHR
            PLO R5
            LDN RB
            ANI 0x0F
            XRI 0x00
            LBZ h8_0
            LDN RB
            ANI 0x0F
            XRI 0x01
            LBZ h8_1
            LDN RB
            ANI 0x0F
            XRI 0x02
            LBZ h8_2
            LDN RB
            ANI 0x0F
            XRI 0x03
            LBZ h8_3
            LDN RB
            ANI 0x0F
            XRI 0x04
            LBZ h8_4
            LDN RB
            ANI 0x0F
            XRI 0x05
            LBZ h8_5
            LDN RB
            ANI 0x0F
            XRI 0x06
            LBZ h8_6
            LDN RB
            ANI 0x0F
            XRI 0x07
            LBZ h8_7
            LDN RB
            ANI 0x0F
            XRI 0x0E
            LBZ h8_E
            SEP R3

h8_0:       LDN R5
            STR R4
            SEP R3
h8_1:       LDN R4
            SEX R5
            OR
            STR R4
            SEP R3
h8_2:       LDN R4
            SEX R5
            AND
            STR R4
            SEP R3
h8_3:       LDN R4
            SEX R5
            XOR
            STR R4
            SEP R3
h8_4:       LDN R4
            SEX R5
            ADD
            STR R4
            LBR SETVF
h8_5:       LDN R4
            SEX R5
            SM
            STR R4
            LBR SETVF
h8_6:       LDN R4
            SHR
            STR R4
            LBR SETVF
h8_7:       LDN R4
            SEX R5
            SD
            STR R4
            LBR SETVF
h8_E:       LDN R4
            SHL
            STR R4
            LBR SETVF

; ---------------------------------------------------------------------------
; Group 9: SNE Vx, Vy
; ---------------------------------------------------------------------------
h9:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RB
            SHR
            SHR
            SHR
            SHR
            PLO R5
            LDN R4
            SEX R5
            XOR
            LBZ h9_no
            LBR SKIP2
h9_no:      SEP R3

; ---------------------------------------------------------------------------
; Group A: LD I, nnn
; ---------------------------------------------------------------------------
hA:
            LDN RA
            ANI 0x0F              ; I is twelve bits: the 0xA goes
            PHI R6
            LDN RB
            PLO R6
            SEP R3

; ---------------------------------------------------------------------------
; Group B: JP V0 + nnn
; ---------------------------------------------------------------------------
hB:
            LDN RA
            ANI 0x0F
            PHI R7
            LDN RB
            PLO R7
            GLO R7
            SEX R8
            ADD
            PLO R7
            GHI R7
            ADCI 0x00
            PHI R7
            SEP R3

; ---------------------------------------------------------------------------
; Group C: RND Vx, nn -- from the board's noise byte
; ---------------------------------------------------------------------------
hC:
            LDN RA
            ANI 0x0F
            PLO R4
            LDI low(NOISE)
            PLO RE
            LDN RE
            SEX RB
            AND
            STR R4
            SEP R3

; ---------------------------------------------------------------------------
; Group D: DRW Vx, Vy, n -- XOR a sprite into the display, four lines per
; CHIP-8 row, and set VF when a pixel went out.
; ---------------------------------------------------------------------------
hD:
            LDI low(W_X)
            PLO RE
            LDI high(V_BASE)
            PHI R4                ; R4 doubles as a display pointer below, so
            LDN RA                ; its page is reset here every time
            ANI 0x0F
            PLO R4
            LDN R4
            ANI 0x3F
            STR RE                ; x = Vx mod 64

            LDI low(W_Y)
            PLO RE
            LDI high(V_BASE)      ; before LDN RB: LDI is what loads D
            PHI R5                ; R5 is a ROWLOW pointer later in the row
            LDN RB
            SHR
            SHR
            SHR
            SHR
            PLO R5
            LDN R5
            ANI 0x1F
            STR RE                ; y = Vy mod 32

            LDI low(W_N)
            PLO RE
            LDN RB
            ANI 0x0F
            STR RE                ; rows

            LDI high(V_BASE)
            PHI R4
            LDI low(VF)
            PLO R4
            LDI 0x00
            STR R4
            LDI low(W_COLL)
            PLO RE
            LDI 0x00
            STR RE                ; VF and the collision flag start clear

            GHI R6
            PHI R2
            GLO R6
            PLO R2                ; R2 -> the sprite

hD_row:
            LDI low(W_N)
            PLO RE
            LDN RE
            XRI 0x00
            LBZ hD_done
            SMI 0x01
            STR RE
            LDI low(W_SPRITE)
            PLO RE
            LDA R2
            STR RE                ; the row's sprite byte

            LDI low(W_COL)
            PLO RE
            LDI low(W_X)
            PLO R1
            LDN R1
            SHR
            SHR
            SHR
            STR RE                ; which byte of the display row

            LDI low(W_Y)
            PLO R1
            LDN R1
            SHR
            SHR
            SHR
            ADI high(DISPLAY)
            PHI R9                ; one of the display's four pages
            LDN R1
            ANI 0x07
            ADI low(ROWLOW)
            PLO R5
            LDI high(ROWLOW)
            PHI R5
            LDI low(W_COL)
            PLO RE
            LDN R5
            SEX RE
            ADD
            PLO R9                ; R9 -> the byte the sprite starts in
            GHI R9
            PHI R4

            LDI low(W_COL1)
            PLO RE
            LDI low(W_COL)
            PLO R1
            LDN R1
            ADI 0x01
            ANI 0x07
            STR RE                ; the second byte wraps within the row
            LDI low(W_Y)
            PLO R1
            LDN R1
            ANI 0x07
            ADI low(ROWLOW)
            PLO R5
            LDI high(ROWLOW)
            PHI R5
            LDI low(W_COL1)
            PLO RE
            LDN R5
            SEX RE
            ADD
            PLO R4                ; R4 -> the byte the sprite spills into

            LDI low(W_B0)
            PLO RE
            LDI low(W_SPRITE)
            PLO R1
            LDN R1
            STR RE                ; the sprite starts in the high half of the
            LDI low(W_B1)         ; 16-bit window, so shifting it right by the
            PLO RE                ; column's bit offset splits it across the two
            LDI 0x00              ; bytes of the row
            STR RE
            LDI low(W_SHIFT)
            PLO RE
            LDI low(W_X)
            PLO R1
            LDN R1
            ANI 0x07
            STR RE
hD_shift:
            LDI low(W_SHIFT)
            PLO RE
            LDN RE
            XRI 0x00
            LBZ hD_shifted
            SMI 0x01
            STR RE
            LDI low(W_B0)
            PLO R1
            LDN R1
            SHR
            STR R1
            LDI low(W_B1)
            PLO R1
            LDN R1
            SHRC
            STR R1
            LBR hD_shift

hD_shifted:
            LDI low(W_B0)
            PLO R1
            LDN R9
            SEX R1
            AND
            XRI 0x00
            LBZ hD_nc0
            LDI low(W_COLL)
            PLO RE
            LDI 0x01
            STR RE
hD_nc0:
            LDI low(W_NEW0)
            PLO RE
            LDI low(W_B0)
            PLO R1
            LDN R9
            SEX R1
            XOR
            STR RE

            LDI low(W_B1)
            PLO R1
            LDN R4
            SEX R1
            AND
            XRI 0x00
            LBZ hD_nc1
            LDI low(W_COLL)
            PLO RE
            LDI 0x01
            STR RE
hD_nc1:
            LDI low(W_NEW1)
            PLO RE
            LDI low(W_B1)
            PLO R1
            LDN R4
            SEX R1
            XOR
            STR RE

            LDI low(W_COUNT)
            PLO RE
            LDI 0x04
            STR RE
hD_write:
            LDI low(W_NEW0)
            PLO RE
            LDN RE
            STR R9
            LDI low(W_NEW1)
            PLO RE
            LDN RE
            STR R4
            GLO R9
            ADI 0x08
            PLO R9
            GHI R9
            ADCI 0x00
            PHI R9
            GLO R4
            ADI 0x08
            PLO R4
            GHI R4
            ADCI 0x00
            PHI R4
            LDI low(W_COUNT)
            PLO RE
            LDN RE
            SMI 0x01
            STR RE
            LBNZ hD_write

            LDI low(W_Y)
            PLO RE
            LDN RE
            ADI 0x01
            ANI 0x1F
            STR RE
            LBR hD_row

hD_done:
            LDI low(W_COLL)
            PLO RE
            LDN RE
            XRI 0x00
            LBZ hD_end
            LDI high(V_BASE)      ; R4 is a display pointer by now
            PHI R4
            LDI low(VF)
            PLO R4
            LDI 0x01
            STR R4
hD_end:     SEP R3

; ---------------------------------------------------------------------------
; Group E: EX9E and EXA1, the two keypad skips.
; ---------------------------------------------------------------------------
hE:
            LDN RB
            XRI 0x9E
            LBZ hE_test
            LDN RB
            XRI 0xA1
            LBZ hE_test
            SEP R3
hE_test:
            LDN RA
            ANI 0x0F
            PLO R4
            LDI low(K_KEY)
            PLO RE
            LDN R4
            ANI 0x0F
            STR RE                ; the key to look for

            LDI low(K_MASK)
            PLO RE
            LDI low(K_KEY)
            PLO R1
            LDN R1
            SHR
            SHR                   ; the row is the key's top two bits
            ADI low(MASKS)
            PLO R5
            LDI high(MASKS)
            PHI R5
            LDN R5
            STR RE
            SEX RE
            OUT 4                 ; strobe that row
            INP 1                 ; and read its four columns

            LDI low(K_COLMASK)
            PLO RE
            LDI low(K_KEY)
            PLO R1
            LDN R1
            ANI 0x03
            ADI low(MASKS)
            PLO R5
            LDI high(MASKS)
            PHI R5
            LDN R5
            STR RE                ; the mask for the key's column

            LDI low(K_COLS)
            PLO RE
            LDI low(K_COLMASK)
            PLO R1
            LDN RE                ; after the LDI: that is what puts a byte in D
            SEX R1
            AND
            XRI 0x00
            LBZ hE_free
            LDN RB
            XRI 0x9E
            LBZ hE_skip
            SEP R3
hE_free:
            LDN RB
            XRI 0xA1
            LBZ hE_skip
            SEP R3
hE_skip:    LBR SKIP2

; ---------------------------------------------------------------------------
; Group F: timers, keypad, address arithmetic and the register blocks.
; ---------------------------------------------------------------------------
hF:
            LDN RB
            XRI 0x07
            LBZ hF_07
            LDN RB
            XRI 0x0A
            LBZ hF_0A
            LDN RB
            XRI 0x15
            LBZ hF_15
            LDN RB
            XRI 0x18
            LBZ hF_18
            LDN RB
            XRI 0x1E
            LBZ hF_1E
            LDN RB
            XRI 0x29
            LBZ hF_29
            LDN RB
            XRI 0x33
            LBZ hF_33
            LDN RB
            XRI 0x55
            LBZ hF_55
            LDN RB
            XRI 0x65
            LBZ hF_65
            LDN RB
            XRI 0x75
            LBZ hF_75
            LDN RB
            XRI 0x85
            LBZ hF_85
            SEP R3

hF_07:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN RD
            STR R4
            SEP R3

hF_15:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN R4
            STR RD
            SEP R3

hF_18:
            LDI low(ST_ADDR)
            PLO RE
            LDN RA
            ANI 0x0F
            PLO R4
            LDN R4
            STR RE
            SEP R3

hF_1E:
            LDN RA
            ANI 0x0F
            PLO R4
            GLO R6
            SEX R4
            ADD
            PLO R6
            GHI R6
            ADCI 0x00
            ANI 0x0F              ; twelve bits of address, so I wraps at 0x1000
            PHI R6
            SEP R3

hF_29:
            LDN RA
            ANI 0x0F
            PLO R4
            LDN R4
            ANI 0x0F
            SHL
            ADI low(FONTTAB)
            PLO R1
            LDI high(FONTTAB)
            PHI R1
            LDA R1
            PHI R6
            LDA R1
            PLO R6
            SEP R3

; FX0A waits: the keypad is scanned row by row until a key answers, and the
; key's number is looked up from the position it was found in.
hF_0A:
            LDN RA
            ANI 0x0F
            PLO R4
hF_0A_row:
            LDI low(K_ROW)
            PLO RE
            LDI 0x00
            STR RE
hF_0A_row2:
            LDI low(K_MASK)
            PLO RE
            LDI low(K_ROW)
            PLO R1
            LDN R1
            ADI low(MASKS)
            PLO R5
            LDI high(MASKS)
            PHI R5
            LDN R5
            STR RE
            SEX RE
            OUT 4
            INP 1                 ; the columns land in K_COLS
            LDI low(K_COLS)
            PLO R1
            LDN R1
            XRI 0x00
            LBNZ hF_0A_found
            LDI low(K_ROW)
            PLO RE
            LDN RE
            ADI 0x01
            ANI 0x03
            STR RE
            LBR hF_0A_row2

hF_0A_found:
            LDI low(K_COL)
            PLO RE
            LDI 0x00
            STR RE
hF_0A_col:
            LDI low(K_COLMASK)
            PLO RE
            LDI low(K_COL)
            PLO R1
            LDN R1
            ADI low(MASKS)
            PLO R5
            LDI high(MASKS)
            PHI R5
            LDN R5
            STR RE
            LDI low(K_COLS)
            PLO R1
            LDI high(V_BASE)      ; R5 was pointing at MASKS, in ROM
            PHI R5
            LDI low(K_COLMASK)
            PLO R5
            LDN R1                ; after the LDI: that is what puts a byte in D
            SEX R5
            AND
            XRI 0x00
            LBNZ hF_0A_got
            LDI low(K_COL)
            PLO RE
            LDN RE
            ADI 0x01
            ANI 0x03              ; four columns, then round again
            STR RE
            LBR hF_0A_col
hF_0A_got:
            LDI high(V_BASE)
            PHI R5
            LDI low(K_COL)
            PLO R1
            LDI low(K_ROW)
            PLO R5
            LDN R5
            SHL
            SHL                   ; row * 4
            SEX R1
            ADD
            ADI low(KEYMAP)
            PLO R1
            LDI high(KEYMAP)
            PHI R1
            LDN R1
            STR R4
            SEP R3

hF_33:
            LDI low(B_REM)
            PLO RE
            LDN RA
            ANI 0x0F
            PLO R4
            LDN R4
            STR RE                ; what is left to divide
            LDI low(B_HUN)
            PLO RE
            LDI 0x00
            STR RE
hF_33_h:
            LDI low(B_REM)
            PLO RE
            LDN RE
            SMI 0x64
            BNF hF_33_hd
            STR RE
            LDI low(B_HUN)
            PLO RE
            LDN RE
            ADI 0x01
            STR RE
            LBR hF_33_h
hF_33_hd:
            ADI 0x64              ; undo the subtraction that borrowed
            STR RE                ; RE still points at the remainder
            LDI low(B_TEN)
            PLO RE
            LDI 0x00
            STR RE
hF_33_t:
            LDI low(B_REM)
            PLO RE
            LDN RE
            SMI 0x0A
            BNF hF_33_td
            STR RE
            LDI low(B_TEN)
            PLO RE
            LDN RE
            ADI 0x01
            STR RE
            LBR hF_33_t
hF_33_td:
            ADI 0x0A
            STR RE
            GHI R6
            PHI R1
            GLO R6
            PLO R1
            LDI low(B_HUN)
            PLO RE
            LDN RE
            STR R1
            INC R1
            LDI low(B_TEN)
            PLO RE
            LDN RE
            STR R1
            INC R1
            LDI low(B_REM)
            PLO RE
            LDN RE
            STR R1
            SEP R3

hF_55:
            LDI low(C_COUNT)
            PLO RE
            LDN RA
            ANI 0x0F
            STR RE
            LDN RE
            ADI 0x01
            STR RE                ; x + 1 bytes
            GHI R8
            PHI R2
            GLO R8
            PLO R2                ; R2 -> V0
            GHI R6
            PHI R1
            GLO R6
            PLO R1                ; R1 -> I
hF_55_l:
            LDN R2
            STR R1
            INC R1
            INC R2
            LDI low(C_COUNT)
            PLO RE
            LDN RE
            SMI 0x01
            STR RE
            LBNZ hF_55_l
            GHI R1
            ANI 0x0F
            PHI R6
            GLO R1
            PLO R6
            SEP R3

hF_65:
            LDI low(C_COUNT)
            PLO RE
            LDN RA
            ANI 0x0F
            STR RE
            LDN RE
            ADI 0x01
            STR RE                ; x + 1 bytes
            GHI R8
            PHI R2
            GLO R8
            PLO R2                ; R2 -> V0
            GHI R6
            PHI R1
            GLO R6
            PLO R1                ; R1 -> I
hF_65_l:
            LDN R1
            STR R2
            INC R1
            INC R2
            LDI low(C_COUNT)
            PLO RE
            LDN RE
            SMI 0x01
            STR RE
            LBNZ hF_65_l
            GHI R1
            ANI 0x0F
            PHI R6
            GLO R1
            PLO R6
            SEP R3

hF_75:
            LDI low(C_COUNT)
            PLO RE
            LDN RA
            ANI 0x0F
            STR RE
            LDN RE
            ADI 0x01
            STR RE                ; x + 1 bytes
            GHI R8
            PHI R2
            GLO R8
            PLO R2                ; R2 -> V0
            LDI low(RPL)
            PLO R1
            LDI high(RPL)
            PHI R1
            GHI R6
            PHI R1
            GLO R6
            PLO R1                ; R1 -> I
hF_75_l:
            LDN R2
            STR R1
            INC R1
            INC R2
            LDI low(C_COUNT)
            PLO RE
            LDN RE
            SMI 0x01
            STR RE
            LBNZ hF_75_l
            GHI R1
            ANI 0x0F
            PHI R6
            GLO R1
            PLO R6
            SEP R3

hF_85:
            LDI low(C_COUNT)
            PLO RE
            LDN RA
            ANI 0x0F
            STR RE
            LDN RE
            ADI 0x01
            STR RE                ; x + 1 bytes
            GHI R8
            PHI R2
            GLO R8
            PLO R2                ; R2 -> V0
            LDI low(RPL)
            PLO R1
            LDI high(RPL)
            PHI R1
            GHI R6
            PHI R1
            GLO R6
            PLO R1                ; R1 -> I
hF_85_l:
            LDN R1
            STR R2
            INC R1
            INC R2
            LDI low(C_COUNT)
            PLO RE
            LDN RE
            SMI 0x01
            STR RE
            LBNZ hF_85_l
            GHI R1
            ANI 0x0F
            PHI R6
            GLO R1
            PLO R6
            SEP R3

; ---------------------------------------------------------------------------
; Tables. The dispatch table starts on a page boundary so that adding two per
; group can never carry out of the low byte.
; ---------------------------------------------------------------------------
.org 0x1F00
TABLE:      DW h0, h1, h2, h3, h4, h5, h6, h7
            DW h8, h9, hA, hB, hC, hD, hE, hF
MASKS:      DB 0x01, 0x02, 0x04, 0x08
ROWLOW:     DB 0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0, 0xE0
FONTTAB:    DW 0x0050, 0x0055, 0x005A, 0x005F, 0x0064, 0x0069, 0x006E, 0x0073
            DW 0x0078, 0x007D, 0x0082, 0x0087, 0x008C, 0x0091, 0x0096, 0x009B
KEYMAP:     DB 0x01, 0x02, 0x03, 0x0C, 0x04, 0x05, 0x06, 0x0D
            DB 0x07, 0x08, 0x09, 0x0E, 0x0A, 0x00, 0x0B, 0x0F
"""


def build_firmware() -> VipProgram:
    """Assemble the listing. Cheap enough to do at import; done once."""

    return assemble_vip(FIRMWARE_SOURCE, origin=0x1000)


_FIRMWARE: VipProgram | None = None


def firmware() -> VipProgram:
    """The assembled interpreter, built on first use."""

    global _FIRMWARE
    if _FIRMWARE is None:
        _FIRMWARE = build_firmware()
    return _FIRMWARE
