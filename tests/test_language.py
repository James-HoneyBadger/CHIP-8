"""The linked language: data, includes, pseudo-instructions and the library."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.assembler import (  # noqa: E402
    Chip8Error,
    assemble_chip8,
    lookup,
    load_program_file,
)
from chip8.machine import Chip8Machine  # noqa: E402


def run(source: str, limit: int = 200_000, base_dir=None) -> Chip8Machine:
    program = assemble_chip8(source, base_dir)
    machine = Chip8Machine.from_program(program.memory, program.entry)
    machine.interactive = True
    for _ in range(limit):
        if machine.halted:
            break
        machine.step()
    return machine


def lit(machine: Chip8Machine) -> set[tuple[int, int]]:
    width = machine.screen_width
    return {
        (x, y)
        for y in range(machine.screen_height)
        for x in range(width)
        if machine.framebuffer[y * width + x]
    }


class DataSectionTests(unittest.TestCase):
    def test_string_bytes_and_words_are_placed_after_the_code(self):
        program = assemble_chip8(
            'start: HALT\n.string msg "HI"\n.bytes tab 1, 2, 3\n.words ptr msg'
        )
        self.assertEqual(program.size, 2 + 3 + 3 + 2)  # HALT, string, table, word
        self.assertEqual(program.labels["msg"], 0x202)
        msg = program.labels["msg"]
        self.assertEqual(bytes(program.memory[msg : msg + 3]), b"HI\0")

    def test_a_string_names_its_character_count_not_its_storage(self):
        program = assemble_chip8('start: HALT\n.string s "HELLO"')
        self.assertEqual(program.labels["s_len"], 5)
        self.assertEqual(program.size, 2 + 6)  # five characters and a NUL

    def test_hi_and_lo_split_an_address(self):
        program = assemble_chip8(
            "start: LD V4, msg_hi\n LD V5, msg_lo\n HALT\n.string msg \"X\""
        )
        self.assertEqual(program.labels["msg"], 0x206)
        self.assertEqual(program.memory[0x201], 0x02)  # the high byte
        self.assertEqual(program.memory[0x203], 0x06)  # the low byte
        self.assertEqual(lookup("msg_hi", program.labels), 0x02)
        self.assertEqual(lookup("msg_lo", program.labels), 0x06)

    def test_art_becomes_sprite_bytes(self):
        program = assemble_chip8(
            "start: HALT\n.art dot\n  #...\n  ###.\n  .###\n.end"
        )
        at = program.labels["dot"]
        self.assertEqual(bytes(program.memory[at : at + 3]), bytes((0x80, 0xE0, 0x70)))

    def test_wide_art_uses_more_than_one_byte_a_row(self):
        program = assemble_chip8(
            "start: HALT\n.art wide\n" + "\n".join("#" * 16 for _ in range(2)) + "\n.end"
        )
        at = program.labels["wide"]
        self.assertEqual(bytes(program.memory[at : at + 4]), b"\xff\xff\xff\xff")

    def test_zero_reserves_space(self):
        program = assemble_chip8("start: HALT\n.zero grid 16\n.bytes after 1")
        self.assertEqual(program.labels["grid_len"], 16)
        self.assertEqual(program.labels["after"], program.labels["grid"] + 16)
        self.assertEqual(program.memory[program.labels["after"]], 1)

    def test_org_moves_the_code(self):
        program = assemble_chip8("start: CLS\n.org 0x300\nlater: CLS")
        self.assertEqual(program.labels["later"], 0x300)

    def test_org_cannot_move_backwards(self):
        with self.assertRaises(Chip8Error) as caught:
            assemble_chip8("start: CLS\n.org 0x200")
        self.assertIn("backwards", str(caught.exception))

    def test_a_string_escape(self):
        program = assemble_chip8('start: HALT\n.string s "A\\nB\\x41"')
        at = program.labels["s"]
        self.assertEqual(bytes(program.memory[at : at + 5]), b"A\nBA\0")

    def test_an_unclosed_block_is_reported(self):
        with self.assertRaises(Chip8Error) as caught:
            assemble_chip8("start: HALT\n.bytes tab 1, 2\n.bytes open\n 3, 4")
        self.assertIn("not closed by .end", str(caught.exception))


class IncludeTests(unittest.TestCase):
    def test_an_include_brings_its_labels_in(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "helper.inc").write_text("h_twice: ADD V0, V0\n RET\n")
            (base / "main.ch8").write_text(
                "start: LD V0, 3\n CALL h_twice\n HALT\n.include helper.inc\n"
            )
            program = load_program_file(base / "main.ch8")
            self.assertEqual(program.includes, ["helper.inc"])
            self.assertIn("h_twice", program.labels)
            machine = Chip8Machine.from_program(program.memory, program.entry)
            while not machine.halted:
                machine.step()
            self.assertEqual(machine.v[0], 6)

    def test_an_error_in_an_include_names_that_file(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "broken.inc").write_text("; comment\noops: FROB V0\n")
            (base / "main.ch8").write_text('start: CLS\n.include "broken.inc"\n')
            with self.assertRaises(Chip8Error) as caught:
                load_program_file(base / "main.ch8")
            self.assertIn("broken.inc:2", str(caught.exception))

    def test_a_missing_include_is_reported(self):
        with self.assertRaises(Chip8Error) as caught:
            assemble_chip8("start: CLS\n.include nowhere")
        self.assertIn("cannot find", str(caught.exception))

    def test_an_include_cycle_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            (base / "a.inc").write_text('.include "b.inc"\n')
            (base / "b.inc").write_text('.include "a.inc"\n')
            with self.assertRaises(Chip8Error):
                assemble_chip8('.include "a.inc"', base_dir=base)

    def test_the_standard_library_is_on_the_search_path(self):
        # `LIB text` finds chip8/lib/text.inc from any directory.
        program = assemble_chip8("start: CALL t_clear\n HALT\n.include text")
        self.assertIn("t_print", program.labels)
        self.assertIn("font_a", program.labels)


class PseudoInstructionTests(unittest.TestCase):
    def test_print_sets_the_pointer_registers_and_calls(self):
        program = assemble_chip8(
            'start: .print msg\n HALT\n.string msg "HI"\n.include text\n'
        )
        at = program.labels["start"]
        self.assertEqual(program.memory[at], 0x64)  # LD V4, msg_hi
        self.assertEqual(program.memory[at + 2], 0x65)  # LD V5, msg_lo
        self.assertEqual(program.memory[at + 4], 0x22)  # CALL t_print

    def test_printc_passes_the_length_as_well(self):
        program = assemble_chip8(
            'start: .printc msg\n HALT\n.string msg "HI"\n.include text\n'
        )
        at = program.labels["start"]
        self.assertEqual(program.memory[at], 0x62)  # LD V2, msg_len
        self.assertEqual(program.memory[at + 1], 2)


class TextLibraryTests(unittest.TestCase):
    def test_a_character_is_five_rows_of_four_pixels(self):
        machine = run(
            "start: LD V0, 0\n LD V1, 0\n LD V2, 0x41\n CALL t_char\n HALT\n"
            ".include text"
        )
        # 'A' is .##. #..# #### #..# #..#, so row 0 has two pixels at x=1,2.
        self.assertIn((1, 0), lit(machine))
        self.assertIn((2, 0), lit(machine))
        self.assertNotIn((0, 0), lit(machine))
        # .##. / #..# / #### / #..# / #..# is twelve pixels.
        self.assertEqual(len([p for p in lit(machine) if p[1] < 5]), 12)

    def test_a_print_draws_a_string_and_advances_the_cursor(self):
        machine = run(
            'start: LD V0, 4\n LD V1, 2\n .print msg\n LD I, out\n LD [I], V1\n HALT\n'
            '.string msg "AB"\n.bytes out 0, 0\n.include text'
        )
        # Two characters, five pixels apart, starting at column 4.
        columns = {x for (x, _y) in lit(machine)}
        self.assertIn(4, columns)
        self.assertIn(9, columns)

    def test_lowercase_is_drawn_as_capitals(self):
        upper = run("start: LD V0,0\n LD V1,0\n LD V2,0x41\n CALL t_char\n HALT\n.include text")
        lower = run("start: LD V0,0\n LD V1,0\n LD V2,0x61\n CALL t_char\n HALT\n.include text")
        self.assertEqual(lit(upper), lit(lower))

    def test_an_unknown_character_is_a_question_mark(self):
        unknown = run("start: LD V0,0\n LD V1,0\n LD V2,0x7F\n CALL t_char\n HALT\n.include text")
        question = run("start: LD V0,0\n LD V1,0\n LD V2,0x3F\n CALL t_char\n HALT\n.include text")
        self.assertEqual(lit(unknown), lit(question))

    def test_a_long_line_wraps_to_the_next_row(self):
        machine = run(
            'start: LD V0, 0\n LD V1, 0\n .print msg\n HALT\n'
            '.string msg "ABCDEFGHIJKLMN"\n.include text'
        )
        rows = {y for (_x, y) in lit(machine)}
        self.assertGreater(max(rows), 5)  # it went past the first text row

    def test_a_number_drops_its_leading_zeroes(self):
        def width(value: int) -> int:
            machine = run(
                f"start: LD V0,0\n LD V1,0\n LD V2,{value}\n CALL t_num\n HALT\n"
                ".include text"
            )
            columns = [x for (x, _y) in lit(machine)]
            return max(columns) + 1 if columns else 0

        # Each digit is five pixels wide, so the width counts the digits drawn.
        self.assertEqual(width(0), 4)      # one digit: the zero is not dropped
        self.assertEqual(width(7), 4)
        self.assertEqual(width(42), 9)     # two digits
        self.assertEqual(width(205), 14)   # three digits

    def test_hex_uses_the_machines_own_font(self):
        digits = run(
            "start: LD V0,0\n LD V1,0\n LD V2,0xAB\n CALL t_hex\n HALT\n.include text"
        )
        # The same two glyphs, drawn by the machine's `LD F, Vx`.
        glyphs = run(
            "start: LD V0, 0\n LD V1, 0\n LD VA, 0x0A\n LD F, VA\n DRW V0, V1, 5\n"
            " LD V0, 5\n LD VB, 0x0B\n LD F, VB\n DRW V0, V1, 5\n HALT"
        )
        self.assertEqual(lit(digits), lit(glyphs))

    def test_centring_puts_the_string_in_the_middle(self):
        machine = run(
            'start: LD V1, 0\n .printc msg\n HALT\n.string msg "ABCD"\n.include text'
        )
        columns = [x for (x, y) in lit(machine) if y == 0]
        self.assertGreaterEqual(min(columns), 20)
        self.assertLessEqual(max(columns), 43)


class DrawLibraryTests(unittest.TestCase):
    def test_plot_sets_one_pixel_and_unplot_clears_it(self):
        machine = run("start: LD V0,5\n LD V1,7\n CALL d_plot\n HALT\n.include draw")
        self.assertEqual(lit(machine), {(5, 7)})

        cleared = run(
            "start: LD V0,5\n LD V1,7\n CALL d_plot\n CALL d_unplot\n HALT\n.include draw"
        )
        self.assertEqual(lit(cleared), set())

    def test_plotting_twice_leaves_one_pixel_not_none(self):
        machine = run(
            "start: LD V0,5\n LD V1,7\n CALL d_plot\n CALL d_plot\n HALT\n.include draw"
        )
        self.assertEqual(lit(machine), {(5, 7)})

    def test_hline_covers_exactly_its_width(self):
        for width in (1, 7, 8, 9, 13, 20):
            with self.subTest(width=width):
                machine = run(
                    f"start: LD V0,3\n LD V1,4\n LD V2,{width}\n CALL d_hline\n HALT\n"
                    ".include draw"
                )
                self.assertEqual(lit(machine), {(x, 4) for x in range(3, 3 + width)})

    def test_vline_covers_exactly_its_height(self):
        for height in (1, 14, 15, 16, 23):
            with self.subTest(height=height):
                machine = run(
                    f"start: LD V0,6\n LD V1,2\n LD V2,{height}\n CALL d_vline\n HALT\n"
                    ".include draw"
                )
                self.assertEqual(lit(machine), {(6, y) for y in range(2, 2 + height)})

    def test_fill_is_solid_and_rect_is_its_outline(self):
        filled = run(
            "start: LD V0,2\n LD V1,2\n LD V2,10\n LD V3,6\n CALL d_fill\n HALT\n"
            ".include draw"
        )
        outline = run(
            "start: LD V0,2\n LD V1,2\n LD V2,10\n LD V3,6\n CALL d_rect\n HALT\n"
            ".include draw"
        )
        self.assertEqual(len(lit(filled)), 60)
        self.assertEqual(len(lit(outline)), 2 * 10 + 2 * 6 - 4)
        self.assertTrue(lit(outline) < lit(filled))

    def test_xfill_erases_what_fill_drew(self):
        machine = run(
            "start: LD V0,4\n LD V1,4\n LD V2,8\n LD V3,8\n CALL d_fill\n"
            " LD V0,4\n LD V1,4\n LD V2,8\n LD V3,8\n CALL d_xfill\n HALT\n.include draw"
        )
        self.assertEqual(lit(machine), set())

    def test_a_line_reaches_both_ends(self):
        machine = run(
            "start: LD V0,0\n LD V1,0\n LD V2,40\n LD V3,20\n CALL d_line\n HALT\n"
            ".include draw"
        )
        pixels = lit(machine)
        self.assertIn((0, 0), pixels)
        self.assertIn((40, 20), pixels)
        # A line has at least one pixel in every column it crosses.
        self.assertEqual({x for (x, _y) in pixels}, set(range(41)))

    def test_a_line_is_one_pixel_a_column(self):
        machine = run(
            "start: LD V0,2\n LD V1,2\n LD V2,50\n LD V3,30\n CALL d_line\n HALT\n"
            ".include draw"
        )
        columns: dict[int, int] = {}
        for x, _y in lit(machine):
            columns[x] = columns.get(x, 0) + 1
        self.assertEqual(set(columns.values()), {1})


class MathLibraryTests(unittest.TestCase):
    def result(self, source: str) -> tuple[int, int]:
        machine = run(source)
        return machine.v[2], machine.v[3]

    def test_multiply(self):
        for a, b in ((13, 11), (200, 3), (255, 255), (0, 9), (1, 1)):
            with self.subTest(a=a, b=b):
                low, high = self.result(
                    f"start: LD V0,{a}\n LD V1,{b}\n CALL m_mul\n HALT\n.include math"
                )
                self.assertEqual(low + (high << 8), a * b)

    def test_divide(self):
        for a, b in ((200, 7), (255, 16), (9, 9), (5, 200), (0, 3)):
            with self.subTest(a=a, b=b):
                quotient, remainder = self.result(
                    f"start: LD V0,{a}\n LD V1,{b}\n CALL m_div\n HALT\n.include math"
                )
                self.assertEqual(quotient, a // b)
                self.assertEqual(remainder, a % b)

    def test_dividing_by_zero_says_so(self):
        quotient, remainder = self.result(
            "start: LD V0,9\n LD V1,0\n CALL m_div\n HALT\n.include math"
        )
        self.assertEqual(quotient, 0xFF)
        self.assertEqual(remainder, 9)

    def test_mod_and_min_max_and_absdiff(self):
        cases = {
            "LD V0,17\n LD V1,5\n CALL m_mod": (17 % 5, None),
            "LD V0,9\n LD V1,4\n CALL m_min": (4, None),
            "LD V0,9\n LD V1,4\n CALL m_max": (9, None),
            "LD V0,4\n LD V1,9\n CALL m_absdiff": (5, None),
            "LD V0,9\n LD V1,4\n CALL m_absdiff": (5, None),
        }
        for body, (expected, _) in cases.items():
            with self.subTest(body=body):
                machine = run(f"start: {body}\n HALT\n.include math")
                answer = machine.v[2] if "m_mod" in body else machine.v[0]
                self.assertEqual(answer, expected)

    def test_random_stays_inside_its_range(self):
        machine = run(
            "start: LD V0,10\n CALL m_rnd\n HALT\n.include math", limit=2000
        )
        self.assertLess(machine.v[0], 10)


class InputLibraryTests(unittest.TestCase):
    def test_poll_reports_no_key_as_ff(self):
        machine = run("start: CALL i_poll\n HALT\n.include input")
        self.assertEqual(machine.v[0], 0xFF)

    def test_poll_reports_the_lowest_key_held(self):
        program = assemble_chip8(
            "start: CALL i_poll\n LD V1, V0\n HALT\n.include input"
        )
        machine = Chip8Machine.from_program(
            program.memory, program.entry, keys=(1 << 9) | (1 << 4)
        )
        machine.interactive = True
        while not machine.halted:
            machine.step()
        self.assertEqual(machine.v[1], 4)  # the lowest of the two

    def test_wait_blocks_until_a_key_is_held(self):
        program = assemble_chip8(
            "start: CALL i_wait\n LD V1, 7\n HALT\n.include input"
        )
        machine = Chip8Machine.from_program(program.memory, program.entry)
        machine.interactive = True
        for _ in range(400):
            machine.step()
        self.assertFalse(machine.halted)   # still inside the wait
        self.assertEqual(machine.v[1], 0)
        machine.set_key(6, True)
        for _ in range(400):   # a poll of sixteen keys is sixty-four opcodes
            if machine.halted:  # the HALT after the wait
                break
            machine.step()
        self.assertEqual(machine.v[0], 6)
        self.assertEqual(machine.v[1], 7)


class LibrarianTests(unittest.TestCase):
    def test_the_catalogue_lists_both_folders(self):
        from chip8 import librarian

        entries = librarian.catalogue()
        names = {entry.name for entry in entries}
        self.assertIn("snake", names)
        self.assertIn("bounce", names)
        self.assertGreaterEqual(len(entries), 20)

    def test_a_program_is_found_by_name(self):
        from chip8 import librarian

        self.assertEqual(librarian.find("snake").name, "snake.ch8")
        with self.assertRaises(Exception):
            librarian.find("no-such-program")

    def test_the_header_gives_the_speed(self):
        from chip8 import librarian

        self.assertEqual(librarian.describe(librarian.find("life")).speed, 2000)

    def test_a_new_program_is_valid_and_runs(self):
        from chip8 import librarian

        source = librarian.scaffold("demo")
        program = assemble_chip8(source)
        self.assertGreater(program.size, 0)
        self.assertIn("t_print", program.labels)


if __name__ == "__main__":
    unittest.main()
