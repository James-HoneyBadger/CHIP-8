"""The COSMAC VIP as a desktop application: one window, no server, no browser.

GTK 4 is the desktop's own toolkit, so the window behaves like every other
window on the machine: it scales with the display, follows the theme, uses the
file chooser and the application menu, and runs on X11 or Wayland. The emulator
itself is the same :class:`~chip8.vip.VipEmulator` the other frontends use, so
this adds no second implementation of anything.

    chip8-vip                       # the application, empty, ready for a program
    chip8-vip roms/snake.ch8        # open a program straight away
    python3 -m chip8.gui --help

PyGObject and GTK are system packages, not pip ones. On Debian and Ubuntu::

    sudo apt install python3-gi gir1.2-gtk-4.0

The window has a platform switch, so it can also run the native machine; the
VIP is the default, because it is the machine the application is named for.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .constants import FRAMES_PER_SECOND, OFF_COLOR, ON_COLOR
from .emulator import Chip8Emulator
from .machine import Chip8Error
from .vip import VipEmulator

__all__ = ["hex_value", "main"]

APP_ID = "org.chip8.Vip"
VERSION = "1.0.0"


def hex_value(text: str, maximum: int) -> int | None:
    """A monitor field's value, or None when it is not hex in range."""

    cleaned = text.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if not cleaned:
        return None
    try:
        value = int(cleaned, 16)
    except ValueError:
        return None
    return value if 0 <= value <= maximum else None

_MISSING = """\
chip8-vip: GTK 4 was not found, so the window cannot open.

Install the desktop toolkit for your distribution:
  Debian/Ubuntu   sudo apt install python3-gi gir1.2-gtk-4.0
  Fedora          sudo dnf install python3-gobject gtk4
  Arch            sudo pacman -S python-gobject gtk4

Without it, the terminal and web frontends still work:
  python3 -m chip8 run game.ch8 --frontend terminal
  python3 -m chip8 run game.ch8 --frontend web
"""


class _MissingGtk(RuntimeError):
    pass


def _import_gtk():
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gio, GLib, Gtk
    except (ImportError, ValueError) as failure:  # pragma: no cover - system package
        raise _MissingGtk(_MISSING) from failure
    return Gtk, Gdk, Gio, GLib


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="chip8-vip",
        description="Run CHIP-8 programs on an emulated COSMAC VIP, in a window.",
    )
    parser.add_argument("program", nargs="?", help="a .ch8 listing or binary ROM")
    parser.add_argument(
        "--platform",
        choices=("vip", "native"),
        default="vip",
        help="which machine to run (default: vip, the COSMAC VIP)",
    )
    parser.add_argument(
        "--screen",
        choices=("chip8", "vip"),
        default="chip8",
        help="the 64x32 CHIP-8 grid, or the board's own 64x128 display",
    )
    parser.add_argument(
        "--speed", type=int, default=None, metavar="N", help="opcodes per frame"
    )
    parser.add_argument("--seed", type=int, default=None, metavar="N")
    parser.add_argument("--paused", action="store_true", help="start paused")
    args = parser.parse_args(argv)
    if args.platform == "native" and args.screen == "vip":
        parser.error("--screen vip is the COSMAC VIP's display; add --platform vip")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        Gtk, Gdk, Gio, GLib = _import_gtk()
    except _MissingGtk as failure:
        print(failure, file=sys.stderr)
        return 2
    return _run(args, Gtk, Gdk, Gio, GLib)


def _run(args: argparse.Namespace, Gtk, Gdk, Gio, GLib) -> int:  # noqa: C901
    from . import librarian

    keypad = {
        Gdk.KEY_1: 0x1,
        Gdk.KEY_2: 0x2,
        Gdk.KEY_3: 0x3,
        Gdk.KEY_4: 0xC,
        Gdk.KEY_q: 0x4,
        Gdk.KEY_w: 0x5,
        Gdk.KEY_e: 0x6,
        Gdk.KEY_r: 0xD,
        Gdk.KEY_a: 0x7,
        Gdk.KEY_s: 0x8,
        Gdk.KEY_d: 0x9,
        Gdk.KEY_f: 0xE,
        Gdk.KEY_z: 0xA,
        Gdk.KEY_x: 0x0,
        Gdk.KEY_c: 0xB,
        Gdk.KEY_v: 0xF,
    }

    class VipWindow(Gtk.ApplicationWindow):
        """The whole application: screen, registers, transport and menus."""

        def __init__(self, application) -> None:
            super().__init__(application=application)
            self.platform = args.platform
            self.screen_view = args.screen
            self.seed = args.seed
            self.program_path = Path(args.program) if args.program else None
            self._start_paused = args.paused
            self._explicit_speed = args.speed is not None
            self._speed = args.speed if args.speed is not None else 10
            self._snapshot = None
            self._closed = False
            self._last: dict[str, str] = {}
            self._hint = ""
            self.emulator = None

            self.set_default_size(1000, 700)
            self.set_title("COSMAC VIP")
            self._build_header()
            self._build_body()
            self._build_actions()
            self._install_css()
            self._install_input()
            self._load_startup_program()

            self._timer = GLib.timeout_add(
                max(1, 1000 // FRAMES_PER_SECOND), self._tick
            )
            self.connect("close-request", self._on_close)
            self.connect("notify::is-active", self._on_active)

        # -- construction --------------------------------------------------
        def _build_header(self) -> None:
            header = Gtk.HeaderBar()
            self.set_titlebar(header)
            self._title = Gtk.Label(label="COSMAC VIP")
            self._title.add_css_class("title")
            header.set_title_widget(self._title)

            open_button = Gtk.Button(label="Open")
            open_button.set_tooltip_text("Open a .ch8 program (Ctrl+O)")
            open_button.connect("clicked", lambda _button: self._open_dialog())
            header.pack_start(open_button)

            self._run_button = Gtk.Button(label="Pause")
            self._run_button.set_tooltip_text("Pause or resume (P)")
            self._run_button.connect("clicked", lambda _button: self._toggle())
            header.pack_start(self._run_button)

            step_button = Gtk.Button(label="Step")
            step_button.set_tooltip_text("Run one CHIP-8 instruction (Space)")
            step_button.connect("clicked", lambda _button: self._step())
            header.pack_start(step_button)

            forward = Gtk.Button(label="\u25b6")
            forward.set_tooltip_text("One rewind moment forward (])")
            forward.connect("clicked", lambda _button: self._rewind(1))
            header.pack_end(forward)
            back = Gtk.Button(label="\u25c0")
            back.set_tooltip_text("One rewind moment back ([)")
            back.connect("clicked", lambda _button: self._rewind(-1))
            header.pack_end(back)

            menu = Gio.Menu()
            program = Gio.Menu()
            program.append("New blank machine", "win.new")
            program.append("Open\u2026", "win.open")
            program.append("Reload", "win.reload")
            program.append("Quit", "win.quit")
            menu.append_section(None, program)

            machine = Gio.Menu()
            machine.append("CHIP-8 native", "win.platform-native")
            machine.append("COSMAC VIP", "win.platform-vip")
            menu.append_section("Machine", machine)

            screen = Gio.Menu()
            screen.append("64\u00d732 CHIP-8 grid", "win.screen-chip8")
            screen.append("64\u00d7128 board display", "win.screen-vip")
            menu.append_section("Screen", screen)

            run = Gio.Menu()
            run.append("Run / pause", "win.toggle")
            run.append("Step", "win.step")
            run.append("Reset", "win.reset")
            run.append("Slower", "win.speed-down")
            run.append("Faster", "win.speed-up")
            menu.append_section("Run", run)

            help_menu = Gio.Menu()
            help_menu.append("About COSMAC VIP", "win.about")
            menu.append_section(None, help_menu)

            menu_button = Gtk.MenuButton()
            menu_button.set_icon_name("open-menu-symbolic")
            menu_button.set_menu_model(menu)
            menu_button.set_tooltip_text("Machine, screen, speed and about")
            header.pack_end(menu_button)

        def _build_body(self) -> None:
            self.drawing = Gtk.DrawingArea()
            self.drawing.set_draw_func(self._draw)
            self.drawing.set_hexpand(True)
            self.drawing.set_vexpand(True)
            self.drawing.set_size_request(480, 300)

            panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            panel.set_margin_top(10)
            panel.set_margin_bottom(10)
            panel.set_margin_start(10)
            panel.set_margin_end(12)

            heading = Gtk.Label(label="Registers", xalign=0)
            heading.add_css_class("heading")
            panel.append(heading)
            grid = Gtk.Grid(column_spacing=8, row_spacing=3)
            self._v_labels: list = []
            for index in range(16):
                name = Gtk.Label(label=f"V{index:X}", xalign=0)
                name.add_css_class("mono")
                name.add_css_class("dim")
                value = Gtk.Label(label="00", xalign=1)
                value.add_css_class("mono")
                grid.attach(name, (index % 4) * 2, index // 4, 1, 1)
                grid.attach(value, (index % 4) * 2 + 1, index // 4, 1, 1)
                self._v_labels.append(value)
            panel.append(grid)

            self._misc = {}
            misc = Gtk.Grid(column_spacing=12, row_spacing=3)
            for slot, label in enumerate(("I", "PC", "DT", "ST", "cycles", "speed")):
                box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
                title = Gtk.Label(label=label, xalign=0)
                title.add_css_class("dim")
                title.add_css_class("mono")
                value = Gtk.Label(label="\u2014", xalign=0)
                value.add_css_class("mono")
                box.append(title)
                box.append(value)
                misc.attach(box, slot % 3, slot // 3, 1, 1)
                self._misc[label] = value
            panel.append(misc)

            self._cpu = Gtk.Label(label="", xalign=0, wrap=True)
            self._cpu.add_css_class("mono")
            self._cpu.add_css_class("dim")
            panel.append(self._cpu)

            keypad_heading = Gtk.Label(label="Keypad", xalign=0)
            keypad_heading.add_css_class("heading")
            panel.append(keypad_heading)
            self._key_buttons: dict[int, object] = {}
            keypad = Gtk.Grid(row_spacing=4, column_spacing=4)
            for row, letters in enumerate(("123C", "456D", "789E", "A0BF")):
                for column, letter in enumerate(letters):
                    key = int(letter, 16)
                    button = Gtk.Button(label=letter)
                    button.add_css_class("keypad")
                    button.set_hexpand(True)
                    button.set_tooltip_text(f"hold keypad {letter}")
                    gesture = Gtk.GestureClick()
                    gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
                    gesture.connect(
                        "pressed",
                        lambda _gesture, _n, _x, _y, key=key: self._pad_down(key),
                    )
                    gesture.connect(
                        "released",
                        lambda _gesture, _n, _x, _y, key=key: self._pad_up(key),
                    )
                    button.add_controller(gesture)
                    keypad.attach(button, column, row, 1, 1)
                    self._key_buttons[key] = button
            panel.append(keypad)

            self._notes = Gtk.Label(label="", xalign=0, wrap=True)
            self._notes.add_css_class("warn")
            panel.append(self._notes)

            monitor = Gtk.Expander(label="Memory / monitor")
            monitor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            monitor_box.set_margin_top(4)
            fields = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            address_label = Gtk.Label(label="addr", xalign=0)
            address_label.add_css_class("dim")
            self._addr_entry = Gtk.Entry()
            self._addr_entry.set_text("0200")
            self._addr_entry.set_width_chars(5)
            self._addr_entry.set_max_length(6)
            self._addr_entry.set_tooltip_text("hex address, for example 0200")
            byte_label = Gtk.Label(label="byte", xalign=0)
            byte_label.add_css_class("dim")
            self._byte_entry = Gtk.Entry()
            self._byte_entry.set_text("00")
            self._byte_entry.set_width_chars(3)
            self._byte_entry.set_max_length(4)
            self._byte_entry.set_tooltip_text("hex byte, for example 3F")
            fields.append(address_label)
            fields.append(self._addr_entry)
            fields.append(byte_label)
            fields.append(self._byte_entry)
            monitor_box.append(fields)
            self._addr_entry.connect("changed", lambda _entry: self._refresh_dump())
            self._addr_entry.connect("activate", lambda _entry: self._write_byte())
            self._byte_entry.connect("activate", lambda _entry: self._write_byte())

            buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            write_button = Gtk.Button(label="Write")
            write_button.set_tooltip_text("write the byte, then step to the next address")
            write_button.connect("clicked", lambda _button: self._write_byte())
            pc_button = Gtk.Button(label="Set PC")
            pc_button.set_tooltip_text("point the CHIP-8 program counter here")
            pc_button.connect("clicked", lambda _button: self._set_pc())
            buttons.append(write_button)
            buttons.append(pc_button)
            monitor_box.append(buttons)

            self._dump = Gtk.Label(label="", xalign=0)
            self._dump.add_css_class("mono")
            self._dump.add_css_class("dump")
            monitor_box.append(self._dump)
            monitor.set_child(monitor_box)
            panel.append(monitor)

            panel.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            bundled_heading = Gtk.Label(label="Bundled programs", xalign=0)
            bundled_heading.add_css_class("heading")
            panel.append(bundled_heading)
            self._catalogue = []
            self._dropdown = None
            try:
                self._catalogue = librarian.catalogue()
            except Chip8Error:
                self._catalogue = []
            if self._catalogue:
                names = ["Choose a bundled program\u2026"] + [
                    entry.name for entry in self._catalogue
                ]
                dropdown = Gtk.DropDown.new_from_strings(names)
                if self.program_path is not None:
                    for index, entry in enumerate(self._catalogue):
                        if Path(entry.path) == self.program_path:
                            dropdown.set_selected(index + 1)
                            break
                dropdown.connect("notify::selected", self._on_catalogue)
                self._dropdown = dropdown
                panel.append(dropdown)
            else:
                missing = Gtk.Label(label="none found beside the package", xalign=0)
                missing.add_css_class("dim")
                panel.append(missing)

            keys = Gtk.Label(
                label=(
                    "keypad  1 2 3 4 / Q W E R / A S D F / Z X C V\n"
                    "P pause  \u00b7  Space/O step  \u00b7  Shift+R reset\n"
                    "[ / ] rewind  \u00b7  + / - speed"
                ),
                xalign=0,
            )
            keys.add_css_class("dim")
            keys.add_css_class("mono")
            panel.append(keys)

            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_child(panel)
            scroller.set_size_request(290, -1)

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            body.append(self.drawing)
            body.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            body.append(scroller)

            self._status = Gtk.Label(label="Open a program to begin", xalign=0)
            self._status.add_css_class("mono")
            self._status.set_margin_top(6)
            self._status.set_margin_bottom(6)
            self._status.set_margin_start(12)
            self._status.set_margin_end(12)

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            root.append(body)
            root.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            root.append(self._status)
            self.set_child(root)

        def _build_actions(self) -> None:
            for name, callback in (
                ("new", self._new_blank),
                ("open", self._open_dialog),
                ("reload", self._reload),
                ("quit", self.close),
                ("platform-native", lambda: self._set_platform("native")),
                ("platform-vip", lambda: self._set_platform("vip")),
                ("screen-chip8", lambda: self._set_screen("chip8")),
                ("screen-vip", lambda: self._set_screen("vip")),
                ("toggle", self._toggle),
                ("step", self._step),
                ("reset", self._reset),
                ("speed-down", lambda: self._nudge_speed(-1)),
                ("speed-up", lambda: self._nudge_speed(1)),
                ("about", self._about),
            ):
                action = Gio.SimpleAction.new(name, None)
                action.connect("activate", lambda *_args, run=callback: run())
                self.add_action(action)

        def _install_css(self) -> None:
            css = b"""
            .mono { font-family: monospace; }
            .dim { color: #8b9298; }
            .warn { color: #d7b46a; }
            .dump { font-size: 11px; color: #b8c0c6; }
            .keypad { min-width: 38px; min-height: 26px; padding: 1px 0; }
            .keypad.held { background: #2f6d4f; border-color: #7fd1a0; }
            """
            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            display = self.get_display() or Gdk.Display.get_default()
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        def _install_input(self) -> None:
            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect("key-pressed", self._key_down)
            keys.connect("key-released", self._key_up)
            self.add_controller(keys)

            drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
            drop.connect("drop", self._dropped)
            self.add_controller(drop)

        # -- the program ---------------------------------------------------
        def _load_startup_program(self) -> None:
            self.emulator = self._build_emulator()
            if self.program_path is not None and not self.emulator.loaded:
                self._hint = self.emulator.error
            elif self.emulator.loaded:
                self.emulator.start()
            if self._start_paused:
                self.emulator.pause()
            self._snapshot = self.emulator.snapshot()
            self._speed = self.emulator.speed
            self._refresh_labels(self.emulator.state_name)

        def _build_emulator(self):
            emulator = (
                VipEmulator(seed=self.seed)
                if self.platform == "vip"
                else Chip8Emulator(seed=self.seed)
            )
            if self.platform == "vip":
                emulator.set_screen_view(self.screen_view)
            if self.program_path is not None:
                emulator.load_file(str(self.program_path))
            emulator.set_speed(self._speed)
            return emulator

        def _rebuild(self, *, start: bool | None = None) -> None:
            if self.emulator is not None and start is None:
                start = self.emulator.running
            self.emulator = self._build_emulator()
            self.emulator.set_speed(self._speed)
            if start:
                self.emulator.start()
            self._snapshot = self.emulator.snapshot()
            self._hint = self.emulator.error
            self._refresh_labels(self.emulator.state_name)
            self.drawing.queue_draw()

        def _open_dialog(self) -> None:
            dialog = Gtk.FileDialog(title="Open a CHIP-8 program")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            programs = Gtk.FileFilter()
            programs.set_name("CHIP-8 programs")
            programs.add_pattern("*.ch8")
            filters.append(programs)
            everything = Gtk.FileFilter()
            everything.set_name("All files")
            everything.add_pattern("*")
            filters.append(everything)
            dialog.set_filters(filters)
            dialog.set_default_filter(programs)
            dialog.open(self, None, self._opened)

        def _opened(self, dialog, result) -> None:
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return
            path = file.get_path()
            if path:
                self._open_path(Path(path))

        def _open_path(self, path: Path) -> None:
            self.program_path = path
            self._hint = ""
            self._speed = (
                self._header_speed(path) if not self._explicit_speed else self._speed
            )
            if self._dropdown is not None:
                matches = [Path(entry.path) == path for entry in self._catalogue]
                if not any(matches):
                    self._dropdown.set_selected(0)
            self._rebuild(start=True)

        def _header_speed(self, path: Path) -> int:
            try:
                return librarian.describe(str(path)).speed
            except Chip8Error:
                return 10

        def _on_catalogue(self, dropdown, _parameter) -> None:
            index = dropdown.get_selected() - 1
            if 0 <= index < len(self._catalogue):
                self._open_path(Path(self._catalogue[index].path))

        def _reload(self) -> None:
            if self.program_path is not None:
                self._rebuild(start=True)

        def _set_platform(self, platform: str) -> None:
            if platform == self.platform:
                return
            self.platform = platform
            if platform == "native":
                self.screen_view = "chip8"
            self._rebuild()

        def _set_screen(self, screen: str) -> None:
            if screen == self.screen_view:
                return
            if screen == "vip" and self.platform != "vip":
                self._hint = "The 64x128 display belongs to the COSMAC VIP."
                return
            self.screen_view = screen
            if self.emulator is not None and self.platform == "vip":
                self.emulator.set_screen_view(screen)
                self._snapshot = self.emulator.snapshot()
                self.drawing.queue_draw()

        def _dropped(self, _target, value, _x, _y) -> bool:
            path = value.get_path()
            if not path:
                return False
            self._open_path(Path(path))
            return True

        def _new_blank(self) -> None:
            """A blank machine: zeros at 0x200, ready for the monitor."""

            if self.emulator is None:
                return
            self.program_path = None
            if self._dropdown is not None:
                self._dropdown.set_selected(0)
            if not self.emulator.load_bytes(b"\x00\x00", "blank machine"):
                self._hint = self.emulator.error
                return
            self.emulator.pause()
            self._speed = self.emulator.speed
            self._snapshot = self.emulator.snapshot()
            self._addr_entry.set_text("0200")
            self._hint = (
                "blank machine: write bytes at 0200, set PC to 0200, then Run"
            )
            self._refresh_labels(self.emulator.state_name)
            self.drawing.queue_draw()

        # -- the keypad and the monitor ------------------------------------
        def _pad_down(self, key: int) -> None:
            if self.emulator is not None:
                self.emulator.set_key(key, True)

        def _pad_up(self, key: int) -> None:
            if self.emulator is not None:
                self.emulator.set_key(key, False)

        def _write_byte(self) -> None:
            address = hex_value(self._addr_entry.get_text(), 0xFFF)
            value = hex_value(self._byte_entry.get_text(), 0xFF)
            if self.emulator is None or not self.emulator.loaded:
                self._hint = "open a program or start a blank machine first"
                return
            if address is None or value is None:
                self._hint = "address must be 000-FFF and the byte 00-FF"
                return
            self.emulator.pause()
            if not self.emulator.write_memory(address, value):
                self._hint = f"0x{address:03X} is not writable"
                return
            self._hint = ""
            if address < 0xFFF:
                self._addr_entry.set_text(f"{address + 1:03X}")
            self._snapshot = self.emulator.snapshot()
            self._refresh_labels(self.emulator.state_name)
            self.drawing.queue_draw()

        def _set_pc(self) -> None:
            address = hex_value(self._addr_entry.get_text(), 0xFFF)
            if address is None:
                self._hint = "address must be 000-FFF"
                return
            if self.emulator is None or not self.emulator.loaded:
                self._hint = "open a program or start a blank machine first"
                return
            self.emulator.pause()
            if not self.emulator.set_pc(address):
                self._hint = f"0x{address:03X} is not an address"
                return
            self._hint = ""
            self._snapshot = self.emulator.snapshot()
            self._refresh_labels(self.emulator.state_name)
            self.drawing.queue_draw()

        def _refresh_dump(self) -> None:
            dump = getattr(self, "_dump", None)
            if dump is None:
                return
            emulator = self.emulator
            if emulator is None or not emulator.loaded:
                dump.set_text("")
                return
            base = hex_value(self._addr_entry.get_text(), 0xFFF)
            if base is None:
                return
            base &= 0xFFF8
            lines = []
            for row in range(8):
                start = base + row * 8
                if start > 0xFFF:
                    break
                count = min(8, 0x1000 - start)
                values = " ".join(
                    f"{emulator.read_memory(start + offset):02X}"
                    for offset in range(count)
                )
                lines.append(f"{start:03X}  {values}")
            dump.set_text("\n".join(lines))

        # -- transport -----------------------------------------------------
        def _toggle(self) -> None:
            self.emulator.toggle()

        def _step(self) -> None:
            self.emulator.pause()
            self.emulator.step()

        def _reset(self) -> None:
            self._hint = ""
            self.emulator.reset()

        def _rewind(self, delta: int) -> None:
            self.emulator.rewind_by(delta)

        def _nudge_speed(self, delta: int) -> None:
            self.emulator.nudge_speed(delta)

        def _about(self) -> None:
            about = Gtk.AboutDialog(
                transient_for=self,
                modal=True,
                program_name="COSMAC VIP",
                version=VERSION,
                comments=(
                    "CHIP-8 on an emulated COSMAC VIP: the 1802, the display in "
                    "memory, the row-scanned keypad, and the 1977 interpreter "
                    "running as 1802 firmware."
                ),
                website="https://github.com/James-HoneyBadger/CHIP-8",
                license_type=Gtk.License.MIT_X11,
            )
            about.present()

        # -- keyboard ------------------------------------------------------
        def _key_down(self, _controller, keyval, _keycode, state) -> bool:
            focus = self.get_focus()
            if isinstance(focus, (Gtk.Entry, Gtk.Text)):
                return False
            modifiers = state & (
                Gdk.ModifierType.CONTROL_MASK
                | Gdk.ModifierType.ALT_MASK
                | Gdk.ModifierType.SUPER_MASK
            )
            if modifiers:
                return False
            key = keypad.get(keyval)
            if key is not None:
                self.emulator.set_key(key, True)
                return True
            if keyval == Gdk.KEY_p:
                self._toggle()
            elif keyval in (Gdk.KEY_space, Gdk.KEY_o):
                self._step()
            elif keyval == Gdk.KEY_R:
                self._reset()
            elif keyval == Gdk.KEY_bracketleft:
                self._rewind(-1)
            elif keyval == Gdk.KEY_bracketright:
                self._rewind(1)
            elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal):
                self._nudge_speed(1)
            elif keyval == Gdk.KEY_minus:
                self._nudge_speed(-1)
            else:
                return False
            return True

        def _key_up(self, _controller, keyval, _keycode, _state) -> bool:
            key = keypad.get(keyval)
            if key is None:
                return False
            self.emulator.set_key(key, False)
            return True

        def _on_active(self, *_args) -> None:
            if not self.is_active() and self.emulator is not None:
                self.emulator.release_all_keys()

        # -- the frame -----------------------------------------------------
        def _tick(self) -> bool:
            if self._closed or self.emulator is None:
                return GLib.SOURCE_REMOVE
            self.emulator.tick()
            self._snapshot = self.emulator.snapshot()
            self._refresh_labels(self.emulator.state_name)
            self.drawing.queue_draw()
            return GLib.SOURCE_CONTINUE

        def _refresh_labels(self, state: str) -> None:
            snapshot = self._snapshot
            if snapshot is None:
                return
            self._run_button.set_label("Pause" if snapshot.running else "Run")
            name = snapshot.name or "no program"
            title = f"COSMAC VIP \u00b7 {name} \u00b7 {state}"
            if self._last.get("title") != title:
                self._last["title"] = title
                self.set_title(title)
                self._title.set_text(title)
            status = (
                f"{state}  \u00b7  PC 0x{snapshot.pc:03X}  "
                f"I 0x{snapshot.i:03X}  \u00b7  "
                f"DT {snapshot.delay_timer}  ST {snapshot.sound_timer}  \u00b7  "
                f"{snapshot.cycles} cycles  \u00b7  {snapshot.speed} opcodes/frame"
            )
            self._text(self._status, status)
            for index, label in enumerate(self._v_labels):
                self._text(label, f"{snapshot.v[index]:02X}")
            self._text(self._misc["I"], f"0x{snapshot.i:03X}")
            self._text(self._misc["PC"], f"0x{snapshot.pc:03X}")
            self._text(self._misc["DT"], str(snapshot.delay_timer))
            self._text(self._misc["ST"], str(snapshot.sound_timer))
            self._text(self._misc["cycles"], str(snapshot.cycles))
            self._text(self._misc["speed"], str(snapshot.speed))
            for key, button in self._key_buttons.items():
                held = bool(snapshot.keys & (1 << key))
                if held and not button.has_css_class("held"):
                    button.add_css_class("held")
                elif not held and button.has_css_class("held"):
                    button.remove_css_class("held")
            if self.emulator is not None and self.platform == "vip":
                cpu = self.emulator.vip.cpu
                self._text(
                    self._cpu,
                    f"D {cpu.d:02X}  DF {cpu.df}  Q {cpu.q}  IE {cpu.ie}  "
                    f"P {cpu.p}  X {cpu.x}  \u00b7  1802 {cpu.instructions}",
                )
            else:
                self._text(self._cpu, "")
            self._refresh_dump()
            notes = (
                snapshot.error
                or self._hint
                or "\n".join(getattr(self.emulator, "notes", ()))
            )
            self._text(self._notes, notes)

        @staticmethod
        def _text(label, text: str) -> None:
            if label.get_text() != text:
                label.set_text(text)

        # -- painting ------------------------------------------------------
        def _draw(self, _area, cr, width: int, height: int) -> None:
            cr.set_source_rgb(*(channel / 255 for channel in OFF_COLOR))
            cr.paint()
            snapshot = self._snapshot
            if snapshot is None or not snapshot.loaded:
                cr.set_source_rgb(0.545, 0.573, 0.596)
                cr.select_font_face("monospace")
                cr.set_font_size(14)
                cr.move_to(24, 40)
                cr.show_text("Open a program (Ctrl+O), or drop a .ch8 here")
                return
            columns = snapshot.screen_width
            rows = snapshot.screen_height
            scale = max(1, int(min(width / columns, height / rows)))
            left = max(0, (width - columns * scale) // 2)
            top = max(0, (height - rows * scale) // 2)
            cr.set_source_rgb(*(channel / 255 for channel in ON_COLOR))
            for index, lit in enumerate(snapshot.framebuffer):
                if lit:
                    cr.rectangle(
                        left + (index % columns) * scale,
                        top + (index // columns) * scale,
                        scale,
                        scale,
                    )
            if snapshot.framebuffer:
                cr.fill()

        def _on_close(self, *_args) -> bool:
            self._closed = True
            if self.emulator is not None:
                self.emulator.release_all_keys()
            return False

    application = Gtk.Application(
        application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    application.set_accels_for_action("win.open", ["<Primary>o"])
    application.set_accels_for_action("win.reload", ["<Primary>r"])
    application.set_accels_for_action("win.quit", ["<Primary>q"])
    application.set_accels_for_action("win.toggle", ["<Primary>p"])
    application.set_accels_for_action("win.step", ["<Primary>s"])

    def activate(app) -> None:
        window = VipWindow(app)
        window.present()

    application.connect("activate", activate)
    return application.run([])


if __name__ == "__main__":  # pragma: no cover - the application itself
    raise SystemExit(main())
