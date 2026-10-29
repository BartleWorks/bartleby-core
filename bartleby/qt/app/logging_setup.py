"""Logging configuration: a per-session file, and a bridge to the in-app log widget.

Moved from BartleBot's ``bartlebot/logging_setup.py``. What made it BartleBot's -- its
name in the header and the file names, its developer mode, its build kind and its
settings -- now arrives as the :class:`~bartleby.shared.product.Product` and a
:class:`LogSettings`, so every app of the family starts its log the same way.

Three things here exist to make a session log readable rather than merely complete.
A five-hour four-client run produces tens of thousands of lines, and before the log-readability work the
overwhelming majority of them were noise of three specific kinds:

  * every node logged through BartleBot's ``BotMachine.log()``, so every record had
    the machine's one name and no node could be grepped for. ``ShortNameFormatter``
    and the per-node loggers ``BotMachine`` now creates are the other half of that fix.
  * the canvas echoed each transition to the log widget, and those records went to the
    file too, saying a second time what the machine had just said. ``gui_only`` records
    are dropped from the file for that reason.
  * a single failing step repeated the same warning hundreds of times.
    ``RepeatCollapsingFilter`` keeps the first and counts the rest.
"""

from __future__ import annotations

import asyncio
import datetime
import importlib.metadata
import logging
import logging.handlers
import platform
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from bartleby.shared.product import Product, current

#: Records carrying this attribute are for the in-app log widget only and never
#: reach the session file. Set it with ``logger.info(msg, extra=GUI_ONLY)`` on a
#: message that restates something another logger has already written.
GUI_ONLY = {"gui_only": True}

#: Libraries whose versions the session header names, beside the app's own, unless the app
#: names its own (BartleBot adds qasync and transitions). A packaged build carries their
#: metadata only because the app's packager copies it.
HEADER_LIBRARIES = ("PyQt6", "wizwalker")

#: The record attribute that says which client a line is about, by its slot: set it with
#: ``logger.info(msg, extra={CLIENT_SLOT: slot})``. The Log tab can filter on it.
CLIENT_SLOT = "client_slot"

#: Third-party loggers whose DEBUG output is not actionable here. Every one of these
#: is startup chatter about platforms we do not run on — keyring probing KWallet and
#: SecretService, wakepy enumerating GNOME and macOS methods — and it used to be the
#: first thing anyone opening a log saw.
_NOISY_LIBRARIES = (
    "asyncio",
    "keyring",
    # Pillow logs every image plugin it imports and every TIFF tag it reads, on each art extraction.
    "PIL",
    "qasync",
    "transitions",
    "wakepy",
    "win32ctypes",
)

#: wizwalker's own DEBUG output is per-memory-read; INFO is the useful level.
_WIZWALKER_LEVEL = logging.INFO

#: wizsprinter's record of what each turn decided and why: the hand, every priority
#: walked, every fuse, the card cast and at which slot. It writes at DEBUG, which the
#: wizwalker level above drops, so ``set_turn_trace`` is what lets it through.
TURN_TRACE_LOGGER = "wizwalker.extensions.wizsprinter.turns"

_FILE_FORMAT = "[%(asctime)s.%(msecs)03d] [%(levelname)-7s] %(name)s: %(message)s"
_GUI_FORMAT = "[%(asctime)s] [%(levelname)-7s] %(name)s: %(message)s"


class LogSettings(Protocol):
    """What logging reads from an app's settings. BartleBot's ``SettingsManager`` answers it."""

    #: Whether wizsprinter's turn trace goes into the log (:func:`set_turn_trace`).
    record_combat_decisions: bool
    #: How many lines the Log tab keeps (``bartleby.qt.sidebar.log_panel``); older ones scroll away.
    log_max_lines: int


@dataclass(frozen=True)
class LogEntry:
    """One record as a GUI callback receives it: its fields, so a Log tab can filter, and its line."""

    #: When it was logged, as ``time.time()`` gives it.
    time: float
    level: int
    #: The logger's name, with the prefixes the formatter drops left off: ``node.quicksell``.
    logger: str
    #: The message, its arguments filled in.
    message: str
    #: The client it is about, by slot, when the record says (:data:`CLIENT_SLOT`).
    slot: int | None
    #: The whole line, formatted as the Log tab shows it.
    text: str


class ShortNameFormatter(logging.Formatter):
    """Formatter that drops the app's package prefix and ``bartleby.`` from logger names.

    Every line in a session log is from this application or the library it is built on
    (bartleby-core, whose lines read ``runtime.clients.group_manager``), so the prefix is
    characters of nothing on every one of them. What is left is what distinguishes
    the lines — ``node.quicksell``, ``bot.machine``, ``gui.canvas.bot_canvas`` —
    and third-party loggers keep their full names, which is how you can still tell
    them apart at a glance.
    """

    def __init__(self, fmt: str | None = None, datefmt: str | None = None, *, prefixes: Sequence[str] = ()) -> None:
        super().__init__(fmt, datefmt)
        #: The prefixes to drop, the first that matches: the app's package and this library's.
        self.prefixes = tuple(prefixes) or ("bartleby.",)

    def short_name(self, name: str) -> str:
        return next((name.removeprefix(p) for p in self.prefixes if name.startswith(p)), name)

    def format(self, record: logging.LogRecord) -> str:
        # Restored afterwards: the same record is handed to every other handler.
        original = record.name
        record.name = self.short_name(original)
        try:
            return super().format(record)
        finally:
            record.name = original


class GuiOnlyFilter(logging.Filter):
    """Drops records marked GUI_ONLY. Installed on the session-file handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "gui_only", False)


class RepeatCollapsingFilter(logging.Filter):
    """Emits the first of a run of identical messages and counts the rest.

    A bot that fails the same step for four hours writes the same WARNING several
    hundred times; one representative run held 1,282 warnings that were five
    distinct messages. Suppressing the repeats outright would lose the fact that it
    kept happening, so the count is reported when the streak ends::

        [WARNING] node.handle_combat: timed out after 30.0s waiting for combat
        ... last message repeated 521 more times
        [INFO   ] node.quicksell: quicksell: keep_list=[...]

    Only *consecutive* identical messages from the same logger at the same level
    collapse, so two failures alternating are both still visible — which is the
    case where a count would actually hide something.

    The summary goes straight to the handler this filter is installed on rather
    than through a logger, because a logger would hand it to every other handler
    and re-enter this filter.
    """

    def __init__(self, handler: logging.Handler) -> None:
        super().__init__()
        self._handler = handler
        self._last: tuple[str, int, str] | None = None
        self._repeats = 0

    def filter(self, record: logging.LogRecord) -> bool:
        key = (record.name, record.levelno, record.getMessage())
        if key == self._last:
            self._repeats += 1
            return False
        self._flush(record)
        self._last = key
        return True

    def _flush(self, following: logging.LogRecord) -> None:
        """Emit the "repeated N more times" line for the streak that just ended."""
        if not self._repeats or self._last is None:
            return
        count, self._repeats = self._repeats, 0
        name, levelno, _ = self._last
        summary = logging.LogRecord(
            name=name,
            level=levelno,
            pathname=following.pathname,
            lineno=following.lineno,
            msg="... last message repeated %d more time%s",
            args=(count, "" if count == 1 else "s"),
            exc_info=None,
        )
        # emit() is normally called by handle() under the handler's lock; this call
        # bypasses handle() to avoid re-entering this filter, so it takes the lock itself.
        try:
            self._handler.acquire()
            try:
                self._handler.emit(summary)
            finally:
                self._handler.release()
        except Exception:
            pass  # a summary line must never take the record that follows it down


class _GuiBridgeHandler(logging.Handler):
    """Forwards each log record, as a :class:`LogEntry`, to registered GUI callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[Callable[[LogEntry], None]] = []
        # The callbacks safe to call on the thread that logged. See add_gui_callback.
        self._any_thread: list[Callable[[LogEntry], None]] = []
        # The GUI thread's loop, taken when a callback is added, for a record logged from
        # another thread. See emit.
        self._loop: asyncio.AbstractEventLoop | None = None

    def add_callback(self, cb: Callable[[LogEntry], None], *, any_thread: bool = False) -> None:
        if any_thread:
            self._any_thread.append(cb)
            return
        self._callbacks.append(cb)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def remove_callback(self, cb: Callable[[LogEntry], None]) -> None:
        self._callbacks = [c for c in self._callbacks if c is not cb]
        self._any_thread = [c for c in self._any_thread if c is not cb]

    def emit(self, record: logging.LogRecord) -> None:
        entry = self._entry(record)
        self._deliver(entry, self._any_thread)
        if threading.current_thread() is threading.main_thread():
            self._deliver(entry, self._callbacks)
            return
        # A worker thread -- asyncio.to_thread's: the update check, an art extraction -- must
        # never touch a widget, and the callbacks write the log panel. The line goes to the
        # GUI thread's loop, or, with none, only to the session file (#154).
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._deliver, entry, self._callbacks)
            except RuntimeError:
                pass  # the loop closed in between

    def _entry(self, record: logging.LogRecord) -> LogEntry:
        text = self.format(record)
        formatter = self.formatter
        name = formatter.short_name(record.name) if isinstance(formatter, ShortNameFormatter) else record.name
        slot = getattr(record, CLIENT_SLOT, None)
        return LogEntry(
            time=record.created,
            level=record.levelno,
            logger=name,
            message=record.getMessage(),
            slot=slot if isinstance(slot, int) else None,
            text=text,
        )

    @staticmethod
    def _deliver(entry: LogEntry, callbacks: list[Callable[[LogEntry], None]]) -> None:
        for cb in list(callbacks):
            try:
                cb(entry)
            except Exception:
                pass  # never let GUI errors silence the logger


_gui_bridge = _GuiBridgeHandler()


def prune_session_logs(log_dir: Path, prefix: str, keep: int = 10) -> None:
    """Delete all but the *keep* most recent session logs in *log_dir*.

    Only ``<prefix>_*.log`` files are touched, the app's own (``bartlebot_*.log``). A file
    that cannot be deleted -- still open in another running instance -- is left for a later
    startup to try again.
    """
    logs = sorted(log_dir.glob(f"{prefix}_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in logs[keep:]:
        try:
            old.unlink()
        except OSError:
            pass


def session_log_dir(product: Product | None = None) -> Path:
    """Where session logs go: the product's :attr:`~bartleby.shared.product.Product.log_dir`, never the install directory.

    An update replaces the install directory whole, so logs there would be lost with it
    (BartleBot #168). In BartleBot's developer mode the user root is the repo, so they stay
    in its ``logs/``. Without *product*, the running one's.
    """
    return (product or current()).log_dir


def configure_logging(
    product: Product,
    settings: LogSettings | Callable[[], LogSettings],
    *,
    libraries: Sequence[str] = HEADER_LIBRARIES,
) -> None:
    """Call once at startup, before anything else logs: the session file, the GUI bridge and the header.

    The header names *product* and its version and build, then each of *libraries* with
    its version. *settings* may be a function that gives them, called only once the
    handlers exist, so settings that log as they load (BartleBot's) log into the session.
    """
    log_dir = session_log_dir(product)
    log_dir.mkdir(parents=True, exist_ok=True)

    session_name = datetime.datetime.now().strftime(f"{product.slug}_%Y-%m-%d_%H-%M-%S.log")
    file_handler = logging.FileHandler(log_dir / session_name, encoding="utf-8")
    # The file carries the date. A session that starts at 21:49 and ends at 01:13 reads
    # as though it runs backwards without one, and a bare "%H:%M:%S" gives a line nothing
    # to be lined up against anything outside the file. The milliseconds let one line be
    # timed against the next: a combat round lasts a few seconds.
    prefixes = (f"{product.slug}.", "bartleby.")
    file_handler.setFormatter(ShortNameFormatter(_FILE_FORMAT, datefmt="%Y-%m-%d %H:%M:%S", prefixes=prefixes))
    file_handler.addFilter(GuiOnlyFilter())
    file_handler.addFilter(RepeatCollapsingFilter(file_handler))

    prune_session_logs(log_dir, product.slug)

    # The widget is watched live, where the date is never in question and the column
    # would cost width the message needs, so it keeps the time alone, to the second.
    _gui_bridge.setFormatter(ShortNameFormatter(_GUI_FORMAT, datefmt="%H:%M:%S", prefixes=prefixes))
    _gui_bridge.addFilter(RepeatCollapsingFilter(_gui_bridge))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(_gui_bridge)

    # Suppress chatty third-party debug output
    for name in _NOISY_LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("wizwalker").setLevel(_WIZWALKER_LEVEL)

    logger = logging.getLogger(__name__)
    logger.info("=== %s startup ===", product.name)
    logger.info("Version: %s", product.version)
    logger.info("Python %s", sys.version.split()[0])
    logger.info("Platform: %s", platform.platform())
    logger.info("Build: %s", product.build_kind)
    for lib in libraries:
        logger.info("%s==%s", lib, _distribution_version(lib))

    if callable(settings):
        settings = settings()
    if set_turn_trace(settings.record_combat_decisions, dev=product.dev_mode):
        logger.info("Combat decision trace on; each turn's choices are logged at DEBUG")


def _distribution_version(name: str) -> str:
    """The installed version of ``name``, or ``unknown``, said rather than left out, when no metadata is found."""
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def set_turn_trace(enabled: bool, *, dev: bool | None = None) -> bool:
    """Let wizsprinter's turn trace into the log when *enabled*, or in developer mode.

    It is the only record of why a turn cast what it did, but it runs to a few kilobytes
    a round for each client, so a user turns it on from BartleBot's Settings › Combat to
    look into a fight, and developer mode always has it. Off, the logger inherits
    wizwalker's level, which drops it. Returns whether it is now on. *dev* is the
    product's developer mode, the running product's when left out.
    """
    on = enabled or (current().dev_mode if dev is None else dev)
    logging.getLogger(TURN_TRACE_LOGGER).setLevel(logging.DEBUG if on else logging.NOTSET)
    return on


def install_excepthook() -> None:
    """Log an exception nothing caught, and carry on (#154).

    PyQt6 aborts the process on an exception that escapes a slot unless ``sys.excepthook``
    has been replaced. A packaged build has no console, so the abort left no trace at all,
    and it took every embedded game window with it. Replaced, the exception is logged at
    CRITICAL with its traceback and the app keeps running. Threads get the same, since a
    worker's uncaught exception otherwise goes only to the missing console.
    """
    log = logging.getLogger(__name__)

    def _hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        name = args.thread.name if args.thread is not None else "?"
        log.critical(
            "Uncaught exception in thread %s", name, exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
        )

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def add_gui_callback(cb: Callable[[LogEntry], None], *, any_thread: bool = False) -> None:
    """Hand *cb* every record from now on, as a :class:`LogEntry`, on the GUI thread.

    A record logged on another thread reaches the GUI thread through its asyncio loop, so
    an app with none there (BartleBuddy, whose GUI runs Qt's own loop) passes
    *any_thread*: *cb* is then called on whichever thread logged, which is safe only for
    a callback that crosses threads itself, as a queued signal's ``emit`` does
    (``bartleby.qt.runtime_bridge.RuntimeSignals.log_record``).
    """
    _gui_bridge.add_callback(cb, any_thread=any_thread)


def remove_gui_callback(cb: Callable[[LogEntry], None]) -> None:
    _gui_bridge.remove_callback(cb)
