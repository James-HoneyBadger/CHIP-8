"""A GUI in a browser: any Linux, any display, no toolkit.

The other two frontends each want something the machine may not have -- a
terminal, or an X server with libX11. This one wants a browser and a socket,
which is as close to universal as a GUI gets: it works under X11, under
Wayland, in a remote session over SSH, and on a machine with no display at
all. The standard library carries the frames; the page is one string.

It draws what the other frontends draw -- the framebuffer, the registers, the
status line -- and it can switch between the native machine and the COSMAC VIP
while it runs, including the VIP's own 64x128 display, by asking the caller to
build the other platform from the same program.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .constants import FRAMES_PER_SECOND
from .emulator import FrameClock, SourceWatcher

__all__ = ["WebFrontend"]

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CHIP-8</title>
<style>
  :root { color-scheme: dark; --bg:#111315; --panel:#1a1d20; --edge:#2b3035;
          --ink:#e6e8e6; --dim:#8b9298; --on:#eef2ea; --accent:#7fd1a0; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:14px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         display:flex; justify-content:center; padding:24px; }
  main { width:max-content; max-width:100%; }
  header { display:flex; align-items:baseline; gap:12px; margin-bottom:12px; }
  h1 { font-size:18px; letter-spacing:.14em; margin:0; color:var(--accent); }
  .name { color:var(--dim); }
  #screen { display:block; background:#0b0c0d; border:1px solid var(--edge);
            image-rendering:pixelated; image-rendering:crisp-edges; }
  .bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
         margin-top:10px; padding:8px 10px; background:var(--panel);
         border:1px solid var(--edge); border-radius:6px; }
  button, select { font:inherit; color:var(--ink); background:#24292e;
                   border:1px solid var(--edge); border-radius:4px;
                   padding:3px 9px; cursor:pointer; }
  button:hover, select:hover { border-color:#4a5158; }
  button:active { background:#30363d; }
  #state { color:var(--accent); font-weight:600; }
  #status, #regs, #notes { white-space:pre; color:var(--dim); margin-top:10px; }
  #regs { color:var(--ink); }
  #notes { color:#d7b46a; }
  .sep { width:1px; height:20px; background:var(--edge); }
  kbd { border:1px solid var(--edge); border-radius:3px; padding:0 4px;
        color:var(--dim); }
</style>
</head>
<body>
<main>
  <header>
    <h1>CHIP&#8209;8</h1>
    <span class="name" id="name">&mdash;</span>
    <span class="name">&#183;</span>
    <span class="name" id="platform-badge">native</span>
  </header>

  <canvas id="screen" width="64" height="32"></canvas>

  <div class="bar">
    <span id="state">paused</span>
    <span class="sep"></span>
    <button id="run">Run</button>
    <button data-act="step">Step</button>
    <button data-act="reset">Reset</button>
    <span class="sep"></span>
    <button data-act="rewind-back" title="one snapshot back">&#9198;</button>
    <button data-act="rewind-forward" title="one snapshot forward">&#9197;</button>
    <span class="sep"></span>
    <button data-act="speed-down" title="slower">&#8722; speed</button>
    <button data-act="speed-up" title="faster">speed +</button>
    <span class="sep"></span>
    <label>platform
      <select id="platform">
        <option value="native">CHIP-8 native</option>
        <option value="vip">COSMAC VIP</option>
      </select>
    </label>
    <label>screen
      <select id="screen-view">
        <option value="chip8">64&#215;32 grid</option>
        <option value="vip">64&#215;128 board</option>
      </select>
    </label>
    <span class="sep"></span>
    <button id="quit">Quit</button>
  </div>

  <div id="status">connecting&hellip;</div>
  <div id="regs"></div>
  <div id="notes"></div>
  <div id="status-help" class="name">
    keypad <kbd>1</kbd><kbd>2</kbd><kbd>3</kbd><kbd>4</kbd> /
    <kbd>Q</kbd><kbd>W</kbd><kbd>E</kbd><kbd>R</kbd> /
    <kbd>A</kbd><kbd>S</kbd><kbd>D</kbd><kbd>F</kbd> /
    <kbd>Z</kbd><kbd>X</kbd><kbd>C</kbd><kbd>V</kbd>
    &#183; <kbd>P</kbd> pause &#183; <kbd>Space</kbd>/<kbd>O</kbd> step
    &#183; <kbd>Shift</kbd>+<kbd>R</kbd> reset
    &#183; <kbd>[</kbd>/<kbd>]</kbd> rewind &#183; <kbd>+</kbd>/<kbd>-</kbd> speed
  </div>
</main>

<script>
const canvas = document.getElementById("screen");
const ctx = canvas.getContext("2d");
const stateEl = document.getElementById("state");
const statusEl = document.getElementById("status");
const regsEl = document.getElementById("regs");
const notesEl = document.getElementById("notes");
const nameEl = document.getElementById("name");
const badgeEl = document.getElementById("platform-badge");
const runBtn = document.getElementById("run");
const platformSel = document.getElementById("platform");
const screenSel = document.getElementById("screen-view");
const KEYPAD = {"1":1,"2":2,"3":3,"4":0xc,"q":4,"w":5,"e":6,"r":0xd,
                "a":7,"s":8,"d":9,"f":0xe,"z":0xa,"x":0,"c":0xb,"v":0xf};
let held = new Set();
let lastSize = "";
let lastPlatform = null;

function post(path, body) {
  return fetch(path, {method:"POST", headers:{"Content-Type":"application/json"},
                      body:JSON.stringify(body || {})}).then(r => r.json());
}

function act(action) { post("/action", {action}); paint(); }

runBtn.addEventListener("click", () => act(runBtn.textContent === "Run" ? "start" : "pause"));
document.querySelectorAll("[data-act]").forEach(b =>
  b.addEventListener("click", () => act(b.dataset.act)));
document.getElementById("quit").addEventListener("click", () => {
  act("quit");
  document.body.innerHTML = "<p style='padding:2em;color:#8b9298'>CHIP-8 has stopped. Close this tab.</p>";
});
platformSel.addEventListener("change", switchPlatform);
screenSel.addEventListener("change", switchPlatform);

function switchPlatform() {
  const platform = platformSel.value;
  const screen = platform === "vip" ? screenSel.value : "chip8";
  post("/platform", {platform, screen}).then(r => {
    if (!r.ok) { notesEl.textContent = r.error || "cannot switch platform"; return; }
    lastSize = "";
    paint();
  });
}

function fit(width, height) {
  const key = width + "x" + height;
  if (key === lastSize) return;
  lastSize = key;
  canvas.width = width;
  canvas.height = height;
  const maxW = Math.max(320, window.innerWidth - 64);
  const maxH = Math.max(240, window.innerHeight - 300);
  const scale = Math.max(1, Math.floor(Math.min(maxW / width, maxH / height)));
  canvas.style.width = (width * scale) + "px";
  canvas.style.height = (height * scale) + "px";
}

function draw(s) {
  fit(s.width, s.height);
  const raw = atob(s.pixels);
  const img = ctx.createImageData(s.width, s.height);
  const data = img.data;
  for (let i = 0; i < raw.length; i++) {
    const on = raw.charCodeAt(i) !== 0;
    const j = i * 4;
    data[j] = on ? 238 : 11;
    data[j + 1] = on ? 242 : 12;
    data[j + 2] = on ? 234 : 13;
    data[j + 3] = 255;
  }
  ctx.putImageData(img, 0, 0);
}

function update(s) {
  stateEl.textContent = s.state;
  runBtn.textContent = s.running ? "Pause" : "Run";
  nameEl.textContent = s.name || "no program";
  badgeEl.textContent = s.platform === "vip" ? "COSMAC VIP" : "native";
  if (lastPlatform !== s.platform) {
    lastPlatform = s.platform;
    platformSel.value = s.platform;
    screenSel.value = s.screen_view;
  }
  screenSel.disabled = s.platform !== "vip";
  const timers = "DT " + s.delay + "  ST " + s.sound;
  statusEl.textContent =
    "PC 0x" + s.pc.toString(16).toUpperCase().padStart(3, "0") +
    "  I 0x" + s.i.toString(16).toUpperCase().padStart(3, "0") +
    "  " + timers + "  " + s.cycles + " cycles  " + s.speed + " opcodes/frame" +
    "  rewind " + s.rewind[0] +
    (s.rewind[1] >= 0 ? " @" + s.rewind[1] : "");
  const v = s.v.map((n, i) => "V" + i.toString(16).toUpperCase() + " " +
                              n.toString(16).toUpperCase().padStart(2, "0"));
  regsEl.textContent = v.slice(0, 8).join("  ") + "\\n" + v.slice(8).join("  ") +
    (s.stack.length ? "\\nstack " + s.stack.map(a => "0x" +
      a.toString(16).toUpperCase().padStart(3, "0")).join(" ") : "");
  notesEl.textContent = (s.error || (s.notes || []).join("\\n"));
}

function paint() {
  fetch("/state", {cache:"no-store"}).then(r => r.json()).then(s => {
    draw(s); update(s);
  }).catch(() => { statusEl.textContent = "no connection"; });
}

function loop() { paint(); setTimeout(loop, 33); }

document.addEventListener("keydown", event => {
  const key = event.key;
  const low = key.toLowerCase();
  if (KEYPAD[low] !== undefined && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
    if (!held.has(low)) {
      held.add(low);
      post("/key", {key: KEYPAD[low], down: true});
    }
    event.preventDefault();
    return;
  }
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  if (key === "p") { act("toggle"); event.preventDefault(); }
  else if (key === "o" || key === " ") { act("step"); event.preventDefault(); }
  else if (key === "R") { act("reset"); event.preventDefault(); }
  else if (key === "[") { act("rewind-back"); event.preventDefault(); }
  else if (key === "]") { act("rewind-forward"); event.preventDefault(); }
  else if (key === "+" || key === "=") { act("speed-up"); event.preventDefault(); }
  else if (key === "-") { act("speed-down"); event.preventDefault(); }
});

document.addEventListener("keyup", event => {
  const low = event.key.toLowerCase();
  if (KEYPAD[low] !== undefined && held.delete(low)) {
    post("/key", {key: KEYPAD[low], down: false});
    event.preventDefault();
  }
});

window.addEventListener("blur", () => {
  held.clear();
  post("/release", {});
});

window.addEventListener("resize", () => { lastSize = ""; });

loop();
</script>
</body>
</html>
"""


class WebFrontend:
    """One emulator, one HTTP server, and a page that never stops polling."""

    def __init__(
        self,
        emulator,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        open_browser: bool = True,
        make_emulator=None,
        platform: str = "native",
        screen_view: str = "chip8",
        watch: str | None = None,
    ) -> None:
        self.emulator = emulator
        self.platform = platform
        self.screen_view = screen_view
        self.url = ""
        self._make_emulator = make_emulator
        self._open_browser = open_browser
        self._watch = watch
        self._watcher = SourceWatcher(watch) if watch else None
        self._lock = threading.RLock()
        self._serving = False
        self._server = ThreadingHTTPServer((host, port), self._handler())
        self._server.daemon_threads = True
        self.url = f"http://{host}:{self._server.server_address[1]}/"

    # -- the page's protocol ----------------------------------------------
    def _handler(self):
        frontend = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the base class's spelling
                frontend._get(self)

            def do_POST(self) -> None:  # noqa: N802
                frontend._post(self)

            def log_message(self, format: str, *args) -> None:
                pass

        return Handler

    def _get(self, handler: BaseHTTPRequestHandler) -> None:
        path = handler.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(handler, 200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/state":
            self._send(handler, 200, self._state(), "application/json")
        else:
            self._send_json(handler, 404, {"ok": False, "error": "not found"})

    def _post(self, handler: BaseHTTPRequestHandler) -> None:
        try:
            length = int(handler.headers.get("Content-Length", 0) or 0)
            body = json.loads(handler.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._send_json(handler, 400, {"ok": False, "error": "bad request"})
            return
        path = handler.path.split("?", 1)[0]
        try:
            if path == "/key":
                with self._lock:
                    self.emulator.set_key(int(body["key"]), bool(body["down"]))
            elif path == "/release":
                with self._lock:
                    self.emulator.release_all_keys()
            elif path == "/action":
                self._action(str(body.get("action", "")))
            elif path == "/platform":
                self._switch(str(body.get("platform", "native")),
                             str(body.get("screen", "chip8")))
            else:
                self._send_json(handler, 404, {"ok": False, "error": "not found"})
                return
        except (KeyError, TypeError, ValueError) as failure:
            self._send_json(handler, 400, {"ok": False, "error": str(failure)})
            return
        except Exception as failure:  # a platform that will not load
            self._send_json(handler, 400, {"ok": False, "error": str(failure)})
            return
        self._send_json(handler, 200, {"ok": True})

    def _action(self, action: str) -> None:
        with self._lock:
            if action == "start":
                self.emulator.start()
            elif action == "pause":
                self.emulator.pause()
            elif action == "toggle":
                self.emulator.toggle()
            elif action == "step":
                self.emulator.pause()
                self.emulator.step()
            elif action == "reset":
                self.emulator.reset()
            elif action == "rewind-back":
                self.emulator.rewind_by(-1)
            elif action == "rewind-forward":
                self.emulator.rewind_by(1)
            elif action == "speed-up":
                self.emulator.nudge_speed(1)
            elif action == "speed-down":
                self.emulator.nudge_speed(-1)
            elif action == "quit":
                self._serving = False
            else:
                raise ValueError(f"there is no {action!r} action")

    def _switch(self, platform: str, screen: str) -> None:
        if platform not in ("native", "vip"):
            raise ValueError(f"there is no {platform!r} platform")
        if screen not in ("chip8", "vip"):
            raise ValueError(f"there is no {screen!r} screen")
        if platform == "native" and screen != "chip8":
            raise ValueError("the VIP's display needs the VIP platform")
        if self._make_emulator is None:
            raise ValueError("this web frontend was not given a program to reload")
        with self._lock:
            was_running = self.emulator.running
            speed = self.emulator.speed
            replacement = self._make_emulator(platform, screen)
            replacement.set_speed(speed)
            if was_running:
                replacement.start()
            self.emulator = replacement
            self.platform = platform
            self.screen_view = screen

    def _state(self) -> bytes:
        with self._lock:
            snapshot = self.emulator.snapshot()
            state = self.emulator.state_name
            platform = self.platform
            screen_view = self.screen_view
            notes = list(getattr(self.emulator, "notes", ()))
        return json.dumps({
            "width": snapshot.screen_width,
            "height": snapshot.screen_height,
            "pixels": base64.b64encode(snapshot.framebuffer).decode("ascii"),
            "state": state,
            "running": snapshot.running,
            "halted": snapshot.halted,
            "waiting": snapshot.waiting_for_key,
            "v": list(snapshot.v),
            "i": snapshot.i,
            "pc": snapshot.pc,
            "stack": list(snapshot.stack),
            "delay": snapshot.delay_timer,
            "sound": snapshot.sound_timer,
            "cycles": snapshot.cycles,
            "speed": snapshot.speed,
            "name": snapshot.name,
            "binary": snapshot.binary,
            "error": snapshot.error,
            "rewind": [snapshot.rewind_count, snapshot.rewind_position],
            "keys": snapshot.keys,
            "platform": platform,
            "screen_view": screen_view,
            "notes": notes,
        }).encode("utf-8")

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler, status: int, body: bytes, content_type: str
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Cache-Control", "no-store")
        handler.end_headers()
        handler.wfile.write(body)

    def _send_json(
        self, handler: BaseHTTPRequestHandler, status: int, payload: dict
    ) -> None:
        self._send(handler, status, json.dumps(payload).encode("utf-8"),
                   "application/json")

    # -- the frame loop ----------------------------------------------------
    def run(self, start_running: bool = True, announce: bool = True) -> int:
        if start_running:
            with self._lock:
                self.emulator.start()
        if announce:
            print(f"chip8: web frontend at {self.url}", flush=True)
            if self._open_browser and not webbrowser.open(self.url):
                print("chip8: no browser to open; use the address above", flush=True)
        self._serving = True
        server = threading.Thread(target=self._server.serve_forever,
                                  name="chip8-web", daemon=True)
        server.start()
        clock = FrameClock(FRAMES_PER_SECOND)
        try:
            while self._serving:
                for _ in range(clock.frames_due()):
                    with self._lock:
                        self.emulator.tick()
                if self._watcher is not None and self._watcher.poll(time.monotonic()):
                    with self._lock:
                        if self.emulator.load_file(self._watch):
                            if start_running:
                                self.emulator.start()
                            else:
                                self.emulator.pause()
                wait = clock.sleep_for()
                if wait > 0:
                    time.sleep(wait)
        except KeyboardInterrupt:
            pass
        finally:
            self._serving = False
            with self._lock:
                self.emulator.release_all_keys()
            self._server.shutdown()
            self._server.server_close()
            server.join(timeout=1.0)
        return 0
