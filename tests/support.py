"""Helpers shared by the tests: assemble a listing, run it, count the pixels."""

from __future__ import annotations

from chip8.assembler import Chip8Program, assemble_chip8
from chip8.machine import Chip8Machine


def assemble(source: str) -> Chip8Program:
    return assemble_chip8(source)


def machine_for(
    source: str, keys: int = 0, interactive: bool = False
) -> tuple[Chip8Machine, Chip8Program]:
    program = assemble(source)
    machine = Chip8Machine.from_program(program.memory, program.entry, keys=keys)
    machine.interactive = interactive
    return machine, program


def run_to_halt(
    source: str, keys: int = 0, interactive: bool = False, limit: int = 10_000
) -> Chip8Machine:
    machine, _ = machine_for(source, keys, interactive)
    steps = 0
    while not machine.halted:
        machine.step()
        steps += 1
        if steps > limit:
            raise AssertionError("the program did not halt")
    return machine


def lit_pixels(machine: Chip8Machine) -> int:
    return sum(1 for pixel in machine.framebuffer if pixel)
