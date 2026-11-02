"""Put the COSMAC VIP window in the desktop's application menu.

A desktop environment finds an application by its ``.desktop`` file, its icon
and, for a program people double-click, a MIME registration for ``*.ch8``.
This module writes those three files into the user's data directory -- no root
needed -- and asks the desktop's own databases to notice.

The ``Exec`` line is resolved when the launcher is installed: the
``chip8-vip`` command if it is on PATH, otherwise this interpreter running
``python3 -m chip8.gui``, so a checkout that was never pip-installed still
launches from the menu.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

__all__ = [
    "DESKTOP_FILE",
    "ICON_FILE",
    "MIME_FILE",
    "command",
    "desktop_text",
    "install",
    "uninstall",
    "user_data",
]

DESKTOP_FILE = "chip8-vip.desktop"
ICON_FILE = "chip8-vip.svg"
MIME_FILE = "chip8-vip-mime.xml"


def data_dir() -> Path:
    """The launcher, icon and MIME description shipped with the package."""

    return Path(__file__).with_name("data")


def user_data(data_home: Path | None = None) -> Path:
    """Where this user's desktop looks: ``$XDG_DATA_HOME`` or ``~/.local/share``."""

    if data_home is not None:
        return Path(data_home)
    configured = os.environ.get("XDG_DATA_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share"


def command() -> list[str]:
    """How a desktop should start the application.

    An installed ``chip8-vip`` is used as it is. A checkout is started by this
    interpreter with the package's parent on ``PYTHONPATH``, because a desktop
    does not launch from the directory the checkout happens to be in.
    """

    found = shutil.which("chip8-vip")
    if found:
        return [found]
    package_root = str(Path(__file__).resolve().parents[1])
    return ["env", f"PYTHONPATH={package_root}", sys.executable, "-m", "chip8.gui"]


def _quote(token: str) -> str:
    """Quote one Exec argument the way the desktop-entry specification does."""

    if token and not any(character in token for character in " \t\n\"'\\><~|&;$*?#()`"):
        return token
    escaped = token.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def desktop_text() -> str:
    """The launcher, with this machine's way of starting it filled in."""

    template = (data_dir() / DESKTOP_FILE).read_text(encoding="utf-8")
    executable = " ".join(_quote(token) for token in command())
    return template.replace("@EXEC@", executable)


def install(data_home: Path | None = None) -> list[Path]:
    """Write the launcher, icon and MIME type; return what was written."""

    root = user_data(data_home)
    applications = root / "applications"
    icons = root / "icons" / "hicolor" / "scalable" / "apps"
    mimes = root / "mime" / "packages"
    for folder in (applications, icons, mimes):
        folder.mkdir(parents=True, exist_ok=True)

    launcher = applications / DESKTOP_FILE
    launcher.write_text(desktop_text(), encoding="utf-8")
    icon = icons / ICON_FILE
    shutil.copyfile(data_dir() / ICON_FILE, icon)
    mime = mimes / MIME_FILE
    shutil.copyfile(data_dir() / MIME_FILE, mime)
    _refresh(root)
    return [launcher, icon, mime]


def uninstall(data_home: Path | None = None) -> list[Path]:
    """Remove the three files install wrote; return the ones that were there."""

    root = user_data(data_home)
    removed: list[Path] = []
    for path in (
        root / "applications" / DESKTOP_FILE,
        root / "icons" / "hicolor" / "scalable" / "apps" / ICON_FILE,
        root / "mime" / "packages" / MIME_FILE,
    ):
        if path.exists():
            path.unlink()
            removed.append(path)
    _refresh(root)
    return removed


def _refresh(root: Path) -> None:
    """Let the desktop rebuild its caches, if it has the tools to do so."""

    for tool, target in (
        ("update-desktop-database", root / "applications"),
        ("update-mime-database", root / "mime"),
    ):
        executable = shutil.which(tool)
        if executable is None:
            continue
        subprocess.run(
            [executable, str(target)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
