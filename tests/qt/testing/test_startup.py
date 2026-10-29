"""An exception nothing caught, in a Qt slot and in a worker thread, is logged and the app runs on (BartleBot #154).

Each in a process of its own, since inside the suite pytest's hook is already in place.
Each product's suite runs the same check over its own entry point's startup.
"""

from __future__ import annotations

from bartleby.qt.testing.startup import raise_in_a_slot_and_a_thread

#: The hook alone, with the log in a file the test reads.
_HOOKED = """
import logging
logging.basicConfig(filename={log!r}, level=logging.DEBUG, encoding="utf-8")
from bartleby.qt.app.logging_setup import install_excepthook
install_excepthook()
"""


def test_with_the_hook_both_are_logged_at_critical_with_their_tracebacks_and_the_app_runs_on(tmp_path):
    log = tmp_path / "session.log"

    outcome = raise_in_a_slot_and_a_thread(_HOOKED.format(log=str(log)))

    assert outcome.kept_running, outcome.stderr
    text = log.read_text(encoding="utf-8")
    assert "CRITICAL:bartleby.qt.app.logging_setup:Uncaught exception\n" in text
    assert "CRITICAL:bartleby.qt.app.logging_setup:Uncaught exception in thread worker\n" in text
    assert text.count("Traceback (most recent call last)") == 2
    assert "RuntimeError: raised in a slot" in text
    assert "RuntimeError: raised in a thread" in text


def test_without_the_hook_pyqt_aborts_the_app():
    """What the hook is for, and why the check runs in a process: the abort is real, and silent."""
    outcome = raise_in_a_slot_and_a_thread("")

    assert outcome.returncode != 0
    assert not outcome.kept_running
