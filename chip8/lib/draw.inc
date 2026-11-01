; draw.inc -- points, lines and boxes.
;
; CHIP-8 has one drawing instruction and it is XOR: `DRW` flips the pixels of a
; sprite. Everything here is built on two tricks with it.
;
; The first is how to *set* a pixel on a machine that can only flip one. Flip it,
; and if `VF` says it was already on, flip it back. Setting is two draws in the
; worst case, and clearing is the same two draws in the other order:
;
;     set:   DRW ; if VF then DRW
;     clear: DRW ; if not VF then DRW
;
; The second is that a horizontal run is a sprite of full bytes with a masked
; byte at the end. The masks are a table rather than a shift loop, because the
; table is nine bytes and the loop is slower than anything else in this file.
;
; ARGUMENTS are in V0 onwards and everything in V0-VF is clobbered: with sixteen
; registers there is nowhere to save them. Callers that need a value across a
; call keep it in memory; `LD [I], Vx` and `LD Vx, [I]` are two instructions and
; a data blob is free.
;
; Primitives wrap at the screen edges exactly as `DRW` does. Nothing here clips:
; a box drawn at x = 60 has part of it on the left edge, which is what the
; machine does and therefore what this library does.

; ---------------------------------------------------------------------------
; d_plot / d_unplot: set, or clear, the pixel at (V0, V1).
; These clobber I and VF only, so a caller can keep its state in V2-VF.
; ---------------------------------------------------------------------------
d_plot:
    LD I, d_one
    DRW V0, V1, 1
    SE VF, 0
    DRW V0, V1, 1       ; it was lit and is now dark: put it back
    RET

d_unplot:
    LD I, d_one
    DRW V0, V1, 1
    SE VF, 1
    DRW V0, V1, 1       ; it was dark and is now lit: take it away
    RET

; ---------------------------------------------------------------------------
; d_hline: eight pixels at a time, then a masked remainder.
;   in: V0 = x, V1 = y, V2 = width
; ---------------------------------------------------------------------------
d_hline:
    SE V2, 0
    JP d_hline_run
    RET
d_hline_run:
    LD V4, V2
    LD V3, 8
    SUB V4, V3          ; VF = 1 when a whole byte is left
    SE VF, 1
    JP d_hline_rest
    LD I, d_solid
    DRW V0, V1, 1
    SE VF, 0
    DRW V0, V1, 1       ; set rather than toggle
    ADD V0, 8
    ADD V2, 0xF8        ; width -= 8
    JP d_hline_run
d_hline_rest:
    SNE V2, 0           ; anything left over is drawn with a mask
    RET
    LD I, d_masks
    ADD I, V2           ; the mask that leaves the last few pixels
    DRW V0, V1, 1
    SE VF, 0
    DRW V0, V1, 1
    RET

; ---------------------------------------------------------------------------
; d_vline: fifteen rows at a time from a tall sprite, then one row at a time.
;   in: V0 = x, V1 = y, V2 = height
; ---------------------------------------------------------------------------
d_vline:
    SE V2, 0
    JP d_vline_run
    RET
d_vline_run:
    LD V4, V2
    LD V3, 15
    SUB V4, V3          ; VF = 1 when a whole tall sprite is left
    SE VF, 1
    JP d_vline_tail
    LD I, d_tall
    DRW V0, V1, 15
    SE VF, 0
    DRW V0, V1, 15
    ADD V1, 15
    ADD V2, 0xF1        ; height -= 15
    JP d_vline_run
d_vline_tail:
    SNE V2, 0
    RET
    CALL d_plot
    ADD V1, 1
    ADD V2, 0xFF
    JP d_vline_tail

; ---------------------------------------------------------------------------
; d_rect: the outline of a box.
;   in: V0 = x, V1 = y, V2 = width, V3 = height
;
; The four numbers go into `d_scratch` because a box needs them twice: once for
; the top and bottom rows, once for the sides. It also means the sides can be
; drawn with the height in V2, which is where `d_vline` wants it.
; ---------------------------------------------------------------------------
d_rect:
    SE V2, 0
    JP d_rect_go
    RET
d_rect_go:
    SE V3, 0
    JP d_rect_real
    RET
d_rect_real:
    LD I, d_scratch
    LD [I], V3          ; [x, y, w, h]
    CALL d_hline        ; the top row
    LD I, d_scratch
    LD V3, [I]          ; V0-V3 = x, y, w, h again
    ADD V1, V3
    ADD V1, 0xFF        ; y + h - 1
    CALL d_hline        ; the bottom row
    LD I, d_scratch
    LD V3, [I]
    LD V2, V3           ; height, for a vertical draw
    CALL d_vline        ; the left side
    LD I, d_scratch
    LD V3, [I]
    ADD V0, V2
    ADD V0, 0xFF        ; x + w - 1
    LD V2, V3
    CALL d_vline        ; the right side
    RET

; ---------------------------------------------------------------------------
; d_fill: a solid box.
;   in: V0 = x, V1 = y, V2 = width, V3 = height
; ---------------------------------------------------------------------------
d_fill:
    SE V3, 0
    JP d_fill_go
    RET
d_fill_go:
    LD I, d_scratch
    LD [I], V3          ; [x, y, w, h]
d_fill_row:
    LD I, d_scratch
    LD V3, [I]          ; V0-V3 = x, y, w, h
    SE V3, 0
    JP d_fill_draw
    RET
d_fill_draw:
    CALL d_hline        ; this row of the box
    LD I, d_scratch
    LD V3, [I]          ; the row draw clobbered V0 and V2
    ADD V1, 1
    ADD V3, 0xFF        ; one row fewer
    LD I, d_scratch
    LD [I], V3          ; [x, y + 1, w, h - 1]
    JP d_fill_row

; ---------------------------------------------------------------------------
; d_xhline / d_xfill: the same shapes, drawn with plain XOR instead of set.
; A thing drawn with XOR over itself disappears, which is how a moving object on
; this machine is erased without an erase instruction.
;   in: as d_hline / d_fill
; ---------------------------------------------------------------------------
d_xhline:
    SE V2, 0
    JP d_xhline_run
    RET
d_xhline_run:
    LD V4, V2
    LD V3, 8
    SUB V4, V3          ; VF = 1 when a whole byte is left
    SE VF, 1
    JP d_xhline_rest
    LD I, d_solid
    DRW V0, V1, 1
    ADD V0, 8
    ADD V2, 0xF8
    JP d_xhline_run
d_xhline_rest:
    SNE V2, 0
    RET
    LD I, d_masks
    ADD I, V2
    DRW V0, V1, 1
    RET

d_xfill:
    SE V3, 0
    JP d_xfill_go
    RET
d_xfill_go:
    LD I, d_scratch2
    LD [I], V3
d_xfill_row:
    LD I, d_scratch2
    LD V3, [I]
    SE V3, 0
    JP d_xfill_draw
    RET
d_xfill_draw:
    CALL d_xhline
    LD I, d_scratch2
    LD V3, [I]
    ADD V1, 1
    ADD V3, 0xFF
    LD I, d_scratch2
    LD [I], V3
    JP d_xfill_row

; ---------------------------------------------------------------------------
; d_line: Bresenham, from (V0, V1) to (V2, V3).
;
; The error term never grows past the longer axis -- at most 127 -- so the whole
; algorithm stays inside eight-bit arithmetic, which is the only kind this
; machine has. The error accumulates along the major axis and spills into a step
; along the minor one, and that is what makes the line straight.
;
; V4 = the minor delta, V5 = the major length while it is being worked out,
; V6 = the x step, V7 = the y step, V8 = the error, V9 = the major length,
; VA = steps left, VB = a scratch copy of the error, VC/VD = the moving point.
; ---------------------------------------------------------------------------
d_line:
    LD VC, V0           ; the moving point
    LD VD, V1
    ; dx = |x1 - x0|, and the step that closes it
    LD V4, V2
    SUB V4, V0
    SE VF, 1
    JP d_line_xneg
    LD V6, 1
    JP d_line_sy
d_line_xneg:
    LD V6, 0xFF
    LD V4, V0
    SUB V4, V2
d_line_sy:
    ; dy = |y1 - y0|, and the step that closes it
    LD V5, V3
    SUB V5, V1
    SE VF, 1
    JP d_line_yneg
    LD V7, 1
    JP d_line_axis
d_line_yneg:
    LD V7, 0xFF
    LD V5, V1
    SUB V5, V3
d_line_axis:
    LD V8, V4
    SUB V8, V5          ; VF = 1 when dx >= dy, so x is the major axis
    SE VF, 1
    JP d_line_ymajor
    ; -- x is major: every pixel steps x, and y when the error spills ------
    LD V9, V4
    LD V8, 0
    LD VA, V9
    ADD VA, 1           ; steps to take
d_line_xloop:
    LD V0, VC
    LD V1, VD
    CALL d_plot
    ADD VA, 0xFF
    SNE VA, 0
    RET
    ADD VC, V6
    ADD V8, V5
    LD VB, V8
    SUB VB, V9
    SE VF, 1
    JP d_line_xloop
    SUB V8, V9          ; the error spilled: take a step across
    ADD VD, V7
    JP d_line_xloop
    ; -- y is major: the same loop with the axes exchanged ----------------
d_line_ymajor:
    LD V9, V8           ; V8 still holds dy from the comparison above
    LD V8, 0
    LD VA, V9
    ADD VA, 1
d_line_yloop:
    LD V0, VC
    LD V1, VD
    CALL d_plot
    ADD VA, 0xFF
    SNE VA, 0
    RET
    ADD VD, V7
    ADD V8, V4          ; the minor delta is dx, still in V4
    LD VB, V8
    SUB VB, V9
    SE VF, 1
    JP d_line_yloop
    SUB V8, V9
    ADD VC, V6
    JP d_line_yloop

; --- the data these routines draw from -------------------------------------
.bytes d_one
    0x80
.end
.bytes d_solid
    0xFF
.end
.bytes d_tall
    0x80,0x80,0x80,0x80,0x80,0x80,0x80,0x80
    0x80,0x80,0x80,0x80,0x80,0x80,0x80
.end
; d_masks[n] is the top n pixels of a byte: 0 draws nothing, 8 draws all of it.
.bytes d_masks
    0x00,0x80,0xC0,0xE0,0xF0,0xF8,0xFC,0xFE,0xFF
.end
.bytes d_scratch
    0,0,0,0
.end
.bytes d_scratch2
    0,0,0,0
.end
