"""Listeners: a Qt-free signal that calls each listener in turn and survives one that raises."""

import logging

from bartleby.shared.listeners import Listeners


def test_every_listener_hears_each_emit_in_the_order_it_connected():
    heard = []
    changed = Listeners()
    changed.connect(lambda value: heard.append(("first", value)))
    changed.connect(lambda value: heard.append(("second", value)))
    changed.emit(1)
    assert heard == [("first", 1), ("second", 1)]


def test_a_disconnected_listener_hears_nothing_more():
    heard = []
    changed = Listeners()
    disconnect = changed.connect(heard.append)
    changed.emit(1)
    disconnect()
    changed.emit(2)
    assert heard == [1]
    changed.disconnect(heard.append)  # never connected: quietly nothing


def test_a_listener_that_raises_is_logged_and_the_rest_still_hear(caplog):
    heard = []
    changed = Listeners("clients_changed")

    def broken(_value):
        raise RuntimeError("a listener's own bug")

    changed.connect(broken)
    changed.connect(heard.append)
    with caplog.at_level(logging.ERROR, logger="bartleby.shared.listeners"):
        changed.emit(3)
    assert heard == [3]
    assert "A clients_changed listener raised" in caplog.text
