"""Tests for the log-readability machinery, moved from BartleBot's ``tests/test_logging_setup.py``.

These three pieces are what turn a session log from complete into readable, and
each of them can get a run wrong in a way that is invisible until someone is
reading a 49,000-line file trying to find where a bot broke: a dropped record,
a miscounted repeat, a mangled logger name.
"""

import asyncio
import importlib.metadata
import logging
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from bartleby.qt.app import logging_setup
from bartleby.qt.app.logging_setup import (
    CLIENT_SLOT,
    GUI_ONLY,
    TURN_TRACE_LOGGER,
    GuiOnlyFilter,
    LogEntry,
    RepeatCollapsingFilter,
    ShortNameFormatter,
    _GuiBridgeHandler,
    install_excepthook,
    prune_session_logs,
    set_turn_trace,
)

#: The prefixes the app's formatters drop: the sample product's package, then this library's.
PREFIXES = ("sample.", "bartleby.")


class _CapturingHandler(logging.Handler):
    """Handler that keeps the records it emits, formatted and raw."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def messages(self) -> list[str]:
        return [r.getMessage() for r in self.records]


@pytest.fixture
def handler():
    return _CapturingHandler()


def _record(name: str = "sample.node.quicksell", msg: str = "hello", level: int = logging.INFO, **extra):
    record = logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=(), exc_info=None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


# ---------------------------------------------------------------------------
# ShortNameFormatter
# ---------------------------------------------------------------------------


def test_the_products_prefix_is_dropped():
    formatter = ShortNameFormatter("%(name)s: %(message)s", prefixes=PREFIXES)

    assert formatter.format(_record(name="sample.node.quicksell")) == "node.quicksell: hello"


def test_this_librarys_prefix_is_dropped_too():
    formatter = ShortNameFormatter("%(name)s: %(message)s", prefixes=PREFIXES)

    assert formatter.format(_record(name="bartleby.runtime.input")) == "runtime.input: hello"


def test_with_no_prefixes_given_only_this_librarys_is_dropped():
    formatter = ShortNameFormatter("%(name)s")

    assert formatter.format(_record(name="bartleby.runtime.input")) == "runtime.input"
    assert formatter.format(_record(name="sample.node.quicksell")) == "sample.node.quicksell"


def test_third_party_names_are_left_alone():
    """Keeping their full names is how a wizwalker line still reads as one."""
    formatter = ShortNameFormatter("%(name)s: %(message)s", prefixes=PREFIXES)

    assert formatter.format(_record(name="wizwalker.memory")) == "wizwalker.memory: hello"


def test_formatting_does_not_mutate_the_record():
    """The same record goes on to every other handler, which must see the real name."""
    formatter = ShortNameFormatter("%(name)s: %(message)s", prefixes=PREFIXES)
    record = _record(name="sample.bot.machine")

    formatter.format(record)

    assert record.name == "sample.bot.machine"


def test_the_name_survives_a_formatting_error():
    """A bad format string must not leave every later handler with a truncated name."""
    formatter = ShortNameFormatter("%(nonexistent)s", prefixes=PREFIXES)
    record = _record(name="sample.bot.machine")

    with pytest.raises(ValueError):
        formatter.format(record)

    assert record.name == "sample.bot.machine"


# ---------------------------------------------------------------------------
# GuiOnlyFilter
# ---------------------------------------------------------------------------


def test_gui_only_records_are_dropped():
    assert GuiOnlyFilter().filter(_record(**GUI_ONLY)) is False


def test_ordinary_records_pass():
    assert GuiOnlyFilter().filter(_record()) is True


# ---------------------------------------------------------------------------
# RepeatCollapsingFilter
# ---------------------------------------------------------------------------


def test_the_first_of_a_run_is_kept(handler):
    collapse = RepeatCollapsingFilter(handler)

    assert collapse.filter(_record(msg="timed out")) is True
    assert collapse.filter(_record(msg="timed out")) is False
    assert collapse.filter(_record(msg="timed out")) is False


def test_the_streak_is_counted_when_it_ends(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for _ in range(4):
        handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="something else"))

    assert handler.messages == [
        "timed out",
        "... last message repeated 3 more times",
        "something else",
    ]


def test_a_single_repeat_reads_as_one_time(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="timed out"))
    handler.handle(_record(msg="something else"))

    assert handler.messages[1] == "... last message repeated 1 more time"


def test_the_summary_keeps_the_name_and_level_of_what_it_summarizes(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(name="sample.node.handle_combat", msg="timed out", level=logging.WARNING))
    handler.handle(_record(name="sample.node.handle_combat", msg="timed out", level=logging.WARNING))
    handler.handle(_record(name="sample.node.quicksell", msg="selling", level=logging.INFO))

    summary = handler.records[1]
    assert summary.name == "sample.node.handle_combat"
    assert summary.levelno == logging.WARNING


def test_alternating_messages_never_collapse(handler):
    """Two failures taking turns is the case where a count would hide something."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for _ in range(3):
        handler.handle(_record(msg="a"))
        handler.handle(_record(msg="b"))

    assert handler.messages == ["a", "b", "a", "b", "a", "b"]


def test_the_same_text_from_two_loggers_is_two_messages(handler):
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(name="sample.node.interact", msg="clicking"))
    handler.handle(_record(name="sample.node.click_path", msg="clicking"))

    assert handler.messages == ["clicking", "clicking"]


def test_the_same_text_at_two_levels_is_two_messages(handler):
    """A message promoted to WARNING is news, not a repeat."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    handler.handle(_record(msg="inventory full", level=logging.DEBUG))
    handler.handle(_record(msg="inventory full", level=logging.WARNING))

    assert [r.levelno for r in handler.records] == [logging.DEBUG, logging.WARNING]


def test_interpolated_args_are_compared_after_interpolation(handler):
    """A "pass %d" line with different numbers is different text, and must not collapse."""
    collapse = RepeatCollapsingFilter(handler)
    handler.addFilter(collapse)

    for n in (1, 2, 3):
        record = _record(msg="=== pass %d ===")
        record.args = (n,)
        handler.handle(record)

    assert handler.messages == ["=== pass 1 ===", "=== pass 2 ===", "=== pass 3 ==="]


def test_a_failing_summary_does_not_take_the_next_record_down(handler):
    """The next real record matters more than the bookkeeping line in front of it."""

    class _Exploding(_CapturingHandler):
        def emit(self, record):
            if record.getMessage().startswith("..."):
                raise RuntimeError("boom")
            super().emit(record)

    exploding = _Exploding()
    exploding.addFilter(RepeatCollapsingFilter(exploding))

    exploding.handle(_record(msg="timed out"))
    exploding.handle(_record(msg="timed out"))
    exploding.handle(_record(msg="something else"))

    assert exploding.messages == ["timed out", "something else"]


# ---------------------------------------------------------------------------
# prune_session_logs
# ---------------------------------------------------------------------------


def _session_logs(log_dir, count: int) -> list:
    """*count* session logs, oldest first, with mtimes a minute apart."""
    logs = []
    for i in range(count):
        path = log_dir / f"sample_2026-09-{i + 1:02d}_12-00-00.log"
        path.write_text("x")
        os.utime(path, (1_000_000 + i * 60, 1_000_000 + i * 60))
        logs.append(path)
    return logs


def test_prune_keeps_the_ten_newest_session_logs(tmp_path):
    logs = _session_logs(tmp_path, 13)

    prune_session_logs(tmp_path, "sample")

    assert sorted(tmp_path.iterdir()) == logs[3:]


def test_prune_goes_by_modification_time_not_name(tmp_path):
    """A log whose name sorts oldest is kept when it was the last one written to."""
    logs = _session_logs(tmp_path, 11)
    os.utime(logs[0], (9_999_999, 9_999_999))

    prune_session_logs(tmp_path, "sample")

    assert logs[0].exists()
    assert not logs[1].exists()


def test_prune_leaves_other_files_alone(tmp_path):
    _session_logs(tmp_path, 12)
    others = [
        tmp_path / "notes.txt",
        tmp_path / "sample_crash.dmp",
        tmp_path / "other_2026-01-01_12-00-00.log",
        tmp_path / "other_2026-01-01.log",
    ]
    for path in others:
        path.write_text("keep me")
        os.utime(path, (1, 1))  # older than every session log

    prune_session_logs(tmp_path, "sample")

    assert all(path.exists() for path in others)


def test_prune_skips_a_log_it_cannot_delete(tmp_path, monkeypatch):
    """A log held open by another running instance must not stop startup, or the rest of the prune."""
    logs = _session_logs(tmp_path, 12)
    real_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == logs[0]:
            raise PermissionError("in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)

    prune_session_logs(tmp_path, "sample")

    assert logs[0].exists()
    assert not logs[1].exists()


# ---------------------------------------------------------------------------
# _GuiBridgeHandler
# ---------------------------------------------------------------------------


def test_the_bridge_hands_each_callback_the_records_fields_and_its_line():
    bridge = _GuiBridgeHandler()
    bridge.setFormatter(ShortNameFormatter("[%(levelname)s] %(name)s: %(message)s", prefixes=PREFIXES))
    seen: list[LogEntry] = []
    bridge.add_callback(seen.append)
    record = _record(msg="hello", level=logging.WARNING)

    bridge.handle(record)

    assert seen == [
        LogEntry(
            time=record.created,
            level=logging.WARNING,
            logger="node.quicksell",
            message="hello",
            slot=None,
            text="[WARNING] node.quicksell: hello",
        )
    ]


def test_a_record_about_one_client_carries_its_slot():
    """So the Log tab can show one client's lines alone."""
    bridge = _GuiBridgeHandler()
    seen: list[LogEntry] = []
    bridge.add_callback(seen.append)

    bridge.handle(_record(**{CLIENT_SLOT: 2}))

    assert seen[0].slot == 2


def test_a_failing_gui_callback_does_not_silence_the_others():
    bridge = _GuiBridgeHandler()
    seen = []

    def broken(_entry):
        raise RuntimeError("widget already deleted")

    bridge.add_callback(broken)
    bridge.add_callback(lambda entry: seen.append(entry.message))

    bridge.handle(_record(msg="first"))
    bridge.handle(_record(msg="second"))

    assert seen == ["first", "second"]


def test_a_removed_callback_stops_receiving():
    bridge = _GuiBridgeHandler()
    seen = []

    def callback(entry):
        seen.append(entry.message)

    bridge.add_callback(callback)
    bridge.handle(_record(msg="before"))
    bridge.remove_callback(callback)
    bridge.handle(_record(msg="after"))

    assert seen == ["before"]


# ---------------------------------------------------------------------------
# The turn trace, and configure_logging
# ---------------------------------------------------------------------------


@pytest.fixture
def trace_level():
    """Put the turn trace's logger back as it was: its level is process-wide."""
    logger = logging.getLogger(TURN_TRACE_LOGGER)
    level = logger.level
    yield logger
    logger.setLevel(level)


def test_the_turn_trace_is_on_when_the_setting_is(trace_level):
    assert set_turn_trace(True) is True
    assert trace_level.level == logging.DEBUG


def test_off_the_turn_trace_inherits_wizwalkers_level(trace_level):
    assert set_turn_trace(False) is False
    assert trace_level.level == logging.NOTSET


def test_developer_mode_records_the_turn_trace_whatever_the_setting(trace_level):
    assert set_turn_trace(False, dev=True) is True
    assert trace_level.level == logging.DEBUG


def test_left_out_developer_mode_is_the_running_products(trace_level, monkeypatch, sample_product):
    monkeypatch.setattr(logging_setup, "current", lambda: replace(sample_product, dev_mode=True))

    assert set_turn_trace(False) is True


class _Settings:
    """What :class:`~bartleby.qt.app.logging_setup.LogSettings` asks of an app's settings."""

    def __init__(self, record_combat_decisions: bool = False) -> None:
        self.record_combat_decisions = record_combat_decisions


@pytest.fixture
def configured(tmp_path, sample_product):
    """Run configure_logging for the sample product into *tmp_path*, and take its handlers back off after."""
    root = logging.getLogger()
    quieted = [logging.getLogger(name) for name in ("wizwalker", "PIL")]
    saved = (list(root.handlers), root.level, [logger.level for logger in quieted])

    def configure(settings=None, **kwargs):
        logging_setup.configure_logging(replace(sample_product, log_root=tmp_path), settings or _Settings(), **kwargs)

    yield configure
    for added in [h for h in root.handlers if h not in saved[0]]:
        root.removeHandler(added)
        added.close()
    root.setLevel(saved[1])
    for logger, level in zip(quieted, saved[2]):
        logger.setLevel(level)


def _session_text(log_dir: Path) -> str:
    (session,) = log_dir.glob("sample_*.log")
    return session.read_text(encoding="utf-8")


@pytest.mark.parametrize("record", [True, False])
def test_the_session_file_has_the_turn_trace_only_when_asked(tmp_path, configured, trace_level, record):
    """And the rest of wizwalker, which reads memory at DEBUG, stays at INFO either way."""
    configured(_Settings(record_combat_decisions=record))
    logging.getLogger(TURN_TRACE_LOGGER).debug("round 2: the trace")
    logging.getLogger("wizwalker.memory").debug("a memory read")

    text = _session_text(tmp_path)
    assert ("round 2: the trace" in text) is record
    assert ("Combat decision trace on" in text) is record
    assert "a memory read" not in text


def test_the_session_header_names_the_product_its_version_and_build_and_pillow_stays_quiet(tmp_path, configured):
    """A pasted log says which app and version wrote it, and whether a build or a checkout did, and art extraction
    adds no Pillow chatter."""
    configured()
    logging.getLogger("PIL.Image").debug("Importing DdsImagePlugin")

    text = _session_text(tmp_path)
    assert "=== Sample startup ===" in text
    assert "Version: 1.2.3" in text
    assert "Build: checkout" in text
    for lib in logging_setup.HEADER_LIBRARIES:
        assert f"{lib}=={importlib.metadata.version(lib)}" in text
    assert "DdsImagePlugin" not in text


def test_the_header_names_the_libraries_the_product_lists(tmp_path, configured):
    configured(libraries=("pytest",))

    text = _session_text(tmp_path)
    assert f"pytest=={importlib.metadata.version('pytest')}" in text
    assert "PyQt6==" not in text


def test_the_session_file_drops_the_products_prefix(tmp_path, configured):
    configured()
    logging.getLogger("sample.node.quicksell").info("selling")

    assert "node.quicksell: selling" in _session_text(tmp_path)


def test_session_logs_are_pruned_to_the_products_ten_newest(tmp_path, configured):
    old = _session_logs(tmp_path, 12)

    configured()

    kept = sorted(tmp_path.glob("sample_*.log"))
    assert len(kept) == 10
    assert not any(path.exists() for path in old[:3])


def test_session_logs_go_in_the_products_own_folder(sample_product):
    assert logging_setup.session_log_dir(sample_product) == sample_product.data_dir / "logs"
    assert logging_setup.session_log_dir() == sample_product.data_dir / "logs", "the running product's"


def test_a_version_with_no_metadata_is_said_to_be_unknown():
    """A build missing a library's metadata says so in the header, rather than leaving the line out unnoticed."""
    assert logging_setup._distribution_version("no-such-distribution-anywhere") == "unknown"


# ---------------------------------------------------------------------------
# The uncaught-exception hook (#154)
# ---------------------------------------------------------------------------


@pytest.fixture
def hooks(monkeypatch):
    """Install the hooks for one test, and put the originals back after it."""
    import sys
    import threading

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    install_excepthook()
    return sys, threading


def test_an_uncaught_exception_is_logged_at_critical_with_its_traceback(hooks, caplog):
    sys, _threading = hooks
    try:
        raise RuntimeError("bug in a slot")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    record = next(r for r in caplog.records if r.levelno == logging.CRITICAL)
    assert record.exc_info[1].args == ("bug in a slot",)


def test_a_threads_uncaught_exception_is_logged_too(hooks, caplog):
    _sys, threading = hooks
    worker = threading.Thread(target=lambda: 1 / 0, name="worker")
    worker.start()
    worker.join()

    assert any(r.levelno == logging.CRITICAL and "worker" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# A worker thread's line reaches the log panel on the GUI thread (#154)
# ---------------------------------------------------------------------------


def _bridge_and_seen():
    bridge = _GuiBridgeHandler()
    seen: list[tuple[str, bool]] = []
    bridge.add_callback(
        lambda entry: seen.append((entry.message, threading.current_thread() is threading.main_thread()))
    )
    return bridge, seen


def _info(msg: str) -> logging.LogRecord:
    return logging.LogRecord("sample.x", logging.INFO, __file__, 1, msg, None, None)


async def test_a_line_logged_from_a_worker_thread_is_delivered_on_the_gui_thread():
    bridge, seen = _bridge_and_seen()

    await asyncio.to_thread(bridge.handle, _info("from a worker"))
    await asyncio.sleep(0)

    assert seen == [("from a worker", True)]


def test_a_line_logged_on_the_gui_thread_is_delivered_at_once():
    bridge, seen = _bridge_and_seen()

    bridge.handle(_info("from the GUI thread"))

    assert seen == [("from the GUI thread", True)]
