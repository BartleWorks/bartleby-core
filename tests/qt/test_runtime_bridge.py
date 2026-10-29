"""The runtime's own thread, and the one crossing: commands in as coroutines, snapshots out as queued signals."""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from bartleby.qt.runtime_bridge import RuntimeSignals, RuntimeThread, TileCrossing


@pytest.fixture
def runtime():
    thread = RuntimeThread("sample-runtime")
    thread.start()
    yield thread
    thread.stop()


def test_a_command_runs_on_the_runtime_thread_and_its_result_comes_back(runtime):
    async def where():
        return threading.current_thread().name

    assert runtime.submit(where).result(timeout=5) == "sample-runtime"


def test_a_failing_command_is_logged_and_the_loop_runs_on(runtime, caplog):
    async def broken():
        raise RuntimeError("a bug in a command")

    async def fine():
        return "after"

    with caplog.at_level(logging.ERROR, logger="bartleby.qt.runtime_bridge"):
        assert runtime.submit(broken, what="Stop all").result(timeout=5) is None

    assert "Stop all failed" in caplog.text
    assert "a bug in a command" in caplog.text
    assert runtime.submit(fine).result(timeout=5) == "after"


def test_a_plain_function_can_be_run_on_the_loops_thread(runtime):
    ran = threading.Event()
    names = []

    def note():
        names.append(threading.current_thread().name)
        ran.set()

    runtime.call(note)

    assert ran.wait(5)
    assert names == ["sample-runtime"]


def test_a_signal_emitted_on_the_runtime_thread_is_received_on_the_gui_thread(qtbot, runtime):
    signals = RuntimeSignals()
    received = []
    signals.log_record.connect(
        lambda entry: received.append((entry, threading.current_thread() is threading.main_thread()))
    )

    async def publish():
        signals.log_record.emit("a record")

    runtime.submit(publish).result(timeout=5)
    qtbot.waitUntil(lambda: bool(received), timeout=5000)

    assert received == [("a record", True)]


def test_stopping_cancels_what_is_still_running_and_ends_the_thread():
    thread = RuntimeThread()
    thread.start()
    cancelled = threading.Event()

    async def forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def start_forever():
        asyncio.get_running_loop().create_task(forever())
        await asyncio.sleep(0)

    thread.submit(start_forever).result(timeout=5)
    thread.stop()

    assert cancelled.is_set()
    assert thread.loop.is_closed()
    assert thread.name == "runtime"


class _View:
    """A game view's three methods, noting the thread each was called on."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object, str]] = []

    def sync_tiles(self, wanted) -> None:
        self.calls.append(("tiles", wanted, threading.current_thread().name))

    def set_wizard_names(self, names) -> None:
        self.calls.append(("names", names, threading.current_thread().name))

    def unembed_all(self) -> None:
        self.calls.append(("unembed", None, threading.current_thread().name))


def test_a_manager_on_the_runtime_thread_reaches_the_view_on_the_gui_thread_in_order(qtbot, runtime):
    view = _View()
    crossing = TileCrossing(view)

    def from_the_runtime():
        crossing.sync_tiles(("tile",))
        crossing.set_wizard_names(("Wizard",))
        crossing.unembed_all()

    runtime.call(from_the_runtime)
    qtbot.waitUntil(lambda: len(view.calls) == 3, timeout=5000)

    gui = threading.current_thread().name
    assert view.calls == [("tiles", ["tile"], gui), ("names", ["Wizard"], gui), ("unembed", None, gui)]


def test_a_call_from_the_gui_thread_waits_for_qt_s_queue_too(qtbot):
    view = _View()
    crossing = TileCrossing(view)

    crossing.sync_tiles([])

    assert view.calls == []
    qtbot.waitUntil(lambda: len(view.calls) == 1, timeout=5000)
