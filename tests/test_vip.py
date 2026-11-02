"""The COSMAC VIP: the board, its firmware and the adapter the frontends use.

The firmware is a CHIP-8 interpreter written in 1802 assembly, so the way to
test it is to compare it with the interpreter that is written in Python: the
same program, instruction for instruction, on both platforms, must leave the
same registers, the same screen and the same timers. That comparison is
:class:`DifferentialTests`; everything before it tests the pieces the two
platforms do not share -- the row-scanned keypad, the display memory, the clock.
"""

from __future__ import annotations

import contextlib
import io
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8 import cli, vipfirmware  # noqa: E402
from chip8.assembler import assemble_chip8  # noqa: E402
from chip8.constants import (  # noqa: E402
    FRAMES_PER_SECOND,
    REWIND_SNAPSHOTS,
    SCREEN_HEIGHT,
)
from chip8.machine import Chip8Error, Chip8Machine  # noqa: E402
from chip8.vip import (  # noqa: E402
    CLOCK,
    DISPLAY_ADDRESS,
    DISPLAY_HEIGHT,
    DISPLAY_WIDTH,
    KEYMAP,
    Vip,
    VipEmulator,
    VipMemory,
    superchip_opcodes,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.ch8"))
EXAMPLES += sorted((ROOT / "roms").glob("*.ch8"))

#: Enough clocks for any one CHIP-8 instruction, and few enough that a blocking
#: FX0A returns to its caller instead of running the scan loop forever.
OPCODE_CLOCKS = 400_000


def booted(source: str = "", keys: int = 0, seed: int | None = 1) -> tuple[Vip, object]:
    program = assemble_chip8(source.encode())
    board = Vip(seed=seed)
    board.load_chip8(program)
    board.set_keys(keys)
    return board, program


def step(board: Vip, count: int = 1) -> None:
    for _ in range(count):
        if not board.step_instruction(OPCODE_CLOCKS):
            break


class BootTests(unittest.TestCase):
    def test_power_on_leaves_the_interpreter_in_its_fetch_loop(self):
        board = Vip()
        cpu = board.cpu
        self.assertEqual(cpu.p, 3, "CHIP-8 runs with R3 as the program counter")
        self.assertEqual(cpu.pc, vipfirmware.firmware().labels["FETCH"])
        self.assertEqual(cpu.r[7], 0x0200, "the CHIP-8 program counter")
        self.assertEqual(board.instructions, 0, "no CHIP-8 instruction has run")

    def test_power_on_clears_the_registers_and_the_display(self):
        board = Vip()
        v, i, pc, delay, sound = board.registers()
        self.assertEqual(v, (0,) * 16)
        self.assertEqual((i, pc, delay, sound), (0, 0x200, 0, 0))
        self.assertFalse(any(board.physical_screen()))

    def test_the_font_is_where_fx29_expects_it(self):
        board = Vip()
        self.assertEqual(board.memory.read(0x0050), 0xF0, "the glyph for 0")
        self.assertEqual(board.memory.read(0x0054), 0xF0, "its last row")

    def test_the_rom_is_read_only_and_the_ram_is_not(self):
        board = Vip()
        board.memory.write(vipfirmware.DISPLAY, 0xAA)
        self.assertEqual(board.memory.read(vipfirmware.DISPLAY), 0xAA)
        before = board.memory.read(0x1000)
        board.memory.write(0x1000, 0x00)
        self.assertEqual(board.memory.read(0x1000), before, "a write to ROM is dropped")

    def test_the_firmware_fits_in_the_rom_it_is_given(self):
        program = vipfirmware.firmware()
        for address, data in program.segments:
            end = address + len(data)
            if address >= VipMemory.RAM_TOP:
                self.assertGreaterEqual(address, 0x1000)
                self.assertLessEqual(end, VipMemory.ROM_TOP)
        self.assertIn("FETCH", program.labels)
        self.assertIn("DONE", program.labels)


class DisplayTests(unittest.TestCase):
    def test_the_display_is_sixty_four_by_a_hundred_and_twenty_eight_bits(self):
        board = Vip()
        self.assertEqual((DISPLAY_WIDTH, DISPLAY_HEIGHT), (64, 128))
        self.assertEqual(len(board.physical_screen()), 64 * 128)
        self.assertEqual(len(board.screen()), 64 * SCREEN_HEIGHT)

    def test_a_written_byte_lights_eight_pixels_of_one_chip8_row(self):
        board = Vip()
        board.memory.write(DISPLAY_ADDRESS, 0x80)
        row = board.screen()[0:8]
        self.assertEqual(list(row), [1, 0, 0, 0, 0, 0, 0, 0])

    def test_the_chip8_view_sees_any_of_the_four_lines_of_a_row(self):
        board = Vip()
        board.memory.write(DISPLAY_ADDRESS + 8, 0x80)  # the second line of row 0
        self.assertEqual(board.screen()[0], 1)

    def test_each_chip8_row_owns_four_display_lines(self):
        board = Vip()
        board.memory.write(DISPLAY_ADDRESS + 4 * 8, 0xFF)  # row 1 of the display
        physical = board.physical_screen()
        self.assertEqual(sum(physical[0:64]), 0, "the first row is empty")
        self.assertEqual(sum(physical[4 * 64 : 5 * 64]), 8, "row 1 has eight pixels")
        self.assertEqual(sum(board.screen()[64:128]), 8)

    def test_drawing_reaches_the_display_memory_the_program_can_see(self):
        board, _ = booted(
            """
            start: LD V0, 8
                   LD V1, 4
                   LD I, dot
                   DRW V0, V1, 1
                   HALT
            dot:   DB 0x80
            """
        )
        step(board, 5)
        self.assertEqual(board.memory.read(DISPLAY_ADDRESS + 4 * 32 + 1), 0x80)


class KeypadTests(unittest.TestCase):
    def test_strobing_a_row_answers_with_its_columns(self):
        board = Vip()
        # Row 0 is 1 2 3 C; holding 3 lights the third column of that row.
        board.set_key(0x3, True)
        board.memory.load(0x0300, b"\x64\x69")  # OUT 4, INP 1
        board.cpu.r[0] = 0x0300
        board.cpu.p = 0
        board.cpu.x = 1
        board.cpu.r[1] = 0x0160
        board.memory.write(0x0160, 0x01)
        board.cpu.step()
        board.cpu.step()
        self.assertEqual(board.memory.read(0x0161), 0b0100, "INP lands in K_COLS")

    def test_every_key_is_in_the_row_and_column_the_map_gives_it(self):
        board = Vip()
        for row, keys in enumerate(KEYMAP):
            for column, key in enumerate(keys):
                with self.subTest(key=f"{key:X}"):
                    board.set_keys(0)
                    board.set_key(key, True)
                    board.row_latch = row
                    self.assertEqual(board._read_port(1), 1 << column)

    def test_first_key_is_the_lowest_one_held(self):
        board = Vip()
        board.set_keys((1 << 0xA) | (1 << 0x2))
        self.assertEqual(board.first_key(), 0x2)
        board.set_keys(0)
        self.assertEqual(board.first_key(), -1)

    def test_keys_outside_the_keypad_are_ignored(self):
        board = Vip()
        board.set_key(16, True)
        board.set_key(-1, True)
        self.assertEqual(board.keys, 0)


class FrameTests(unittest.TestCase):
    def test_a_frame_is_a_sixtieth_of_the_clock(self):
        board = Vip()
        self.assertEqual(board.cycles_per_frame, CLOCK // FRAMES_PER_SECOND)

    def test_a_frame_runs_the_opcodes_it_is_given(self):
        board, _ = booted("start: JP start")
        board.advance_frame(10)
        self.assertEqual(board.instructions, 10)

    def test_a_stopped_program_cannot_run_past_the_frame(self):
        # Clearing the display costs more than a frame's clocks, and a frame
        # ends on the first instruction boundary at or after the deadline -- so
        # it may overrun by one instruction, and by no more.
        board, _ = booted("start: CLS\n HALT")
        before = board.cpu.cycles
        board.advance_frame(10)
        overrun = board.cpu.cycles - before - board.cycles_per_frame
        self.assertLessEqual(overrun, 3 * 8, "one long instruction is 24 clocks")

    def test_clearing_the_display_costs_a_frame_and_a_bit(self):
        board, _ = booted("start: CLS\n HALT")
        before = board.cpu.cycles
        board.step_instruction(10_000_000)
        frames = (board.cpu.cycles - before) / board.cycles_per_frame
        self.assertGreater(frames, 1.0, "a clear cannot fit in one frame")
        self.assertLess(frames, 1.5, "and it is close: four cycles a byte is the floor")

    def test_the_timers_count_frames_down_to_zero(self):
        board, _ = booted(
            """
            start: LD V0, 3
                   LD DT, V0
                   LD ST, V0
            loop:  JP loop
            """
        )
        step(board, 3)
        self.assertEqual((board.delay_timer, board.sound_timer), (3, 3))
        board.advance_frame(1)
        self.assertEqual((board.delay_timer, board.sound_timer), (2, 2))
        for _ in range(5):
            board.advance_frame(1)
        self.assertEqual((board.delay_timer, board.sound_timer), (0, 0))

    def test_the_noise_byte_changes_every_frame(self):
        board = Vip(seed=1)
        values = set()
        for _ in range(8):
            board.advance_frame(1)
            values.add(board.memory.read(vipfirmware.NOISE))
        self.assertGreater(len(values), 1)


class InstructionTests(unittest.TestCase):
    """The pieces of the interpreter a native comparison cannot check."""

    def test_rnd_masks_the_boards_own_noise_byte(self):
        board, _ = booted("start: RND V1, 0x0F\n HALT")
        board.memory.write(vipfirmware.NOISE, 0xAB)
        step(board, 1)
        self.assertEqual(board.registers()[0][1], 0x0B)

    def test_fx0a_waits_for_a_key_and_then_keeps_it(self):
        board, _ = booted("start: LD V0, K\n HALT")
        self.assertFalse(board.step_instruction(OPCODE_CLOCKS // 8))
        self.assertTrue(board.waiting_for_key)
        board.set_key(0x7, True)
        for _ in range(60):
            if board.step_instruction(OPCODE_CLOCKS):
                break
        self.assertEqual(board.registers()[0][0], 0x7, "the key is V0, not its label")
        self.assertFalse(board.waiting_for_key)

    def test_the_block_instructions_move_i_past_the_block(self):
        board, program = booted(
            """
            start: LD V0, 0x11
                   LD V1, 0x22
                   LD I, data
                   LD [I], V1
                   LD I, data
                   LD V2, 0
                   LD V1, [I]
                   HALT
            data:  DB 0
                   DB 0
            """
        )
        step(board, 7)
        v, i, _pc, _d, _s = board.registers()
        data = program.labels["data"]
        self.assertEqual((v[0], v[1]), (0x11, 0x22), "the block came back")
        self.assertEqual(board.memory.read(data), 0x11, "and it went to memory")
        self.assertEqual(board.memory.read(data + 1), 0x22)
        self.assertEqual(i, data + 2, "each block instruction moved I past its bytes")

    def test_i_wraps_within_the_twelve_bit_address_space(self):
        board, _ = booted("start: LD I, 0xFFF\n ADD I, V0\n HALT")
        board.memory.write(0x0100, 0x02)  # V0
        step(board, 3)
        self.assertEqual(board.registers()[1], 0x001)

    def test_a_halt_stops_the_interpreter(self):
        board, _ = booted("start: HALT")
        step(board, 1)
        self.assertTrue(board.cpu.halted)
        self.assertFalse(board.step_instruction(OPCODE_CLOCKS))


class DifferentialTests(unittest.TestCase):
    """The firmware against the native machine, instruction by instruction."""

    def assert_platforms_agree(self, source: str, keys: int = 0, limit: int = 200):
        program = assemble_chip8(source.encode())
        native = Chip8Machine.from_program(
            program.memory, program.entry, keys=keys, rng=random.Random(7)
        )
        board = Vip(seed=7)
        board.load_chip8(program)
        board.set_keys(keys)

        for index in range(limit):
            if native.halted:
                break
            native.step()
            if not board.step_instruction(OPCODE_CLOCKS):
                if board.waiting_for_key:
                    continue
                self.fail(f"the VIP did not retire instruction {index}")
            v, i, pc, delay, sound = board.registers()
            self.assertEqual(tuple(native.v), v, f"V after instruction {index}")
            self.assertEqual(native.i, i, f"I after instruction {index}")
            self.assertEqual(native.pc, pc, f"PC after instruction {index}")
            self.assertEqual(native.delay_timer, delay)
            self.assertEqual(native.sound_timer, sound)
            self.assertEqual(
                bytes(native.framebuffer),
                board.screen(),
                f"screen after instruction {index}",
            )
        return board

    def test_load_store_and_jumps(self):
        self.assert_platforms_agree(
            """
            start: LD V0, 0x12
                   LD V1, 0x34
                   LD I, block
                   LD [I], V1
                   LD I, block
                   LD V2, 0x99
                   LD V0, [I]
                   LD V1, [I]
                   CALL sub
                   LD V3, 0x55
                   HALT
            sub:   LD V4, 0x66
                   RET
            block: DB 0
                   DB 0
            """
        )

    def test_every_arithmetic_opcode(self):
        cases = (
            "LD V2, V3",
            "OR V2, V3",
            "AND V2, V3",
            "XOR V2, V3",
            "ADD V2, V3",
            "SUB V2, V3",
            "SHR V2",
            "SUBN V2, V3",
            "SHL V2",
        )
        for mnemonic in cases:
            with self.subTest(mnemonic=mnemonic):
                self.assert_platforms_agree(
                    f"""
                    start: LD V2, 0x8F
                           LD V3, 0x0B
                           {mnemonic}
                           HALT
                    """
                )

    def test_the_flags_of_borrow_and_carry(self):
        for mnemonic in ("ADD", "SUB", "SUBN"):
            for vx, vy in ((0x10, 0x20), (0x20, 0x10), (0x00, 0x00), (0xFF, 0x01)):
                with self.subTest(mnemonic=mnemonic, vx=vx, vy=vy):
                    self.assert_platforms_agree(
                        f"""
                        start: LD V0, {vx}
                               LD V1, {vy}
                               {mnemonic} V0, V1
                               HALT
                        """
                    )

    def test_skips_in_both_directions(self):
        self.assert_platforms_agree(
            """
            start: LD V0, 5
                   LD V1, 5
                   SE V0, 5
                   LD V2, 1
                   SNE V0, 6
                   LD V3, 2
                   SE V0, V1
                   LD V4, 3
                   SNE V0, V1
                   LD V5, 4
                   LD V0, 6
                   SE V0, 6
                   LD V6, 5
                   HALT
            """
        )

    def test_drawing_wraps_and_reports_the_collision(self):
        self.assert_platforms_agree(
            """
            start: CLS
                   LD V0, 62
                   LD V1, 30
                   LD I, wide
                   DRW V0, V1, 4
                   DRW V0, V1, 4
                   LD V0, 0
                   LD V1, 0
                   LD I, wide
                   DRW V0, V1, 4
                   DRW V0, V1, 4
                   HALT
            wide:  DB 0xF0
                   DB 0x90
                   DB 0x90
                   DB 0x90
            """
        )

    def test_font_bcd_and_memory_blocks(self):
        self.assert_platforms_agree(
            """
            start: LD V0, 147
                   LD I, digits
                   LD B, V0
                   LD I, digits
                   LD V1, [I]
                   LD V2, [I]
                   LD F, V1
                   DRW V0, V1, 5
                   LD I, 0x52E
                   HALT
            digits: DB 0
                    DB 0
                    DB 0
            """
        )

    def test_the_keypad_skips_follow_the_held_keys(self):
        for keys in (0x0000, 0x0001, 0x8000):
            with self.subTest(keys=f"{keys:04X}"):
                self.assert_platforms_agree(
                    """
                    start: LD V0, 1
                           SKNP V0
                           LD V1, 0xAA
                           SKP V0
                           LD V2, 0xBB
                           HALT
                    """,
                    keys=keys,
                )

    def test_randomised_programs_agree(self):
        """A quick stand-in for a fuzzer: safe opcodes, random operands."""

        rng = random.Random(20250601)
        for trial in range(12):
            # The first opcode points I at unused RAM: the VIP has a boot stub
            # in the first three bytes and the native machine has zeros there,
            # which is a difference in the boards rather than in the
            # interpreter, so nothing here should read through I = 0.
            opcodes = ["A300"]
            for _ in range(12):
                x, y = rng.randrange(16), rng.randrange(16)
                nn, nnn = rng.randrange(256), rng.randrange(0x300, 0x600)
                group = rng.choice("3456789ADF")
                if group == "3":
                    opcodes.append(f"3{x:X}{nn:02X}")
                elif group == "4":
                    opcodes.append(f"4{x:X}{nn:02X}")
                elif group == "5":
                    opcodes.append(f"5{x:X}{y:X}0")
                elif group == "6":
                    opcodes.append(f"6{x:X}{nn:02X}")
                elif group == "7":
                    opcodes.append(f"7{x:X}{nn:02X}")
                elif group == "8":
                    opcodes.append(f"8{x:X}{y:X}{rng.choice('01234567E')}")
                elif group == "9":
                    opcodes.append(f"9{x:X}{y:X}0")
                elif group == "A":
                    opcodes.append(f"A{nnn:03X}")
                elif group == "D":
                    rows = rng.choice((1, 2, 3, 5, 8, 15))
                    opcodes.append(f"D{x:X}{y:X}{rows:X}")
                else:
                    low = rng.choice(("07", "15", "18", "1E", "29", "33", "55", "65"))
                    opcodes.append(f"F{x:X}{low}")
            words = "\n       ".join(f"DW 0x{opcode}" for opcode in opcodes)
            source = "start: " + words + "\n       HALT"
            with self.subTest(trial=trial):
                self.assert_platforms_agree(source, limit=len(opcodes) + 1)

    def test_the_examples_run_the_same_as_they_do_natively(self):
        """One frame's worth of every bundled 1977 program, both platforms.

        A program that reads the noise byte diverges at its first ``RND``
        because two random generators cannot agree; those runs are compared up
        to that point, which still covers everything the program did before it.
        """

        compared = 0
        for path in EXAMPLES:
            program = assemble_chip8(path.read_bytes())
            native = Chip8Machine.from_program(program.memory, program.entry)
            board = Vip(seed=1)
            board.load_chip8(program)
            if superchip_opcodes(program):
                continue  # SuperChip: the VIP is the 1977 machine
            with self.subTest(program=path.name):
                for index in range(200):
                    if native.halted:
                        break
                    try:
                        native.step()
                    except Chip8Error:
                        break
                    if not board.step_instruction(OPCODE_CLOCKS):
                        if board.waiting_for_key:
                            continue
                        break
                    v, i, pc, delay, sound = board.registers()
                    if tuple(native.v) != v or native.i != i:
                        opcode = (native.memory[native.pc - 2] << 8) | native.memory[
                            native.pc - 1
                        ]
                        if opcode >> 12 == 0xC:
                            break  # RND: the two boards draw different bytes
                        self.fail(
                            f"{path.name}: {opcode:04X} left V={list(v)} I={i:03X}, "
                            f"native V={list(native.v)} I={native.i:03X}"
                        )
                    self.assertEqual(native.pc, pc, path.name)
                    self.assertEqual(
                        bytes(native.framebuffer), board.screen(), path.name
                    )
                compared += 1
        self.assertGreaterEqual(compared, 10)


class EmulatorTests(unittest.TestCase):
    """The adapter: what the frontends, the inspector and the CLI call."""

    def emulator(self, source: str | None = None) -> VipEmulator:
        emulator = VipEmulator(seed=1)
        if source is not None:
            self.assertTrue(emulator.load_source(source), emulator.error)
        return emulator

    def test_an_empty_emulator_reports_itself_empty(self):
        emulator = self.emulator()
        self.assertFalse(emulator.loaded)
        self.assertEqual(emulator.state_name, "empty")
        self.assertFalse(emulator.binary)
        emulator.advance_frame()
        self.assertEqual(emulator.snapshot().framebuffer, bytes(64 * 32))

    def test_a_loading_listing_reports_the_chip8_state(self):
        emulator = self.emulator("start: LD V0, 7\n HALT")
        snapshot = emulator.snapshot()
        self.assertTrue(snapshot.loaded)
        self.assertEqual(snapshot.name, "document")
        self.assertEqual((snapshot.screen_width, snapshot.screen_height), (64, 32))
        self.assertEqual(len(snapshot.framebuffer), 64 * 32)
        self.assertEqual(len(snapshot.memory), 0x1000)
        emulator.start()
        emulator.advance_frame()
        self.assertEqual(emulator.snapshot().v[0], 7)
        self.assertEqual(emulator.cycles, 1)

    def test_loading_a_binary_rom_works_too(self):
        emulator = VipEmulator()
        self.assertTrue(emulator.load_bytes(b"\x00\xe0\x12\x00", "rom.ch8"))
        self.assertTrue(emulator.binary)
        self.assertEqual(emulator.snapshot().name, "rom.ch8")

    def test_a_broken_listing_reports_an_error_rather_than_raising(self):
        emulator = VipEmulator()
        self.assertFalse(emulator.load_source("start: NOSUCH V0, 1"))
        self.assertNotEqual(emulator.error, "")

    def test_a_superchip_program_loads_with_a_note(self):
        emulator = VipEmulator()
        self.assertTrue(emulator.load_source("start: DW 0x00FF\n HALT"))
        self.assertTrue(emulator.notes)
        self.assertIn("SuperChip", emulator.notes[0])

    def test_only_the_opcodes_the_board_lacks_are_noted(self):
        emulator = VipEmulator()
        for opcode in ("0x00C4", "0x00FB", "0x00FE", "0x00FF", "0xDAB0", "0xF030"):
            with self.subTest(opcode=opcode):
                self.assertTrue(emulator.load_source(f"start: DW {opcode}\n HALT"))
                self.assertTrue(emulator.notes, f"{opcode} should be noted")
        for opcode in ("0x00FD", "0xF075", "0xF085", "0xF029"):
            with self.subTest(opcode=opcode):
                self.assertTrue(emulator.load_source(f"start: DW {opcode}\n HALT"))
                self.assertEqual(emulator.notes, [], f"{opcode} is runnable")

    def test_the_screen_view_is_the_chip8_grid_by_default(self):
        emulator = self.emulator("start: LD V0, 8\n LD V1, 4\n LD I, dot\n"
                                 " DRW V0, V1, 1\n HALT\ndot: DB 0x80")
        emulator.start()
        emulator.advance_frame()
        snapshot = emulator.snapshot()
        self.assertEqual((snapshot.screen_width, snapshot.screen_height), (64, 32))
        self.assertEqual(len(snapshot.framebuffer), 64 * 32)
        self.assertEqual(sum(snapshot.framebuffer), 1)

    def test_the_vip_view_is_the_boards_own_display(self):
        emulator = self.emulator("start: LD V0, 8\n LD V1, 4\n LD I, dot\n"
                                 " DRW V0, V1, 1\n HALT\ndot: DB 0x80")
        emulator.set_screen_view("vip")
        emulator.start()
        emulator.advance_frame()
        snapshot = emulator.snapshot()
        self.assertEqual((snapshot.screen_width, snapshot.screen_height), (64, 128))
        self.assertEqual(len(snapshot.framebuffer), 64 * 128)
        # One CHIP-8 pixel is four lines of the board, not one.
        self.assertEqual(sum(snapshot.framebuffer), 4)
        rows = [
            index // 64
            for index, value in enumerate(snapshot.framebuffer)
            if value
        ]
        self.assertEqual(rows, [16, 17, 18, 19])

    def test_an_unknown_screen_view_is_refused(self):
        emulator = self.emulator()
        with self.assertRaises(ValueError):
            emulator.set_screen_view("arcade")

    def test_speed_is_clamped_and_bumps_in_useful_steps(self):
        emulator = self.emulator()
        emulator.set_speed(0)
        self.assertEqual(emulator.speed, 1)
        emulator.set_speed(5000)
        self.assertEqual(emulator.speed, 2000)
        emulator.set_speed(10)
        emulator.nudge_speed(1)
        self.assertEqual(emulator.speed, 20)
        emulator.nudge_speed(-1)
        self.assertEqual(emulator.speed, 10)
        emulator.nudge_speed(-1)
        self.assertEqual(emulator.speed, 9)

    def test_tick_waits_for_the_run_but_step_does_not(self):
        emulator = self.emulator("start: LD V0, 1\n LD V1, 2\n HALT")
        emulator.tick()
        self.assertEqual(emulator.cycles, 0, "a paused machine does not tick")
        emulator.step()
        self.assertEqual(emulator.cycles, 1)
        self.assertEqual(emulator.snapshot().v[0], 1)
        self.assertFalse(emulator.running)
        emulator.start()
        emulator.tick()
        self.assertEqual(emulator.cycles, 2, "it runs to the halt and stops")

    def test_start_and_pause_and_reset(self):
        emulator = self.emulator("start: JP start")
        emulator.start()
        self.assertTrue(emulator.running)
        self.assertEqual(emulator.state_name, "running")
        emulator.advance_frame()
        self.assertGreater(emulator.cycles, 0)
        emulator.pause()
        self.assertFalse(emulator.running)
        self.assertEqual(emulator.state_name, "paused")
        emulator.reset()
        self.assertEqual(emulator.cycles, 0)
        self.assertEqual(emulator.state_name, "paused")

    def test_rewind_walks_back_through_the_frames(self):
        emulator = self.emulator("start: ADD V0, 1\n JP start")
        emulator.start()
        for _ in range(REWIND_SNAPSHOTS // 4):
            emulator.advance_frame()
        count = emulator.rewind_count
        self.assertGreater(count, 1)
        newest = emulator.cycles
        emulator.rewind_by(-1)
        self.assertFalse(emulator.running)
        self.assertLess(emulator.cycles, newest)
        emulator.rewind_by(1)
        self.assertEqual(emulator.cycles, newest)
        emulator.clear_rewind()
        self.assertEqual(emulator.rewind_count, 0)

    def test_the_machine_property_gives_the_frontends_a_sound_timer(self):
        emulator = self.emulator("start: LD V0, 9\n LD ST, V0\n HALT")
        emulator.start()
        emulator.advance_frame()
        # The frame ends by ticking the timers, so the 9 the program wrote is
        # 8 by the time anyone looks, on both platforms.
        self.assertEqual(emulator.machine.sound_timer, 8)
        self.assertEqual(emulator.snapshot().sound_timer, 8)

    def test_the_snapshot_reports_the_call_stack_the_interpreter_keeps(self):
        emulator = self.emulator(
            """
            start: CALL sub
                   HALT
            sub:   CALL sub2
                   RET
            sub2:  RET
            """
        )
        emulator.step()
        emulator.step()
        self.assertEqual(len(emulator.snapshot().stack), 2)

    def test_a_halting_program_stops_the_run(self):
        emulator = self.emulator("start: HALT")
        emulator.start()
        emulator.advance_frame()
        self.assertFalse(emulator.running)
        self.assertEqual(emulator.state_name, "halted")


class CommandLineTests(unittest.TestCase):
    """``--platform vip`` and ``--screen`` as the command line spells them."""

    def run_cli(self, *argv: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli.main(list(argv))
        return code, buffer.getvalue()

    def test_a_program_runs_on_the_vip_from_the_command_line(self):
        code, output = self.run_cli(
            "run",
            str(ROOT / "roms" / "plot.ch8"),
            "--platform",
            "vip",
            "--frontend",
            "headless",
            "--frames",
            "5",
            "--show",
        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(output.splitlines()), 17, "16 rows and a status")
        self.assertIn("running", output)

    def test_the_full_display_from_the_command_line(self):
        code, output = self.run_cli(
            "run",
            str(ROOT / "roms" / "plot.ch8"),
            "--platform",
            "vip",
            "--screen",
            "vip",
            "--frontend",
            "headless",
            "--frames",
            "5",
            "--show",
        )
        self.assertEqual(code, 0)
        self.assertGreaterEqual(len(output.splitlines()), 65, "64 rows and a status")

    def test_the_vip_display_needs_the_vip_platform(self):
        with self.assertRaises(SystemExit) as raised:
            cli.main(
                [
                    "run",
                    str(ROOT / "roms" / "plot.ch8"),
                    "--screen",
                    "vip",
                    "--frontend",
                    "headless",
                ]
            )
        self.assertIn("--platform vip", str(raised.exception.code))

    def test_the_vip_command_describes_the_firmware(self):
        code, output = self.run_cli("vip")
        self.assertEqual(code, 0)
        self.assertIn("interpreter:", output)
        self.assertIn("FETCH", output)


if __name__ == "__main__":
    unittest.main()
