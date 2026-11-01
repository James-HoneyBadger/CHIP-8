"""The bugs that got through the first time, pinned down.

Each test here failed once on real code. They are kept together, and written
against what the program *shows* rather than what it computes, because the bugs
that got through were exactly the ones that computed the right number and drew
the wrong one.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.assembler import assemble_chip8  # noqa: E402
from chip8.cli import parse_keys, parse_schedule  # noqa: E402
from chip8.emulator import Chip8Emulator  # noqa: E402
from chip8.librarian import find  # noqa: E402
from chip8.machine import Chip8Error, Chip8Machine  # noqa: E402
from tools.make_font import GLYPHS  # noqa: E402


def glyph(character: str) -> set[tuple[int, int]]:
    rows = GLYPHS[character].split()
    return {(c, r) for r in range(5) for c in range(4) if rows[r][c] == "#"}


def read_text(emulator: Chip8Emulator, x: int, y: int, slots: int) -> str:
    """What 4x5 glyphs the screen actually holds at a position.

    The numbers are matched against the font bitmap, so a glyph drawn from the
    wrong address reads as ``?`` instead of quietly looking like a digit.
    """

    framebuffer = emulator.machine.framebuffer
    out: list[str] = []
    for slot in range(slots):
        block = {
            (c, r)
            for r in range(5)
            for c in range(4)
            if framebuffer[(y + r) * 64 + x + slot * 5 + c]
        }
        if not block:
            out.append(" ")
            continue
        match = [character for character in "0123456789" if glyph(character) == block]
        out.append(match[0] if match else "?")
    return "".join(out).rstrip()


def number_on_screen(value: int) -> str:
    program = assemble_chip8(
        f"start: LD V0,0\n LD V1,0\n LD V2,{value}\n CALL t_num\n HALT\n.include text\n"
    )
    machine = Chip8Machine.from_program(program.memory, program.entry)
    while not machine.halted:
        machine.step()
    return read_machine(machine, 0, 0, 4)


def read_machine(machine: Chip8Machine, x: int, y: int, slots: int) -> str:
    framebuffer = machine.framebuffer
    out: list[str] = []
    for slot in range(slots):
        block = {
            (c, r)
            for r in range(5)
            for c in range(4)
            if framebuffer[(y + r) * 64 + x + slot * 5 + c]
        }
        if not block:
            out.append(" ")
            continue
        match = [character for character in "0123456789" if glyph(character) == block]
        out.append(match[0] if match else "?")
    return "".join(out).rstrip()


class NumberDrawingTests(unittest.TestCase):
    """`t_num` kept its digit pointer in I, and drawing a glyph takes I."""

    def test_every_digit_is_drawn(self):
        for value, shown in (
            (0, "0"),
            (7, "7"),
            (10, "10"),
            (34, "34"),
            (100, "100"),
            (205, "205"),
            (255, "255"),
        ):
            with self.subTest(value=value):
                self.assertEqual(number_on_screen(value), shown)

    def test_a_zero_inside_a_number_is_kept(self):
        # The first digit suppresses leading zeroes; the ones after it are real.
        self.assertEqual(number_on_screen(205), "205")
        self.assertEqual(number_on_screen(101), "101")

    def test_the_cursor_advances_one_glyph_per_digit(self):
        program = assemble_chip8(
            "start: LD V0,3\n LD V1,0\n LD V2,205\n CALL t_num\n HALT\n.include text\n"
        )
        machine = Chip8Machine.from_program(program.memory, program.entry)
        while not machine.halted:
            machine.step()
        self.assertEqual(machine.v[0], 3 + 3 * 5)


class ClockDrawingTests(unittest.TestCase):
    """The clock parked hours, minutes and seconds above V8, which `t_num` eats."""

    def shows(self, hours: int, minutes: int, seconds: int) -> tuple[str, str, str]:
        emulator = Chip8Emulator(seed=1)
        self.assertTrue(emulator.load_file(find("clock")))
        emulator.set_speed(200)
        emulator.start()
        at = emulator.program.labels["cl_time"]
        for _ in range(8):
            emulator.advance_frame()
        emulator.machine.memory[at : at + 3] = bytes((hours, minutes, seconds - 1))
        before = emulator.machine.memory[at + 2]
        for _ in range(600):            # let the ROM's own loop tick and redraw
            emulator.advance_frame()
            if emulator.machine.memory[at + 2] != before:
                break
        for _ in range(12):
            emulator.advance_frame()
        return (
            read_text(emulator, 6, 14, 2),
            read_text(emulator, 22, 14, 2),
            read_text(emulator, 38, 14, 2),
        )

    def test_all_three_fields_are_drawn(self):
        self.assertEqual(self.shows(12, 34, 56), ("12", "34", "56"))

    def test_single_digits_have_no_leading_zeroes(self):
        self.assertEqual(self.shows(9, 5, 8), ("9", "5", "8"))


class SnakeGameTests(unittest.TestCase):
    """Eating corrupted the state, scored the food, and lost the head slot."""

    def game(self, food: int = 73):
        emulator = Chip8Emulator(seed=3)
        self.assertTrue(emulator.load_file(find("snake")))
        emulator.set_speed(2000)
        machine = emulator.machine
        labels = emulator.program.labels
        machine.memory[labels["sn_state"] :][:4] = bytes((8, 6, 3, 1))
        body = labels["body"]
        machine.memory[body + 6] = 70
        machine.memory[body + 7] = 71
        machine.memory[body + 8] = 72
        machine.memory[labels["sn_more"]] = food
        machine.memory[labels["sn_score"]] = 0
        machine.memory[labels["sn_over"]] = 0
        machine.delay_timer = 0
        machine.pc = labels["main"]
        emulator.running = True
        return emulator

    def test_eating_grows_the_snake_and_scores_a_point(self):
        emulator = self.game()
        labels = emulator.program.labels
        for _ in range(3):
            emulator.advance_frame()
        length = emulator.machine.memory[labels["sn_state"] + 2]
        score = emulator.machine.memory[labels["sn_score"]]
        self.assertEqual((length, score), (4, 1))

    def test_eating_leaves_the_ring_consistent(self):
        emulator = self.game()
        labels = emulator.program.labels
        for _ in range(3):
            emulator.advance_frame()
        state = list(emulator.machine.memory[labels["sn_state"] :][:4])
        self.assertEqual(state[2:], [4, 1])       # one longer, still heading right
        self.assertLess(state[0], 64)             # the head slot is a slot
        self.assertLess(state[1], 64)
        self.assertEqual(emulator.machine.memory[labels["sn_over"]], 0)

    def test_the_game_over_screen_shows_the_score_it_holds(self):
        emulator = self.game(food=200)   # a meal it cannot reach, so the score holds
        labels = emulator.program.labels
        emulator.machine.memory[labels["sn_score"]] = 42
        emulator.machine.memory[labels["sn_over"]] = 1
        for _ in range(80):
            emulator.advance_frame()
        self.assertEqual(emulator.machine.memory[labels["sn_score"]], 42)
        self.assertEqual(read_text(emulator, 31, 16, 3), "42")


class TerminalInputTests(unittest.TestCase):
    """Every byte of an escape sequence used to be read as a keypad key."""

    def test_an_escape_sequence_is_swallowed(self):
        from chip8.frontend_terminal import interpret_input

        # F5, which is five bytes, and an arrow key.
        self.assertEqual(interpret_input(b"\x1b[15~", False), [])
        self.assertEqual(interpret_input(b"\x1b[A", False), [])

    def test_a_lone_escape_is_the_quit_key(self):
        from chip8.frontend_terminal import interpret_input

        self.assertEqual(interpret_input(b"\x1b", False), ["\x1b"])
        self.assertEqual(interpret_input(b"\x1b", True), [])

    def test_ordinary_characters_are_characters(self):
        from chip8.frontend_terminal import interpret_input

        self.assertEqual(interpret_input(b"pw4", False), ["p", "w", "4"])


class MachineEdgeTests(unittest.TestCase):
    """Bounds the machine has to survive rather than crash on."""

    def run_source(self, source: str, steps: int = 200) -> Chip8Machine:
        program = assemble_chip8(source)
        machine = Chip8Machine.from_program(program.memory, program.entry)
        machine.interactive = True
        for _ in range(steps):
            if machine.halted:
                break
            machine.step()
        return machine

    def test_block_transfer_wraps_past_the_end_of_memory(self):
        machine = self.run_source(
            "start: LD I, 0xFFA\n LD V0, 1\n LD V1, 2\n LD VF, 3\n LD [I], VF\n HALT"
        )
        self.assertEqual(machine.memory[0xFFA], 1)
        self.assertEqual(machine.memory[0xFFF], 0)

    def test_bcd_wraps_past_the_end_of_memory(self):
        machine = self.run_source("start: LD V0, 205\n LD I, 0xFFF\n LD B, V0\n HALT")
        self.assertEqual(machine.memory[0xFFF], 2)
        self.assertEqual(machine.memory[0], 0)
        self.assertEqual(machine.memory[1], 5)

    def test_a_skip_on_a_register_above_f_finds_no_key(self):
        machine = self.run_source("start: LD V0, 0xFF\n SKP V0\n LD V1, 1\n HALT")
        self.assertEqual(machine.v[1], 1)

    def test_scrolling_moves_the_drawing_rather_than_erasing_it(self):
        machine = self.run_source(
            "start: LD I, dot\n DRW V0, V1, 1\n DW 0x00C1\n HALT\ndot: DB 0xFF"
        )
        rows = [y for y in range(32) if any(machine.framebuffer[y * 64 + x] for x in range(64))]
        self.assertEqual(rows, [1])


class NamedKeyTests(unittest.TestCase):
    """`--press right@60` is in the manual, so it has to work."""

    def test_a_name_is_its_digit(self):
        self.assertEqual(parse_keys("right"), 1 << 6)
        self.assertEqual(parse_keys("up"), 1 << 2)
        self.assertEqual(parse_keys("fire"), 1 << 5)

    def test_a_mask_still_works(self):
        self.assertEqual(parse_keys("1F"), (1 << 1) | (1 << 0xF))

    def test_a_schedule_takes_a_name(self):
        events = parse_schedule("right@60", "--press")
        self.assertEqual(events, [(60, 6, 1 << 6, True)])

    def test_a_word_with_no_hex_digit_names_no_key(self):
        # Note that a word *containing* hex letters is a mask, which is the
        # documented meaning of `--press dead@30`.
        with self.assertRaises(SystemExit):
            parse_schedule("jump@3", "--press")


class PseudoInstructionTests(unittest.TestCase):
    """`.num score` used to assemble and silently ignore `score`."""

    def test_num_with_a_name_is_refused(self):
        with self.assertRaises(Chip8Error):
            assemble_chip8('start: LD V2, 5\n .num score\nLIB text\n')

    def test_num_alone_assembles(self):
        program = assemble_chip8('start: LD V2, 5\n .num\nLIB text\n')
        self.assertIn("t_num", program.labels)


class ClockRefreshTests(unittest.TestCase):
    """A redraw that takes forty frames flickers once a second."""

    def test_the_face_is_never_blank_at_its_own_speed(self):
        emulator = Chip8Emulator(seed=1)
        self.assertTrue(emulator.load_file(find("clock")))
        emulator.set_speed(clock_speed())        # the listing's own `; speed`
        emulator.start()
        for _ in range(200):
            emulator.advance_frame()
            self.assertTrue(
                any(emulator.machine.framebuffer),
                "the clock cleared the screen and had not redrawn it by the end "
                "of the frame",
            )


def clock_speed() -> int:
    """The speed in the clock listing's header, as the emulator would read it."""

    for line in find("clock").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(";"):
            body = stripped[1:].strip().lower()
            if body.startswith("speed"):
                return int(body.split()[1])
    raise AssertionError("the clock listing has no `; speed` header")


if __name__ == "__main__":
    unittest.main()
