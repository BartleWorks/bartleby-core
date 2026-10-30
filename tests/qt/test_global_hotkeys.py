"""The authoring hotkeys (#81): binding syntax, arming, and dispatch of WM_HOTKEY."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QByteArray

from bartleby.qt.global_hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    WM_HOTKEY,
    GlobalHotkeys,
    binding_problems,
    parse_binding,
)
from bartleby.runtime.game.game_keybindings import GameBinding


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("F1", (0, 0x70)),
        ("F24", (0, 0x87)),
        ("Shift+F2", (MOD_SHIFT, 0x71)),
        ("Ctrl+Alt+K", (MOD_CONTROL | MOD_ALT, ord("K"))),
        ("8", (0, ord("8"))),
        ("", None),
        ("Space", None),
        ("F25", None),
        ("Meta+F1", None),
    ],
)
def test_a_binding_parses_to_its_modifiers_and_virtual_key(text, expected):
    assert parse_binding(text) == expected


def _hotkeys(on_hotkey=None) -> tuple[GlobalHotkeys, MagicMock]:
    hk = GlobalHotkeys(on_hotkey or (lambda _a: None))
    user32 = MagicMock()
    user32.RegisterHotKey.return_value = 1
    hk._user32 = user32
    return hk, user32


def test_keys_are_registered_only_while_armed(qapp):
    hk, user32 = _hotkeys()
    hk.set_bindings({"xyz_sync": "F1", "toggle_speed": ""})
    user32.RegisterHotKey.assert_not_called()

    hk.set_armed(True)
    assert hk.registered_actions == ["xyz_sync"]
    _hwnd, _id, modifiers, vk = user32.RegisterHotKey.call_args.args
    assert modifiers == MOD_NOREPEAT and vk == 0x70

    hk.set_armed(False)
    assert hk.registered_actions == []
    user32.UnregisterHotKey.assert_called_once()


def test_a_key_another_application_holds_is_skipped(qapp):
    hk, user32 = _hotkeys()
    user32.RegisterHotKey.return_value = 0
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    assert hk.registered_actions == []


def test_a_refused_key_is_reported_as_held_elsewhere(qapp, caplog):
    hk, user32 = _hotkeys()
    user32.RegisterHotKey.return_value = 0
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    assert "xyz_sync is already registered elsewhere" in caplog.text


def test_two_actions_on_one_key_are_reported_as_our_own_clash(qapp, caplog):
    """Windows refuses the second one exactly as it refuses another application's key, so
    the clash has to be caught before asking it. The alphabetically first action keeps it."""
    hk, user32 = _hotkeys()
    hk.set_bindings({"toggle_speed_authoring": "F5", "toggle_dialogue": "F5", "xyz_sync": "F1"})
    hk.set_armed(True)

    assert hk.registered_actions == ["toggle_dialogue", "xyz_sync"]
    assert user32.RegisterHotKey.call_count == 2
    assert (
        "The hotkeys for toggle_dialogue and toggle_speed_authoring are the same key; only toggle_dialogue gets it"
        in caplog.text
    )
    assert "elsewhere" not in caplog.text

    # Once a session, not on every focus change.
    hk.set_armed(False)
    hk.set_armed(True)
    assert caplog.text.count("are the same key") == 1


def test_an_unbindable_key_is_reported_and_left_out(qapp):
    hk, _user32 = _hotkeys()
    assert hk.set_bindings({"xyz_sync": "Space", "toggle_speed": "F3"}) == ["xyz_sync"]
    hk.set_armed(True)
    assert hk.registered_actions == ["toggle_speed"]


def test_rebinding_while_armed_registers_the_new_keys(qapp):
    hk, user32 = _hotkeys()
    hk.set_bindings({"xyz_sync": "F1"})
    hk.set_armed(True)
    hk.set_bindings({"xyz_sync": "F5"})
    assert user32.RegisterHotKey.call_args.args[3] == 0x74
    assert hk.registered_actions == ["xyz_sync"]


def _message(message: int, w_param: int) -> ctypes.wintypes.MSG:
    msg = ctypes.wintypes.MSG()
    msg.message = message
    msg.wParam = w_param
    return msg


def test_a_hotkey_message_fires_its_action_and_is_consumed(qapp):
    pressed = []
    hk, user32 = _hotkeys(pressed.append)
    hk.set_bindings({"toggle_combat": "F4"})
    hk.set_armed(True)
    hotkey_id = user32.RegisterHotKey.call_args.args[1]

    msg = _message(WM_HOTKEY, hotkey_id)
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg)) == (True, 0)
    assert pressed == ["toggle_combat"]

    other = _message(0x0100, hotkey_id)  # WM_KEYDOWN
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(other)) == (False, 0)
    assert pressed == ["toggle_combat"]


def test_a_handler_that_raises_does_not_escape_the_filter(qapp):
    def boom(_action):
        raise RuntimeError("handler")

    hk, user32 = _hotkeys(boom)
    hk.set_bindings({"toggle_combat": "F4"})
    hk.set_armed(True)
    msg = _message(WM_HOTKEY, user32.RegisterHotKey.call_args.args[1])
    assert hk.nativeEventFilter(QByteArray(b"windows_generic_MSG"), ctypes.addressof(msg)) == (True, 0)


# ---------------------------------------------------------------------------
# The settings behind them, and Bar A
# ---------------------------------------------------------------------------


# -- what is wrong with a set of bindings --------------------------------------------

_LABELS = {"stop": "Stop all", "skip": "Skip dialogue"}
_PRINT = [GameBinding("PrintScreen", "F12")]


def test_bindings_with_nothing_wrong_have_no_problem():
    assert binding_problems({"stop": "Shift+F5", "skip": "F7"}, _LABELS, _PRINT) == []


def test_a_key_the_game_binds_is_a_problem_named_by_the_products_label():
    assert binding_problems({"skip": "F12"}, _LABELS, _PRINT) == [
        "Skip dialogue: F12 is the game's key for Print Screen, which won't work in the game while this is set."
    ]


def test_two_actions_on_one_key_and_a_key_nothing_registers_are_problems():
    assert binding_problems({"stop": "F7", "skip": "F7"}, _LABELS) == [
        "Skip dialogue and Stop all both use F7. Choose another key for one of them."
    ]
    assert binding_problems({"stop": "Space"}, _LABELS) == [
        "Stop all: Space can't be used. Choose F1–F24, a letter or a digit."
    ]
