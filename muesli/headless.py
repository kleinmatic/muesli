# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Scott Klein
"""Record from a terminal, without the GUI: muesli --headless [--seconds N] [name]

Prints a level line per second. Ctrl+C (or --seconds) stops and merges.
"""

import math
import signal
import sys
import time

from .engine import Recorder
from .merge import merge


def db(peak: int) -> str:
    return f"{20 * math.log10(peak / 32768):6.1f} dB" if peak > 0 else "  -inf dB"


def run(seconds: float | None, name: str) -> int:
    rec = Recorder()
    folder = rec.start(name)
    print(f"RECORDING to {folder}")
    print(f"  me:   {rec.me.device}\n  them: {rec.them.device}")
    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    shown: set[str] = set()
    t0 = time.monotonic()
    while not stop and (seconds is None or time.monotonic() - t0 < seconds):
        time.sleep(1)
        print(f"  {rec.elapsed():5d}s  me {db(rec.me.take_peak())}   "
              f"them {db(rec.them.take_peak())}", flush=True)
        problems = rec.problems()
        for key, text in problems.items():
            if key not in shown:
                print(f"!!! {text}", file=sys.stderr, flush=True)
        shown = set(problems)
    rec.stop()
    print("Stopped. Merging...")
    try:
        print(f"Saved {merge(folder)}")
    except RuntimeError as e:
        print(f"Merge failed: {e}. The .flac tracks are in {folder}", file=sys.stderr)
        return 1
    return 0
