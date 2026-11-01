; text.inc -- writing on the screen.
;
; The machine has no text: a character is a sprite, and a string is a run of
; characters drawn one after another. This library is that loop, plus the ASCII
; font in `font.inc` (4x5, the same shape as the machine's own font at 0x50).
;
; THE CURSOR IS V0 (x) AND V1 (y). Every routine here reads it from those two
; registers and leaves the advanced position there, so calling twice continues
; where the first call stopped, and a caller that wants a particular place sets
; V0 and V1 first. Everything else -- V2 onwards, VF, and I -- is clobbered:
; with sixteen registers, saving them costs more than documenting this.
;
;     LD V0, 8            ; x
;     LD V1, 3            ; y
;     .print hello        ; draws it; V0/V1 now point after the last character
;
; A string is a run of ASCII bytes ended by a zero, which is what `.string`
; declares. `.string hello "HI"` also defines `hello_hi`, `hello_lo` and
; `hello_len`, which is what `.print` and `.printc` use:
;
;     LD V0, 0            ; x
;     LD V1, 12           ; y
;     .printc title       ; centred, and it works out x itself

.include font

; ---------------------------------------------------------------------------
; t_char: draw one character.
;   in : V0 = x, V1 = y, V2 = ASCII code
;   out: V0 = x + 5, V1 = y, wrapped to the next line past column 59
; ---------------------------------------------------------------------------
t_char:
    ; Below space there is nothing to draw, so draw a space.
    LD V3, 0x1F
    SUB V3, V2          ; VF = 1 when 0x1F >= code
    SE VF, 1
    JP t_char_case
    LD V2, 32
t_char_case:
    ; The font is capitals, so a lowercase letter is its capital.
    LD V3, 0x60
    SUB V3, V2          ; VF = 1 when 0x60 >= code, so not lowercase
    SE VF, 0
    JP t_char_high
    LD V3, 0x7A
    SUB V3, V2          ; VF = 1 when 0x7A >= code, so lowercase
    SE VF, 1
    JP t_char_high
    ADD V2, 0xE0        ; -0x20
t_char_high:
    ; Above '_' there is no glyph, so draw a question mark.
    LD V3, 0x5F
    SUB V3, V2          ; VF = 1 when 0x5F >= code
    SE VF, 0
    JP t_char_split
    LD V2, 0x3F
t_char_split:
    ; Fifty-one glyphs is 255 bytes, the most one base address can cover, so
    ; the font comes in two blobs and this picks one.
    LD V3, 0x53         ; font_split: the first code in blob B
    SUB V3, V2          ; VF = 1 when code <= 82
    SE VF, 1
    JP t_char_b
    ADD V2, 0xE0        ; index = code - 32
    LD I, font_a
    JP t_char_offset
t_char_b:
    ADD V2, 0xAD        ; index = code - 83
    LD I, font_b
t_char_offset:
    ; offset = index * 5, built by adding: five bytes a glyph.
    LD V3, V2
    ADD V2, V2
    ADD V2, V2
    ADD V2, V3
    ADD I, V2
    DRW V0, V1, 5
    ADD V0, 5
    ; Past column 59 there is no room for another character.
    LD V3, 59
    SUB V3, V0
    SE VF, 1
    JP t_char_wrap
    RET
t_char_wrap:
    LD V0, 0
    ADD V1, 6
    RET

; ---------------------------------------------------------------------------
; t_print: draw a zero-terminated string at the cursor.
;   in : V4 = high byte of the string address, V5 = low byte, V0 = x, V1 = y
;   out: V0 = x, V1 = y after the last character
;
; The address arrives in two registers rather than in I, and that is not a
; quirk of this library: after the first glyph is drawn, I belongs to the font,
; and the machine has no instruction that reads I back. `.print NAME` writes the
; three instructions that set V4 and V5 for you.
;
; Walking the string needs a pointer the machine cannot index, so the loader
; instruction is patched: `t_patch` is rewritten with the current address, and
; then executed. Programs on this machine live in writable memory, which is how
; a 1977 interpreter would have done it too.
; ---------------------------------------------------------------------------
t_print:
    LD V6, V0           ; the cursor lives in V6/V7 across the calls below
    LD V7, V1
    LD V8, V4           ; and the pointer in V8/V9, which t_char leaves alone
    LD V9, V5
t_print_loop:
    LD V0, V8
    LD V2, 0xA0
    OR V0, V2           ; 0xA0 | high byte: the opcode of `LD I, nnn`
    LD V1, V9
    LD I, t_patch
    LD [I], V1          ; store both bytes: the loader now points at the string
t_patch:
    LD I, 0x200         ; patched above; the address is a placeholder
    LD V0, [I]          ; the character, and I moves past it
    ADD V9, 1
    SE V9, 0
    JP t_print_have
    ADD V8, 1           ; the low byte wrapped, so carry into the high one
t_print_have:
    SE V0, 0
    JP t_print_draw
    LD V0, V6
    LD V1, V7
    RET
t_print_draw:
    LD V2, V0
    LD V0, V6
    LD V1, V7
    CALL t_char
    LD V6, V0
    LD V7, V1
    JP t_print_loop

; ---------------------------------------------------------------------------
; t_center: draw a string centred on the screen.
;   in : V1 = y, V2 = how many characters, V4/V5 = the address, as t_print
;   out: V0 = x, V1 = y after the last character
;
; `.printc NAME` passes the length for you: `.string` and `.art` define
; `NAME_len` alongside the data.
; ---------------------------------------------------------------------------
t_center:
    LD V3, V2
    ADD V3, V3          ; 2n
    ADD V3, V3          ; 4n
    ADD V3, V2          ; 5n
    SHR V3              ; half of it
    LD V0, 32
    SUB V0, V3
    JP t_print

; ---------------------------------------------------------------------------
; t_num: draw a value 0-255 in decimal, without leading zeroes.
;   in : V0 = x, V1 = y, V2 = the value
;   out: V0 = x, V1 = y after the digits
; ---------------------------------------------------------------------------
t_num:
    LD V6, V0
    LD V7, V1
    LD I, t_bcd
    LD B, V2            ; three decimal digits land at t_bcd
    LD V9, 0            ; which digit, 0 to 2
    LD V4, 3            ; digits left to look at
    LD V8, 0            ; nothing printed yet
t_num_loop:
    ; The address is worked out again every time round: drawing a glyph takes
    ; I for the font, so the digit pointer cannot be kept in it.
    LD I, t_bcd
    ADD I, V9
    LD V0, [I]          ; the digit
    SE V0, 0
    JP t_num_show       ; a non-zero digit always shows
    SE V8, 1
    JP t_num_last       ; nothing printed yet, so this zero is a leading one
    JP t_num_show       ; something has been printed: the zero belongs
t_num_last:
    SE V4, 1
    JP t_num_next       ; suppress it
    JP t_num_show       ; unless it is the only digit there is
t_num_show:
    LD V8, 1
    ADD V0, 0x30        ; '0' + digit
    LD V5, V0
    LD V0, V6
    LD V1, V7
    LD V2, V5
    CALL t_char
    LD V6, V0
    LD V7, V1
t_num_next:
    ADD V9, 1
    ADD V4, 0xFF        ; one fewer digit
    SE V4, 0
    JP t_num_loop
    LD V0, V6
    LD V1, V7
    RET

; ---------------------------------------------------------------------------
; t_hex: draw a value 0-255 as two hexadecimal digits.
;   in : V0 = x, V1 = y, V2 = the value
;   out: V0 = x + 10, V1 = y
;
; This one uses the machine's own 4x5 hex font at 0x50 rather than the ASCII
; one: `LD F, Vx` points I straight at the glyph, which is exactly what that
; instruction is for.
; ---------------------------------------------------------------------------
t_hex:
    LD V6, V0
    LD V7, V1
    LD V4, V2
    SHR V4
    SHR V4
    SHR V4
    SHR V4              ; V4 = the high nibble
    LD V5, V2
    LD V3, 0x0F
    AND V5, V3          ; V5 = the low nibble
    LD F, V4
    DRW V0, V1, 5
    ADD V0, 5
    LD V1, V7
    LD F, V5
    DRW V0, V1, 5
    ADD V0, 5
    RET

; ---------------------------------------------------------------------------
; t_clear: the empty screen, with the cursor at the top left.
;   out: V0 = 0, V1 = 0
; ---------------------------------------------------------------------------
t_clear:
    CLS
    LD V0, 0
    LD V1, 0
    RET

; Three bytes of scratch for the BCD conversion, in the data section.
.bytes t_bcd
    0, 0, 0
.end
