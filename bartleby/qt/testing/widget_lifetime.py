"""Free the top-level widgets each test built, before the next test starts, and catch one shown parentless.

Moved from BartleBot's ``tests/conftest.py`` with the first widgets core holds that a test
builds by the dozen (cards, sections, the Log tab); ``parentless_shows`` followed with the
player cards (Stage 8 of BartleBot #167), which every product's tests build. A product's
or core's own ``conftest.py`` imports the names, which is what registers them with pytest::

    from bartleby.qt.testing.widget_lifetime import (  # noqa: F401 - registers them
        _free_widgets_this_test_created,
        parentless_shows,
        pytest_runtest_call,
    )

``tests/CLAUDE.md`` (core's) has why, under Widget lifetime.
"""

import pytest

_WIDGETS_BEFORE = pytest.StashKey[set[int]]()
_WIDGETS_PINNED = pytest.StashKey[list]()


@pytest.hookimpl(wrapper=True, trylast=True)
def pytest_runtest_call(item):
    """Hold the test's top-level widgets from the moment it returns until they are reaped.

    pytest-qt's own ``pytest_runtest_call`` wrapper is ``tryfirst``, so it is outermost and
    calls ``processEvents()`` after the test function returns but before any fixture
    teardown. By then a widget the test kept only in a local is garbage — a ``QDialog``
    with its signals connected to its own bound methods is a reference cycle — and the
    cyclic collector can run inside a paint event that pump is delivering. Collecting the
    wrapper deletes the C++ dialog, its popup, and the very row whose ``paintEvent`` is on
    the stack: an access violation in ``QPainter``, and only when the allocation count
    happens to trip a collection there (BartleBot #80).

    This wrapper is innermost, so it runs first once the test returns, and a strong
    reference is all it takes. ``_free_widgets_this_test_created`` releases the pins
    after it has freed the widgets deterministically.
    """
    try:
        return (yield)
    finally:
        before_ids = item.stash.get(_WIDGETS_BEFORE, None)
        if before_ids is not None:
            from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

            if QApplication.instance() is not None:
                item.stash[_WIDGETS_PINNED] = [w for w in QApplication.topLevelWidgets() if id(w) not in before_ids]


@pytest.fixture(autouse=True)
def _free_widgets_this_test_created(request):
    """Destroy the top-level widgets a test built, before the next test starts.

    ``deleteLater()`` on its own frees nothing here: it posts a ``DeferredDelete``
    event that only arrives on an event-loop spin, and no Qt event loop runs during
    the suite. So every widget every test constructed used to stay alive for the
    whole session — 27,918 of them after 20 GUI files in one process.

    That is quadratic, not merely wasteful, because ``QApplication.setStyleSheet``
    delivers a ``StyleChange`` to every live widget: one app-wide theme broadcast
    cost 26 s at that population and 0.001 s at zero. Two tests in
    BartleBot's ``test_game_view_theme.py`` were spending 130 s between them on it.

    What is reaped, and why that is safe:

    * Only *top-level* widgets, since deleting one takes its children with it.
      41 parentless widgets were holding the other 20,000 alive, kept off the
      garbage collector by signal connections into the long-lived singletons.
    * Only widgets that did not exist when this fixture was set up. Being autouse
      and function-scoped, it is set up after every module- and session-scoped
      fixture and torn down after every other function-scoped one, so a widget
      owned by a wider-scoped fixture is already in ``before`` and survives, and a
      function-scoped fixture's widget is only freed once that fixture's own
      teardown has run.

    The scoping is the whole point. A blanket sweep over ``topLevelWidgets()``
    frees widgets a still-live fixture is holding, and the next
    ``processEvents()`` touches the corpse: exit 127, no traceback, reproducibly
    inside BartleBot's ``test_launch_card_widgets.py``. Reap per test, never globally.

    ``sendPostedEvents(None, DeferredDelete)`` is what actually performs the
    deletion, standing in for the event-loop spin. It flushes the whole queue,
    which is fine precisely because nothing accumulates in it any more: any
    ``deleteLater()`` a test posted names a widget this same teardown is freeing.
    """
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415 - keeps Qt out of non-GUI runs

    before = QApplication.topLevelWidgets() if QApplication.instance() is not None else []
    before_ids = {id(w) for w in before}
    request.node.stash[_WIDGETS_BEFORE] = before_ids
    yield
    if QApplication.instance() is None:
        return
    from PyQt6 import sip  # noqa: PLC0415
    from PyQt6.QtCore import QEvent, Qt  # noqa: PLC0415

    new = [w for w in QApplication.topLevelWidgets() if id(w) not in before_ids and not sip.isdeleted(w)]
    # Popups last. A QCompleter's popup is a parentless window the completer holds by raw
    # pointer and deletes itself, so freeing the popup first leaves its owner to free it
    # again: exit 127, no traceback, and only when topLevelWidgets() happens to list the
    # popup first. Freeing owners first lets each take its popup with it.
    popups = [w for w in new if w.windowType() == Qt.WindowType.Popup]
    for batch in ([w for w in new if w not in popups], popups):
        for widget in batch:
            if sip.isdeleted(widget):
                continue
            widget.close()
            widget.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    # Freed deterministically above, so the collector no longer has anything to race.
    if _WIDGETS_PINNED in request.node.stash:
        del request.node.stash[_WIDGETS_PINNED]
    # Held until here only so an id() cannot be recycled onto a new widget.
    del before


@pytest.fixture
def parentless_shows(qapp):
    """The class name of every widget shown while it had no parent, as the test goes on.

    Qt gives such a widget a window of its own, maps it, and unmaps it again the moment
    the next ``addWidget`` reparents it, so the user sees a window flash where the
    surface is about to appear. Nothing in the finished widget tree records it, which
    is why this watches the events instead: a filter on the ``QApplication`` sees the
    ``Show`` delivered to every object in the process.

    Ask for it in the test that builds a new dialog, card or row, build the surface, and
    assert the list is empty. Opt-in rather than autouse: a filter on every event of
    every test is paid thousands of times, and some tests show a top-level widget on purpose.
    ``bartleby/qt/CLAUDE.md``, "Never show a widget before it has a parent", has the rule.
    """
    from PyQt6.QtCore import QEvent, QObject  # noqa: PLC0415
    from PyQt6.QtWidgets import QWidget  # noqa: PLC0415

    shown: list[str] = []

    class _Spy(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
            if event.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.parent() is None:
                shown.append(type(obj).__name__)
            return False

    spy = _Spy()
    qapp.installEventFilter(spy)
    try:
        yield shown
    finally:
        qapp.removeEventFilter(spy)
