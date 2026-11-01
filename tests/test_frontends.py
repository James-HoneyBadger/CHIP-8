"""The frontends' pure parts, plus the command line.

Everything a frontend computes before it touches a terminal or a display is
testable without one: the half-block renderer, the packed X11 framebuffer, the
inspector text and the status line.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8 import cli  # noqa: E402
from chip8.constants import OFF_COLOR, ON_COLOR, PIXEL_SCALE  # noqa: E402
from chip8.emulator import Chip8Emulator  # noqa: E402
from chip8.frontend_terminal import (  # noqa: E402
    KEY_HOLD,
    KEYPAD,
    Palette,
    TerminalFrontend,
    render_display,
)
from chip8.inspector import (  # noqa: E402
    hex_dump,
    listing_text,
    memory_text,
    registers_text,
    status_text,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def emulator_with(source: str) -> Chip8Emulator:
    emulator = Chip8Emulator()
    assert emulator.load_source(source, "test")
    return emulator


def drawn(source: str, frames: int = 1):
    """The machine after it has actually executed *source*."""

    emulator = emulator_with(source)
    emulator.start()
    for _ in range(frames):
        emulator.advance_frame()
    return emulator.snapshot()


class DisplayRenderTests(unittest.TestCase):
    def test_low_res_is_sixty_four_by_sixteen_cells(self):
        snapshot = drawn("start: HALT")
        lines = render_display(snapshot, palette=Palette("ascii"))
        self.assertEqual(len(lines), 16)
        self.assertEqual(set(len(line) for line in lines), {64})
        self.assertEqual(set("".join(lines)), {" "})

    def test_a_lit_pixel_is_an_upper_half_block(self):
        snapshot = drawn("start: LD I, dot\n DRW V0, V1, 1\n HALT\ndot: DB 0x80")
        lines = render_display(snapshot, palette=Palette("ascii"))
        self.assertEqual(lines[0][0], "\u2580")
        self.assertEqual(lines[0][1], " ")

    def test_the_second_row_of_a_cell_is_the_lower_half(self):
        snapshot = drawn(
            "start: LD I, dot\n LD V1, 1\n DRW V0, V1, 1\n HALT\ndot: DB 0x80"
        )
        lines = render_display(snapshot, palette=Palette("ascii"))
        self.assertEqual(lines[0][0], "\u2584")

    def test_both_rows_lit_is_a_full_block(self):
        snapshot = drawn(
            "start: LD I, dot\n DRW V0, V1, 2\n HALT\ndot: DB 0x80\n DB 0x80"
        )
        lines = render_display(snapshot, palette=Palette("ascii"))
        self.assertEqual(lines[0][0], "\u2588")

    def test_hi_res_is_twice_the_cells(self):
        snapshot = drawn("start: DW 0x00FF\n HALT")
        lines = render_display(snapshot, palette=Palette("ascii"))
        self.assertEqual(len(lines), 32)
        self.assertEqual(set(len(line) for line in lines), {128})

    def test_scale_repeats_each_cell_both_ways(self):
        snapshot = drawn("start: LD I, dot\n DRW V0, V1, 1\n HALT\ndot: DB 0x80")
        lines = render_display(snapshot, scale=2, palette=Palette("ascii"))
        self.assertEqual(len(lines), 32)
        self.assertEqual(len(lines[0]), 128)
        self.assertEqual(lines[0][:2], "\u2580\u2580")
        self.assertEqual(lines[1][:2], "\u2580\u2580")

    def test_colour_is_written_once_per_run_of_cells(self):
        snapshot = drawn("start: HALT")
        line = render_display(snapshot, palette=Palette("truecolor"))[0]
        # An empty row is one escape pair and one reset, not sixty-four of them.
        self.assertLess(line.count("\x1b["), 5)
        self.assertIn(f"48;2;{OFF_COLOR[0]};{OFF_COLOR[1]};{OFF_COLOR[2]}", line)

    def test_the_two_hundred_and_fifty_six_colour_palette(self):
        snapshot = drawn("start: HALT")
        line = render_display(snapshot, palette=Palette("256"))[0]
        self.assertIn("\x1b[48;5;234m", line)

    def test_the_ascii_palette_has_no_escapes(self):
        snapshot = drawn("start: HALT")
        line = render_display(snapshot, palette=Palette("ascii"))[0]
        self.assertNotIn("\x1b", line)


class X11BufferTests(unittest.TestCase):
    def sample(self, source: str):
        return drawn(source)

    def test_pixels_are_packed_in_the_visual_s_masks(self):
        from chip8.frontend_x11 import PixelFormat

        pixel_format = PixelFormat(
            red_mask=0x00FF0000,
            green_mask=0x0000FF00,
            blue_mask=0x000000FF,
            bits_per_pixel=32,
            byte_order=0,
        )
        on = pixel_format.pack(ON_COLOR)
        off = pixel_format.pack(OFF_COLOR)
        self.assertEqual(on, bytes((ON_COLOR[2], ON_COLOR[1], ON_COLOR[0], 0)))
        self.assertEqual(off, bytes((OFF_COLOR[2], OFF_COLOR[1], OFF_COLOR[0], 0)))

    def test_a_twenty_four_bit_visual_packs_three_bytes(self):
        from chip8.frontend_x11 import PixelFormat

        pixel_format = PixelFormat(
            red_mask=0xFF0000,
            green_mask=0x00FF00,
            blue_mask=0x0000FF,
            bits_per_pixel=24,
            byte_order=0,
        )
        self.assertEqual(len(pixel_format.pack(ON_COLOR)), 3)

    def test_the_buffer_is_the_window_at_the_scale(self):
        from chip8.frontend_x11 import PixelFormat, frame_buffer

        snapshot = self.sample(
            "start: LD I, dot\n DRW V0, V1, 1\n HALT\ndot: DB 0x80"
        )
        pixel_format = PixelFormat(
            red_mask=0xFF0000,
            green_mask=0x00FF00,
            blue_mask=0x0000FF,
            bits_per_pixel=32,
            byte_order=0,
        )
        data = frame_buffer(snapshot, 3, pixel_format)
        self.assertEqual(len(data), 64 * 3 * 32 * 3 * 4)
        # The first pixel is lit, and a scale of three makes it a 3x3 block.
        self.assertEqual(data[0:4], pixel_format.pack(ON_COLOR))
        self.assertEqual(data[4:8], pixel_format.pack(ON_COLOR))
        row = 64 * 3 * 4
        self.assertEqual(data[row : row + 4], pixel_format.pack(ON_COLOR))

    def test_rows_are_padded_to_the_servers_stride(self):
        from chip8.frontend_x11 import PixelFormat, frame_buffer

        snapshot = self.sample("start: HALT")
        pixel_format = PixelFormat(
            red_mask=0xFF0000,
            green_mask=0x00FF00,
            blue_mask=0x0000FF,
            bits_per_pixel=32,
            byte_order=0,
        )
        stride = 64 * 4 + 8
        data = frame_buffer(snapshot, 1, pixel_format, stride)
        self.assertEqual(len(data), stride * 32)

    @unittest.skipUnless(
        __import__("chip8.frontend_x11", fromlist=["available"]).available(),
        "no X display on this machine",
    )
    def test_a_window_opens_draws_and_closes(self):
        from chip8.frontend_x11 import X11Frontend

        emulator = emulator_with("start: LD I, dot\nloop: JP loop\ndot: DB 0x80")
        emulator.advance_frame()
        view = X11Frontend(emulator, scale=PIXEL_SCALE, mute=True)
        try:
            view.pump()
            view.draw()
            self.assertEqual(
                view._size,  # noqa: SLF001 - the window geometry is the assertion
                (64 * PIXEL_SCALE, 32 * PIXEL_SCALE),
            )
            view.handle_key(0x70, True)  # p: run/pause
            self.assertTrue(emulator.running)
            view.handle_key(0x70, False)
            view.handle_key(0x70, True)
            self.assertFalse(emulator.running)
        finally:
            view.close()


class PressTrackerTests(unittest.TestCase):
    def test_a_repeat_is_not_a_second_press(self):
        from chip8.frontend_x11 import PressTracker

        presses = PressTracker()
        self.assertTrue(presses.press(9))
        # The server repeats a held key: same keycode, no release.
        self.assertFalse(presses.press(9))
        self.assertFalse(presses.press(9))
        presses.release(9)
        self.assertTrue(presses.press(9))

    def test_a_focus_change_forgets_what_was_held(self):
        from chip8.frontend_x11 import PressTracker

        presses = PressTracker()
        presses.press(38)  # p
        presses.clear()
        self.assertTrue(presses.press(38))


class InspectorTests(unittest.TestCase):
    def test_registers_show_every_register(self):
        emulator = emulator_with("start: LD V0, 1\n LD VF, 2\n HALT")
        emulator.step()
        emulator.step()
        text = "\n".join(registers_text(emulator.snapshot()))
        for index in range(16):
            self.assertIn(f"V{index:X}", text)
        self.assertIn("V0 01", text)
        self.assertIn("PC 0x204", text)

    def test_memory_opens_on_the_program_s_page(self):
        snapshot = emulator_with("start: HALT").snapshot()
        lines = memory_text(snapshot)
        self.assertEqual(lines[0], "Memory")
        self.assertTrue(lines[1].startswith("200 "))

    def test_listing_marks_the_current_line(self):
        emulator = emulator_with("start: CLS\n LD V0, 1\n LD V1, 2\n HALT")
        emulator.step()
        emulator.step()
        lines = listing_text(
            emulator.snapshot(), emulator.program, emulator.source
        )
        self.assertEqual(lines[0], "Listing")
        marked = [line for line in lines[1:] if line.startswith(">")]
        self.assertEqual(len(marked), 1)
        # PC points at the *next* instruction, which is the line to show.
        self.assertIn("LD V1, 2", marked[0])

    def test_a_binary_rom_has_no_listing(self):
        emulator = Chip8Emulator()
        emulator.load_bytes(b"\x60\x07\x00\x00", "demo")
        self.assertEqual(listing_text(emulator.snapshot(), emulator.program, emulator.source), [])

    def test_the_status_line_names_the_state_and_the_clock(self):
        emulator = emulator_with("start: LD V0, 1\n HALT")
        emulator.set_speed(10)
        text = status_text(emulator.snapshot(), emulator.state_name)
        self.assertIn("paused", text)
        self.assertIn("PC 0x200", text)
        self.assertIn("10 opcodes/frame (600 Hz)", text)

    def test_hex_dump(self):
        lines = hex_dump(b"\x60\x05\x00", 0x200)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("0200  60 05 00"))


class TerminalFrontendTests(unittest.TestCase):
    def test_the_footer_and_status_fit_beside_the_screen(self):
        emulator = emulator_with("start: LD V0, 1\n HALT")
        view = TerminalFrontend(emulator, color="ascii")
        lines = view.compose(emulator.snapshot(), columns=80)
        self.assertEqual(len(lines[0]), 64)
        self.assertIn("paused", lines[-3])
        self.assertIn("quit", lines[-1])

    def test_the_inspector_sits_beside_the_screen_when_it_fits(self):
        emulator = emulator_with("start: LD V0, 1\n HALT")
        view = TerminalFrontend(emulator, color="ascii", inspector=True)
        wide = view.compose(emulator.snapshot(), columns=120)
        self.assertIn("V0 00", wide[1])
        narrow = view.compose(emulator.snapshot(), columns=80)
        self.assertTrue(any(line.startswith("Registers") for line in narrow))

    def test_the_keypad_maps_the_original_keys(self):
        expected = {
            "1": 0x1, "2": 0x2, "3": 0x3, "4": 0xC,
            "q": 0x4, "w": 0x5, "e": 0x6, "r": 0xD,
            "a": 0x7, "s": 0x8, "d": 0x9, "f": 0xE,
            "z": 0xA, "x": 0x0, "c": 0xB, "v": 0xF,
        }
        self.assertEqual(KEYPAD, expected)

    def test_a_key_is_held_then_released(self):
        emulator = emulator_with("start: LD V0, K\n HALT")
        view = TerminalFrontend(emulator, color="ascii")
        emulator.step()
        view.handle("x")
        self.assertEqual(emulator.keys, 1 << 0)
        view._expire_keys(__import__("time").monotonic() + KEY_HOLD + 1)  # noqa: SLF001
        self.assertEqual(emulator.keys, 0)

    def test_control_keys_do_not_collide_with_the_keypad(self):
        emulator = emulator_with("start: LD V0, 1\nloop: JP loop")
        view = TerminalFrontend(emulator, color="ascii")
        view.handle("p")
        self.assertTrue(emulator.running)
        view.handle("p")
        self.assertFalse(emulator.running)
        view.handle("i")
        self.assertTrue(view.show_inspector)
        view.handle("m")
        self.assertTrue(view.show_memory)
        # Shift+R is reset, lowercase r is the keypad's D.
        emulator.set_speed(10)
        view.handle("R")
        self.assertEqual(emulator.snapshot().cycles, 0)
        view.handle("r")
        self.assertEqual(emulator.keys, 1 << 0xD)

    def test_escape_quits(self):
        view = TerminalFrontend(emulator_with("start: HALT"), color="ascii")
        view.handle("\x1b")
        self.assertFalse(view.running)

    def test_step_and_speed_keys(self):
        emulator = emulator_with("start: LD V0, 1\n LD V1, 2\n HALT")
        view = TerminalFrontend(emulator, color="ascii")
        view.handle("o")
        self.assertEqual(emulator.snapshot().v[0], 1)
        view.handle("+")
        self.assertGreater(emulator.speed, 10)
        view.handle("-")
        self.assertEqual(emulator.speed, 10)


class CommandLineTests(unittest.TestCase):
    def run_cli(self, arguments: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(arguments)
        return code, buffer.getvalue()

    def test_headless_runs_a_program_and_reports(self):
        code, output = self.run_cli(
            [
                "run",
                str(EXAMPLES / "bounce.ch8"),
                "--frontend",
                "headless",
                "--frames",
                "30",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("running", output)
        self.assertIn("opcodes/frame", output)

    def test_headless_prints_the_inspector(self):
        code, output = self.run_cli(
            [
                "run",
                str(EXAMPLES / "collision_lab.ch8"),
                "--frontend",
                "headless",
                "--frames",
                "10",
                "--inspector",
                "--memory",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("Registers", output)
        self.assertIn("Listing", output)
        self.assertIn("Memory", output)

    def test_headless_seeds_the_keypad_from_the_listing(self):
        code, output = self.run_cli(
            [
                "run",
                str(EXAMPLES / "keypad_probe.ch8"),
                "--frontend",
                "headless",
                "--frames",
                "60",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("halted", output)

    def test_a_program_that_cannot_load_exits_non_zero(self):
        code, _ = self.run_cli(["run", "/nonexistent/game.ch8", "--frontend", "headless"])
        self.assertEqual(code, 1)

    def test_info_describes_a_listing(self):
        code, output = self.run_cli(["info", str(EXAMPLES / "bounce.ch8"), "--labels"])
        self.assertEqual(code, 0)
        self.assertIn("kind      listing", output)
        self.assertIn("loop", output)

    def test_asm_writes_the_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            output_path = Path(folder) / "out.c8"
            code, output = self.run_cli(
                ["asm", str(EXAMPLES / "bounce.ch8"), "-o", str(output_path)]
            )
            self.assertEqual(code, 0)
            self.assertIn("0200", output)
            body = output_path.read_bytes()
            self.assertGreater(len(body), 0)
            # The bytes are loadable again: they contain the HALT's two NULs.
            emulator = Chip8Emulator()
            self.assertTrue(emulator.load_bytes(body, "out.c8"))

    def test_asm_reports_an_error_with_a_line_number(self):
        with tempfile.TemporaryDirectory() as folder:
            listing = Path(folder) / "bad.ch8"
            listing.write_text("start: CLS\n       NOPE V0\n", encoding="utf-8")
            code, _ = self.run_cli(["asm", str(listing)])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
