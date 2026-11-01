; input.inc -- asking the keypad a question.
;
; `LD Vx, K` waits for a key, and `SKP`/`SKNP` test one; that is the whole
; keypad interface the machine has. These three routines are the shapes a
; program actually wants:
;
;     i_poll          V0 = the lowest key held, or 0xFF when none is
;     i_wait          V0 = a key, waiting as long as it takes
;     i_wait_release  waits until V0 is let go, which is what stops one press
;                     from being read as ten
;
; A whole-program run has no hands, so `i_wait` there never returns -- use the
; `; keys` comment or `--keys` to hold a key, and the wait completes.

; ---------------------------------------------------------------------------
; i_poll: the lowest key held, or 0xFF.
; clobbers V0 and VF only.
; ---------------------------------------------------------------------------
i_poll:
    LD V0, 0
i_poll_loop:
    SKNP V0         ; not held: try the next key
    RET             ; held: V0 already holds the key
    ADD V0, 1
    SE V0, 0x10
    JP i_poll_loop
    LD V0, 0xFF
    RET

; ---------------------------------------------------------------------------
; i_wait: block until a key is held, and return it in V0.
; clobbers V0 and VF only.
; ---------------------------------------------------------------------------
i_wait:
    CALL i_poll
    SE V0, 0xFF
    RET
    JP i_wait

; ---------------------------------------------------------------------------
; i_wait_release: block until the key in V0 is let go.
; clobbers VF only.
; ---------------------------------------------------------------------------
i_wait_release:
    SKNP V0
    RET
    JP i_wait_release
