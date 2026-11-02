"""The command line: run, assemble and inspect CHIP-8 programs.

``python -m chip8 run game.ch8`` picks a frontend that fits where it was
started: the terminal when it has one, an X11 window when there is a display and
no terminal, and a headless run otherwise.

By default the program runs on the native machine. ``--platform vip`` runs it on
a COSMAC VIP instead: an 1802 executing the original interpreter as firmware, so
the CHIP-8 is emulated by emulated hardware rather than by the native machine.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .assembler import assemble_chip8, keypad_seed, parse_key_mask
from . import librarian
from .constants import CYCLES_PER_FRAME, MAX_FRAMES, PIXEL_SCALE, PROGRAM_ADDRESS
from .emulator import Chip8Emulator
from .inspector import (
    hex_dump,
    listing_text,
    memory_text,
    registers_text,
    status_text,
)
from .machine import Chip8Error
from .screen import Palette, render_display


def _check_screen(args: argparse.Namespace) -> None:
    """Refuse the board's display on a command that is not running the board."""

    if (
        getattr(args, "screen", "chip8") != "chip8"
        and getattr(args, "platform", "native") != "vip"
    ):
        raise SystemExit(
            "chip8: --screen vip is the COSMAC VIP's display; add --platform vip"
        )


def _emulator(args: argparse.Namespace) -> Chip8Emulator:
    """The platform the user asked for.

    Both platforms answer the same interface, so everything after this -- the
    frontends, the inspector, the headless run -- does not know which one it
    has. The VIP is imported lazily: it assembles its firmware on import.
    """

    seed = getattr(args, "seed", None)
    view = getattr(args, "screen", "chip8")
    _check_screen(args)
    if getattr(args, "platform", "native") != "vip":
        return Chip8Emulator(seed=seed)
    from .vip import VipEmulator

    emulator = VipEmulator(seed=seed)
    emulator.set_screen_view(view)
    return emulator




def _web_factory(args: argparse.Namespace):
    """A way for the web frontend to rebuild the other platform mid-run."""

    def make(platform: str, screen: str):
        scope = argparse.Namespace(**vars(args))
        scope.platform = platform
        scope.screen = screen
        emulator = _emulator(scope)
        if not emulator.load_file(args.program):
            raise Chip8Error(emulator.error)
        return emulator

    return make


def _load(emulator: Chip8Emulator, path: str) -> bool:
    if not emulator.load_file(path):
        print(f"chip8: {path}: {emulator.error}", file=sys.stderr)
        return False
    for note in getattr(emulator, "notes", ()):
        print(f"chip8: {path}: {note}", file=sys.stderr)
    return True


#: Names for the keys a game is most likely to want, so a script does not have to
#: know which hex digit its author chose. `fire` is 5 because that is the middle
#: of the direction square on the keypad.
KEY_NAMES = {
    "up": "2",
    "down": "8",
    "left": "4",
    "right": "6",
    "fire": "5",
}


def parse_keys(text: str) -> int:
    """A key literal with names allowed: ``right`` is ``6``, ``1F`` is both."""

    expanded = "".join(KEY_NAMES.get(word.lower(), word) for word in text.split())
    return parse_key_mask(expanded)


def parse_schedule(spec: str, attribute: str) -> list[tuple[int, int, int, bool]]:
    """``KEY@FRAME,KEY@FRAME`` into (frame, key, mask, down) events.

    Splitting the mask and the key apart here keeps the two spellings the
    command line offers honest: ``--press 5@30`` presses one key, and a mask
    expands to the keys it names.
    """

    events: list[tuple[int, int, int, bool]] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "@" not in chunk:
            raise SystemExit(f"chip8: {attribute} wants KEY@FRAME, not '{chunk}'")
        keys, _, frame = chunk.partition("@")
        try:
            number = int(frame.strip())
        except ValueError as failure:
            raise SystemExit(f"chip8: {attribute}: '{frame}' is not a frame") from failure
        mask = parse_keys(keys)
        if not mask:
            raise SystemExit(f"chip8: {attribute}: '{keys}' names no key")
        down = attribute == "--press"
        for key in range(16):
            if mask & (1 << key):
                events.append((max(0, number), key, mask, down))
    return events


def _hold_keys(emulator: Chip8Emulator, mask: int) -> None:
    for key in range(16):
        if mask & (1 << key):
            emulator.set_key(key, True)


def _seeded_keys(emulator: Chip8Emulator, literal: str) -> int:
    """The `--keys` literal, plus whatever the listing's `; keys` comments say.

    A keypad has to be held for a whole-program run: there are no hands to press
    one later, so this is how a program that waits for a key gets one.
    """

    mask = parse_keys(literal)
    if not emulator.binary:
        mask |= keypad_seed(emulator.source.decode("latin-1"))
    return mask


def _library(args: argparse.Namespace) -> int:
    _check_screen(args)
    entries = librarian.catalogue()
    if args.run:
        return _run_program_named(args.run, args)
    if not entries:
        print("chip8: no programs found")
        return 1
    width = max(len(entry.name) for entry in entries)
    folder = ""
    for entry in entries:
        if entry.folder != folder:
            folder = entry.folder
            where = "written for this emulator" if folder == "roms" else "bundled examples"
            print(f"\n{where} ({folder}/)")
        print(f"  {entry.name:<{width}}  {entry.description[:58]}")
        if entry.speed != 10:
            print(f"  {'':<{width}}  run it at {entry.speed} opcodes a frame")
    print(
        f"\n{len(entries)} programs. "
        "chip8 run NAME starts one; chip8 library --run NAME does it at its own speed."
    )
    return 0


def _new(args: argparse.Namespace) -> int:
    target = Path(args.program)
    if target.suffix != ".ch8":
        target = target.with_suffix(".ch8")
    if target.exists() and not args.force:
        print(f"chip8: {target} exists; --force overwrites it", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(librarian.scaffold(target.name, args.speed), encoding="utf-8")
    print(f"wrote {target}")
    print(f"run it with: python3 -m chip8 dev {target}")
    return 0


def _dev(args: argparse.Namespace) -> int:
    """Run a listing and reload it whenever the file changes."""

    emulator = _emulator(args)
    if not _load(emulator, args.program):
        return 1
    if args.speed == CYCLES_PER_FRAME:
        # A program that says `; speed N` in its header knows better than the
        # default does -- a game needs more opcodes a frame than a title card.
        args.speed = librarian.describe(args.program).speed
    emulator.set_speed(args.speed)
    print(f"watching {args.program}: edit and save, and the machine reloads", file=sys.stderr)

    frontend = _pick_frontend(args.frontend)
    if frontend == "headless":
        return _headless(emulator, args.frames, args.report, True, False)
    if frontend == "x11":
        from . import frontend_x11

        try:
            view = frontend_x11.X11Frontend(
                emulator, scale=args.scale or PIXEL_SCALE + 2, mute=args.mute
            )
        except frontend_x11.X11Unavailable as failure:
            print(f"chip8: {failure}", file=sys.stderr)
            return 1
        view.watch = args.program
        with view:
            return view.run(start_running=not args.paused)
    if frontend == "web":
        from .frontend_web import WebFrontend

        try:
            view = WebFrontend(
                emulator,
                port=args.web_port,
                open_browser=not args.no_browser,
                make_emulator=_web_factory(args),
                platform=args.platform,
                screen_view=args.screen,
                watch=args.program,
            )
        except OSError as failure:
            print(f"chip8: {failure}", file=sys.stderr)
            return 1
        return view.run(start_running=not args.paused)

    if not sys.stdin.isatty():
        print("chip8: no terminal to read keys from", file=sys.stderr)
        return 1
    from .frontend_terminal import TerminalFrontend

    view = TerminalFrontend(
        emulator,
        scale=args.scale or 1,
        color=args.color,
        mute=args.mute,
        inspector=True,
        memory=args.memory,
        fps=args.fps,
        watch=args.program,
    )
    return view.run(start_running=not args.paused)


def _run_program_named(name: str, args: argparse.Namespace) -> int:
    """``chip8 library --run NAME``: run it at the speed it asks for."""

    try:
        path = librarian.find(name)
    except Chip8Error as failure:
        print(f"chip8: {failure}", file=sys.stderr)
        return 1
    entry = librarian.describe(path)
    args.program = str(path)
    if args.speed == CYCLES_PER_FRAME:
        args.speed = entry.speed
    return _run(args)


def _pick_frontend(requested: str) -> str:
    if requested != "auto":
        return requested
    if sys.stdin.isatty() and sys.stdout.isatty():
        return "terminal"
    from . import frontend_x11

    if frontend_x11.available():
        return "x11"
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        # A display exists but libX11 cannot talk to it (a bare Wayland
        # session, or a missing library): the browser frontend still can.
        return "web"
    return "headless"


def _headless(
    emulator: Chip8Emulator,
    frames: int,
    report: int,
    show_inspector: bool,
    show_memory: bool,
    start: bool = True,
    events: list[tuple[int, int, int, bool]] | None = None,
    show_screen: bool = False,
) -> int:
    schedule = sorted(events or [])
    by_frame: dict[int, list[tuple[int, int, bool]]] = {}
    for frame, key, _mask, down in schedule:
        by_frame.setdefault(frame, []).append((key, _mask, down))

    if start:
        emulator.start()
    for frame in range(frames):
        for key, _mask, down in by_frame.get(frame, ()):
            emulator.set_key(key, down)
        if not emulator.running and frame > 0:
            break
        emulator.advance_frame()
        if report and frame % report == 0:
            snapshot = emulator.snapshot()
            print(f"frame {frame:5d}  {status_text(snapshot, emulator.state_name)}")

    snapshot = emulator.snapshot()
    if show_screen:
        for line in render_display(snapshot, 1, Palette("ascii")):
            print(line)
        print()
    print(status_text(snapshot, emulator.state_name))
    if show_inspector:
        for line in registers_text(snapshot):
            print(line)
        for line in listing_text(snapshot, emulator.program, emulator.source):
            print(line)
    if show_memory:
        for line in memory_text(snapshot):
            print(line)
    return 0 if not snapshot.error else 1


def _run(args: argparse.Namespace) -> int:
    try:
        args.program = str(librarian.find(args.program))
    except Chip8Error:
        pass  # a path that does not exist is reported by the loader below
    emulator = _emulator(args)
    if not _load(emulator, args.program):
        return 1
    if args.speed == CYCLES_PER_FRAME:
        # A program that says `; speed N` in its header knows better than the
        # default does -- a game needs more opcodes a frame than a title card.
        args.speed = librarian.describe(args.program).speed
    emulator.set_speed(args.speed)

    frontend = _pick_frontend(args.frontend)
    if frontend == "headless":
        _hold_keys(emulator, _seeded_keys(emulator, args.keys))
        events = parse_schedule(args.press, "--press") + parse_schedule(
            args.release, "--release"
        )
        return _headless(
            emulator,
            args.frames,
            args.report,
            args.inspector,
            args.memory,
            start=not args.paused,
            events=events,
            show_screen=args.show,
        )
    if frontend == "x11":
        from . import frontend_x11

        try:
            view = frontend_x11.X11Frontend(
                emulator,
                scale=args.scale or PIXEL_SCALE + 2,
                mute=args.mute,
                inspector=args.inspector,
            )
        except frontend_x11.X11Unavailable as failure:
            print(f"chip8: {failure}", file=sys.stderr)
            return 1
        with view:
            return view.run(start_running=not args.paused)
    if frontend == "web":
        from .frontend_web import WebFrontend

        try:
            view = WebFrontend(
                emulator,
                port=args.web_port,
                open_browser=not args.no_browser,
                make_emulator=_web_factory(args),
                platform=args.platform,
                screen_view=args.screen,
            )
        except OSError as failure:
            print(f"chip8: {failure}", file=sys.stderr)
            return 1
        return view.run(start_running=not args.paused)

    if not sys.stdin.isatty():
        print(
            "chip8: no terminal to read keys from; use --frontend headless to run "
            "without one",
            file=sys.stderr,
        )
        return 1

    from .frontend_terminal import TerminalFrontend

    view = TerminalFrontend(
        emulator,
        scale=args.scale or 1,
        color=args.color,
        mute=args.mute,
        inspector=args.inspector,
        memory=args.memory,
        fps=args.fps,
    )
    return view.run(start_running=not args.paused)


def _assemble(args: argparse.Namespace) -> int:
    data = Path(args.program).read_bytes()
    try:
        program = assemble_chip8(data, Path(args.program).parent)
    except Chip8Error as failure:
        print(f"chip8: {failure}", file=sys.stderr)
        return 1
    body = bytes(program.memory[PROGRAM_ADDRESS : PROGRAM_ADDRESS + program.size])
    for line in hex_dump(body, PROGRAM_ADDRESS):
        print(line)
    if args.labels:
        _print_labels(program)
    if args.output:
        Path(args.output).write_bytes(body)
        if b"\0" not in body:
            # The loader tells a ROM from a listing by the NUL byte, as the
            # reference does; a ROM without one would be read back as text.
            print(
                "chip8: note: these bytes contain no NUL, so this file would be "
                "read as a listing if opened again",
                file=sys.stderr,
            )
    return 0


def _info(args: argparse.Namespace) -> int:
    data = Path(args.program).read_bytes()
    try:
        program = assemble_chip8(data, Path(args.program).parent)
    except Chip8Error as failure:
        print(f"chip8: {failure}", file=sys.stderr)
        return 1
    kind = "binary ROM" if program.binary else "listing"
    print(f"file      {args.program}")
    print(f"kind      {kind}")
    print(f"size      {program.size} bytes at 0x{PROGRAM_ADDRESS:03X}")
    print(f"entry     0x{program.entry:03X}")
    print(f"labels    {len(program.labels)}")
    if program.source_lines:
        first = min(program.source_lines)
        last = max(program.source_lines)
        print(f"code      0x{first:03X}-0x{last:03X}")
    if args.labels:
        _print_labels(program)
    return 0


def _print_labels(program) -> None:
    """The label table, without the derived names a data blob makes for you."""

    derived = ("_len", "_hi", "_lo")
    named = [
        (address, name)
        for name, address in program.labels.items()
        if not name.endswith(derived)
    ]
    for address, name in sorted(named):
        print(f"{address:04X}  {name}")
    hidden = len(program.labels) - len(named)
    if hidden:
        print(f"({hidden} derived names such as NAME_hi and NAME_len omitted)")


def _vip(args: argparse.Namespace) -> int:
    """What the COSMAC VIP is made of, and the firmware it runs."""

    from . import vipfirmware
    from .vip import CLOCK, DISPLAY_ADDRESS, DISPLAY_HEIGHT, DISPLAY_WIDTH, VipMemory

    image = vipfirmware.firmware()
    print(f"COSMAC VIP: 1802 at {CLOCK:,} Hz, 4K of RAM, firmware in ROM above it")
    print(
        f"display {DISPLAY_WIDTH} x {DISPLAY_HEIGHT} at 0x{DISPLAY_ADDRESS:04X}"
        "; a CHIP-8 pixel is 1 x 4 of them"
    )
    print()
    segments = len(image.segments)
    print(f"interpreter: {image.size} bytes, built from {segments} segment(s)")
    for address, data in image.segments:
        end = address + len(data) - 1
        where = "RAM (boot stub)" if address < VipMemory.RAM_TOP else "ROM"
        print(f"  {address:04X}-{end:04X}  {len(data):5d} bytes  {where}")
    named = ("BOOT", "START", "DONE", "FETCH", "TABLE")
    print("  " + "  ".join(f"{name} {image.labels[name]:04X}" for name in named))
    if args.write:
        rom = image.bytes(VipMemory.ROM_TOP)[VipMemory.RAM_TOP :]
        Path(args.write).write_bytes(rom)
        print(f"\nwrote {len(rom)} bytes of ROM to {args.write}")
    if args.labels:
        print()
        for address, name in sorted(
            (address, name) for name, address in image.labels.items()
        ):
            print(f"  {address:04X}  {name}")
    return 0


def _add_web_arguments(parser: argparse.ArgumentParser) -> None:
    """The two knobs the browser frontend needs on a machine without a desktop."""

    parser.add_argument(
        "--web-port",
        type=int,
        default=0,
        metavar="N",
        help="web frontend: port to listen on (default: any free port)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="web frontend: print the address instead of opening a browser",
    )


def _gui(args: argparse.Namespace) -> int:
    """The desktop application, started from ``chip8 gui``."""

    from . import gui

    argv: list[str] = []
    if args.program:
        argv.append(args.program)
    argv += ["--platform", args.platform, "--screen", args.screen]
    if args.speed is not None:
        argv += ["--speed", str(args.speed)]
    if args.seed is not None:
        argv += ["--seed", str(args.seed)]
    if args.paused:
        argv.append("--paused")
    return gui.main(argv)


def _desktop(args: argparse.Namespace) -> int:
    """Install or remove the COSMAC VIP application in the desktop."""

    from . import desktop

    if args.action == "install":
        paths = desktop.install()
        for path in paths:
            print(f"installed {path}")
        print("COSMAC VIP is now in the application menu; .ch8 files offer it too.")
    else:
        paths = desktop.uninstall()
        for path in paths:
            print(f"removed {path}")
        if not paths:
            print("nothing to remove")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chip8",
        description=(
            "A COSMAC VIP emulator with a complete CHIP-8 system: the 1977 "
            "machine, its assembler, its library and its keypad."
        ),
    )
    parser.add_argument(
        "--version", action="version", version="cosmac-vip 1.0.0"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="load a program and run it")
    run.add_argument("program", help="a .ch8 listing or binary ROM")
    run.add_argument(
        "--frontend",
        choices=("auto", "terminal", "x11", "web", "headless"),
        default="auto",
        help="where to draw the screen (default: wherever it fits)",
    )
    run.add_argument(
        "--platform",
        choices=("native", "vip"),
        default="native",
        help=(
            "native runs the opcodes here; vip boots a COSMAC VIP and runs the "
            "1977 interpreter as 1802 firmware (default: native)"
        ),
    )
    run.add_argument(
        "--screen",
        choices=("chip8", "vip"),
        default="chip8",
        help=(
            "on --platform vip: draw the 64x32 CHIP-8 grid, or the board's own "
            "64x128 display (default: chip8)"
        ),
    )
    _add_web_arguments(run)
    run.add_argument(
        "--speed",
        type=int,
        default=CYCLES_PER_FRAME,
        metavar="N",
        help=f"opcodes per 60 Hz frame (default {CYCLES_PER_FRAME}, the 1977 pace)",
    )
    run.add_argument(
        "--scale", type=int, default=0, help="pixels per CHIP-8 pixel (frontend default)"
    )
    run.add_argument(
        "--fps", type=int, default=30, help="how often the terminal repaints (default 30)"
    )
    run.add_argument(
        "--frames",
        type=int,
        default=MAX_FRAMES,
        metavar="N",
        help=f"headless: how many frames to run (default {MAX_FRAMES})",
    )
    run.add_argument(
        "--report",
        type=int,
        default=0,
        metavar="N",
        help="headless: print the status every N frames",
    )
    run.add_argument("--keys", default="", metavar="MASK", help="hold these keys, e.g. 1F")
    run.add_argument(
        "--press",
        default="",
        metavar="KEY@FRAME",
        help="press a key at a frame, e.g. 5@30,2@45",
    )
    run.add_argument(
        "--release",
        default="",
        metavar="KEY@FRAME",
        help="release a key at a frame, e.g. 5@40",
    )
    run.add_argument(
        "--show",
        action="store_true",
        help="headless: print the screen as characters when the run ends",
    )
    run.add_argument("--paused", action="store_true", help="start paused")
    run.add_argument(
        "--seed",
        type=int,
        default=None,
        metavar="N",
        help="seed the random generator, so a run of a program that uses RND repeats",
    )
    run.add_argument("--mute", action="store_true", help="silence the buzzer")
    run.add_argument("--inspector", action="store_true", help="show registers and listing")
    run.add_argument("--memory", action="store_true", help="show memory")
    run.add_argument(
        "--color",
        choices=("auto", "truecolor", "256", "ascii"),
        default="auto",
        help="terminal colours (default: whatever the terminal supports)",
    )
    run.set_defaults(handler=_run)

    asm = sub.add_parser("asm", help="assemble a listing and show or write its bytes")
    asm.add_argument("program", help="a .ch8 listing")
    asm.add_argument("-o", "--output", help="write the bytes to this file")
    asm.add_argument("--labels", action="store_true", help="list the labels as well")
    asm.set_defaults(handler=_assemble)

    library = sub.add_parser("library", help="list the bundled programs")
    library.add_argument("--run", metavar="NAME", help="run one at its own speed")
    library.add_argument(
        "--frontend", choices=("auto", "terminal", "x11", "web", "headless"), default="auto"
    )
    library.add_argument(
        "--platform",
        choices=("native", "vip"),
        default="native",
        help="where CHIP-8 runs: this machine, or a COSMAC VIP and its firmware",
    )
    library.add_argument(
        "--screen",
        choices=("chip8", "vip"),
        default="chip8",
        help=(
            "on --platform vip: draw the 64x32 CHIP-8 grid, or the board's own "
            "64x128 display (default: chip8)"
        ),
    )
    _add_web_arguments(library)
    library.add_argument("--speed", type=int, default=CYCLES_PER_FRAME, metavar="N")
    library.add_argument("--scale", type=int, default=0)
    library.add_argument("--fps", type=int, default=30)
    library.add_argument("--frames", type=int, default=MAX_FRAMES)
    library.add_argument("--report", type=int, default=0)
    library.add_argument("--keys", default="")
    library.add_argument("--press", default="")
    library.add_argument("--release", default="")
    library.add_argument("--show", action="store_true")
    library.add_argument("--paused", action="store_true")
    library.add_argument("--seed", type=int, default=None, metavar="N")
    library.add_argument("--mute", action="store_true")
    library.add_argument("--inspector", action="store_true")
    library.add_argument("--memory", action="store_true")
    library.add_argument(
        "--color", choices=("auto", "truecolor", "256", "ascii"), default="auto"
    )
    library.set_defaults(handler=_library)

    new = sub.add_parser("new", help="start a program from a template")
    new.add_argument("program", help="the file to write, e.g. roms/pong.ch8")
    new.add_argument("--speed", type=int, default=30, metavar="N")
    new.add_argument("--force", action="store_true", help="overwrite an existing file")
    new.set_defaults(handler=_new)

    dev = sub.add_parser("dev", help="run a listing and reload it when it is saved")
    dev.add_argument("program", help="the listing to watch")
    dev.add_argument(
        "--frontend", choices=("auto", "terminal", "x11", "web", "headless"), default="auto"
    )
    dev.add_argument(
        "--platform",
        choices=("native", "vip"),
        default="native",
        help="where CHIP-8 runs: this machine, or a COSMAC VIP and its firmware",
    )
    dev.add_argument(
        "--screen",
        choices=("chip8", "vip"),
        default="chip8",
        help=(
            "on --platform vip: draw the 64x32 CHIP-8 grid, or the board's own "
            "64x128 display (default: chip8)"
        ),
    )
    _add_web_arguments(dev)
    dev.add_argument("--speed", type=int, default=CYCLES_PER_FRAME, metavar="N")
    dev.add_argument("--scale", type=int, default=0)
    dev.add_argument("--fps", type=int, default=30)
    dev.add_argument("--frames", type=int, default=MAX_FRAMES)
    dev.add_argument("--report", type=int, default=0)
    dev.add_argument("--keys", default="")
    dev.add_argument("--press", default="")
    dev.add_argument("--release", default="")
    dev.add_argument("--show", action="store_true")
    dev.add_argument("--paused", action="store_true")
    dev.add_argument("--seed", type=int, default=None, metavar="N")
    dev.add_argument("--mute", action="store_true")
    dev.add_argument("--inspector", action="store_true")
    dev.add_argument("--memory", action="store_true")
    dev.add_argument(
        "--color", choices=("auto", "truecolor", "256", "ascii"), default="auto"
    )
    dev.set_defaults(handler=_dev)

    info = sub.add_parser("info", help="describe a program without running it")
    info.add_argument("program", help="a .ch8 listing or binary ROM")
    info.add_argument("--labels", action="store_true", help="list the labels")
    info.set_defaults(handler=_info)

    vip = sub.add_parser(
        "vip", help="describe the COSMAC VIP firmware that runs a program"
    )
    vip.add_argument("--write", metavar="FILE", help="write the 8K ROM image")
    vip.add_argument(
        "--labels", action="store_true", help="list the interpreter's labels"
    )
    vip.set_defaults(handler=_vip)

    gui = sub.add_parser("gui", help="run the COSMAC VIP desktop application")
    gui.add_argument("program", nargs="?", help="a .ch8 listing or binary ROM")
    gui.add_argument(
        "--platform",
        choices=("vip", "native"),
        default="vip",
        help="which machine to run (default: vip)",
    )
    gui.add_argument(
        "--screen",
        choices=("chip8", "vip"),
        default="chip8",
        help="the 64x32 CHIP-8 grid, or the board's own 64x128 display",
    )
    gui.add_argument("--speed", type=int, default=None, metavar="N")
    gui.add_argument("--seed", type=int, default=None, metavar="N")
    gui.add_argument("--paused", action="store_true", help="start paused")
    gui.set_defaults(handler=_gui)

    desktop = sub.add_parser(
        "desktop", help="install or remove the COSMAC VIP desktop application"
    )
    desktop.add_argument("action", choices=("install", "uninstall"))
    desktop.set_defaults(handler=_desktop)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except Chip8Error as failure:
        print(f"chip8: {failure}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
