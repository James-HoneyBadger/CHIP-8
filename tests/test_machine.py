"""The machine's instructions, quirks and screen, ported from the reference tests."""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.constants import (  # noqa: E402
    FONT_ADDRESS,
    HI_RES_HEIGHT,
    HI_RES_WIDTH,
    LARGE_FONT_ADDRESS,
    MEMORY_SIZE,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
)
from chip8.machine import Chip8Error, Chip8Machine  # noqa: E402
from tests.support import assemble, lit_pixels, machine_for, run_to_halt  # noqa: E402


class ArithmeticTests(unittest.TestCase):
    def test_ld_and_add_with_a_byte(self):
        self.assertEqual(run_to_halt("start: LD V0, 5\n ADD V0, 3\n HALT").v[0], 8)

    def test_add_sets_the_carry_flag(self):
        machine = run_to_halt(
            "start: LD V0, 200\n LD V1, 100\n ADD V0, V1\n HALT"
        )
        self.assertEqual(machine.v[0], 44)
        self.assertEqual(machine.v[0xF], 1)

    def test_subtract_sets_the_borrow_flag(self):
        machine = run_to_halt("start: LD V0, 5\n LD V1, 9\n SUB V0, V1\n HALT")
        self.assertEqual(machine.v[0], 252)
        self.assertEqual(machine.v[0xF], 0)

    def test_shr_shifts_the_named_register_in_place(self):
        machine = run_to_halt("start: LD V0, 8\n LD V1, 1\n SHR V0\n HALT")
        self.assertEqual(machine.v[0], 4)
        self.assertEqual(machine.v[1], 1)
        self.assertEqual(machine.v[0xF], 0)

    def test_logic_and_shl(self):
        machine = run_to_halt(
            "start: LD V0, 0x0C\n LD V1, 0x0A\n"
            " LD V2, 0x0C\n OR V2, V1\n"
            " LD V3, 0x0C\n AND V3, V1\n"
            " LD V4, 0x0C\n XOR V4, V1\n"
            " LD V5, 3\n LD V6, 10\n SUBN V5, V6\n"
            " LD V7, 0x81\n SHL V7\n HALT"
        )
        self.assertEqual(machine.v[2], 0x0E)
        self.assertEqual(machine.v[3], 0x08)
        self.assertEqual(machine.v[4], 0x06)
        self.assertEqual(machine.v[5], 7)
        self.assertEqual(machine.v[7], 2)
        self.assertEqual(machine.v[0xF], 1)

    def test_add_byte_wraps_without_carry(self):
        machine = run_to_halt("start: LD V0, 250\n ADD V0, 10\n HALT")
        self.assertEqual(machine.v[0], 4)
        # 7XNN does not touch VF; only the arithmetic group does.
        self.assertEqual(machine.v[0xF], 0)

    def test_index_arithmetic(self):
        self.assertEqual(
            run_to_halt("start: LD I, 0x300\n LD V0, 5\n ADD I, V0\n HALT").i, 0x305
        )

    def test_index_arithmetic_wraps_at_twelve_bits(self):
        machine = run_to_halt("start: LD I, 0xFFF\n LD V0, 2\n ADD I, V0\n HALT")
        self.assertEqual(machine.i, 0x001)

    def test_bcd_conversion_writes_three_digits(self):
        machine, program = machine_for(
            "start: LD V0, 205\n LD I, data\n LD B, V0\n HALT\n"
            "data: DB 0\n DB 0\n DB 0"
        )
        while not machine.halted:
            machine.step()
        start = program.labels["data"]
        self.assertEqual(machine.memory[start], 2)
        self.assertEqual(machine.memory[start + 1], 0)
        self.assertEqual(machine.memory[start + 2], 5)

    def test_store_and_load_through_i(self):
        machine = run_to_halt(
            "start: LD V0, 1\n LD V1, 2\n LD I, buf\n LD [I], V1\n"
            " LD I, buf\n LD V3, [I]\n HALT\n"
            "buf: DB 0\n DB 0"
        )
        self.assertEqual(machine.v[0], 1)
        self.assertEqual(machine.v[1], 2)
        self.assertEqual(machine.v[3], 0)

    def test_block_transfer_advances_i(self):
        # The 1977 interpreter leaves I one past the block; CHIP-48 and SCHIP
        # leave it where it was. This machine is the 1977 one.
        machine, program = machine_for(
            "start: LD V0, 1\n LD V1, 2\n LD I, buf\n LD [I], V1\n HALT\n"
            "buf: DB 0\n DB 0"
        )
        while not machine.halted:
            machine.step()
        self.assertEqual(machine.i, program.labels["buf"] + 2)

        loaded, program = machine_for(
            "start: LD I, buf\n LD V1, [I]\n HALT\nbuf: DB 7\n DB 9"
        )
        while not loaded.halted:
            loaded.step()
        self.assertEqual(loaded.v[0], 7)
        self.assertEqual(loaded.v[1], 9)
        self.assertEqual(loaded.i, program.labels["buf"] + 2)

    def test_random_is_masked_by_the_operand(self):
        program = assemble("start: RND V0, 0x0F\n HALT")
        machine = Chip8Machine.from_program(
            program.memory, program.entry, rng=random.Random(4)
        )
        while not machine.halted:
            machine.step()
        self.assertLessEqual(machine.v[0], 0x0F)


class FlowTests(unittest.TestCase):
    def test_skips(self):
        self.assertEqual(
            run_to_halt("start: LD V0, 5\n SE V0, 5\n LD V1, 1\n HALT").v[1], 0
        )
        self.assertEqual(
            run_to_halt("start: LD V0, 5\n SNE V0, 5\n LD V1, 1\n HALT").v[1], 1
        )
        self.assertEqual(
            run_to_halt(
                "start: LD V0, 5\n LD V1, 5\n SE V0, V1\n LD V2, 1\n HALT"
            ).v[2],
            0,
        )
        self.assertEqual(
            run_to_halt(
                "start: LD V0, 5\n LD V1, 5\n SNE V0, V1\n LD V2, 1\n HALT"
            ).v[2],
            1,
        )

    def test_call_and_return(self):
        machine = run_to_halt(
            "start: LD V0, 7\n CALL sub\n HALT\nsub: ADD V0, 1\n RET"
        )
        self.assertEqual(machine.v[0], 8)
        self.assertEqual(machine.stack, [])

    def test_ret_without_call_is_reported(self):
        with self.assertRaises(Chip8Error) as caught:
            run_to_halt("start: RET")
        self.assertIn("empty call stack", str(caught.exception))

    def test_jump_with_offset_uses_v0(self):
        def program(offset: int) -> str:
            return (
                f"start: LD V0, {offset}\n JP V0, target\n LD V1, 1\n"
                "target: LD V2, 9\n HALT"
            )

        self.assertEqual(run_to_halt(program(0)).v[2], 9)
        # Two bytes past the label skips `LD V2, 9`, so V2 keeps its zero.
        machine = run_to_halt(program(2))
        self.assertEqual(machine.v[2], 0)
        self.assertEqual(machine.v[1], 0)

    def test_sys_is_ignored(self):
        self.assertEqual(run_to_halt("start: SYS 0x123\n LD V0, 7\n HALT").v[0], 7)

    def test_unknown_opcode_is_reported(self):
        with self.assertRaises(Chip8Error) as caught:
            run_to_halt("start: DW 0xF0FF")
        self.assertIn("unknown opcode", str(caught.exception))

    def test_program_counter_past_memory_is_reported(self):
        machine, _ = machine_for("start: HALT")
        # The last word is a real instruction, so the next fetch is past the end.
        machine.pc = MEMORY_SIZE - 2
        machine.memory[MEMORY_SIZE - 2] = 0x60
        machine.memory[MEMORY_SIZE - 1] = 0x05
        machine.step()
        self.assertEqual(machine.v[0], 5)
        with self.assertRaises(Chip8Error) as caught:
            machine.step()
        self.assertIn("past the end of memory", str(caught.exception))


class KeypadTests(unittest.TestCase):
    def test_keypad_is_live(self):
        def run_with_key(down: bool) -> Chip8Machine:
            machine, _ = machine_for(
                "start: LD V0, 3\n SKP V0\n LD V1, 1\n HALT", interactive=True
            )
            machine.set_key(3, True)
            if not down:
                machine.set_key(3, False)
            while not machine.halted:
                machine.step()
            return machine

        self.assertEqual(run_with_key(True).v[1], 0)  # skipped
        self.assertEqual(run_with_key(False).v[1], 1)  # not skipped

    def test_ld_key_waits_for_a_press(self):
        machine, _ = machine_for(
            "start: LD V0, K\n LD V1, 9\n HALT", interactive=True
        )
        machine.step()  # LD V0, K: no key held yet
        self.assertTrue(machine.waiting_for_key)
        self.assertEqual(machine.v[0], 0)
        waiting_pc = machine.pc
        machine.step()
        self.assertTrue(machine.waiting_for_key)
        self.assertEqual(machine.pc, waiting_pc)
        machine.set_key(7, True)
        machine.step()  # the wait completes, V0 gets the key
        self.assertFalse(machine.waiting_for_key)
        self.assertEqual(machine.v[0], 7)
        machine.step()
        self.assertEqual(machine.v[1], 9)

    def test_whole_program_run_reads_the_keypad_once(self):
        machine, _ = machine_for("start: LD V0, K\n HALT")
        machine.step()
        self.assertFalse(machine.waiting_for_key)
        self.assertEqual(machine.v[0], 0)

    def test_seeded_keypad_is_read(self):
        self.assertEqual(run_to_halt("start: LD V0, K\n HALT", 1 << 5).v[0], 5)

    def test_skip_on_key(self):
        skip = "start: LD V0, 3\n SKP V0\n LD V1, 1\n HALT"
        self.assertEqual(run_to_halt(skip, 1 << 3).v[1], 0)
        self.assertEqual(run_to_halt(skip).v[1], 1)

        skip_not = "start: LD V0, 3\n SKNP V0\n LD V1, 1\n HALT"
        self.assertEqual(run_to_halt(skip_not).v[1], 0)
        self.assertEqual(run_to_halt(skip_not, 1 << 3).v[1], 1)

    def test_a_register_above_fifteen_holds_no_key(self):
        # V0 = 0xFF names no key, so SKP does not skip and SKNP does.
        self.assertEqual(
            run_to_halt("start: LD V0, 0xFF\n SKP V0\n LD V1, 1\n HALT").v[1], 1
        )
        self.assertEqual(
            run_to_halt("start: LD V0, 0xFF\n SKNP V0\n LD V1, 1\n HALT").v[1], 0
        )


class DisplayTests(unittest.TestCase):
    def test_draw_lights_the_expected_pixels(self):
        # The glyph for 3 is 0xF0,0x10,0xF0,0x10,0xF0: four, one, four, one, four.
        machine = run_to_halt(
            "start: LD V0, 3\n LD F, V0\n LD V0, 0\n LD V1, 0\n"
            " DRW V0, V1, 5\n HALT"
        )
        self.assertEqual(lit_pixels(machine), 14)
        self.assertEqual(machine.i, FONT_ADDRESS + 3 * 5)

    def test_drawing_twice_erases_and_reports_the_collision(self):
        machine = run_to_halt(
            "start: LD I, dot\n LD V0, 0\n LD V1, 0\n"
            " DRW V0, V1, 1\n DRW V0, V1, 1\n HALT\n"
            "dot: DB 0x80"
        )
        self.assertEqual(machine.v[0xF], 1)
        self.assertEqual(lit_pixels(machine), 0)
        self.assertEqual(machine.collisions, 1)

    def test_pixels_wrap_around_the_screen(self):
        machine = run_to_halt(
            "start: LD I, pair\n LD V0, 63\n LD V1, 0\n"
            " DRW V0, V1, 1\n HALT\npair: DB 0xC0"
        )
        self.assertEqual(machine.framebuffer[0], 1)
        self.assertEqual(machine.framebuffer[63], 1)

    def test_hires_mode_changes_the_grid_and_starts_blank(self):
        hires = run_to_halt("start: DW 0x00FF\n HALT")
        self.assertTrue(hires.hi_res)
        self.assertEqual(hires.screen_width, HI_RES_WIDTH)
        self.assertEqual(hires.screen_height, HI_RES_HEIGHT)
        self.assertEqual(len(hires.framebuffer), HI_RES_WIDTH * HI_RES_HEIGHT)

        lores = run_to_halt(
            "start: LD I, dot\n DRW V0, V1, 1\n DW 0x00FF\n DW 0x00FE\n HALT\n"
            "dot: DB 0xFF"
        )
        self.assertFalse(lores.hi_res)
        self.assertEqual(len(lores.framebuffer), SCREEN_WIDTH * SCREEN_HEIGHT)
        # A mode switch does not keep the old drawing: the grid changed under it.
        self.assertEqual(lit_pixels(lores), 0)

    def test_cls_in_hires_keeps_the_hi_res_grid(self):
        cleared = run_to_halt("start: DW 0x00FF\n CLS\n HALT")
        self.assertTrue(cleared.hi_res)
        self.assertEqual(len(cleared.framebuffer), HI_RES_WIDTH * HI_RES_HEIGHT)

        bottom = run_to_halt(
            "start: DW 0x00FF\n CLS\n LD V0, 0\n LD V1, 63\n LD I, dot\n"
            " DRW V0, V1, 1\n HALT\ndot: DB 0x80"
        )
        self.assertEqual(len(bottom.framebuffer), HI_RES_WIDTH * HI_RES_HEIGHT)
        self.assertEqual(bottom.framebuffer[63 * HI_RES_WIDTH], 1)

    def test_large_sprite_is_sixteen_by_sixteen_in_hires(self):
        rows = "".join(" DW 0xFFFF\n" for _ in range(16))
        machine = run_to_halt(
            "start: DW 0x00FF\n LD I, sprite\n DRW V0, V1, 0\n HALT\nsprite:\n" + rows
        )
        self.assertEqual(lit_pixels(machine), 16 * 16)
        self.assertEqual(machine.framebuffer[0], 1)
        self.assertEqual(machine.framebuffer[15], 1)
        self.assertEqual(machine.framebuffer[15 * HI_RES_WIDTH + 15], 1)

    def test_large_sprite_doubles_columns_in_lores(self):
        rows = "".join(" DB 0x80\n" for _ in range(16))
        machine = run_to_halt(
            "start: LD I, sprite\n DRW V0, V1, 0\n HALT\nsprite:\n" + rows
        )
        # Each row's top bit becomes two pixels: 16 rows of two.
        self.assertEqual(lit_pixels(machine), 32)
        self.assertEqual(machine.framebuffer[0], 1)
        self.assertEqual(machine.framebuffer[1], 1)
        self.assertEqual(machine.framebuffer[2], 0)
        self.assertEqual(machine.framebuffer[SCREEN_WIDTH], 1)

    def test_scroll_down_moves_the_drawing_and_clears_the_top(self):
        machine = run_to_halt(
            "start: LD I, dot\n DRW V0, V1, 1\n DW 0x00C2\n HALT\ndot: DB 0x80"
        )
        self.assertEqual(machine.framebuffer[2 * SCREEN_WIDTH], 1)
        self.assertEqual(machine.framebuffer[0], 0)
        self.assertEqual(machine.framebuffer[SCREEN_WIDTH], 0)

    def test_scroll_left_and_right_move_four_pixels(self):
        base = (
            "start: LD V0, 8\n LD I, dot\n DRW V0, V1, 1\n"
            " DW 0x00FC\n HALT\ndot: DB 0x80"
        )
        left = run_to_halt(base)
        self.assertEqual(left.framebuffer[4], 1)
        self.assertEqual(left.framebuffer[8], 0)

        right = run_to_halt(base.replace("0x00FC", "0x00FB"))
        self.assertEqual(right.framebuffer[12], 1)
        self.assertEqual(right.framebuffer[8], 0)

    def test_exit_opcode_halts(self):
        machine = run_to_halt("start: DW 0x00FD\n LD V0, 9\n HALT")
        self.assertTrue(machine.halted)
        self.assertEqual(machine.v[0], 0)

    def test_fx30_points_at_the_large_font(self):
        machine = run_to_halt("start: LD V0, 3\n DW 0xF030\n HALT")
        self.assertEqual(machine.i, LARGE_FONT_ADDRESS + 30)

    def test_large_font_is_in_memory(self):
        # The small glyph for 0 is 0xF0, so the doubled first row is 0xFF twice.
        program = assemble("start: HALT")
        self.assertEqual(program.memory[LARGE_FONT_ADDRESS], 0xFF)
        self.assertEqual(program.memory[LARGE_FONT_ADDRESS + 1], 0xFF)

    def test_rpl_flags_round_trip(self):
        machine = run_to_halt(
            "start: LD V0, 1\n LD V1, 2\n DW 0xF175\n"
            " LD V0, 0\n LD V1, 0\n DW 0xF185\n HALT"
        )
        self.assertEqual(machine.v[0], 1)
        self.assertEqual(machine.v[1], 2)


class TimerTests(unittest.TestCase):
    def test_timers(self):
        machine, _ = machine_for(
            "start: LD V0, 2\n LD DT, V0\n LD ST, V0\n LD V1, DT\n HALT"
        )
        while not machine.halted:
            machine.step()
        self.assertEqual(machine.v[1], 2)
        self.assertEqual(machine.delay_timer, 2)
        self.assertEqual(machine.sound_timer, 2)
        # One tick per frame, never below zero.
        machine.tick_timers()
        self.assertEqual(machine.delay_timer, 1)
        self.assertEqual(machine.sound_timer, 1)
        machine.tick_timers()
        machine.tick_timers()
        self.assertEqual(machine.delay_timer, 0)
        self.assertEqual(machine.sound_timer, 0)


class StateTests(unittest.TestCase):
    def test_save_and_restore_round_trip(self):
        machine = run_to_halt(
            "start: LD V0, 3\n LD I, dot\n DRW V0, V0, 1\n LD DT, V0\n HALT\n"
            "dot: DB 0xC0"
        )
        saved = machine.save_state()
        machine.v[0] = 0xAA
        machine.framebuffer = bytearray(len(machine.framebuffer))
        machine.tick_timers()
        machine.restore_state(saved)
        self.assertEqual(machine.v[0], 3)
        self.assertEqual(machine.delay_timer, 3)
        self.assertEqual(lit_pixels(machine), 2)

    def test_from_program_pads_memory_to_four_kilobytes(self):
        machine, _ = machine_for("start: HALT")
        self.assertEqual(len(machine.memory), MEMORY_SIZE)

    def test_from_program_refuses_an_oversized_image(self):
        with self.assertRaises(Chip8Error):
            Chip8Machine.from_program(bytes(MEMORY_SIZE + 1))


if __name__ == "__main__":
    unittest.main()
