"""The terminal frontend: the screen as half-block characters.

Two CHIP-8 pixels share one character cell -- the upper half block carries the
top pixel in its foreground colour and the bottom pixel in its background -- so
the 64x32 screen is 64 columns by 16 rows and fits a normal terminal with room
for a status line.

The keypad is the original one, so the finger positions the books show still
work. A terminal reports presses but not releases, so a key is held for
:data:`KEY_HOLD` seconds and then released: enough for a game to notice, short
enough that nothing sticks.
"""

from __future__ import annotations

import os
import select
import shutil
import sys
import termios
import time
import tty
from dataclasses import dataclass

from .constants import FRAMES_PER_SECOND
from .emulator import Chip8Emulator, Chip8Snapshot, FrameClock, SourceWatcher
from .screen import Palette, render_display
from .inspector import (
    INSPECTOR_WIDTH,
    listing_text,
    memory_text,
    registers_text,
    status_text,
)

#: How long a key press from a terminal counts as held, in seconds.
KEY_HOLD = 0.12

#: PC key -> CHIP-8 key, in the original keypad's layout:
#:
#:     1 2 3 C        1 2 3 4
#:     4 5 6 D   <=   Q W E R
#:     7 8 9 E        A S D F
#:     A 0 B F        Z X C V
KEYPAD = {
    "1": 0x1, "2": 0x2, "3": 0x3, "4": 0xC,
    "q": 0x4, "w": 0x5, "e": 0x6, "r": 0xD,
    "a": 0x7, "s": 0x8, "d": 0x9, "f": 0xE,
    "z": 0xA, "x": 0x0, "c": 0xB, "v": 0xF,
}

HELP_LINES = (
    "p run/pause \u00b7 o or space step \u00b7 R reset \u00b7 i inspector \u00b7 m memory",
    "[ ] rewind \u00b7 + - speed \u00b7 ESC quit",
)


def detect_color_mode(stream=None) -> str:
    """Truecolor when the terminal says so, 256 colours otherwise."""

    stream = sys.stdout if stream is None else stream
    if not getattr(stream, "isatty", lambda: False)():
        return "ascii"
    colorterm = os.environ.get("COLORTERM", "").lower()
    if "truecolor" in colorterm or "24bit" in colorterm:
        return "truecolor"
    if os.environ.get("TERM", "").lower() in ("", "dumb"):
        return "ascii"
    return "256"


class TerminalSession:
    """Raw input and an alternate screen, restored however the loop ends."""

    def __init__(self) -> None:
        self._attributes = None
        self._active = False

    def __enter__(self) -> "TerminalSession":
        if sys.stdin.isatty():
            self._attributes = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        # The alternate screen keeps the user's scrollback where they left it.
        sys.stdout.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        sys.stdout.flush()
        self._active = True
        return self

    def __exit__(self, *_: object) -> None:
        if not self._active:
            return
        sys.stdout.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()
        if self._attributes is not None:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._attributes)
        self._active = False

    def read(self, timeout: float) -> list[str]:
        """Characters typed within *timeout* seconds; arrow keys are ignored."""

        if not sys.stdin.isatty():
            time.sleep(max(0.0, timeout))
            return []
        ready, _, _ = select.select([sys.stdin], [], [], max(0.0, timeout))
        if not ready:
            return []
        data = os.read(sys.stdin.fileno(), 64)
        if data == b"\x1b":
            # A bare escape is the quit key; an escape sequence is an arrow key
            # and means nothing here.
            if select.select([sys.stdin], [], [], 0.02)[0]:
                os.read(sys.stdin.fileno(), 64)
                return []
            return ["\x1b"]
        return list(data.decode("utf-8", "ignore"))


class TerminalFrontend:
    """Draws the machine and feeds it the keypad."""

    def __init__(
        self,
        emulator: Chip8Emulator,
        *,
        scale: int = 1,
        color: str = "auto",
        mute: bool = False,
        inspector: bool = False,
        memory: bool = False,
        fps: int = 30,
        watch: str | None = None,
    ) -> None:
        self.emulator = emulator
        self.scale = max(1, scale)
        self.palette = Palette(detect_color_mode() if color == "auto" else color)
        self.mute = mute
        self.show_inspector = inspector
        self.show_memory = memory
        self.fps = max(1, fps)
        self.running = True
        self._held: dict[int, float] = {}
        self._watcher = SourceWatcher(watch) if watch else None
        self._last_bell = 0.0
        self._dirty = True

    # -- input ------------------------------------------------------------
    def handle(self, character: str) -> None:
        emulator = self.emulator
        if character == "\x1b":
            self.running = False
            return
        key = KEYPAD.get(character)
        if key is not None:
            emulator.set_key(key, True)
            self._held[key] = time.monotonic() + KEY_HOLD
            self._dirty = True
            return
        if character == "p":
            emulator.toggle()
        elif character in ("o", " "):
            emulator.pause()
            emulator.step()
        elif character == "R":
            emulator.reset()
        elif character == "i":
            self.show_inspector = not self.show_inspector
        elif character == "m":
            self.show_memory = not self.show_memory
        elif character == "[":
            emulator.rewind_by(-1)
        elif character == "]":
            emulator.rewind_by(1)
        elif character in ("+", "="):
            emulator.nudge_speed(1)
        elif character in ("-", "_"):
            emulator.nudge_speed(-1)
        self._dirty = True

    def _reload_if_changed(self, now: float) -> None:
        """`chip8 dev`: the file was saved, so assemble it again.

        A listing that does not assemble leaves the running program alone and
        puts the error on the status line, which is the same contract the Qt
        window in Time Warp Studio has.
        """

        if self._watcher is None or not self._watcher.poll(now):
            return
        was_running = self.emulator.running
        if self.emulator.load_file(self._watcher.path):
            if was_running:
                self.emulator.start()
        self._dirty = True

    def _expire_keys(self, now: float) -> None:
        for key, deadline in list(self._held.items()):
            if now >= deadline:
                self.emulator.set_key(key, False)
                del self._held[key]
                self._dirty = True

    # -- drawing ----------------------------------------------------------
    def panels(self, snapshot: Chip8Snapshot) -> list[list[str]]:
        """The inspector blocks, in the order they are worth showing.

        Registers come first because they are what a paused machine is being
        asked about; memory last, because it is the page viewer.
        """

        blocks: list[list[str]] = []
        if self.show_inspector:
            blocks.append(registers_text(snapshot))
            listing = listing_text(snapshot, self.emulator.program, self.emulator.source)
            if listing:
                blocks.append(listing)
        if self.show_memory:
            blocks.append(memory_text(snapshot))
        return blocks

    def sections(
        self, snapshot: Chip8Snapshot, columns: int, rows: int | None = None
    ) -> tuple[list[str], str]:
        """The screen and inspector, and the status line that goes under them."""

        display = render_display(snapshot, self.scale, self.palette)
        status = status_text(snapshot, self.emulator.state_name)
        if snapshot.error:
            status += f" \u2014 {snapshot.error}"

        blocks = self.panels(snapshot)
        if not blocks:
            return display, status

        panels: list[str] = []
        for block in blocks:
            if panels:
                panels.append("")
            panels.extend(block)

        # Beside the screen when there is width for both, which is what the
        # window version of this does with its tabs.
        display_width = max((_visible_width(line) for line in display), default=0)
        if display_width + 2 + INSPECTOR_WIDTH <= columns:
            height = max(len(display), len(panels))
            padded = display + [""] * (height - len(display))
            panels += [""] * (height - len(panels))
            return [
                f"{_pad(line, display_width)}  {panel}"
                for line, panel in zip(padded, panels)
            ], status

        # Under the screen when the terminal is tall enough for both. A short
        # terminal gets the inspector in place of the screen rather than half a
        # panel -- asking for the registers is asking to look at them.
        if rows is None or len(display) + len(panels) + 4 <= rows:
            return display + [""] + panels, status
        return [f"inspector (i: {len(display)} rows of screen hidden)"] + panels, status

    def compose(
        self, snapshot: Chip8Snapshot, columns: int, rows: int | None = None
    ) -> list[str]:
        body, status = self.sections(snapshot, columns, rows)
        return body + ["", status, *HELP_LINES]

    def draw(self) -> None:
        snapshot = self.emulator.snapshot()
        columns, rows = shutil.get_terminal_size((80, 24))
        body, status = self.sections(snapshot, columns, rows)
        lines = body + ["", status, *HELP_LINES]
        if len(lines) > rows:
            # The help footer goes first, then whatever is left of the panel;
            # the status line is the last thing to be dropped.
            lines = body + ["", status]
        if len(lines) > rows:
            keep = max(0, rows - 2)
            lines = body[:keep] + ["", status]
        lines = lines[:rows]
        out = ["\x1b[H"]
        for line in lines:
            out.append(line)
            out.append("\x1b[K\r\n")
        out.append("\x1b[J")
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self._dirty = False

    # -- the loop ---------------------------------------------------------
    def run(self, start_running: bool = True) -> int:
        if start_running:
            self.emulator.start()
        try:
            with TerminalSession() as session:
                clock = FrameClock(FRAMES_PER_SECOND)
                repaint = FrameClock(self.fps)
                while self.running:
                    now = time.monotonic()
                    for _ in range(clock.frames_due(now)):
                        self.emulator.tick()
                        self._sound()
                    self._expire_keys(now)
                    self._reload_if_changed(now)
                    wait = min(clock.sleep_for(now), repaint.sleep_for(now))
                    for character in session.read(wait):
                        self.handle(character)
                        if not self.running:
                            break
                    if self._dirty or repaint.frames_due():
                        self.draw()
        except KeyboardInterrupt:
            pass
        finally:
            # Whatever ended the loop, the machine must not be left believing a
            # key is held.
            self.emulator.release_all_keys()
        return 0

    def _sound(self) -> None:
        snapshot = self.emulator.machine
        if self.mute or snapshot is None or snapshot.sound_timer == 0:
            return
        now = time.monotonic()
        # One beep every eighth of a second: a square wave, not a rattle.
        if now - self._last_bell >= 0.125:
            self._last_bell = now
            sys.stdout.write("\a")
            sys.stdout.flush()


def _visible_width(line: str) -> int:
    """How many columns a line of escape-decorated text occupies."""

    width = 0
    in_escape = False
    for character in line:
        if in_escape:
            if character.isalpha():
                in_escape = False
            continue
        if character == "\x1b":
            in_escape = True
            continue
        width += 1
    return width


def _pad(line: str, width: int) -> str:
    return line + "\x1b[0m" + " " * (width - _visible_width(line))
