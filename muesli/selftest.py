# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Scott Klein
"""GUI self-test, run with MUESLI_SELFTEST=1 (ideally QT_QPA_PLATFORM=offscreen
and MUESLI_DIR pointing at a scratch folder).

Drives the real window: start, watch the meters for a few seconds, stop, wait
for the merge, then print a result line and exit 0 or 1.
"""

import sys

from PySide6.QtCore import QTimer


def drive(app, win) -> None:
    state = {"me_max": 0.0, "them_max": 0.0, "ticks": 0}

    def fail(msg: str) -> None:
        print(f"SELFTEST FAIL: {msg}", flush=True)
        win.quitting = True
        if win.rec.recording:
            win.rec.stop()
        app.exit(1)

    def sample() -> None:
        state["me_max"] = max(state["me_max"], win.me_meter.level)
        state["them_max"] = max(state["them_max"], win.them_meter.level)

    def after_record() -> None:
        sampler.stop()
        if not win.rec.recording:
            return fail("recording did not start: " + win.note.text())
        if win.rec.problems():
            return fail(f"problems: {win.rec.problems()}")
        state["folder"] = win.rec.dir
        win.toggle()                       # Stop -> save -> merge
        waiter.start(250)

    def wait_done() -> None:
        state["ticks"] += 1
        if win.busy and state["ticks"] < 240:
            return
        waiter.stop()
        folder = state["folder"]
        out = folder / f"{folder.name}.m4a"
        if not out.is_file() or out.stat().st_size < 1000:
            return fail(f"no merged file: {win.note.text()}")
        print(f"SELFTEST OK: {out} ({out.stat().st_size} bytes), "
              f"meter max me={state['me_max']:.2f} them={state['them_max']:.2f}, "
              f"status={win.status.text()!r}", flush=True)
        win.quitting = True
        app.exit(0)

    sampler = QTimer(win)
    sampler.timeout.connect(sample)
    waiter = QTimer(win)
    waiter.timeout.connect(wait_done)

    def begin() -> None:
        win.name.setText("self test")
        win.toggle()
        sampler.start(50)
        QTimer.singleShot(6000, after_record)

    QTimer.singleShot(200, begin)
    win._selftest = (sampler, waiter)      # keep references alive
