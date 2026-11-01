"""The X11 frontend: a real window, through libX11 and nothing else.

The reference implementation draws its screen in Qt. This one has to do without
a toolkit -- the standard library has no way to open a window -- so it talks to
Xlib through :mod:`ctypes` and puts a framebuffer on it with ``XPutImage``. The
pixels are built by hand, which is the only expensive part and is still cheap at
640x320.

What the window offers is what the Qt one offers: the screen at an integer
scale, the keypad on the original keys, and function keys for the transport.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import select
import sys
import time
from dataclasses import dataclass, field

from .constants import FRAMES_PER_SECOND, OFF_COLOR, ON_COLOR
from .emulator import Chip8Emulator, Chip8Snapshot, FrameClock, SourceWatcher

#: X11 protocol constants.
_KEY_PRESS = 2
_KEY_RELEASE = 3
_FOCUS_OUT = 9
_EXPOSE = 12
_CLIENT_MESSAGE = 33
_STRUCTURE_NOTIFY = 1 << 17
_KEY_PRESS_MASK = 1 << 0
_KEY_RELEASE_MASK = 1 << 1
_EXPOSURE_MASK = 1 << 15
_ZPIXMAP = 2
_LSB_FIRST = 0

#: Keysyms: the unshifted character keys, and the function keys.
_XK_ESCAPE = 0xFF1B
_XK_SPACE = 0x0020
_XK_F5 = 0xFFC2
_XK_F6 = 0xFFC3
_XK_F7 = 0xFFC4
_XK_F8 = 0xFFC5
_XK_F9 = 0xFFC6
_XK_F10 = 0xFFC7
_XK_F11 = 0xFFC8
_XK_BRACKET_LEFT = 0x005B
_XK_BRACKET_RIGHT = 0x005D
_XK_MINUS = 0x002D
_XK_EQUAL = 0x003D
_XK_PLUS = 0x002B

#: Keysym -> CHIP-8 key, in the original keypad's layout:
#:
#:     1 2 3 C      1 2 3 4
#:     4 5 6 D  <=  Q W E R
#:     7 8 9 E      A S D F
#:     A 0 B F      Z X C V
KEYPAD = {
    ord("1"): 0x1, ord("2"): 0x2, ord("3"): 0x3, ord("4"): 0xC,
    ord("q"): 0x4, ord("w"): 0x5, ord("e"): 0x6, ord("r"): 0xD,
    ord("a"): 0x7, ord("s"): 0x8, ord("d"): 0x9, ord("f"): 0xE,
    ord("z"): 0xA, ord("x"): 0x0, ord("c"): 0xB, ord("v"): 0xF,
}


class X11Unavailable(RuntimeError):
    """No display, or no Xlib to talk to it with."""


@dataclass
class PressTracker:
    """One press is one action.

    A held key repeats: the server sends ``KeyPress`` again and again, with or
    without detectable auto-repeat. Acting on each repeat would make ``p``
    oscillate and turn one press of ``i`` into a strobe, so a press only counts
    when its release has been seen. A keypad key is state rather than an event,
    so it loses nothing here.
    """

    held: set[int] = field(default_factory=set)

    def press(self, keycode: int) -> bool:
        """True the first time *keycode* is pressed, False while it is held."""

        if keycode in self.held:
            return False
        self.held.add(keycode)
        return True

    def release(self, keycode: int) -> None:
        self.held.discard(keycode)

    def clear(self) -> None:
        self.held.clear()


class _XKeyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("root", ctypes.c_ulong),
        ("subwindow", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("x_root", ctypes.c_int),
        ("y_root", ctypes.c_int),
        ("state", ctypes.c_uint),
        ("keycode", ctypes.c_uint),
        ("same_screen", ctypes.c_int),
    ]


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_ulong * 5),
    ]


class _XConfigureEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("event", ctypes.c_ulong),
        ("window", ctypes.c_ulong),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("border_width", ctypes.c_int),
        ("above", ctypes.c_ulong),
        ("override_redirect", ctypes.c_int),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [
        ("type", ctypes.c_int),
        ("xkey", _XKeyEvent),
        ("xclient", _XClientMessageEvent),
        ("xconfigure", _XConfigureEvent),
        # XEvent is a union of 24 longs whatever the member is.
        ("pad", ctypes.c_long * 24),
    ]


class _XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong),
        ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong),
        ("obdata", ctypes.c_void_p),
        ("funcs", ctypes.c_void_p * 6),
    ]


def load_xlib() -> ctypes.CDLL:
    """libX11, with every signature spelled out: a missing ``argtypes`` would
    truncate window handles to 32 bits and crash on the first call."""

    library = ctypes.util.find_library("X11")
    if library is None:
        raise X11Unavailable("libX11 was not found")
    xlib = ctypes.CDLL(library)
    pointer = ctypes.c_void_p
    ulong = ctypes.c_ulong
    signatures = {
        "XOpenDisplay": ([ctypes.c_char_p], pointer),
        "XCloseDisplay": ([pointer], ctypes.c_int),
        "XDefaultScreen": ([pointer], ctypes.c_int),
        "XRootWindow": ([pointer, ctypes.c_int], ulong),
        "XDefaultVisual": ([pointer, ctypes.c_int], pointer),
        "XDefaultDepth": ([pointer, ctypes.c_int], ctypes.c_int),
        "XDefaultGC": ([pointer, ctypes.c_int], pointer),
        "XCreateSimpleWindow": (
            [pointer, ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint,
             ctypes.c_uint, ulong, ulong],
            ulong,
        ),
        "XStoreName": ([pointer, ulong, ctypes.c_char_p], ctypes.c_int),
        "XSelectInput": ([pointer, ulong, ctypes.c_long], ctypes.c_int),
        "XMapWindow": ([pointer, ulong], ctypes.c_int),
        "XUnmapWindow": ([pointer, ulong], ctypes.c_int),
        "XResizeWindow": ([pointer, ulong, ctypes.c_uint, ctypes.c_uint], ctypes.c_int),
        "XFlush": ([pointer], ctypes.c_int),
        "XSync": ([pointer, ctypes.c_int], ctypes.c_int),
        "XInternAtom": ([pointer, ctypes.c_char_p, ctypes.c_int], ulong),
        "XSetWMProtocols": ([pointer, ulong, ctypes.POINTER(ulong), ctypes.c_int], ctypes.c_int),
        "XCreateImage": (
            [pointer, pointer, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_char_p,
             ctypes.c_uint, ctypes.c_uint, ctypes.c_int, ctypes.c_int],
            ctypes.POINTER(_XImage),
        ),
        "XCreateGC": ([pointer, ulong, ctypes.c_ulong, pointer], pointer),
        "XFreeGC": ([pointer, pointer], ctypes.c_int),
        "XPutImage": (
            [pointer, ulong, pointer, ctypes.POINTER(_XImage), ctypes.c_int, ctypes.c_int,
             ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint],
            ctypes.c_int,
        ),
        "XDestroyImage": ([ctypes.POINTER(_XImage)], ctypes.c_int),
        "XPending": ([pointer], ctypes.c_int),
        "XNextEvent": ([pointer, ctypes.POINTER(_XEvent)], ctypes.c_int),
        "XLookupKeysym": ([ctypes.POINTER(_XKeyEvent), ctypes.c_int], ulong),
        "XConnectionNumber": ([pointer], ctypes.c_int),
        "XkbSetDetectableAutoRepeat": (
            [pointer, ctypes.c_int, ctypes.POINTER(ctypes.c_int)],
            ctypes.c_int,
        ),
        "XBell": ([pointer, ctypes.c_int], ctypes.c_int),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(xlib, name, None)
        if function is None:  # pragma: no cover - ancient Xlib
            continue
        function.argtypes = arguments
        function.restype = result
    return xlib


def available() -> bool:
    """Whether a window could be opened at all."""

    try:
        xlib = load_xlib()
    except (X11Unavailable, OSError):
        return False
    display = xlib.XOpenDisplay(None)
    if not display:
        return False
    xlib.XCloseDisplay(display)
    return True


def _channel(colour: int, mask: int) -> int:
    """Spread an 8-bit channel across a visual's mask, exactly."""

    if mask == 0:
        return 0
    return (colour * mask) // 255 & mask


@dataclass(frozen=True)
class PixelFormat:
    """How one pixel goes into the X server's image, read back from the image."""

    red_mask: int
    green_mask: int
    blue_mask: int
    bits_per_pixel: int
    byte_order: int

    @property
    def bytes_per_pixel(self) -> int:
        return max(1, self.bits_per_pixel // 8)

    def pack(self, colour: tuple[int, int, int]) -> bytes:
        value = (
            _channel(colour[0], self.red_mask)
            | _channel(colour[1], self.green_mask)
            | _channel(colour[2], self.blue_mask)
        )
        order = "little" if self.byte_order == _LSB_FIRST else "big"
        return value.to_bytes(self.bytes_per_pixel, order)


def frame_buffer(
    snapshot: Chip8Snapshot,
    scale: int,
    pixel_format: PixelFormat,
    bytes_per_line: int | None = None,
) -> bytes:
    """The framebuffer as the X server wants it: scaled, packed, row-padded.

    Pure, so a test can check the pixels without opening a window.
    """

    width = snapshot.screen_width
    height = snapshot.screen_height
    scale = max(1, scale)
    on = pixel_format.pack(ON_COLOR)
    off = pixel_format.pack(OFF_COLOR)
    framebuffer = snapshot.framebuffer

    rows: list[bytes] = []
    for row in range(height):
        start = row * width
        pixels = [
            (on if framebuffer[start + column] else off) * scale
            for column in range(width)
        ]
        line = b"".join(pixels)
        if bytes_per_line and len(line) < bytes_per_line:
            line += b"\x00" * (bytes_per_line - len(line))
        rows.extend([line] * scale)
    return b"".join(rows)


class X11Frontend:
    """A window holding the screen, with the transport on function keys."""

    def __init__(
        self,
        emulator: Chip8Emulator,
        *,
        scale: int = 10,
        mute: bool = False,
        inspector: bool = False,
    ) -> None:
        self.emulator = emulator
        self.scale = max(1, scale)
        self.mute = mute
        self.show_inspector = inspector
        self.running = True
        #: A listing to reload when it is saved; `chip8 dev` sets this.
        self.watch: str | None = None
        self._watcher: SourceWatcher | None = None
        self._presses = PressTracker()
        self._last_bell = 0.0
        self._last_title = ""
        self._dirty = True

        self.xlib = load_xlib()
        self.display = self.xlib.XOpenDisplay(None)
        if not self.display:
            raise X11Unavailable(
                "cannot open an X display (is DISPLAY set, and is there a server?)"
            )
        self.screen = self.xlib.XDefaultScreen(self.display)
        self.root = self.xlib.XRootWindow(self.display, self.screen)
        self.visual = self.xlib.XDefaultVisual(self.display, self.screen)
        self.depth = self.xlib.XDefaultDepth(self.display, self.screen)

        snapshot = emulator.snapshot()
        width, height = self._window_size(snapshot)
        self.window = self.xlib.XCreateSimpleWindow(
            self.display, self.root, 0, 0, width, height, 1, 0, 0
        )
        self.gc = self.xlib.XCreateGC(self.display, self.window, 0, None)
        self.wm_delete = self.xlib.XInternAtom(self.display, b"WM_DELETE_WINDOW", 0)
        atoms = (ctypes.c_ulong * 1)(self.wm_delete)
        self.xlib.XSetWMProtocols(self.display, self.window, atoms, 1)
        self.xlib.XSelectInput(
            self.display,
            self.window,
            _KEY_PRESS_MASK | _KEY_RELEASE_MASK | _EXPOSURE_MASK | _STRUCTURE_NOTIFY,
        )
        self.xlib.XMapWindow(self.display, self.window)
        self.xlib.XFlush(self.display)

        # Without this every held key arrives as a stream of presses and games
        # that poll the keypad see a key that never lets go.
        self._detectable_repeat = ctypes.c_int(0)
        self.xlib.XkbSetDetectableAutoRepeat(
            self.display, 1, ctypes.byref(self._detectable_repeat)
        )
        self._connection = self.xlib.XConnectionNumber(self.display)
        self._image = None
        self._size = (0, 0)
        self._make_image(snapshot)

    # -- window plumbing ---------------------------------------------------
    def _window_size(self, snapshot: Chip8Snapshot) -> tuple[int, int]:
        return snapshot.screen_width * self.scale, snapshot.screen_height * self.scale

    def _free_image(self) -> None:
        """Give the old image and its buffer back before making a new one.

        The buffer is ours -- XCreateImage may leave `data` to the caller -- so
        the pointer is unhooked before the image is destroyed: XDestroyImage
        would otherwise free memory Python owns.
        """

        if self._image is None:
            return
        self._image.contents.data = None
        self.xlib.XDestroyImage(self._image)
        self._image = None
        self._pixels = None

    def _make_image(self, snapshot: Chip8Snapshot) -> None:
        width, height = self._window_size(snapshot)
        image = self.xlib.XCreateImage(
            self.display,
            self.visual,
            self.depth,
            _ZPIXMAP,
            0,
            None,
            width,
            height,
            32,
            0,
        )
        contents = image.contents
        # XCreateImage may leave `data` to the caller (libX11 built on XCB does),
        # so the buffer is ours: allocated here, kept alive here, and handed to
        # XPutImage through the image's pointer.
        self._pixels = ctypes.create_string_buffer(contents.bytes_per_line * height)
        contents.data = ctypes.cast(self._pixels, ctypes.c_void_p)
        self._image = image
        self._size = (width, height)
        self._format = PixelFormat(
            red_mask=contents.red_mask,
            green_mask=contents.green_mask,
            blue_mask=contents.blue_mask,
            bits_per_pixel=contents.bits_per_pixel,
            byte_order=contents.byte_order,
        )
        self._bytes_per_line = contents.bytes_per_line

    def _resize(self, snapshot: Chip8Snapshot) -> None:
        width, height = self._window_size(snapshot)
        if (width, height) == self._size:
            return
        self._free_image()
        self._make_image(snapshot)
        self.xlib.XResizeWindow(self.display, self.window, width, height)
        self.xlib.XFlush(self.display)

    def close(self) -> None:
        self._free_image()
        if self.gc:
            self.xlib.XFreeGC(self.display, self.gc)
            self.gc = None
        if self.display:
            self.xlib.XUnmapWindow(self.display, self.window)
            self.xlib.XCloseDisplay(self.display)
            self.display = None

    def __enter__(self) -> "X11Frontend":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- drawing -----------------------------------------------------------
    def draw(self) -> None:
        snapshot = self.emulator.snapshot()
        self._resize(snapshot)
        data = frame_buffer(snapshot, self.scale, self._format, self._bytes_per_line)
        ctypes.memmove(self._image.contents.data, data, len(data))
        self.xlib.XPutImage(
            self.display,
            self.window,
            self.gc,
            self._image,
            0,
            0,
            0,
            0,
            self._size[0],
            self._size[1],
        )
        self.xlib.XFlush(self.display)
        self._update_title(snapshot)
        self._dirty = False

    def _update_title(self, snapshot: Chip8Snapshot) -> None:
        state = self.emulator.state_name
        title = (
            f"CHIP-8 \u00b7 {snapshot.name or 'no program'} \u00b7 {state} "
            f"\u00b7 {snapshot.speed} opcodes/frame"
        )
        if title == self._last_title:
            return
        self._last_title = title
        self.xlib.XStoreName(self.display, self.window, title.encode("utf-8"))
        self.xlib.XFlush(self.display)

    # -- input -------------------------------------------------------------
    def handle_key(self, keysym: int, pressed: bool) -> None:
        key = KEYPAD.get(keysym)
        if key is not None:
            self.emulator.set_key(key, pressed)
            self._dirty = True
            return
        if not pressed:
            return
        emulator = self.emulator
        if keysym == _XK_ESCAPE:
            self.running = False
        elif keysym in (_XK_F5, ord("p")):
            emulator.toggle()
        elif keysym in (_XK_F6, ord("o")):
            emulator.pause()
            emulator.step()
        elif keysym in (_XK_F7, ord("R")):
            emulator.reset()
        elif keysym == _XK_F8:
            self.show_inspector = not self.show_inspector
            self._print_inspector()
        elif keysym == _XK_F9:
            self._print_memory()
        elif keysym == _XK_F10 or keysym == _XK_BRACKET_LEFT:
            emulator.rewind_by(-1)
            self._print_inspector()
        elif keysym in (_XK_F11, _XK_BRACKET_RIGHT):
            emulator.rewind_by(1)
            self._print_inspector()
        elif keysym in (_XK_PLUS, _XK_EQUAL):
            emulator.nudge_speed(1)
        elif keysym == _XK_MINUS:
            emulator.nudge_speed(-1)
        elif keysym == _XK_SPACE:
            emulator.pause()
            emulator.step()
        self._dirty = True

    def _print_inspector(self) -> None:
        # The window has no room for a second pane without a toolkit, so the
        # inspector goes to the terminal the program was started from.
        from .inspector import registers_text

        for line in registers_text(self.emulator.snapshot()):
            print(line)

    def _print_memory(self) -> None:
        from .inspector import memory_text

        for line in memory_text(self.emulator.snapshot()):
            print(line)

    def _reload_if_changed(self) -> None:
        if self.watch is None:
            self._watcher = None
            return
        if self._watcher is None:
            self._watcher = SourceWatcher(self.watch)
        if not self._watcher.poll(time.monotonic()):
            return
        was_running = self.emulator.running
        if self.emulator.load_file(self.watch):
            if was_running:
                self.emulator.start()
        else:
            print(f"chip8: {self.emulator.error}", file=sys.stderr)
        self._dirty = True

    def pump(self) -> None:
        """Handle every event the server has for us."""

        self._reload_if_changed()
        event = _XEvent()
        while self.xlib.XPending(self.display):
            self.xlib.XNextEvent(self.display, ctypes.byref(event))
            if event.type == _KEY_PRESS:
                if not self._presses.press(event.xkey.keycode):
                    continue
                keysym = self.xlib.XLookupKeysym(ctypes.byref(event.xkey), 0)
                self.handle_key(keysym, True)
            elif event.type == _KEY_RELEASE:
                self._presses.release(event.xkey.keycode)
                if self._detectable_repeat.value:
                    keysym = self.xlib.XLookupKeysym(ctypes.byref(event.xkey), 0)
                    self.handle_key(keysym, False)
            elif event.type == _EXPOSE:
                self._dirty = True
            elif event.type == _FOCUS_OUT:
                # The release for a key held when the window lost focus will be
                # delivered somewhere else, so the machine would believe in a key
                # nobody is holding for the rest of the run.
                self._presses.clear()
                self.emulator.release_all_keys()
            elif event.type == _CLIENT_MESSAGE:
                if event.xclient.data[0] == self.wm_delete:
                    self.running = False

    # -- the loop ----------------------------------------------------------
    def run(self, start_running: bool = True) -> int:
        if start_running:
            self.emulator.start()
        clock = FrameClock(FRAMES_PER_SECOND)
        try:
            while self.running:
                self.pump()
                if not self.running:
                    break
                frames = clock.frames_due()
                for _ in range(frames):
                    self.emulator.tick()
                    self._sound()
                # A frame the machine executed is a frame worth painting; the
                # dirty flag is for the events that change the window itself.
                if frames or self._dirty:
                    self.draw()
                wait = clock.sleep_for()
                if wait > 0:
                    select.select([self._connection], [], [], wait)
        except KeyboardInterrupt:
            pass
        finally:
            self.emulator.release_all_keys()
        return 0

    def _sound(self) -> None:
        machine = self.emulator.machine
        if self.mute or machine is None or machine.sound_timer == 0:
            return
        now = time.monotonic()
        if now - self._last_bell >= 0.125:
            self._last_bell = now
            self.xlib.XBell(self.display, 0)
            self.xlib.XFlush(self.display)
