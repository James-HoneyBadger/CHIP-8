"""The CDP1802: registers, flags, branches and the two ports.

The processor is the part of the VIP that a CHIP-8 test cannot reach: the
interpreter only ever uses the accumulator, the sixteen registers, the DF flag
and two ports. Everything else here is the data sheet, checked instruction by
instruction, because the firmware's correctness is only as good as the emulated
chip's.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.cosmac1802 import CLOCKS_PER_MACHINE_CYCLE, Cdp1802, Ram  # noqa: E402

DATA = 0x0300


def cpu_running(
    program: bytes, ram: Ram | None = None, **kwargs
) -> tuple[Cdp1802, Ram]:
    """A processor whose program is at 0x0000 and whose R1 points at DATA.

    R0 is the program counter and stays where the program is; R1 is the data
    pointer, so a test can move it without moving the program.
    """

    ram = ram if ram is not None else Ram()
    ram.load(0x0000, program)
    cpu = Cdp1802(ram, **kwargs)
    cpu.p = 0
    cpu.x = 1
    cpu.r[1] = DATA
    return cpu, ram


class RegisterTests(unittest.TestCase):
    def test_plo_and_phi_build_a_sixteen_bit_register(self):
        cpu, _ = cpu_running(b"\xf8\x12\xb3\xf8\x34\xa3")  # LDI 12, PHI R3 ...
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.r[3], 0x1200)
        cpu.step()
        cpu.step()
        self.assertEqual(cpu.r[3], 0x1234)

    def test_glo_and_ghi_read_the_halves_back(self):
        cpu, _ = cpu_running(b"\x83\x93")  # GLO R3, GHI R3
        cpu.r[3] = 0xABCD
        cpu.step()
        self.assertEqual(cpu.d, 0xCD)
        cpu.step()
        self.assertEqual(cpu.d, 0xAB)

    def test_inc_and_dec_wrap_at_sixteen_bits(self):
        cpu, _ = cpu_running(b"\x13\x13\x23")  # INC R3, INC R3, DEC R3
        cpu.r[3] = 0xFFFF
        cpu.step()
        self.assertEqual(cpu.r[3], 0x0000)
        cpu.step()
        self.assertEqual(cpu.r[3], 0x0001)
        cpu.step()
        self.assertEqual(cpu.r[3], 0x0000)

    def test_ldn_reads_through_a_register_without_moving_it(self):
        ram = Ram()
        ram.load(DATA, b"\xAA")
        cpu, _ = cpu_running(b"\x03", ram)  # LDN R3
        cpu.r[3] = DATA
        cpu.step()
        self.assertEqual(cpu.d, 0xAA)
        self.assertEqual(cpu.r[3], DATA)

    def test_lda_reads_and_advances(self):
        ram = Ram()
        ram.load(DATA, b"\xBB")
        cpu, _ = cpu_running(b"\x44", ram)  # LDA R4
        cpu.r[4] = DATA
        cpu.step()
        self.assertEqual(cpu.d, 0xBB)
        self.assertEqual(cpu.r[4], DATA + 1)

    def test_str_and_stxd_write_memory_in_opposite_directions(self):
        cpu, ram = cpu_running(b"\x53\x73")  # STR R3, STXD
        cpu.r[3] = 0x0200
        cpu.x = 3
        cpu.d = 0x5A
        cpu.step()
        self.assertEqual(ram.read(0x0200), 0x5A)
        cpu.d = 0x5B
        cpu.step()
        self.assertEqual(ram.read(0x0200), 0x5B)
        self.assertEqual(cpu.r[3], 0x01FF)

    def test_ldx_reads_the_data_pointer(self):
        ram = Ram()
        ram.load(DATA, b"\x42")
        cpu, _ = cpu_running(b"\xf0", ram)  # LDX
        cpu.step()
        self.assertEqual(cpu.d, 0x42)

    def test_mark_saves_x_and_p_and_enables_interrupts(self):
        cpu, ram = cpu_running(b"\x79")  # MARK
        cpu.p, cpu.x, cpu.ie = 3, 5, 0
        cpu.r[2] = 0x0400
        cpu.step()
        self.assertEqual(cpu.t, (5 << 4) | 3)
        self.assertEqual(ram.read(0x0400), (5 << 4) | 3)
        self.assertEqual(cpu.r[2], 0x03FF)
        self.assertEqual(cpu.x, 3, "MARK points X at the program counter")
        self.assertEqual(cpu.ie, 1)


class ArithmeticTests(unittest.TestCase):
    def test_add_sets_df_on_carry_out(self):
        cpu, _ = cpu_running(b"\xfc\x01")  # ADI 1
        cpu.d = 0xFF
        cpu.step()
        self.assertEqual(cpu.d, 0x00)
        self.assertEqual(cpu.df, 1)

    def test_add_with_carry_uses_df(self):
        cpu, _ = cpu_running(b"\x7c\x01")  # ADCI 1
        cpu.d = 0x01
        cpu.df = 1
        cpu.step()
        self.assertEqual(cpu.d, 0x03)
        self.assertEqual(cpu.df, 0)

    def test_sd_subtracts_the_accumulator_from_memory(self):
        # SD (0xF5) is "subtract D": D = M(R(X)) - D, and DF is the no-borrow.
        ram = Ram()
        ram.load(DATA, b"\x05")
        cpu, _ = cpu_running(b"\xf5", ram)
        cpu.d = 0x03
        cpu.step()
        self.assertEqual(cpu.d, 0x02)
        self.assertEqual(cpu.df, 1, "no borrow")

    def test_sd_borrows_when_d_is_larger(self):
        ram = Ram()
        ram.load(DATA, b"\x02")
        cpu, _ = cpu_running(b"\xf5", ram)
        cpu.d = 0x05
        cpu.step()
        self.assertEqual(cpu.d, 0xFD)
        self.assertEqual(cpu.df, 0, "a borrow happened")

    def test_sm_subtracts_memory_from_the_accumulator(self):
        # SM (0xF7) is "subtract memory": D = D - M(R(X)).
        ram = Ram()
        ram.load(DATA, b"\x05")
        cpu, _ = cpu_running(b"\xf7", ram)
        cpu.d = 0x08
        cpu.step()
        self.assertEqual(cpu.d, 0x03)
        self.assertEqual(cpu.df, 1)

    def test_smi_and_sdi_are_the_immediate_forms_of_the_same_pair(self):
        cpu, _ = cpu_running(b"\xff\x05")  # SMI 5: D = D - 5
        cpu.d = 0x08
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x03, 1))

        cpu, _ = cpu_running(b"\xfd\x08")  # SDI 8: D = 8 - D
        cpu.d = 0x05
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x03, 1))

    def test_the_borrow_forms_take_one_more(self):
        cpu, _ = cpu_running(b"\x7f\x01")  # SMBI 1 with DF = 0: D - 1 - 1
        cpu.d = 0x08
        cpu.df = 0
        cpu.step()
        self.assertEqual(cpu.d, 0x06)

        cpu, _ = cpu_running(b"\x7f\x01")
        cpu.d = 0x08
        cpu.df = 1
        cpu.step()
        self.assertEqual(cpu.d, 0x07)

    def test_shifts_put_the_bit_that_falls_off_in_df(self):
        cpu, _ = cpu_running(b"\xfe\xf6")  # SHL, SHR
        cpu.d = 0x81
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x02, 1))
        cpu.d = 0x03
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x01, 1))

    def test_the_carry_shifts_bring_df_in(self):
        cpu, _ = cpu_running(b"\x7e\x76")  # SHLC, SHRC
        cpu.d = 0x80
        cpu.df = 0
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x00, 1))
        cpu.df = 1
        cpu.step()
        self.assertEqual((cpu.d, cpu.df), (0x80, 0))

    def test_logic_operations_read_the_data_pointer(self):
        ram = Ram()
        ram.load(DATA, b"\x0F")
        for opcode, expected in ((0xF1, 0x1F), (0xF2, 0x05), (0xF3, 0x1A)):
            with self.subTest(opcode=opcode):
                cpu, _ = cpu_running(bytes((opcode,)), ram)
                cpu.d = 0x15
                cpu.step()
                self.assertEqual(cpu.d, expected)


class BranchTests(unittest.TestCase):
    def test_short_branch_takes_a_signed_displacement(self):
        cpu, _ = cpu_running(b"\x30\x02")  # BR +2
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0004)

        cpu, _ = cpu_running(b"\x30\xfe")  # BR -2: back to the branch itself
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0000)

    def test_bz_and_bnz_test_the_accumulator(self):
        cpu, _ = cpu_running(b"\x32\x02")  # BZ +2
        cpu.d = 0
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0004)

        cpu, _ = cpu_running(b"\x3A\x02")  # BNZ +2
        cpu.d = 0
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0002)

    def test_bdf_and_bnf_test_the_carry_flag(self):
        cpu, _ = cpu_running(b"\x33\x02")  # BDF +2
        cpu.df = 1
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0004)

        cpu, _ = cpu_running(b"\x3B\x02")  # BNF +2
        cpu.df = 1
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0002)

    def test_skp_skips_its_operand_and_goes_nowhere(self):
        # The operand of SKP is the byte it skips, not a displacement: a value
        # of 0x7F must leave the program counter past it, still at 0x0002.
        cpu, _ = cpu_running(b"\x38\x7f")
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0002)

    def test_b1_through_b4_test_the_external_flags(self):
        cpu, _ = cpu_running(b"\x3c\x02", ef=(0, 1, 0, 0))  # B2 +2
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0004)

        cpu, _ = cpu_running(b"\x3c\x02", ef=(1, 0, 1, 1))
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0002)

    def test_a_long_branch_loads_the_address_only_when_taken(self):
        cpu, _ = cpu_running(b"\xc0\x12\x34")  # LBR 0x1234
        cpu.step()
        self.assertEqual(cpu.r[0], 0x1234)

        cpu, _ = cpu_running(b"\xc2\x12\x34")  # LBZ 0x1234 with D != 0
        cpu.d = 1
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0003)

    def test_a_long_skip_steps_over_two_bytes(self):
        # The opcode has already been fetched, so the two bytes stepped over are
        # the ones that follow it: the counter lands on the third.
        cpu, _ = cpu_running(b"\xc8\xff\xff")  # LSKP
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0003)

        cpu, _ = cpu_running(b"\xc6\xff\xff")  # LSNZ with D = 0: not taken
        cpu.d = 0
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0001)

        cpu, _ = cpu_running(b"\xc6\xff\xff")  # LSNZ with D != 0: taken
        cpu.d = 9
        cpu.step()
        self.assertEqual(cpu.r[0], 0x0003)

    def test_sep_and_sex_choose_the_registers(self):
        cpu, _ = cpu_running(b"\xda")  # SEP RA
        cpu.step()
        self.assertEqual(cpu.p, 0x0A)

        cpu, _ = cpu_running(b"\xe5")  # SEX R5
        cpu.step()
        self.assertEqual(cpu.x, 0x05)

    def test_ret_restores_x_and_p_from_memory(self):
        ram = Ram()
        ram.load(DATA, b"\xa5")  # X = 0xA, P = 0x5
        cpu, _ = cpu_running(b"\x70", ram)  # RET
        cpu.step()
        self.assertEqual((cpu.x, cpu.p), (0x0A, 0x05))
        self.assertEqual(cpu.r[1], DATA + 1, "the return address advances")
        self.assertEqual(cpu.ie, 1)

    def test_dis_returns_with_interrupts_off(self):
        ram = Ram()
        ram.load(DATA, b"\x00")
        cpu, _ = cpu_running(b"\x71", ram)  # DIS
        cpu.step()
        self.assertEqual(cpu.ie, 0)
        self.assertEqual(cpu.r[1], DATA + 1)


class PortTests(unittest.TestCase):
    def test_out_writes_the_byte_the_data_pointer_points_at(self):
        ram = Ram()
        ram.load(DATA, b"\x2A")
        written: list[tuple[int, int]] = []
        cpu, _ = cpu_running(
            b"\x64", ram, out_port=lambda port, value: written.append((port, value))
        )
        cpu.step()
        self.assertEqual(written, [(4, 0x2A)])
        self.assertEqual(cpu.r[1], DATA + 1, "OUT advances the data pointer")

    def test_inp_leaves_the_byte_in_d_and_in_memory(self):
        ram = Ram()
        cpu, _ = cpu_running(b"\x69", ram, in_port=lambda port: 0x0C)  # INP 1
        cpu.step()
        self.assertEqual(cpu.d, 0x0C)
        self.assertEqual(ram.read(DATA), 0x0C)
        self.assertEqual(cpu.r[1], DATA + 1)

    def test_irx_steps_the_data_pointer_without_touching_d(self):
        cpu, _ = cpu_running(b"\x60")
        cpu.d = 0x77
        cpu.step()
        self.assertEqual(cpu.r[1], DATA + 1)
        self.assertEqual(cpu.d, 0x77)


class TimingTests(unittest.TestCase):
    def test_an_instruction_is_two_machine_cycles(self):
        cpu, _ = cpu_running(b"\xf8\x00")  # LDI 0
        self.assertEqual(cpu.step(), 2 * CLOCKS_PER_MACHINE_CYCLE)
        self.assertEqual(cpu.cycles, 16)

    def test_a_long_branch_is_three_machine_cycles(self):
        cpu, _ = cpu_running(b"\xc0\x00\x00")
        self.assertEqual(cpu.step(), 3 * CLOCKS_PER_MACHINE_CYCLE)

    def test_run_stops_on_an_instruction_boundary(self):
        # LDI is sixteen clocks; a budget of exactly sixteen runs one of them.
        cpu, _ = cpu_running(b"\xf8\x01\xf8\x02")
        self.assertEqual(cpu.run(16), 16)
        self.assertEqual(cpu.instructions, 1)
        self.assertEqual(cpu.run(16), 16)
        self.assertEqual(cpu.instructions, 2)

    def test_idl_halts_and_run_stops_there(self):
        cpu, _ = cpu_running(b"\x00\xf8\x01")
        cpu.run(1000)
        self.assertTrue(cpu.halted)
        self.assertEqual(cpu.r[0], 0x0001, "nothing after IDL runs")

    def test_reset_is_the_power_on_state(self):
        cpu, _ = cpu_running(b"\x00")
        cpu.r[5] = 0x1234
        cpu.p = cpu.x = 7
        cpu.d = cpu.df = cpu.q = cpu.t = 1
        cpu.reset()
        self.assertEqual(cpu.r, [0] * 16)
        self.assertEqual((cpu.d, cpu.df, cpu.q, cpu.t, cpu.ie), (0, 0, 0, 0, 1))
        self.assertEqual((cpu.p, cpu.x), (0, 0))
        self.assertFalse(cpu.halted)


class MemoryTests(unittest.TestCase):
    def test_ram_wraps_addresses_into_sixteen_bits(self):
        ram = Ram()
        ram.write(0x10000, 0x55)
        self.assertEqual(ram.read(0x0000), 0x55)

    def test_ram_keeps_only_the_low_byte(self):
        ram = Ram()
        ram.write(0x0010, 0x1FF)
        self.assertEqual(ram.read(0x0010), 0xFF)


if __name__ == "__main__":
    unittest.main()
