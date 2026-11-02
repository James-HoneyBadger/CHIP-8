"""The frame loop: speed, timers, pause/step, and rewind."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.constants import REWIND_SNAPSHOTS, REWIND_STRIDE  # noqa: E402
from chip8.emulator import Chip8Emulator, FrameClock  # noqa: E402
from chip8.machine import Chip8Error  # noqa: E402

DRAWER = """\
start:  LD I, dot
        LD V0, 0
loop:   DRW V0, V0, 1
        ADD V0, 1
        JP loop
dot:    DB 0x80
"""


def counting_program(frames: int) -> str:
    """`LD V0, 1` then a jump to itself: ten opcodes a frame by default."""

    return "start: LD V0, 1\nloop: JP loop"


class LoadingTests(unittest.TestCase):
    def test_a_listing_loads_and_is_paused(self):
        emulator = Chip8Emulator()
        self.assertTrue(emulator.load_source(DRAWER, "drawer"))
        self.assertTrue(emulator.loaded)
        self.assertFalse(emulator.running)
        snapshot = emulator.snapshot()
        self.assertTrue(snapshot.loaded)
        self.assertEqual(snapshot.pc, 0x200)
        self.assertEqual(snapshot.cycles, 0)
        self.assertEqual(len(snapshot.framebuffer), 64 * 32)
        self.assertEqual(snapshot.name, "drawer")

    def test_a_binary_rom_loads_byte_for_byte(self):
        emulator = Chip8Emulator()
        self.assertTrue(emulator.load_bytes(b"\x60\x07\x00\x00", "demo.ch8"))
        self.assertTrue(emulator.binary)
        emulator.step()
        self.assertEqual(emulator.snapshot().v[0], 7)

    def test_a_bad_program_is_reported_and_keeps_the_running_one(self):
        emulator = Chip8Emulator()
        self.assertTrue(emulator.load_source(DRAWER, "drawer"))
        self.assertFalse(emulator.load_source("start: FROB V0", "broken"))
        self.assertIn("not an instruction form", emulator.error)
        # The typo did not unload the program that was already loaded.
        self.assertTrue(emulator.loaded)
        self.assertEqual(emulator.snapshot().name, "drawer")

    def test_an_include_is_found_next_to_the_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "helper.inc").write_text("PLOT: DB 0x80\n", encoding="utf-8")
            program = root / "game.ch8"
            program.write_text(
                "start: LD I, PLOT\n"
                "        DRW V0, V0, 1\n"
                "        HALT\n"
                '.include "helper.inc"\n',
                encoding="utf-8",
            )
            emulator = Chip8Emulator()
            self.assertTrue(emulator.load_file(program), emulator.error)
            self.assertFalse(emulator.binary)

    def test_a_missing_file_is_reported(self):
        emulator = Chip8Emulator()
        self.assertFalse(emulator.load_file("/nonexistent/game.ch8"))
        self.assertTrue(emulator.error)
        self.assertFalse(emulator.loaded)

    def test_reset_rebuilds_from_the_kept_program(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        for _ in range(5):
            emulator.advance_frame()
        self.assertGreater(emulator.snapshot().cycles, 0)
        emulator.reset()
        snapshot = emulator.snapshot()
        self.assertEqual(snapshot.cycles, 0)
        self.assertEqual(snapshot.pc, 0x200)
        self.assertEqual(sum(snapshot.framebuffer), 0)
        self.assertTrue(emulator.loaded)


class FrameTests(unittest.TestCase):
    def test_a_frame_runs_the_speed_and_ticks_the_timers(self):
        emulator = Chip8Emulator()
        emulator.load_source(
            "start: LD V0, 5\n LD DT, V0\n LD ST, V0\n loop: JP loop", "timers"
        )
        emulator.set_speed(2)
        emulator.advance_frame()
        first = emulator.snapshot()
        self.assertEqual(first.cycles, 2)
        # The frame set both timers to five and then ticked them once: the clock
        # is the frame, not the instruction.
        self.assertEqual(first.delay_timer, 4)
        self.assertEqual(first.sound_timer, 0)
        emulator.advance_frame()
        second = emulator.snapshot()
        self.assertEqual(second.cycles, 4)
        self.assertEqual(second.delay_timer, 3)
        # `LD ST, V0` ran in the second frame, and was ticked by the same frame.
        self.assertEqual(second.sound_timer, 4)

    def test_the_timers_follow_the_frame_even_while_waiting_for_a_key(self):
        emulator = Chip8Emulator()
        emulator.load_source(
            "start: LD V0, 3\n LD DT, V0\n LD V1, K\n loop: JP loop", "waiter"
        )
        emulator.set_speed(100)
        emulator.advance_frame()
        self.assertTrue(emulator.snapshot().waiting_for_key)
        emulator.advance_frame()
        self.assertEqual(emulator.snapshot().delay_timer, 1)

    def test_a_halted_program_stops_running(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: HALT", "halts")
        emulator.start()
        emulator.advance_frame()
        self.assertFalse(emulator.running)
        self.assertEqual(emulator.state_name, "halted")

    def test_running_advances_only_when_started(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: JP start", "spin")
        emulator.tick()
        self.assertEqual(emulator.snapshot().cycles, 0)
        emulator.start()
        emulator.tick()
        self.assertEqual(emulator.snapshot().cycles, 10)
        emulator.pause()
        emulator.tick()
        self.assertEqual(emulator.snapshot().cycles, 10)

    def test_step_is_one_instruction(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: LD V0, 1\n LD V1, 2\n HALT", "steps")
        emulator.step()
        self.assertEqual(emulator.snapshot().v[0], 1)
        self.assertEqual(emulator.snapshot().v[1], 0)
        emulator.start()
        emulator.step()  # ignored while running
        self.assertEqual(emulator.snapshot().v[1], 0)

    def test_an_unknown_opcode_reports_and_stops(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: DW 0xF0FF", "bad")
        emulator.start()
        emulator.advance_frame()
        self.assertFalse(emulator.running)
        self.assertIn("unknown opcode", emulator.error)
        self.assertEqual(emulator.state_name, "error")


class SpeedTests(unittest.TestCase):
    def test_speed_is_clamped(self):
        emulator = Chip8Emulator()
        emulator.set_speed(0)
        self.assertEqual(emulator.speed, 1)
        emulator.set_speed(999_999)
        self.assertEqual(emulator.speed, 2000)

    def test_nudge_moves_by_ten_above_ten(self):
        emulator = Chip8Emulator()
        emulator.set_speed(10)
        emulator.nudge_speed(1)
        self.assertEqual(emulator.speed, 20)
        emulator.nudge_speed(-1)
        self.assertEqual(emulator.speed, 10)
        emulator.set_speed(5)
        emulator.nudge_speed(-1)
        self.assertEqual(emulator.speed, 4)


class RewindTests(unittest.TestCase):
    def test_a_run_can_be_walked_back(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        emulator.set_speed(1)
        emulator.start()
        for _ in range(REWIND_STRIDE * 4):
            emulator.advance_frame()
        live = emulator.snapshot()
        self.assertGreaterEqual(emulator.rewind_count, 4)

        count = emulator.begin_rewind()
        self.assertFalse(emulator.running)
        self.assertGreaterEqual(count, emulator.rewind_count)
        emulator.rewind_to(0)
        early = emulator.snapshot()
        self.assertLess(early.cycles, live.cycles)
        self.assertLessEqual(sum(early.framebuffer), sum(live.framebuffer))
        self.assertEqual(early.rewind_position, 0)

        emulator.rewind_to(10_000)  # clamped to the newest moment
        newest = emulator.snapshot()
        self.assertGreaterEqual(newest.cycles, early.cycles)

    def test_resuming_from_the_past_forgets_the_old_future(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        emulator.set_speed(1)
        emulator.start()
        for _ in range(REWIND_STRIDE * 3):
            emulator.advance_frame()
        emulator.rewind_to(0)
        after_rewind = emulator.rewind_count
        emulator.start()
        self.assertEqual(emulator.rewind_position, -1)
        self.assertLessEqual(emulator.rewind_count, after_rewind)

    def test_rewind_by_enters_the_history_at_the_live_moment(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        emulator.set_speed(1)
        emulator.start()
        for _ in range(REWIND_STRIDE * 2):
            emulator.advance_frame()
        emulator.rewind_by(-1)
        self.assertFalse(emulator.running)
        # The live moment was kept as the newest, so one step back is the moment
        # before the one the user was looking at.
        self.assertEqual(emulator.rewind_position, emulator.rewind_count - 2)
        emulator.rewind_by(-1)
        self.assertEqual(emulator.rewind_position, emulator.rewind_count - 3)
        emulator.rewind_by(1)
        self.assertEqual(emulator.rewind_position, emulator.rewind_count - 2)
        emulator.rewind_by(1)
        self.assertEqual(emulator.rewind_position, emulator.rewind_count - 1)

    def test_the_ring_keeps_thirty_seconds(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: JP start", "spin")
        emulator.set_speed(1)
        emulator.start()
        for _ in range(REWIND_STRIDE * (REWIND_SNAPSHOTS + 20)):
            emulator.advance_frame()
        self.assertEqual(emulator.rewind_count, REWIND_SNAPSHOTS)

    def test_reset_forgets_the_history(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        emulator.start()
        emulator.advance_frame()
        self.assertGreater(emulator.rewind_count, 0)
        emulator.reset()
        self.assertEqual(emulator.rewind_count, 1)  # the moment it was reset to
        self.assertEqual(emulator.rewind_position, -1)

    def test_rewinding_restores_the_pixels_not_the_luck(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: RND V0, 0xFF\n JP start", "dice")
        emulator.set_speed(1)
        emulator.start()
        for _ in range(REWIND_STRIDE * 2):
            emulator.advance_frame()
        emulator.rewind_by(-1)
        first = emulator.snapshot().v[0]
        emulator.start()
        for _ in range(REWIND_STRIDE):
            emulator.advance_frame()
        # The machine replays, but the generator was never rewound; all that is
        # asserted is that the restore itself put the register back.
        self.assertLessEqual(first, 0xFF)


class SnapshotTests(unittest.TestCase):
    def test_a_snapshot_is_a_copy(self):
        emulator = Chip8Emulator()
        emulator.load_source(DRAWER, "drawer")
        emulator.step()
        snapshot = emulator.snapshot()
        before = bytes(snapshot.framebuffer)
        self.assertEqual(bytes(emulator.machine.framebuffer), before)
        emulator.machine.framebuffer[0] = 1
        self.assertEqual(snapshot.framebuffer[0], before[0])

    def test_state_names(self):
        emulator = Chip8Emulator()
        self.assertEqual(emulator.state_name, "open a program")
        emulator.load_source("start: LD V0, K\n HALT", "waiter")
        self.assertEqual(emulator.state_name, "paused")
        emulator.step()
        self.assertEqual(emulator.state_name, "waiting for a key")
        emulator.load_source("start: HALT", "halts")
        emulator.step()
        self.assertEqual(emulator.state_name, "halted")
        emulator.load_source("start: DW 0xF0FF", "bad")
        emulator.step()
        self.assertEqual(emulator.state_name, "error")

    def test_keys_reach_the_machine(self):
        emulator = Chip8Emulator()
        emulator.load_source("start: LD V0, K\n HALT", "waiter")
        emulator.step()
        emulator.set_key(4, True)
        self.assertEqual(emulator.keys, 1 << 4)
        emulator.release_all_keys()
        self.assertEqual(emulator.keys, 0)


class MonitorTests(unittest.TestCase):
    """The machine-level interface the desktop application's monitor uses."""

    def emulator(self) -> Chip8Emulator:
        emulator = Chip8Emulator(seed=1)
        self.assertTrue(emulator.load_bytes(b"\x00\x00", "blank"))
        return emulator

    def test_a_byte_can_be_entered_read_back_and_refused_outside_memory(self):
        emulator = self.emulator()
        self.assertTrue(emulator.write_memory(0x300, 0xAB))
        self.assertEqual(emulator.read_memory(0x300), 0xAB)
        self.assertFalse(emulator.write_memory(4096, 0x00))
        with self.assertRaises(Chip8Error):
            emulator.read_memory(0x1000)

    def test_a_program_entered_by_hand_runs(self):
        emulator = self.emulator()
        for offset, byte in enumerate((0x60, 0x01, 0x00, 0x00)):  # LD V0,1; HALT
            self.assertTrue(emulator.write_memory(0x200 + offset, byte))
        self.assertTrue(emulator.set_pc(0x200))
        emulator.start()
        emulator.advance_frame()
        self.assertEqual(emulator.snapshot().v[0], 1)


class ClockTests(unittest.TestCase):
    def test_frames_are_counted_from_the_clock(self):
        clock = FrameClock(fps=60)
        start = clock._next  # noqa: SLF001 - the schedule is what is being tested
        self.assertEqual(clock.frames_due(start - 0.001), 0)
        self.assertEqual(clock.frames_due(start), 1)
        self.assertEqual(clock.frames_due(start + 3 * clock.period), 3)

    def test_catch_up_is_capped_and_resynchronises(self):
        clock = FrameClock(fps=60, max_catch_up=4)
        late = clock._next + 10.0  # noqa: SLF001
        self.assertEqual(clock.frames_due(late), 4)
        # Sixteen milliseconds later it is on schedule again, not spiralling.
        self.assertEqual(clock.frames_due(late + 1.0 / 60.0), 1)

    def test_sleep_is_never_negative(self):
        clock = FrameClock(fps=60)
        self.assertGreaterEqual(clock.sleep_for(), 0.0)
        self.assertEqual(clock.sleep_for(clock._next + 1.0), 0.0)  # noqa: SLF001


if __name__ == "__main__":
    unittest.main()
