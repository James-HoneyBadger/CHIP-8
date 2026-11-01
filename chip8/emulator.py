"""A live CHIP-8 machine: wall-clock frames, key state and pause/step.

:class:`~chip8.machine.Chip8Machine` executes one instruction. A learner
pressing Run on a game needs the other model, the one the original interpreters
had: a fixed number of opcodes per 60 Hz frame, timers that follow the frame
clock, and a keypad that can change while the program runs. This class owns that
loop; a frontend is only a view of it.

Two things here are not in the 1977 machine:

* **speed** -- opcodes per frame, 1 to 2000, ten being the original pace; and
* **rewind** -- the machine's state is four kilobytes of memory and eight of
  pixels, so a snapshot every quarter second costs little and puts "it died just
  after that jump" somewhere a person can look at it.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .assembler import assemble_chip8
from .constants import (
    CYCLES_PER_FRAME,
    FRAMES_PER_SECOND,
    MAX_SPEED,
    MIN_SPEED,
    PROGRAM_ADDRESS,
    REWIND_SNAPSHOTS,
    REWIND_STRIDE,
    SCREEN_HEIGHT,
    SCREEN_WIDTH,
)
from .machine import Chip8Error, Chip8Machine, Chip8State


@dataclass(slots=True)
class Chip8Snapshot:
    """Everything a frontend draws, copied in one go.

    So the inspector cannot read a half-executed instruction, the frontend reads
    this rather than the machine.
    """

    v: tuple[int, ...] = (0,) * 16
    i: int = 0
    pc: int = PROGRAM_ADDRESS
    stack: tuple[int, ...] = ()
    delay_timer: int = 0
    sound_timer: int = 0
    cycles: int = 0
    collisions: int = 0
    loaded: bool = False
    running: bool = False
    halted: bool = False
    waiting_for_key: bool = False
    speed: int = CYCLES_PER_FRAME
    screen_width: int = SCREEN_WIDTH
    screen_height: int = SCREEN_HEIGHT
    framebuffer: bytes = b""
    memory: bytes = b""
    name: str = ""
    binary: bool = False
    error: str = ""
    rewind_count: int = 0
    rewind_position: int = -1
    keys: int = 0


class Chip8Emulator:
    """The frame loop over a machine, with the program it was loaded from kept."""

    def __init__(self, seed: int | None = None) -> None:
        #: RND is the machine's only source of luck. A seed makes a run
        #: reproducible, which is what a test of a game needs and what a bug
        #: report wants; leaving it None seeds from the OS, as the machine does.
        self._rng = random.Random(seed) if seed is not None else None
        self.program = None
        self.machine: Chip8Machine | None = None
        self.source = b""
        self.name = ""
        self.path = ""
        self.error = ""
        self.speed = CYCLES_PER_FRAME
        self.running = False
        self._states: deque[Chip8State] = deque(maxlen=REWIND_SNAPSHOTS)
        self._rewind_position = -1
        self._frames_since_snapshot = 0

    # -- loading ----------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self.machine is not None

    @property
    def binary(self) -> bool:
        return bool(self.program is not None and self.program.binary)

    def load_file(self, path: str | Path) -> bool:
        """Read a ``.ch8`` file exactly as stored."""

        try:
            data = Path(path).read_bytes()
        except OSError as failure:
            self.error = str(failure)
            return False
        if not self.load_bytes(data, Path(path).name):
            return False
        self.path = str(Path(path).absolute())
        return True

    def load_bytes(self, data: bytes, name: str = "program") -> bool:
        """A listing (assembled) or a binary ROM (contains a NUL).

        A typo in a listing must not unload the game that is running, so a
        failure reports and leaves the old program exactly as it was.
        """

        try:
            program = assemble_chip8(data)
        except Chip8Error as failure:
            self.error = str(failure)
            return False

        self.running = False
        self.program = program
        self.source = data
        self.name = name
        self.path = ""
        self.error = ""
        self._make_machine()
        return True

    def load_source(self, source: str, name: str = "document") -> bool:
        return self.load_bytes(source.encode("utf-8"), name)

    def _make_machine(self) -> None:
        assert self.program is not None
        self.machine = Chip8Machine.from_program(
            self.program.memory, self.program.entry, rng=self._rng
        )
        # The whole-program executor reads the key mask once because it cannot
        # receive a later press. This machine can, so Fx0A waits.
        self.machine.interactive = True
        self.clear_rewind()
        self._remember()

    # -- transport --------------------------------------------------------
    def start(self) -> None:
        if self.machine is None or self.running or self.machine.halted:
            return
        # A rewind puts the machine in the past; running from there makes a new
        # timeline, and the old future is not it.
        self._forget_future()
        self.error = ""
        self.running = True
        self._frames_since_snapshot = 0

    def pause(self) -> None:
        if not self.running:
            return
        self.running = False
        # The moment the user paused is the one they want to go back to, and it
        # is newer than the last scheduled snapshot.
        self._remember()

    def toggle(self) -> None:
        if self.running:
            self.pause()
        else:
            self.start()

    def step(self) -> None:
        """One instruction, while paused."""

        if self.machine is None or self.running or self.machine.halted:
            return
        try:
            self.machine.step()
        except Chip8Error as failure:
            self._fail(str(failure))

    def reset(self) -> None:
        """Rebuild the machine from the loaded program, which is kept."""

        self.running = False
        if self.machine is None:
            return
        self._make_machine()
        self.error = ""

    def tick(self) -> None:
        """One 60 Hz frame's worth of work, if the machine is running."""

        if self.running:
            self.advance_frame()

    def advance_frame(self) -> None:
        """Run one frame's opcodes and tick the timers, without waiting."""

        if self.machine is None:
            return
        self._forget_future()
        self._frames_since_snapshot += 1
        if self._frames_since_snapshot >= REWIND_STRIDE:
            self._frames_since_snapshot = 0
            self._remember()
        try:
            for _ in range(self.speed):
                if self.machine.halted:
                    break
                self.machine.step()
            # The clock is the frame, so the timers follow it whether the
            # program executed ten opcodes or none while waiting for a key.
            self.machine.tick_timers()
        except Chip8Error as failure:
            self._fail(str(failure))
            return
        if self.machine.halted:
            self.running = False

    def _fail(self, message: str) -> None:
        self.error = message
        self.running = False

    # -- speed and keys ---------------------------------------------------
    def set_speed(self, cycles_per_frame: int) -> None:
        self.speed = max(MIN_SPEED, min(MAX_SPEED, int(cycles_per_frame)))

    def nudge_speed(self, delta: int) -> None:
        """A step through the speeds a person actually wants, not by one."""

        if delta > 0:
            step = 10 if self.speed >= 10 else 1
        else:
            step = 10 if self.speed > 10 else 1
        self.set_speed(self.speed + (step if delta > 0 else -step))

    def set_key(self, key: int, down: bool) -> None:
        if self.machine is not None:
            self.machine.set_key(key, down)

    def release_all_keys(self) -> None:
        if self.machine is not None:
            self.machine.set_keys(0)

    @property
    def keys(self) -> int:
        return 0 if self.machine is None else self.machine.keys

    # -- rewind -----------------------------------------------------------
    @property
    def rewind_count(self) -> int:
        return len(self._states)

    @property
    def rewind_position(self) -> int:
        return self._rewind_position

    def _remember(self) -> None:
        if self.machine is None:
            return
        self._frames_since_snapshot = 0
        # Nothing has happened since the last moment, so it is the same moment.
        # Two copies of one frame in the ring would make the strip's right edge
        # a place that does nothing.
        if self._states and self._states[-1].cycles == self.machine.cycles:
            last = self._states[-1]
            if last.pc == self.machine.pc:
                return
        self._states.append(self.machine.save_state())
        self._rewind_position = -1

    def _forget_future(self) -> None:
        if self._rewind_position < 0:
            return
        while len(self._states) > self._rewind_position + 1:
            self._states.pop()
        self._rewind_position = -1

    def begin_rewind(self) -> int:
        """Pause, keep the live state as the newest moment, and return the count."""

        self.running = False
        # The live state is worth keeping even if it is only a few frames newer
        # than the last scheduled snapshot: rewinding to "now" has to be exact.
        if self._rewind_position < 0:
            self._remember()
        return len(self._states)

    def rewind_to(self, index: int) -> None:
        """Put the machine into moment *index* (0 is the oldest)."""

        if self.machine is None or not self._states:
            return
        wanted = max(0, min(index, len(self._states) - 1))
        self.running = False
        self.machine.restore_state(self._states[wanted])
        self._rewind_position = wanted
        self._frames_since_snapshot = 0

    def rewind_by(self, delta: int) -> None:
        """Move through the history, entering it at the live moment first.

        The first press pauses and keeps the live state as the newest moment, so
        a step back from there is the frame before the one the user was looking
        at, and stepping forward again returns to it.
        """

        if self.machine is None:
            return
        position = self._rewind_position
        if position < 0:
            self.begin_rewind()
            position = len(self._states) - 1
        self.rewind_to(position + delta)

    def clear_rewind(self) -> None:
        self._states.clear()
        self._rewind_position = -1
        self._frames_since_snapshot = 0

    # -- reading ----------------------------------------------------------
    @property
    def state_name(self) -> str:
        """The status line's word for what the machine is doing."""

        if self.error:
            return "error"
        if self.machine is None:
            return "open a program"
        if self.machine.halted:
            return "halted"
        # A waiting machine is also running, but "waiting for a key" is the word
        # that explains why the screen is not moving.
        if self.machine.waiting_for_key:
            return "waiting for a key"
        if self.running:
            return "running"
        return "paused"

    def snapshot(self) -> Chip8Snapshot:
        snapshot = Chip8Snapshot(
            speed=self.speed,
            name=self.name,
            binary=self.binary,
            error=self.error,
            rewind_count=self.rewind_count,
            rewind_position=self.rewind_position,
        )
        machine = self.machine
        if machine is None:
            return snapshot
        snapshot.v = tuple(machine.v)
        snapshot.i = machine.i
        snapshot.pc = machine.pc
        snapshot.stack = tuple(machine.stack)
        snapshot.delay_timer = machine.delay_timer
        snapshot.sound_timer = machine.sound_timer
        snapshot.cycles = machine.cycles
        snapshot.collisions = machine.collisions
        snapshot.keys = machine.keys
        snapshot.loaded = True
        snapshot.running = self.running
        snapshot.halted = machine.halted
        snapshot.waiting_for_key = machine.waiting_for_key
        snapshot.screen_width = machine.screen_width
        snapshot.screen_height = machine.screen_height
        snapshot.framebuffer = bytes(machine.framebuffer)
        snapshot.memory = bytes(machine.memory)
        return snapshot


@dataclass(slots=True)
class SourceWatcher:
    """Notices a listing being saved, so `chip8 dev` can reload it.

    Polling a modification time is not elegant and does not need to be: the
    question is asked four times a second, and the answer is what makes the
    edit-run loop a loop instead of a restart.
    """

    path: str
    interval: float = 0.25
    _stamp: float = 0.0
    _checked: float = -1e9

    def poll(self, now: float) -> bool:
        """True when the file has been written since the last time."""

        if now - self._checked < self.interval:
            return False
        self._checked = now
        try:
            stamp = Path(self.path).stat().st_mtime
        except OSError:
            return False
        if stamp == self._stamp:
            return False
        self._stamp = stamp
        return True


@dataclass(slots=True)
class FrameClock:
    """Wall-clock 60 Hz frames without a timer library.

    :meth:`frames_due` reports how many frames the clock owes since it was last
    asked. It is capped so a frontend that was blocked -- a resize, a debugger,
    a stopped process -- cannot be handed a second of catch-up work and start
    spiralling.
    """

    fps: int = FRAMES_PER_SECOND
    max_catch_up: int = 4
    _next: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self._next = time.monotonic() + 1.0 / self.fps

    @property
    def period(self) -> float:
        return 1.0 / self.fps

    def frames_due(self, now: float | None = None) -> int:
        moment = time.monotonic() if now is None else now
        due = 0
        # The schedule is accumulated, so it drifts by a few nanoseconds; without
        # the slack a frame exactly on time would occasionally not be due yet.
        while moment + 1e-6 >= self._next and due < self.max_catch_up:
            due += 1
            self._next += self.period
        if due >= self.max_catch_up and moment >= self._next:
            # Too far behind to catch up: resynchronise rather than stutter.
            self._next = moment + self.period
        return due

    def sleep_for(self, now: float | None = None) -> float:
        """How long until the next frame is due, never negative."""

        moment = time.monotonic() if now is None else now
        return max(0.0, self._next - moment)
