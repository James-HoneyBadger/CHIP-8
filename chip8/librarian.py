"""Finding programs, and describing them.

A CHIP-8 program is a listing, and a listing's first comment line is where a
person writes what it is for. That is all this module needs: it reads the
comments back out and hands them to ``chip8 library``. There is no index file to
fall out of date, because the index *is* the programs.

Two folders are searched:

``roms/``      the programs written for this emulator, with the standard library
``examples/``  the ones that came from Time Warp Studio

A listing may also say, in its header:

``; speed 2000``   the opcodes a frame it is meant to be run at
``; keys 1F``      the keypad a headless run should hold
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .constants import CYCLES_PER_FRAME
from .machine import Chip8Error

#: Where programs live, in the order they should be offered.
PROGRAM_DIRS = ("roms", "examples")


@dataclass(frozen=True)
class Entry:
    """One runnable program, and what its own header says about it."""

    name: str
    path: Path
    title: str
    description: str
    speed: int = CYCLES_PER_FRAME
    is_library: bool = False

    @property
    def folder(self) -> str:
        return self.path.parent.name


def search_dirs(root: str | Path | None = None) -> list[Path]:
    """The directories a program may be found in, existing ones only."""

    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    return [base / name for name in PROGRAM_DIRS if (base / name).is_dir()]


def describe(path: str | Path) -> Entry:
    """Read a listing's header: its title, its description and its speed."""

    source = Path(path)
    title = source.stem
    description = ""
    speed = CYCLES_PER_FRAME
    lines: list[str] = []
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for raw in text.split("\n")[:40]:
        stripped = raw.strip()
        if not stripped.startswith(";"):
            if stripped:
                break
            continue
        body = stripped.lstrip(";").strip()
        lower = body.lower()
        if lower.startswith("speed "):
            try:
                speed = max(1, min(2000, int(body.split()[1])))
            except (IndexError, ValueError):
                pass
            continue
        if lower.startswith("keys "):
            continue
        if not body:
            continue
        lines.append(body)
    if lines:
        # "HELLO -- a title card for the machine." reads best as two fields.
        first = lines[0]
        if " -- " in first:
            title, description = first.split(" -- ", 1)
        else:
            title = first
            description = lines[1] if len(lines) > 1 else ""
    return Entry(
        name=source.stem,
        path=source,
        title=title.strip(),
        description=description.strip(),
        speed=speed,
        is_library=source.parent.name == "roms",
    )


def catalogue(root: str | Path | None = None) -> list[Entry]:
    """Every program, the ones written here first."""

    entries: list[Entry] = []
    for folder in search_dirs(root):
        for path in sorted(folder.glob("*.ch8")):
            entries.append(describe(path))
    return entries


def find(name: str, root: str | Path | None = None) -> Path:
    """A program by name, or by path.

    ``chip8 run snake`` finds ``roms/snake.ch8``; a path is used as given.
    """

    candidate = Path(name)
    if candidate.is_file():
        return candidate
    cleaned = name if candidate.suffix else f"{name}.ch8"
    for folder in search_dirs(root):
        path = folder / cleaned
        if path.is_file():
            return path
    available = ", ".join(entry.name for entry in catalogue(root))
    raise Chip8Error(f"no program called '{name}'; there is: {available}")


#: What ``chip8 new`` writes. It is a program that already runs, because the
#: first thing to do with a new file is see it do something.
TEMPLATE = """\
; {name} -- what this program does, in one line.
;
; The comments at the top of a listing are its manual: the first line becomes
; the title `chip8 library` shows, and the rest is the description.
;
; Keys: 1 to F.
; speed {speed}

start:  CLS
        LD V1, 4
        .printc title          ; centred: .printc works the column out
        LD V1, 14
        .printc prompt
        CALL i_wait            ; block until a key is pressed
        CALL i_wait_release
        LD V0, 4
        LD V1, 22
        LD V2, V0              ; print the row number as a demonstration
        CALL t_num
        HALT

.string title "{name}"
.string prompt "PRESS A KEY"

.include text
.include draw
.include input
"""


def scaffold(name: str, speed: int = 30) -> str:
    """A small program that runs, to start from."""

    title = Path(name).stem.upper().replace("_", " ")
    return TEMPLATE.format(name=Path(name).stem, speed=speed).replace(
        '.string title "' + Path(name).stem + '"', f'.string title "{title}"'
    )
