"""muesli tray app (PySide6)."""

import argparse
import math
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from . import __version__
from .engine import Recorder, default_devices, meetings_dir
from .merge import Merge, find_unmerged


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="muesli", description="Record both sides of a call.")
    ap.add_argument("--headless", action="store_true", help="record from the terminal, no GUI")
    ap.add_argument("--seconds", type=float, help="with --headless: stop after this long")
    ap.add_argument("--version", action="version", version=f"muesli {__version__}")
    ap.add_argument("name", nargs="?", default="", help="optional name for the recording")
    args = ap.parse_args(argv)
    if args.headless:
        from .headless import run
        return run(args.seconds, args.name)
    return run_gui()


# ----------------------------------------------------------------------------

def run_gui() -> int:
    from PySide6.QtCore import QObject, QSize, Qt, QTimer, QUrl, Signal
    from PySide6.QtGui import (QAction, QColor, QDesktopServices, QFont, QIcon,
                               QPainter, QPixmap)
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import (QApplication, QGridLayout, QLabel, QLineEdit,
                                   QMenu, QMessageBox, QPushButton,
                                   QSystemTrayIcon, QVBoxLayout, QWidget)

    SOCKET = f"muesli-{os.getuid()}"

    def dot_icon(color: str, ring: str | None = None) -> QIcon:
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        if ring:
            p.setBrush(QColor(ring))
            p.drawEllipse(4, 4, 56, 56)
            p.setBrush(QColor(color))
            p.drawEllipse(18, 18, 28, 28)
        else:
            p.setBrush(QColor(color))
            p.drawEllipse(6, 6, 52, 52)
        p.end()
        return QIcon(pm)

    class Meter(QWidget):
        """Horizontal level bar, -60 dB .. 0 dB, with quick attack, slow decay."""

        def __init__(self):
            super().__init__()
            self.level = 0.0   # 0..1
            self.active = False
            self.setMinimumSize(QSize(220, 16))

        def feed(self, peak: int) -> None:
            if peak > 0:
                db = 20 * math.log10(peak / 32768)
                new = min(1.0, max(0.0, (db + 60) / 60))
            else:
                new = 0.0
            self.level = max(new, self.level - 0.04)
            self.update()

        def reset(self) -> None:
            self.level = 0.0
            self.update()

        def paintEvent(self, _):
            p = QPainter(self)
            w, h = self.width(), self.height()
            p.fillRect(0, 0, w, h, QColor("#2b2b2b" if self.active else "#3a3a3a"))
            if self.active and self.level > 0:
                color = "#d9534f" if self.level > 0.97 else "#f0ad4e" if self.level > 0.85 else "#5cb85c"
                p.fillRect(0, 0, int(w * self.level), h, QColor(color))
            p.end()

    class Bridge(QObject):
        saved = Signal(object, str)       # folder, error text ("" = ok)
        merged = Signal(object, str)      # folder, error text

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.rec = Recorder()
            self.bridge = Bridge()
            self.bridge.saved.connect(self.on_saved)
            self.bridge.merged.connect(self.on_merged)
            self.busy = False             # stopping or merging
            self.merges: list[Merge] = []
            self.queue: list[Path] = []
            self.notified: set[str] = set()
            self.quitting = False

            self.idle_icon = dot_icon("#9aa0a6", ring="#5f6368")
            self.rec_icon = dot_icon("#e53935")
            self.setWindowTitle("Muesli")
            self.setWindowIcon(self.idle_icon)

            self.status = QLabel("Ready")
            self.clock = QLabel("00:00:00")
            f = QFont()
            f.setPointSize(22)
            f.setBold(True)
            self.clock.setFont(f)
            self.me_meter, self.them_meter = Meter(), Meter()
            self.name = QLineEdit()
            self.name.setPlaceholderText("Name for this recording (optional)")
            self.button = QPushButton("Record")
            self.button.setMinimumHeight(40)
            self.button.clicked.connect(self.toggle)
            self.finish = QPushButton()
            self.finish.clicked.connect(self.merge_unfinished)
            self.finish.hide()
            self.folder_btn = QPushButton("Open recordings folder")
            self.folder_btn.clicked.connect(self.open_folder)
            self.note = QLabel("")
            self.note.setWordWrap(True)

            grid = QGridLayout()
            grid.addWidget(QLabel("Me"), 0, 0)
            grid.addWidget(self.me_meter, 0, 1)
            grid.addWidget(QLabel("Them"), 1, 0)
            grid.addWidget(self.them_meter, 1, 1)
            lay = QVBoxLayout(self)
            lay.addWidget(self.status)
            lay.addWidget(self.clock)
            lay.addLayout(grid)
            lay.addWidget(self.name)
            lay.addWidget(self.button)
            lay.addWidget(self.finish)
            lay.addWidget(self.folder_btn)
            lay.addWidget(self.note)
            self.setMinimumWidth(340)

            self.tray = QSystemTrayIcon(self.idle_icon, self)
            menu = QMenu()
            self.act_toggle = QAction("Start recording", self)
            self.act_toggle.triggered.connect(self.toggle)
            act_show = QAction("Show window", self)
            act_show.triggered.connect(self.present)
            act_folder = QAction("Open recordings folder", self)
            act_folder.triggered.connect(self.open_folder)
            act_quit = QAction("Quit", self)
            act_quit.triggered.connect(self.quit)
            for a in (self.act_toggle, act_show, act_folder):
                menu.addAction(a)
            menu.addSeparator()
            menu.addAction(act_quit)
            self._menu = menu
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self.on_tray)
            self.tray.setToolTip("Muesli: not recording")
            self.has_tray = QSystemTrayIcon.isSystemTrayAvailable()
            if self.has_tray:
                self.tray.show()

            self.fast = QTimer(self)
            self.fast.timeout.connect(self.tick_meters)
            self.fast.start(80)
            self.slow = QTimer(self)
            self.slow.timeout.connect(self.tick_second)
            self.slow.start(1000)
            self.refresh_unfinished()

        # -- actions ---------------------------------------------------------

        def present(self):
            self.show()
            self.raise_()
            self.activateWindow()

        def on_tray(self, reason):
            if reason != QSystemTrayIcon.Trigger:
                return
            # A click starts a recording, but never stops one: stopping takes
            # the Stop button, so a stray click can't end an important call.
            if not self.rec.recording and not self.busy:
                self.toggle()
            if self.isVisible() and self.rec.recording is False and self.busy is False:
                self.hide()
            else:
                self.present()

        def notify(self, title: str, text: str, bad: bool = False):
            self.note.setText(text)
            self.note.setStyleSheet("color: #e57373;" if bad else "")
            if self.has_tray:
                icon = QSystemTrayIcon.Critical if bad else QSystemTrayIcon.Information
                self.tray.showMessage(title, text, icon, 15000 if bad else 6000)

        def toggle(self):
            if self.busy:
                return
            if self.rec.recording:
                self.stop()
            else:
                self.start()

        def start(self):
            try:
                folder = self.rec.start(self.name.text())
            except Exception as e:  # noqa: BLE001 - show any startup failure
                self.notify("Muesli couldn't start recording", str(e), bad=True)
                self.present()
                return
            self.notified.clear()
            self.button.setText("Stop")
            self.act_toggle.setText("Stop recording")
            self.name.setEnabled(False)
            self.status.setText(f"Recording to {folder.name}")
            self.status.setStyleSheet("color: #e53935; font-weight: bold;")
            self.me_meter.active = self.them_meter.active = True
            self.tray.setIcon(self.rec_icon)
            self.setWindowIcon(self.rec_icon)
            self.tray.setToolTip("Muesli: RECORDING")
            self.note.setText("")
            self.present()

        def stop(self):
            self.busy = True
            self.button.setEnabled(False)
            self.act_toggle.setEnabled(False)
            self.status.setText("Saving...")
            self.status.setStyleSheet("")

            def work():
                try:
                    folder = self.rec.stop()
                    self.bridge.saved.emit(folder, "")
                except Exception as e:  # noqa: BLE001
                    self.bridge.saved.emit(None, str(e))
            threading.Thread(target=work, daemon=True).start()

        def on_saved(self, folder, error):
            self.me_meter.active = self.them_meter.active = False
            self.me_meter.reset()
            self.them_meter.reset()
            self.tray.setIcon(self.idle_icon)
            self.setWindowIcon(self.idle_icon)
            self.tray.setToolTip("Muesli: not recording")
            self.button.setText("Record")
            self.act_toggle.setText("Start recording")
            self.name.setEnabled(True)
            self.name.clear()
            if error or folder is None:
                self.busy = False
                self.button.setEnabled(True)
                self.act_toggle.setEnabled(True)
                self.status.setText("Ready")
                self.notify("Muesli: problem while stopping", error, bad=True)
                return
            if self.quitting:
                return
            self.queue.append(folder)
            self.run_queue()

        def run_queue(self):
            if not self.queue:
                self.busy = False
                self.button.setEnabled(True)
                self.act_toggle.setEnabled(True)
                self.status.setText("Ready")
                self.refresh_unfinished()
                return
            self.busy = True
            self.button.setEnabled(False)
            self.act_toggle.setEnabled(False)
            folder = self.queue.pop(0)
            self.status.setText(f"Merging {folder.name}...")

            def work():
                try:
                    m = Merge(folder)
                    self.merges.append(m)
                    try:
                        m.wait()
                    finally:
                        self.merges.remove(m)
                    self.bridge.merged.emit(folder, "")
                except Exception as e:  # noqa: BLE001
                    self.bridge.merged.emit(folder, str(e))
            threading.Thread(target=work, daemon=True).start()

        def on_merged(self, folder, error):
            if self.quitting:
                return
            if error:
                self.notify("Muesli: merge failed",
                            f"{error}. Both tracks are still in {folder}.", bad=True)
            else:
                self.notify("Muesli: recording saved", f"{folder.name}.m4a is ready.")
            self.run_queue()

        def refresh_unfinished(self):
            n = len(find_unmerged(exclude=self.rec.dir if self.rec.recording else None))
            if n and not self.busy:
                self.finish.setText(
                    f"Finish {n} interrupted recording{'s' if n > 1 else ''}")
                self.finish.show()
            else:
                self.finish.hide()
            return n

        def merge_unfinished(self):
            if self.busy:
                return
            self.queue.extend(
                find_unmerged(exclude=self.rec.dir if self.rec.recording else None))
            self.finish.hide()
            if not self.rec.recording:
                self.run_queue()

        def open_folder(self):
            target = self.rec.dir if self.rec.recording else meetings_dir()
            target.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

        # -- timers ----------------------------------------------------------

        def tick_meters(self):
            if self.rec.recording:
                self.me_meter.feed(self.rec.me.take_peak())
                self.them_meter.feed(self.rec.them.take_peak())

        def tick_second(self):
            if not self.rec.recording:
                return
            s = self.rec.elapsed()
            self.clock.setText(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
            problems = self.rec.problems()
            for key, text in problems.items():
                if key not in self.notified:
                    self.notify("Muesli: recording problem", text, bad=True)
                    self.present()
            if self.notified and not problems:
                self.note.setText("")
            self.notified = set(problems)

        # -- window / quit ---------------------------------------------------

        def closeEvent(self, event):
            if self.has_tray and not self.quitting:
                event.ignore()
                self.hide()          # keep running in the tray
            elif not self.quitting:
                event.ignore()
                self.quit()

        def quit(self, ask: bool = True):
            if self.quitting:
                return
            if ask and self.rec.recording:
                self.present()
                r = QMessageBox.question(
                    self, "Muesli", "A recording is in progress. Stop it and quit?")
                if r != QMessageBox.Yes:
                    return
            self.quitting = True
            if self.rec.recording:
                try:
                    self.rec.stop()   # finalize the tracks; merge on next launch
                except Exception:     # noqa: BLE001
                    pass
            for m in list(self.merges):
                m.cancel()
            self.tray.hide()
            QApplication.quit()

    # -- application -----------------------------------------------------------

    app = QApplication(sys.argv[:1])
    app.setApplicationName("Muesli")
    app.setDesktopFileName("muesli")
    app.setQuitOnLastWindowClosed(False)

    # Single instance: a second launch just raises the first one's window.
    probe = QLocalSocket()
    probe.connectToServer(SOCKET)
    if probe.waitForConnected(300):
        probe.write(b"show")
        probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(SOCKET)
    server = QLocalServer()
    server.listen(SOCKET)

    win = Window()
    server.newConnection.connect(
        lambda: (server.nextPendingConnection().deleteLater(), win.present()))

    # Ctrl+C / kill / logout: stop cleanly so the tracks are finalized.
    signal.signal(signal.SIGINT, lambda *_: win.quit(ask=False))
    signal.signal(signal.SIGTERM, lambda *_: win.quit(ask=False))
    app.commitDataRequest.connect(lambda *_: win.quit(ask=False))

    if os.environ.get("MUESLI_SELFTEST"):
        from .selftest import drive
        drive(app, win)
    else:
        win.present()
        n = win.refresh_unfinished()
        if n:
            win.notify("Muesli", f"{n} interrupted recording"
                       f"{'s were' if n > 1 else ' was'} found. "
                       "Use the button in the Muesli window to finish merging.")
        try:
            default_devices()
        except (OSError, subprocess.SubprocessError) as e:
            win.notify("Muesli", f"Can't reach the audio system: {e}", bad=True)
    code = app.exec()
    server.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
