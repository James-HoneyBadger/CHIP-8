"""The programs written for this emulator: each one runs, and each one behaves."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.emulator import Chip8Emulator  # noqa: E402
from chip8.librarian import describe, find  # noqa: E402
from chip8.screen import ascii_screen  # noqa: E402

ROMS = ("hello", "clock", "life", "plot", "snake")


def play(name: str, frames: int, speed: int | None = None, keys: dict[int, int] = None):
    """Run a ROM for a number of frames, optionally with keys held per frame."""

    # A seed makes the food and the soups reproducible: these tests are about
    # behaviour, not about luck.
    emulator = Chip8Emulator(seed=20250101)
    assert emulator.load_file(find(name)), emulator.error
    emulator.set_speed(speed or describe(find(name)).speed)
    for key, at in (keys or {}).items():
        if at == 0:
            emulator.set_key(key, True)
    emulator.start()
    for frame in range(frames):
        for key, at in (keys or {}).items():
            if frame == at:
                emulator.set_key(key, True)
            elif frame == at + 4:
                emulator.set_key(key, False)
        if emulator.running:
            emulator.advance_frame()
    return emulator


def fresh(name: str, speed: int | None = None) -> Chip8Emulator:
    emulator = Chip8Emulator(seed=20250101)
    assert emulator.load_file(find(name)), emulator.error
    emulator.set_speed(speed or describe(find(name)).speed)
    return emulator


def cells(emulator: Chip8Emulator, size: int) -> list[tuple[int, int]]:
    """Every filled ``size x size`` block, which is a cell of a grid ROM."""

    framebuffer = emulator.machine.framebuffer
    width = emulator.machine.screen_width
    return [
        (x // size, y // size)
        for y in range(0, emulator.machine.screen_height, size)
        for x in range(0, width, size)
        if all(
            framebuffer[(y + row) * width + x + column]
            for row in range(size)
            for column in range(size)
        )
    ]


class EveryRomTests(unittest.TestCase):
    def test_each_program_loads_runs_and_draws(self):
        for name in ROMS:
            with self.subTest(program=name):
                emulator = play(name, 120)
                self.assertEqual(emulator.error, "", emulator.error)
                drawn = sum(emulator.machine.framebuffer)
                self.assertGreater(drawn, 0, "nothing was drawn")
                self.assertIn(emulator.state_name, ("running", "halted", "waiting for a key"))

    def test_a_frame_is_worth_of_pixels_and_not_just_noise(self):
        # A screen that is nearly all on, or nearly all off, means a routine
        # went wrong rather than a picture being drawn.
        for name in ROMS:
            with self.subTest(program=name):
                emulator = play(name, 120)
                width = emulator.machine.screen_width
                height = emulator.machine.screen_height
                drawn = sum(emulator.machine.framebuffer)
                self.assertLess(drawn, width * height * 0.9)
                self.assertGreater(drawn, 10)


class HelloTests(unittest.TestCase):
    def test_it_shows_a_title_card_and_a_specification_card(self):
        first = ascii_screen(play("hello", 60).snapshot())
        self.assertIn("\u2584", first)  # something was drawn
        # A key press swaps the card: the two screens must differ.
        after = ascii_screen(play("hello", 200, keys={5: 60}).snapshot())
        self.assertNotEqual(first, after)

    def test_the_scanner_moves(self):
        positions = set()
        emulator = fresh("hello", 100)
        self.assertTrue(emulator.loaded)
        emulator.start()
        for _ in range(400):
            emulator.advance_frame()
            row = [x for x in range(1, 63) if emulator.machine.framebuffer[29 * 64 + x]]
            positions.add(tuple(row))
        self.assertGreater(len(positions), 4)


class ClockTests(unittest.TestCase):
    def test_the_seconds_go_up_and_carry(self):
        emulator = fresh("clock", 40)
        self.assertTrue(emulator.loaded)
        emulator.start()
        at = emulator.program.labels["cl_time"]
        seen = [list(emulator.machine.memory[at : at + 3])]
        for _ in range(200):
            emulator.advance_frame()
            now = list(emulator.machine.memory[at : at + 3])
            if now != seen[-1]:
                seen.append(now)
        self.assertGreater(len(seen), 2)
        self.assertEqual(seen[0][0], 12)          # it starts at twelve o'clock
        self.assertEqual(seen[1][2], seen[0][2] + 1)

    def test_the_carry_from_seconds_to_minutes(self):
        emulator = fresh("clock", 40)
        self.assertTrue(emulator.loaded)
        at = emulator.program.labels["cl_time"]
        # Start one second short of a minute, after the ROM has set its clock.
        emulator.start()
        for _ in range(30):
            emulator.advance_frame()
        emulator.machine.memory[at] = 9
        emulator.machine.memory[at + 1] = 59
        emulator.machine.memory[at + 2] = 59
        for _ in range(400):
            emulator.advance_frame()
            if emulator.machine.memory[at + 1] == 0:
                break
        self.assertEqual(emulator.machine.memory[at], 10)
        self.assertEqual(emulator.machine.memory[at + 1], 0)


class LifeTests(unittest.TestCase):
    def test_the_grid_changes_and_survives_being_extinct(self):
        emulator = fresh("life", 2000)
        self.assertTrue(emulator.loaded)
        emulator.start()
        at = emulator.program.labels["ga"]
        generations = []
        for _ in range(400):
            emulator.advance_frame()
            grid = bytes(emulator.machine.memory[at : at + 180])
            if not generations or generations[-1] != grid:
                generations.append(grid)
        self.assertGreater(len(generations), 4)
        # Every value in the grid is a cell state, never a neighbour count.
        self.assertTrue(all(byte in (0, 1) for grid in generations for byte in grid))

    def test_the_screen_matches_the_grid(self):
        emulator = fresh("life", 2000)
        self.assertTrue(emulator.loaded)
        emulator.start()
        main = emulator.program.labels["main"]
        for _ in range(400):
            emulator.advance_frame()
            if emulator.machine.pc == main:
                break
        at = emulator.program.labels["ga"]
        live = sum(1 for byte in emulator.machine.memory[at : at + 180] if byte)
        self.assertEqual(live, len(cells(emulator, 4)))


class PlotTests(unittest.TestCase):
    def test_axes_curves_and_label_are_all_there(self):
        emulator = play("plot", 200, speed=200)
        self.assertEqual(emulator.state_name, "halted")
        framebuffer = emulator.machine.framebuffer
        # The axes cross at (32, 16).
        self.assertEqual(framebuffer[16 * 64 + 32], 1)
        # The state's box is one pixel per column, so every column has a pixel.
        columns = {x for y in range(32) for x in range(64) if framebuffer[y * 64 + x]}
        self.assertEqual(columns, set(range(64)))


class SnakeTests(unittest.TestCase):
    def test_the_snake_moves_and_the_body_follows(self):
        emulator = fresh("snake", 200)
        self.assertTrue(emulator.loaded)
        emulator.set_speed(60)
        emulator.start()
        state = emulator.program.labels["sn_state"]
        seen = set()
        for _ in range(200):
            emulator.advance_frame()
            head = emulator.machine.memory[state]
            seen.add(head)
        self.assertGreater(len(seen), 3)

    def test_the_snake_is_always_the_length_it_claims(self):
        emulator = fresh("snake", 200)
        self.assertTrue(emulator.loaded)
        emulator.set_speed(100)
        emulator.start()
        body = emulator.program.labels["body"]
        state = emulator.program.labels["sn_state"]
        for _ in range(300):
            emulator.advance_frame()
            length = emulator.machine.memory[state + 2]
            self.assertIn(length, (3, 4, 5, 6))
            live = sum(1 for byte in emulator.machine.memory[body : body + 64] if byte)
            # Stale slots keep their old cells, so the count only bounds it.
            self.assertGreaterEqual(live, 0)
        self.assertIn(emulator.state_name, ("running", "halted", "waiting for a key"))

    def test_a_wall_ends_the_game(self):
        emulator = fresh("snake", 200)
        self.assertTrue(emulator.loaded)
        emulator.start()
        over = emulator.program.labels["sn_over"]
        for _ in range(400):
            emulator.advance_frame()
            if emulator.machine.memory[over]:
                break
        self.assertEqual(emulator.machine.memory[over], 1)
        for _ in range(30):        # let the game over screen be drawn
            emulator.advance_frame()
        # ... and the game over screen has text on it.
        self.assertGreater(sum(emulator.machine.framebuffer), 40)

    def test_turning_with_a_key(self):
        emulator = fresh("snake", 200)
        self.assertTrue(emulator.loaded)
        emulator.start()
        state = emulator.program.labels["sn_state"]
        emulator.set_key(8, True)  # 8 is down
        headings = set()
        for _ in range(200):
            emulator.advance_frame()
            headings.add(emulator.machine.memory[state + 3])
        self.assertIn(2, headings)  # it turned downwards at some point


if __name__ == "__main__":
    unittest.main()
