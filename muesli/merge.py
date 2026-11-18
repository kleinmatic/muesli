# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Scott Klein
"""Merge me.flac + them.flac into one mono .m4a.

Each track is loudness-normalized first, so your voice and theirs come out at
similar levels, then mixed and limited. The .flac files are kept.
"""

import subprocess
from pathlib import Path

from .engine import meetings_dir

FILTER = (
    "[0:a]aformat=channel_layouts=mono,loudnorm=I=-20:TP=-2,aresample=48000[me];"
    "[1:a]aformat=channel_layouts=mono,loudnorm=I=-20:TP=-2,aresample=48000[them];"
    "[me][them]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.9[out]"
)


def output_path(folder: Path) -> Path:
    return folder / f"{folder.name}.m4a"


def _usable(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def needs_merge(folder: Path) -> bool:
    return (
        (folder / "session.json").is_file()
        and _usable(folder / "me.flac")
        and _usable(folder / "them.flac")
        and not output_path(folder).exists()
    )


def find_unmerged(exclude: Path | None = None) -> list[Path]:
    base = meetings_dir()
    if not base.is_dir():
        return []
    return sorted(
        d for d in base.iterdir()
        if d.is_dir() and d != exclude and needs_merge(d)
    )


class Merge:
    """A running merge. wait() returns the .m4a path or raises RuntimeError."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.out = output_path(folder)
        # Written under a temporary name so a half-finished file is never
        # mistaken for a finished one.
        self.tmp = folder / f".{folder.name}.partial.m4a"
        me, them = folder / "me.flac", folder / "them.flac"
        if not (_usable(me) and _usable(them)):
            raise RuntimeError("one of the two tracks is missing or empty")
        self.proc = subprocess.Popen(
            ["nice", "-n", "19",
             "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(me), "-i", str(them),
             "-filter_complex", FILTER, "-map", "[out]",
             "-c:a", "aac", "-b:a", "64k", str(self.tmp)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            start_new_session=True)

    def wait(self) -> Path:
        _, err = self.proc.communicate()
        if self.proc.returncode != 0:
            self.tmp.unlink(missing_ok=True)
            last = (err or "").strip().splitlines()[-1:] or ["ffmpeg failed"]
            raise RuntimeError(last[0])
        self.tmp.replace(self.out)
        return self.out

    def cancel(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.tmp.unlink(missing_ok=True)


def merge(folder: Path) -> Path:
    return Merge(folder).wait()
