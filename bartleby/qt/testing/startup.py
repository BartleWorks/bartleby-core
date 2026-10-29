"""Whether an app survives an exception nothing caught, checked in a process of its own.

PyQt6 aborts the process on an exception that escapes a slot unless ``sys.excepthook`` has
been replaced (BartleBot #154). Only a real process shows that: inside the suite, pytest's
own hook is already in place. So :func:`raise_in_a_slot_and_a_thread` starts one, runs the
app's own startup code in it, then raises once in a Qt slot and once in a worker thread,
and reports what happened. Each product's test hands it the startup its entry point runs;
core's hands it the bare hook.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: Printed once both exceptions were raised and the event loop still turned.
STILL_RUNNING = "still running after both"

#: The part after the app's startup: a slot that raises, then a thread that raises, then a quit.
_AFTERWARDS = f"""
import threading as _threading
from PyQt6.QtCore import QTimer as _QTimer
from PyQt6.QtWidgets import QApplication as _QApplication

_app = _QApplication.instance() or _QApplication(sys.argv[:1])


def _slot():
    raise RuntimeError("raised in a slot")


def _worker():
    raise RuntimeError("raised in a thread")


def _then():
    _thread = _threading.Thread(target=_worker, name="worker")
    _thread.start()
    _thread.join()
    _QTimer.singleShot(0, _finish)


def _finish():
    print({STILL_RUNNING!r}, flush=True)
    _app.quit()


_QTimer.singleShot(0, _slot)
_QTimer.singleShot(50, _then)
_app.exec()
"""


@dataclass(frozen=True)
class Outcome:
    returncode: int
    stdout: str
    stderr: str

    @property
    def kept_running(self) -> bool:
        """The process went on past both exceptions to quit on its own."""
        return self.returncode == 0 and STILL_RUNNING in self.stdout


def raise_in_a_slot_and_a_thread(
    startup: str, *, cwd: Path | None = None, env: Mapping[str, str] | None = None, timeout: float = 60
) -> Outcome:
    """Run *startup* (Python source) in a new process, then raise in a Qt slot and in a worker thread.

    *startup* is what the app's entry point runs before its controller: it should put the
    hooks in place and send the log wherever the caller will look. It runs with ``sys``
    imported, in *cwd*, with this process's environment and *env* over it. Qt is
    offscreen, so nothing is shown.
    """
    script = "import sys\n" + textwrap.dedent(startup) + "\n" + _AFTERWARDS
    env = {**os.environ, **(env or {}), "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run(
        [sys.executable, "-c", script], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
    )
    return Outcome(done.returncode, done.stdout, done.stderr)
