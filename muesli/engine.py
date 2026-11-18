# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Scott Klein
"""Recording engine.

Each side of the call is one Track:

    parecord --raw  ->  this process (level meter)  ->  ffmpeg  ->  .flac

The meter is computed from the very bytes that get written to disk, so a
healthy-looking meter means audio is really being saved.

"me" is the default microphone; "them" is the monitor of the default output
(everything the speakers or headphones play). Devices are looked up by name
when recording starts. Nothing in PipeWire is configured or changed.
"""

import array
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

RATE = 32000          # Hz, mono, 16-bit. Plenty for speech; Whisper uses 16 kHz.
STALL_SECONDS = 3     # no audio data at all for this long = a side has dropped
LOUD = 600            # peak (of 32768) that counts as "someone is talking"
SILENT_THEM_SECONDS = 300


def meetings_dir() -> Path:
    return Path(os.environ.get("MUESLI_DIR") or Path.home() / "Meetings")


def _pactl(*args: str) -> str:
    return subprocess.run(
        ["pactl", *args], capture_output=True, text=True, check=True, timeout=5
    ).stdout.strip()


def default_devices() -> tuple[str, str]:
    """(microphone source, monitor source of the speakers)."""
    return _pactl("get-default-source"), _pactl("get-default-sink") + ".monitor"


class Track:
    def __init__(self, name: str, device: str, path: Path):
        self.name = name
        self.device = device
        self.path = path
        self.error: str | None = None
        self.samples = 0
        self.last_data = time.monotonic()
        self.last_loud = 0.0
        self._peak = 0
        self._stopping = False
        self._rec: subprocess.Popen | None = None
        self._enc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # start_new_session: a Ctrl+C in a terminal must not hit the children
        # directly; we shut them down in order so the FLAC file is finalized.
        self._enc = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
             "-c:a", "flac", "-flush_packets", "1", "-y", str(self.path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        self._rec = subprocess.Popen(
            ["parecord", "--raw", "--format=s16le", "--channels=1",
             f"--rate={RATE}", "--latency-msec=50",
             "--client-name=muesli", f"--stream-name=muesli: {self.name}",
             "-d", self.device],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True)
        self.last_data = time.monotonic()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        out, enc_in = self._rec.stdout, self._enc.stdin
        odd = b""
        try:
            while True:
                data = out.read1(16384)
                if not data:
                    if not self._stopping:
                        self.error = "the recorder stopped"
                    break
                enc_in.write(data)
                data = odd + data
                cut = len(data) & ~1
                odd = data[cut:]
                a = array.array("h")
                a.frombytes(data[:cut])
                if a:
                    peak = max(max(a), -min(a))
                    now = time.monotonic()
                    self.samples += len(a)
                    self.last_data = now
                    if peak > self._peak:
                        self._peak = peak
                    if peak >= LOUD:
                        self.last_loud = now
        except (BrokenPipeError, OSError):
            if not self._stopping:
                self.error = "the encoder stopped"
        finally:
            try:
                enc_in.close()
            except OSError:
                pass

    def take_peak(self) -> int:
        """Highest sample since the last call (0..32768)."""
        p, self._peak = self._peak, 0
        return p

    @property
    def seconds(self) -> float:
        return self.samples / RATE

    def stalled(self) -> bool:
        return time.monotonic() - self.last_data > STALL_SECONDS

    def stop(self) -> None:
        self._stopping = True
        if self._rec and self._rec.poll() is None:
            self._rec.send_signal(signal.SIGINT)
        if self._thread:
            self._thread.join(timeout=5)
        for proc in (self._rec, self._enc):
            if not proc:
                continue
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


class Recorder:
    """One recording session: a folder with me.flac, them.flac, session.json."""

    def __init__(self) -> None:
        self.dir: Path | None = None
        self.me: Track | None = None
        self.them: Track | None = None
        self.started: float = 0.0
        self._meta: dict = {}

    @property
    def recording(self) -> bool:
        return self.me is not None

    @property
    def tracks(self) -> list[Track]:
        return [t for t in (self.me, self.them) if t]

    def elapsed(self) -> int:
        return int(time.monotonic() - self.started) if self.recording else 0

    def start(self, name: str = "") -> Path:
        if self.recording:
            raise RuntimeError("already recording")
        for tool in ("parecord", "pactl", "ffmpeg"):
            if not shutil.which(tool):
                raise RuntimeError(f"'{tool}' is not installed")
        source, monitor = default_devices()
        base = meetings_dir()
        slug = "".join(c if c.isalnum() or c in "._-" else "-" for c in name.strip())
        slug = "-".join(filter(None, slug.split("-")))
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.dir = base / (stamp + (f"-{slug}" if slug else ""))
        self.dir.mkdir(parents=True, exist_ok=False)
        self._meta = {
            "name": name.strip(),
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "ended": None,
            "me_device": source,
            "them_device": monitor,
            "rate": RATE,
        }
        self._write_meta()
        self.me = Track("me", source, self.dir / "me.flac")
        self.them = Track("them", monitor, self.dir / "them.flac")
        self.started = time.monotonic()
        # Start both as close together as possible so the tracks line up.
        self.them.start()
        self.me.start()
        return self.dir

    def _write_meta(self) -> None:
        tmp = self.dir / "session.json.tmp"
        tmp.write_text(json.dumps(self._meta, indent=2) + "\n")
        tmp.replace(self.dir / "session.json")

    def problems(self) -> dict[str, str]:
        """Current problems, keyed so the caller can notify once per problem."""
        out: dict[str, str] = {}
        if not self.recording:
            return out
        for t in self.tracks:
            side = "Your microphone" if t.name == "me" else "The call audio"
            if t.error:
                out[f"{t.name}-dead"] = f"{side} is no longer being recorded ({t.error})."
            elif t.stalled():
                out[f"{t.name}-stalled"] = f"{side} has stopped sending audio."
        now = time.monotonic()
        if (self.elapsed() > SILENT_THEM_SECONDS
                and now - max(self.them.last_loud, self.started) > SILENT_THEM_SECONDS
                and now - self.me.last_loud < 60):
            out["them-silent"] = (
                "Nothing has been heard from the other side for 5 minutes, "
                "but your mic is active. Check that the call's sound is "
                "playing through this computer's default output.")
        free = shutil.disk_usage(self.dir).free
        if free < 300 * 1024 * 1024:
            out["disk"] = f"Only {free // (1024 * 1024)} MB of disk space left."
        return out

    def stop(self) -> Path:
        """Stop both tracks and finalize the files. Returns the session folder."""
        if not self.recording:
            raise RuntimeError("not recording")
        tracks, folder = self.tracks, self.dir
        threads = [threading.Thread(target=t.stop) for t in tracks]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self._meta["ended"] = datetime.now().astimezone().isoformat(timespec="seconds")
        self._meta["seconds"] = {t.name: round(t.seconds, 2) for t in tracks}
        self._meta["errors"] = {t.name: t.error for t in tracks if t.error}
        self._write_meta()
        self.me = self.them = None
        return folder
