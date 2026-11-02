"""The 1802 assembler: two passes, .org sections, labels and the tables.

``chip8.vipasm`` is small on purpose. It exists so that the interpreter in
``chip8.vipfirmware`` can be read, edited and re-checked rather than stored as a
blob of bytes, so what these tests care about is that a listing means on the
machine what it says on the page.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.vipasm import VipAsmError, assemble_vip  # noqa: E402


def code(source: str, **kwargs) -> bytes:
    program = assemble_vip("            .org 0x1000\n" + source, **kwargs)
    image = bytearray(program.bytes())
    return bytes(image[0x1000 : 0x1000 + program.size])


class EncodingTests(unittest.TestCase):
    def test_register_instructions_carry_the_register_in_the_opcode(self):
        self.assertEqual(code("LDI 0x01\nPLO R3"), b"\xf8\x01\xa3")
        self.assertEqual(code("GHI R7"), b"\x97")
        self.assertEqual(code("SEP RF"), b"\xdf")
        self.assertEqual(code("SEX R2"), b"\xe2")
        self.assertEqual(code("INC R9"), b"\x19")

    def test_immediates_take_a_byte(self):
        self.assertEqual(code("LDI 0xFF"), b"\xf8\xff")
        self.assertEqual(code("ANI 0x0F"), b"\xfa\x0f")

    def test_ports_are_named_inside_the_opcode(self):
        self.assertEqual(code("OUT 4\nINP 1"), b"\x64\x69")

    def test_long_branches_carry_a_sixteen_bit_address(self):
        self.assertEqual(code("LBR 0x1234"), b"\xc0\x12\x34")
        self.assertEqual(code("LBNZ 0x0000"), b"\xca\x00\x00")

    def test_short_branches_are_relative_to_the_next_instruction(self):
        # BR is two bytes, so a jump to its own label is -2.
        self.assertEqual(code("here: BR here"), b"\x30\xfe")
        self.assertEqual(code("BR there\nNOP\nthere: DB 0"), b"\x30\x01\xc4\x00")

    def test_a_short_branch_that_cannot_reach_is_an_error(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nBR far\n.org 0x2000\nfar: DB 0")
        self.assertIn("cannot reach", str(raised.exception))

    def test_db_and_dw_are_little_of_the_endian_they_look(self):
        # DW stores high byte first, which is the order LDA reads back.
        self.assertEqual(code("DW 0x1234"), b"\x12\x34")
        self.assertEqual(code("DB 0x12, 0x34"), b"\x12\x34")


class LabelTests(unittest.TestCase):
    def test_a_label_is_the_address_of_the_line_it_names(self):
        program = assemble_vip("            .org 0x1000\nfirst:\nNOP\nsecond: LDI 7")
        self.assertEqual(program.labels["first"], 0x1000)
        self.assertEqual(program.labels["second"], 0x1001)

    def test_a_forward_reference_resolves_on_the_second_pass(self):
        self.assertEqual(code("LBR later\nNOP\nlater: RET"), b"\xc0\x10\x04\xc4\x70")

    def test_constants_are_values_not_addresses(self):
        program = assemble_vip(
            "            .org 0x1000\nBASE = 0x0100\nLDI high(BASE)"
        )
        self.assertEqual(program.labels["BASE"], 0x0100)
        self.assertEqual(bytes(program.bytes())[0x1000 : 0x1002], b"\xf8\x01")

    def test_low_and_high_take_a_constant_apart(self):
        source = "            .org 0x1000\nHERE = 0x1234\nLDI low(HERE)\nLDI high(HERE)"
        self.assertEqual(code(source), b"\xf8\x34\xf8\x12")

    def test_expressions_add_and_subtract(self):
        source = "            .org 0x1000\nLDI 4+2\nLDI 8-2\nLDI 2+2-1"
        self.assertEqual(code(source), b"\xf8\x06\xf8\x06\xf8\x03")

    def test_a_duplicate_label_is_an_error(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nsame: NOP\nsame: NOP")
        self.assertIn("used twice", str(raised.exception))

    def test_an_unknown_name_is_an_error_with_a_line_number(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nLDI nowhere")
        self.assertIn("line 2", str(raised.exception))
        self.assertIn("nowhere", str(raised.exception))


class ErrorTests(unittest.TestCase):
    def test_a_byte_that_does_not_fit_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nLDI 0x100")
        self.assertIn("does not fit in a byte", str(raised.exception))

    def test_an_unknown_mnemonic_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nFLY R3")
        self.assertIn("no FLY instruction", str(raised.exception))

    def test_an_unknown_register_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nSEP R16")
        self.assertIn("no register", str(raised.exception))

    def test_an_unknown_port_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nOUT 8")
        self.assertIn("no port 8", str(raised.exception))

    def test_an_operand_where_none_is_wanted_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip("            .org 0x1000\nSHR R3")
        self.assertIn("takes no operand", str(raised.exception))

    def test_a_second_org_over_placed_bytes_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip(".org 0x1000\nDB 0xAA\n.org 0x1000\nDB 0xBB")
        self.assertIn("overlaps", str(raised.exception))

    def test_a_partly_overlapping_org_is_refused(self):
        with self.assertRaises(VipAsmError) as raised:
            assemble_vip(".org 0x1000\nDW 0x1234\n.org 0x1001\nDB 0xAA")
        self.assertIn("overlaps", str(raised.exception))

    def test_a_comment_is_not_an_instruction(self):
        source = "; nothing but a comment\nNOP  ; and a trailing one"
        self.assertEqual(code(source), b"\xc4")


class ProgramTests(unittest.TestCase):
    def test_segments_stay_apart_and_keep_their_addresses(self):
        program = assemble_vip(
            "            .org 0x0000\nLBR 0x1000\n"
            "            .org 0x1000\nNOP\n"
            "            .org 0x2000\nDB 0xFF"
        )
        self.assertEqual(len(program.segments), 3)
        self.assertEqual(program.size, 3 + 1 + 1)
        self.assertEqual(program.segments[0], (0x0000, b"\xc0\x10\x00"))

    def test_contiguous_lines_become_one_segment(self):
        program = assemble_vip("            .org 0x1000\nNOP\nNOP\nLDI 1")
        self.assertEqual(len(program.segments), 1)
        self.assertEqual(program.segments[0][1], b"\xc4\xc4\xf8\x01")

    def test_the_image_leaves_gaps_as_zeros(self):
        program = assemble_vip("            .org 0x1000\nDB 0xAA\n.org 0x1010\nDB 0xBB")
        image = program.bytes()
        self.assertEqual(image[0x1000], 0xAA)
        self.assertEqual(image[0x1010], 0xBB)
        self.assertEqual(image[0x1001:0x1010], bytes(15))

    def test_load_into_skips_addresses_that_are_not_writable(self):
        from chip8.cosmac1802 import Ram

        program = assemble_vip("            .org 0x1000\nDB 0xAA")
        memory = Ram()
        program.load_into(memory)
        self.assertEqual(memory.read(0x1000), 0xAA)

        # A board that names its own RAM: the loader then leaves the rest of the
        # image alone, which is how firmware goes into ROM without turning the
        # write protection off.
        memory = Ram()
        program.load_into(memory, writable=lambda address: address < 0x1000)
        self.assertEqual(memory.read(0x1000), 0x00, "the ROM is left alone")


if __name__ == "__main__":
    unittest.main()
