; math.inc -- the arithmetic the machine does not have.
;
; CHIP-8 can add and subtract and shift; it has no multiply, no divide, no
; comparison and no sign. Everything here is built out of those four, in eight
; bits, and the arguments are in V0 and V1 just as the instructions take them.
;
; Results are where the caller would put them:
;
;     m_mul   V0 * V1        -> V2 = low byte, V3 = high byte
;     m_div   V0 / V1        -> V2 = quotient, V3 = remainder
;     m_mod   V0 mod V1      -> V2 = remainder
;     m_rnd   V0 = range     -> V0 = a number in 0 .. range-1
;     m_absdiff V0, V1       -> V0 = |V0 - V1|
;     m_min / m_max          -> V0 = the smaller / larger
;
; Division is repeated subtraction, because the shift-and-subtract form needs
; nine bits of remainder and this machine has eight. That makes `m_div` cost up
; to 255 iterations: at the default speed a division is a visible moment, so a
; program that divides inside a per-frame loop should be run faster with
; `--speed`.

; ---------------------------------------------------------------------------
; m_mul: V0 * V1, eight bits by eight bits into sixteen.
;
; Shift and add: the multiplicand doubles and the multiplier halves, and a set
; bit in the multiplier adds the multiplicand into the result. V7 carries the
; multiplicand's own high byte, which is what keeps a shifted-out bit from
; being lost.
; ---------------------------------------------------------------------------
m_mul:
    LD V2, 0
    LD V3, 0
    LD V7, 0
    LD V4, 8
m_mul_loop:
    LD V5, V1
    LD V6, 1
    AND V5, V6          ; the low bit of the multiplier
    SNE V5, 0           ; a clear bit adds nothing
    JP m_mul_skip
    ADD V2, V0
    ADD V3, VF          ; the carry out of the low byte
    ADD V3, V7          ; and the multiplicand's high half
m_mul_skip:
    ; The multiplicand is sixteen bits wide, so it is shifted as V7:V0. `SHL`
    ; leaves the bit it pushed out in VF, which is exactly the bit that belongs
    ; in the high half.
    LD V6, V0
    SHL V6
    LD V6, VF
    SHL V0
    SHL V7
    OR V7, V6
    SHR V1
    ADD V4, 0xFF
    SE V4, 0
    JP m_mul_loop
    RET

; ---------------------------------------------------------------------------
; m_div: V0 / V1 -> quotient in V2, remainder in V3. A divisor of zero gives
; 0xFF and leaves the dividend as the remainder, rather than looping for ever.
; ---------------------------------------------------------------------------
m_div:
    LD V2, 0
    LD V3, V0
    SE V1, 0
    JP m_div_loop
    LD V2, 0xFF         ; dividing by zero: say so rather than loop for ever
    RET
m_div_loop:
    LD V4, V3
    SUB V4, V1          ; VF = 1 when the remainder still holds the divisor
    SE VF, 1
    RET
    LD V3, V4
    ADD V2, 1
    JP m_div_loop

; ---------------------------------------------------------------------------
; m_mod: V0 mod V1 -> V2.
; ---------------------------------------------------------------------------
m_mod:
    CALL m_div
    LD V2, V3
    RET

; ---------------------------------------------------------------------------
; m_rnd: V0 = a number in 0 .. range-1.
;
; The generator is the machine's own `RND`, so this is a modulo of a uniform
; byte: the low values are very slightly likelier. For colour, motion and dice
; that is invisible; for anything where it is not, this is the wrong routine
; and a rejection loop is the right one.
; ---------------------------------------------------------------------------
m_rnd:
    LD V1, V0
    RND V0, 0xFF
    CALL m_div
    LD V0, V3
    RET

; ---------------------------------------------------------------------------
; m_absdiff: V0 = |V0 - V1|.
; ---------------------------------------------------------------------------
m_absdiff:
    LD V2, V0
    SUB V2, V1          ; VF = 1 when V0 >= V1
    SE VF, 1
    JP m_absdiff_neg
    LD V0, V2
    RET
m_absdiff_neg:
    SUB V1, V0
    LD V0, V1
    RET

; ---------------------------------------------------------------------------
; m_min / m_max: V0 = the smaller, or the larger, of V0 and V1.
; ---------------------------------------------------------------------------
m_min:
    LD V2, V0
    SUB V2, V1          ; VF = 1 when V0 >= V1, so V1 is the answer
    SE VF, 1
    RET
    LD V0, V1
    RET

m_max:
    LD V2, V0
    SUB V2, V1          ; VF = 1 when V0 >= V1, so V0 is the answer
    SE VF, 1
    JP m_max_swap
    RET
m_max_swap:
    LD V0, V1
    RET
