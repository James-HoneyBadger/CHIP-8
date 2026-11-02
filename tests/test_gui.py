"""The desktop application's parts that do not need a display.

The window itself is GTK's business; what is worth testing here is that the
application parses its arguments the way it says, that the launcher names the
right executable and accepts a program, and that installing and removing it
leaves the user's data directory exactly as it was.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chip8 import cli, desktop, gui  # noqa: E402


class ArgumentTests(unittest.TestCase):
    def test_the_application_is_the_vip_by_default(self):
        args = gui._parse([])
        self.assertEqual((args.platform, args.screen), ("vip", "chip8"))
        self.assertIsNone(args.program)
        self.assertFalse(args.paused)

    def test_a_native_window_cannot_ask_for_the_vip_display(self):
        with self.assertRaises(SystemExit):
            gui._parse(["--platform", "native", "--screen", "vip"])

    def test_the_program_can_be_a_path_and_the_transport_can_start_paused(self):
        args = gui._parse(["game.ch8", "--paused", "--speed", "30"])
        self.assertEqual(args.program, "game.ch8")
        self.assertTrue(args.paused)
        self.assertEqual(args.speed, 30)


    def test_monitor_fields_accept_the_forms_a_person_types(self):
        self.assertEqual(gui.hex_value("0200", 0xFFF), 0x200)
        self.assertEqual(gui.hex_value("0x2a", 0xFF), 0x2A)
        self.assertIsNone(gui.hex_value("", 0xFF))
        self.assertIsNone(gui.hex_value("xyz", 0xFF))
        self.assertIsNone(gui.hex_value("1000", 0xFFF))


class LauncherTests(unittest.TestCase):
    def test_the_launcher_names_the_icon_and_takes_a_file(self):
        text = desktop.desktop_text()
        self.assertIn("Exec=", text)
        self.assertIn(" %f", text)
        self.assertIn("Icon=chip8-vip", text)
        self.assertIn("MimeType=application/x-chip8-rom;", text)

    def test_the_mime_description_matches_the_launcher(self):
        text = (desktop.data_dir() / desktop.MIME_FILE).read_text(encoding="utf-8")
        self.assertIn('type="application/x-chip8-rom"', text)
        self.assertIn('glob pattern="*.ch8"', text)
        self.assertIn('icon name="chip8-vip"', text)
        launcher = desktop.desktop_text()
        self.assertIn("MimeType=application/x-chip8-rom;", launcher)
        self.assertTrue((desktop.data_dir() / desktop.ICON_FILE).exists())

    def test_the_command_is_the_installed_script_or_this_interpreter(self):
        command = desktop.command()
        self.assertTrue(command)
        self.assertTrue(
            command[0] == "chip8-vip" or command[-2:] == ["-m", "chip8.gui"]
        )

    def test_install_and_uninstall_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            written = desktop.install(root)
            self.assertEqual(len(written), 3)
            for path in written:
                self.assertTrue(path.exists(), path)
            launcher = root / "applications" / desktop.DESKTOP_FILE
            self.assertIn("Exec=", launcher.read_text(encoding="utf-8"))
            removed = desktop.uninstall(root)
            self.assertEqual(set(removed), set(written))
            self.assertFalse(any(path.exists() for path in written))

    def test_uninstall_with_nothing_installed_is_quiet(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(desktop.uninstall(Path(folder)), [])


class CommandLineTests(unittest.TestCase):
    def test_desktop_takes_install_and_uninstall(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["desktop", "install"]).action, "install")
        self.assertEqual(
            parser.parse_args(["desktop", "uninstall"]).action, "uninstall"
        )

    def test_gui_defaults_to_the_vip(self):
        args = cli.build_parser().parse_args(["gui"])
        self.assertEqual((args.platform, args.screen), ("vip", "chip8"))


if __name__ == "__main__":
    unittest.main()
