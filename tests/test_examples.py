"""Every bundled listing loads and runs: eighteen programs as a smoke test."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.assembler import load_program_file  # noqa: E402
from chip8.emulator import Chip8Emulator  # noqa: E402

EXAMPLES = sorted((Path(__file__).resolve().parents[1] / "examples").glob("*.ch8"))


class ExampleTests(unittest.TestCase):
    def test_there_are_programs_to_run(self):
        self.assertGreaterEqual(len(EXAMPLES), 10)

    def test_every_example_assembles(self):
        for path in EXAMPLES:
            with self.subTest(program=path.name):
                program = load_program_file(path)
                self.assertGreater(program.size, 0)
                self.assertFalse(program.binary)
                self.assertIn(program.entry, program.source_lines)

    def test_every_example_runs_without_an_error(self):
        for path in EXAMPLES:
            with self.subTest(program=path.name):
                emulator = Chip8Emulator()
                self.assertTrue(emulator.load_file(path), emulator.error)
                emulator.start()
                for _ in range(180):
                    if not emulator.running:
                        break
                    emulator.advance_frame()
                snapshot = emulator.snapshot()
                self.assertEqual(snapshot.error, "")
                self.assertIn(
                    emulator.state_name,
                    ("running", "halted", "waiting for a key"),
                )
                self.assertLessEqual(set(snapshot.framebuffer), {0, 1})
                self.assertEqual(
                    len(snapshot.framebuffer),
                    snapshot.screen_width * snapshot.screen_height,
                )

    def test_most_examples_draw_something(self):
        drawing = 0
        for path in EXAMPLES:
            emulator = Chip8Emulator()
            self.assertTrue(emulator.load_file(path), emulator.error)
            emulator.start()
            for _ in range(180):
                if not emulator.running:
                    break
                emulator.advance_frame()
                if any(emulator.machine.framebuffer):
                    drawing += 1
                    break
        self.assertGreaterEqual(drawing, len(EXAMPLES) - 3)

    def test_the_short_programs_halt(self):
        # These listings end with HALT rather than looping; the machine has to
        # agree with the last line of the file.
        for name in ("collision_lab.ch8", "font_ledger.ch8", "hires.ch8"):
            with self.subTest(program=name):
                emulator = Chip8Emulator()
                self.assertTrue(emulator.load_file(EXAMPLES[0].parent / name))
                emulator.start()
                for _ in range(60):
                    if not emulator.running:
                        break
                    emulator.advance_frame()
                self.assertEqual(emulator.state_name, "halted")

    def test_a_program_that_waits_for_a_key_waits(self):
        emulator = Chip8Emulator()
        self.assertTrue(emulator.load_file(EXAMPLES[0].parent / "typewriter.ch8"))
        emulator.start()
        for _ in range(10):
            emulator.advance_frame()
        self.assertEqual(emulator.state_name, "waiting for a key")
        emulator.set_key(0x5, True)
        emulator.advance_frame()
        self.assertNotEqual(emulator.state_name, "waiting for a key")


if __name__ == "__main__":
    unittest.main()
