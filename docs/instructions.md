# Instructions

Getting the COSMAC VIP emulator running, in order, from nothing to a program you
wrote yourself. About ten minutes.

---

## 1. What you need

| | |
| --- | --- |
| Python | 3.10 or newer (`python3 --version`) |
| Anything else | nothing. There are no dependencies to install |
| A window, optionally | libX11, for `--frontend x11` (most Linux desktops have it) |
| The application, optionally | GTK 4: `python3-gi` and `gir1.2-gtk-4.0` on Debian and Ubuntu |

The terminal frontend and the headless mode work over SSH; the window needs a
display, and so does the application.

---

## 2. Get it

```sh
git clone https://github.com/James-HoneyBadger/CHIP-8
cd CHIP-8
```

There is nothing to build. Every command below runs from that directory as
`python3 -m chip8 …`.

If you would rather have a `chip8` command on your path:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e .
chip8 --version
```

The virtual environment is only needed because Debian and Ubuntu mark their
system Python as externally managed; with `python3 -m chip8` you can skip it
entirely.

---

## 3. See it do something

```sh
python3 -m chip8 run bounce
```

That finds `examples/bounce.ch8`, works out where to draw — the terminal if you
are in one, an X11 window if you have a display and no terminal, headless
otherwise — and starts it. Press `Esc` to leave.

Other ways in:

```sh
python3 -m chip8 library                        # what there is: 23 programs
python3 -m chip8 library --run snake            # one of them, at its own speed
python3 -m chip8 run hello --frontend x11       # a window, explicitly
python3 -m chip8 run life --frontend headless --frames 300 --show
```

The same programs run on the machine CHIP-8 was written for:

```sh
python3 -m chip8 run bounce --platform vip      # a COSMAC VIP, emulated
python3 -m chip8 gui bounce                     # the desktop application
```

`chip8 desktop install` puts the application in the menu and lets `.ch8` files
open in it. Its monitor is how a program is typed into memory a byte at a time:
see [the application](user-manual.md#application).

---

## 4. Write a program

```sh
python3 -m chip8 new pong.ch8
python3 -m chip8 dev pong.ch8
```

`new` writes a listing that already runs. `dev` runs it and reloads it every
time you save the file — edit in any editor, `:w`, and the machine picks the
new version up within a quarter of a second.

Change a line and watch it change:

```text
.string title "PONG"        →   .string title "HELLO"
```

If you break it, the status line at the bottom says what and where, and the
program that was already running keeps running:

```text
▪ paused   PC 0x206 · 40 cycles · 30 opcodes/frame (1800 Hz) — line 9: FROB is
  not an instruction form this assembler knows
```

Press `Esc` to stop the dev loop.

---

## 5. Look at the screen without a window

```sh
python3 -m chip8 run pong.ch8 --frontend headless --frames 60 --show
```

`--show` prints the framebuffer as characters — each one is two rows of two
pixels — which is the quickest way to see whether a drawing routine did what you
meant. `--report 10` prints the status every ten frames while it runs.

---

## 6. Check your work

The test suite is the reference for how everything is supposed to behave, and it
runs in about two seconds:

```sh
python3 -m unittest discover -s tests -t .
```

---

## 7. Where to go next

| If you want to | Read |
| --- | --- |
| use the emulator well | [the user manual](user-manual.md) |
| write programs | [the programmer's reference](programmer-reference.md) |
| know how it works inside | [the technical reference](technical-reference.md) |
| look up an opcode | [the instruction set](instruction-set.md) |

---

## If something goes wrong

| Symptom | Cause and cure |
| --- | --- |
| `no program called 'foo'` | the name is not in `roms/` or `examples/`; `python3 -m chip8 library` lists them all |
| `cannot open an X display` | no `DISPLAY`, or no X server. Use `--frontend terminal` or `headless` |
| `no terminal to read keys from` | you are in a pipe. Add `--frontend headless` |
| the terminal shows nothing but escape codes | `--color ascii` |
| a program just sits there | it is waiting for a key: press one, or give it `--keys 1F` |
| `unknown opcode 0xNNNN at 0xNNN` | the program ran into data. Check the instruction before that address |
| `RET with an empty call stack` | a `RET` without a `CALL` |
| the program needs N bytes and memory ends at 0x1000 | the linked program is too big; the library is about 800 bytes of it |
