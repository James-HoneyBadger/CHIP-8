"""The assembler: layout, encoding, the two spellings of a number, and its refusals."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8.assembler import (  # noqa: E402
    assemble_chip8,
    keypad_seed,
    load_program_file,
    parse_key_mask,
    parse_number,
)
from chip8.constants import (  # noqa: E402
    FONT_ADDRESS,
    MEMORY_SIZE,
    PROGRAM_ADDRESS,
    ROM_LIMIT,
)
from chip8.machine import Chip8Error  # noqa: E402
from tests.support import assemble  # noqa: E402


class LayoutTests(unittest.TestCase):
    def test_program_starts_at_0x200(self):
        program = assemble("start: CLS")
        self.assertEqual(program.labels["start"], 0x200)
        self.assertEqual(program.memory[0x200], 0x00)
        self.assertEqual(program.memory[0x201], 0xE0)
        self.assertEqual(program.size, 2)

    def test_labels_resolve_forward_references(self):
        program = assemble("start: JP later\nlater: HALT")
        self.assertEqual(program.labels["later"], 0x202)
        self.assertEqual(program.memory[0x200], 0x12)
        self.assertEqual(program.memory[0x201], 0x02)

    def test_a_label_alone_costs_nothing(self):
        program = assemble("start: CLS\nonly_a_label:\n HALT")
        self.assertEqual(program.labels["only_a_label"], 0x202)

    def test_labels_are_case_sensitive(self):
        program = assemble("Start: CLS\nstart: HALT")
        self.assertNotEqual(program.labels["Start"], program.labels["start"])

    def test_comments_and_blank_lines_are_ignored(self):
        program = assemble("; a comment\n\n   \nstart: CLS ; trailing\n")
        self.assertEqual(program.size, 2)

    def test_crlf_listings_assemble(self):
        program = assemble("start: CLS\r\n       HALT\r\n")
        self.assertEqual(bytes(program.memory[0x200:0x204]), b"\x00\xe0\x00\x00")

    def test_source_lines_are_recorded_for_the_inspector(self):
        program = assemble("start: CLS\n       LD V0, 1")
        self.assertEqual(program.source_lines[0x200], 1)
        self.assertEqual(program.source_lines[0x202], 2)


class NumberTests(unittest.TestCase):
    def test_both_hex_spellings_agree(self):
        prefixed = assemble("start: LD V0, 0xFF")
        suffixed = assemble("start: LD V0, FFh")
        bare = assemble("start: LD V0, 15")
        self.assertEqual(prefixed.memory[0x201], 0xFF)
        self.assertEqual(suffixed.memory[0x201], 0xFF)
        self.assertEqual(bare.memory[0x201], 15)

    def test_decimal_and_hex_prefixes(self):
        self.assertEqual(parse_number("512"), 512)
        self.assertEqual(parse_number("0x200"), 512)
        self.assertEqual(parse_number("0X200"), 512)
        self.assertEqual(parse_number("200h"), 512)
        self.assertEqual(parse_number("200H"), 512)
        self.assertEqual(parse_number(" +7 "), 7)
        self.assertEqual(parse_number("-7"), -7)

    def test_a_bad_literal_is_refused(self):
        for text in ("", "0x", "12z", "0b101"):
            with self.subTest(text=text), self.assertRaises(Chip8Error):
                parse_number(text)


class DataTests(unittest.TestCase):
    def test_font_is_present_in_memory(self):
        self.assertEqual(assemble("start: HALT").memory[FONT_ADDRESS], 0xF0)

    def test_db_and_dw_emit_data(self):
        program = assemble("start: LD I, data\n HALT\ndata: DB 0xAB\n DW 0x1234")
        data = program.labels["data"]
        self.assertEqual(program.memory[data], 0xAB)
        self.assertEqual(program.memory[data + 1], 0x12)
        self.assertEqual(program.memory[data + 2], 0x34)

    def test_sys_assembles_to_its_address(self):
        program = assemble("start: SYS 0x123")
        self.assertEqual(program.memory[0x200], 0x01)
        self.assertEqual(program.memory[0x201], 0x23)

    def test_halt_is_two_zero_bytes(self):
        program = assemble("start: HALT")
        self.assertEqual(bytes(program.memory[0x200:0x202]), b"\x00\x00")

    def test_every_instruction_form_encodes(self):
        # One line per form in the assembler's table; the expected bytes are the
        # encoding the listing documents.
        cases = {
            "CLS": b"\x00\xe0",
            "RET": b"\x00\xee",
            "JP 0x234": b"\x12\x34",
            "JP V0, 0x234": b"\xb2\x34",
            "CALL 0x234": b"\x22\x34",
            "SE V1, 5": b"\x31\x05",
            "SE V1, V2": b"\x51\x20",
            "SNE V1, 5": b"\x41\x05",
            "SNE V1, V2": b"\x91\x20",
            "LD V1, 5": b"\x61\x05",
            "LD V1, V2": b"\x81\x20",
            "LD I, 0x345": b"\xa3\x45",
            "LD V1, DT": b"\xf1\x07",
            "LD V1, K": b"\xf1\x0a",
            "LD DT, V1": b"\xf1\x15",
            "LD ST, V1": b"\xf1\x18",
            "LD F, V1": b"\xf1\x29",
            "LD B, V1": b"\xf1\x33",
            "LD [I], V1": b"\xf1\x55",
            "LD V1, [I]": b"\xf1\x65",
            "ADD V1, 5": b"\x71\x05",
            "ADD V1, V2": b"\x81\x24",
            "ADD I, V1": b"\xf1\x1e",
            "OR V1, V2": b"\x81\x21",
            "AND V1, V2": b"\x81\x22",
            "XOR V1, V2": b"\x81\x23",
            "SUB V1, V2": b"\x81\x25",
            "SUBN V1, V2": b"\x81\x27",
            "SHR V1": b"\x81\x06",
            "SHL V1, V2": b"\x81\x0e",
            "RND V1, 0x3F": b"\xc1\x3f",
            "DRW V1, V2, 5": b"\xd1\x25",
            "SKP V1": b"\xe1\x9e",
            "SKNP V1": b"\xe1\xa1",
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                program = assemble(f"start: {source}")
                self.assertEqual(bytes(program.memory[0x200 : 0x200 + len(expected)]), expected)

    def test_case_does_not_matter(self):
        self.assertEqual(
            bytes(assemble("ld v0, 5").memory[0x200:0x202]),
            bytes(assemble("LD V0, 5").memory[0x200:0x202]),
        )

    def test_operands_split_on_commas_or_spaces(self):
        expected = bytes(assemble("DRW V0, V1, 5").memory[0x200:0x202])
        for spelling in ("DRW V0 V1 5", "DRW V0,V1,5", "DRW  V0 , V1 ,5"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    bytes(assemble(f"start: {spelling}").memory[0x200:0x202]), expected
                )


class RefusalTests(unittest.TestCase):
    def assert_refused(self, source: str, fragment: str) -> None:
        with self.assertRaises(Chip8Error) as caught:
            assemble(source)
        self.assertIn(fragment, str(caught.exception))

    def test_unknown_mnemonic(self):
        self.assert_refused("start: FROB V0", "not an instruction form")

    def test_byte_out_of_range(self):
        self.assert_refused("start: LD V0, 999", "not a byte")

    def test_word_out_of_range(self):
        self.assert_refused("start: LD I, 0x10000", "not a 16-bit value")

    def test_duplicate_label_names_the_line(self):
        self.assert_refused("dup: CLS\ndup: CLS", "line 2: label dup is defined twice")

    def test_too_many_rows(self):
        self.assert_refused("start: DRW V0, V1, 20", "draws at most 15 rows")

    def test_bad_register(self):
        self.assert_refused("start: LD VG, 1", "not a register (V0-VF)")

    def test_register_with_two_digits_is_not_a_register_and_a_value(self):
        self.assert_refused("start: LD V10, 1", "not a register (V0-VF)")

    def test_errors_name_their_line(self):
        self.assert_refused("start: CLS\n       NOPE V0", "line 2")

    def test_jp_with_another_register_is_refused(self):
        self.assert_refused("start: JP V5, target\ntarget: HALT", "V0 only")

    def test_a_listing_that_does_not_fit(self):
        with self.assertRaises(Chip8Error) as caught:
            assemble("\n".join("JP 0x200" for _ in range(2000)))
        self.assertIn("memory ends at 0x1000", str(caught.exception))


class RomTests(unittest.TestCase):
    def test_a_binary_rom_loads_at_0x200(self):
        # LD V0, 5; LD V1, 7; HALT. The NUL in the HALT opcode is what marks
        # this as a ROM rather than a listing.
        program = assemble_chip8(bytes((0x60, 0x05, 0x61, 0x07, 0x00, 0x00)))
        self.assertTrue(program.binary)
        self.assertEqual(program.memory[0x200], 0x60)
        self.assertEqual(program.memory[0x205], 0x00)
        self.assertEqual(program.size, 6)
        # The font is still where Fx29 points.
        self.assertEqual(program.memory[FONT_ADDRESS], 0xF0)

    def test_a_binary_rom_is_not_decoded_as_text(self):
        program = assemble_chip8(bytes((0xFF, 0x00, 0x80)))
        self.assertEqual(bytes(program.memory[0x200:0x203]), b"\xff\x00\x80")

    def test_the_memory_limit_is_reported(self):
        with self.assertRaises(Chip8Error) as caught:
            assemble_chip8(bytes(ROM_LIMIT + 1))
        self.assertIn("memory from 0x200 holds 3584", str(caught.exception))

    def test_a_rom_that_fits_is_accepted(self):
        program = assemble_chip8(bytes(ROM_LIMIT))
        self.assertEqual(program.size, ROM_LIMIT)
        self.assertEqual(len(program.memory), MEMORY_SIZE)


class KeypadSeedTests(unittest.TestCase):
    def test_a_keys_comment_seeds_the_mask(self):
        self.assertEqual(keypad_seed("; keys 5\nstart: HALT"), 1 << 5)
        self.assertEqual(keypad_seed("; keys 1F"), (1 << 1) | (1 << 0xF))

    def test_several_comments_combine_and_case_is_free(self):
        self.assertEqual(keypad_seed("; keys 1\n; KEYS a"), (1 << 1) | (1 << 0xA))

    def test_other_comments_do_not_seed(self):
        self.assertEqual(keypad_seed("; press 5 to start"), 0)

    def test_the_mask_literal_spells_keys(self):
        self.assertEqual(parse_key_mask("1F"), (1 << 1) | (1 << 0xF))


class FileTests(unittest.TestCase):
    def test_a_listing_file_loads(self):
        path = Path(__file__).resolve().parents[1] / "examples" / "bounce.ch8"
        program = load_program_file(path)
        self.assertFalse(program.binary)
        self.assertIn("loop", program.labels)
        self.assertGreater(program.size, 0)

    def test_a_missing_file_is_an_os_error(self):
        with self.assertRaises(OSError):
            load_program_file(Path("/nonexistent/nothing.ch8"))
        # ... which the emulator turns into a message rather than a crash.
        self.assertEqual(PROGRAM_ADDRESS, 0x200)


if __name__ == "__main__":
    unittest.main()
