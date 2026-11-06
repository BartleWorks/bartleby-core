"""System-wide hotkeys, armed while a game window has the keyboard, and a product's one exception.

A game window embedded in BartleBot or BartleBuddy belongs to another process, so its
keystrokes never reach Qt and a ``QShortcut`` cannot see them. ``RegisterHotKey`` can:
Windows delivers ``WM_HOTKEY`` to the registering thread whichever window has the keyboard.

Two decisions follow from that key being system-wide:

* **Registered only while a game window holds the keyboard.** Registered any longer, F1
  would stop reaching every other application on the machine, and a binding on a digit
  would take the canvas's favorites keys and every text field in the app with it.
  ``GameView.game_focus_changed`` says when; ``set_armed`` follows it. The one exception is
  BartleBuddy's Stop all, armed all the time so control can be taken back from anywhere,
  which is why a key armed everywhere can't be one that types (``binding_refusals``).
* **Read through a Qt native event filter, not WizWalker's ``HotkeyListener``.** That
  listener takes ``WM_HOTKEY`` off the thread queue with its own ``PeekMessage`` loop, and
  under qasync Qt's dispatcher pumps the same queue first -- the message would be gone
  before the listener looked. Qt hands every message it pumps to its native filters.

Each set of keys armed on its own terms is a ``GlobalHotkeys`` of its own, with its own id
base. BartleBot has two: the authoring keys and speed authoring's digits. BartleBuddy has
three: Stop all, armed everywhere; the keys that type in chat (``types_in_chat``), which
stand down while the game's chat is open; and the rest.

A binding is a ``QKeySequence`` portable string of one key and its modifiers ("F1",
"Shift+F2", "Ctrl+8"). Only keys with a fixed virtual-key code are accepted -- F1-F24,
A-Z and 0-9 -- because the rest depend on the keyboard layout.

What is wrong with a set of bindings comes in two kinds. A refusal can't work: a key
nothing can register, two actions on one key, or a key that types on an action armed
everywhere (``binding_refusals``). A clash with the game works, and takes that key from the
game while a game window has the keyboard (``game_key_clashes``). BartleBot's Authoring tab
warns of both alike (``binding_problems``); BartleBuddy's Hotkeys tab refuses only the first.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence

from PyQt6.QtCore import QAbstractNativeEventFilter

from bartleby.runtime.game.game_keybindings import GameBinding
from bartleby.shared.product import current

logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

_MODIFIERS = {"ctrl": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT}

# Ids an application may register, per the RegisterHotKey docs, are 0x0000-0xBFFF. These
# sit near the top so they stay clear of anything else in the process that registers low.
# One base per ``GlobalHotkeys``: every instance registers on the same thread with no
# window, so two sharing ids would each replace the other's keys.
ID_BASE_AUTHORING = 0xB800
ID_BASE_SPEED_AUTHORING = 0xB900
#: BartleBuddy's sets (Stage 6 of its #14): Stop all, armed everywhere.
ID_BASE_EVERYWHERE = 0xBA00
#: BartleBuddy's keys armed while a game has the keyboard that don't type in chat: the F-keys and chords.
ID_BASE_IN_GAME = 0xBB00
#: BartleBuddy's keys armed while a game has the keyboard that do type in chat (``types_in_chat``).
ID_BASE_TYPING = 0xBC00

_VK_F1 = 0x70
_VK_F24 = 0x87


def parse_binding(text: str) -> tuple[int, int] | None:
    """``(modifiers, virtual_key)`` for a binding like ``"Shift+F2"``, or None if it has none.

    None for an empty string (an unbound action) and for anything not in the fixed-code set.
    """
    parts = [p.strip() for p in (text or "").split("+")]
    if not parts or not parts[-1]:
        return None
    modifiers = 0
    for name in parts[:-1]:
        flag = _MODIFIERS.get(name.lower())
        if flag is None:
            return None
        modifiers |= flag
    vk = _virtual_key(parts[-1])
    return None if vk is None else (modifiers, vk)


def types_in_chat(text: str) -> bool:
    """Whether the binding *text* types a character in a text box: a letter or digit with no Ctrl or Alt.

    Shift or not, since Shift only changes what it types. An F-key types nothing, and
    neither does a key held with Ctrl or Alt. False for an empty or unbindable *text*.
    """
    combo = parse_binding(text)
    if combo is None:
        return False
    modifiers, vk = combo
    return not modifiers & (MOD_CONTROL | MOD_ALT) and not _VK_F1 <= vk <= _VK_F24


def binding_problems(
    bindings: Mapping[str, str],
    labels: Mapping[str, str],
    game_bindings: Sequence[GameBinding] = (),
) -> list[str]:
    """What is wrong with a set of bindings, as sentences; empty when nothing is.

    *bindings* and *labels* are by action id, and *labels* gives the order the actions are
    judged in, the order a tab shows them. Pure, so the rules are testable without a
    QApplication. A key nothing can register is one problem; two actions on one key is
    another, since only the first would ever fire; a key the game itself binds
    (*game_bindings*, the players' own) is a third, since while the hotkey is armed the game
    never sees that key. Moved from BartleBot's Authoring tab in Stage 11 of #167, for
    BartleBuddy's Hotkeys tab, which since Stage 6 of BartleBuddy #14 tells the two kinds
    apart through :func:`binding_refusals` and :func:`game_key_clashes`.
    """
    return [sentence for _refused, sentence in _judged(bindings, labels, game_bindings)]


def binding_refusals(
    bindings: Mapping[str, str],
    labels: Mapping[str, str],
    *,
    armed_everywhere: Collection[str] = (),
) -> list[str]:
    """What can't work in a set of bindings, as sentences; empty when everything can.

    A key nothing can register, and two actions on one key, as :func:`binding_problems`
    says them. And a key that types (:func:`types_in_chat`) on an action in
    *armed_everywhere*, which would take that key from every application on the machine.
    """
    return [sentence for refused, sentence in _judged(bindings, labels, (), armed_everywhere) if refused]


def game_key_clashes(
    bindings: Mapping[str, str],
    labels: Mapping[str, str],
    game_bindings: Sequence[GameBinding],
) -> list[str]:
    """What each binding takes from the game, as :func:`binding_problems` says it; empty when nothing.

    A key the game binds still works as a hotkey, and the game doesn't see it while a game
    window has the keyboard. A key that is refused, or a second action on one key, isn't
    judged here: it is a refusal (:func:`binding_refusals`).
    """
    return [sentence for refused, sentence in _judged(bindings, labels, game_bindings) if not refused]


def _judged(
    bindings: Mapping[str, str],
    labels: Mapping[str, str],
    game_bindings: Sequence[GameBinding],
    armed_everywhere: Collection[str] = (),
) -> Iterator[tuple[bool, str]]:
    """Each problem in a set of bindings, in *labels*' order, and whether it is a refusal rather than a clash."""
    game: dict[tuple[int, int], GameBinding] = {}
    for gb in game_bindings:
        combo = parse_binding(gb.binding)
        if combo is not None:
            game.setdefault(combo, gb)
    seen: dict[tuple[int, int], str] = {}
    for action, label in labels.items():
        text = bindings.get(action, "")
        if not text:
            continue
        combo = parse_binding(text)
        if combo is None:
            yield True, f"{label}: {text} can't be used. Choose F1–F24, a letter or a digit."
        elif combo in seen:
            yield True, f"{label} and {seen[combo]} both use {text}. Choose another key for one of them."
        elif action in armed_everywhere and types_in_chat(text):
            yield (
                True,
                (
                    f"{label}: {text} types in every application, and {label} works everywhere. "
                    "Choose an F-key, or add Ctrl or Alt."
                ),
            )
        else:
            seen[combo] = label
            if clash := game.get(combo):
                yield (
                    False,
                    (
                        f"{label}: {text} is the game's key for {clash.description}, which won't work in "
                        "the game while this is set."
                    ),
                )


def _virtual_key(key: str) -> int | None:
    upper = key.upper()
    if len(upper) == 1 and ("A" <= upper <= "Z" or "0" <= upper <= "9"):
        return ord(upper)
    if upper.startswith("F") and upper[1:].isdigit() and 1 <= int(upper[1:]) <= 24:
        return _VK_F1 + int(upper[1:]) - 1
    return None


class GlobalHotkeys(QAbstractNativeEventFilter):
    """Register a set of bindings while armed, and call *on_hotkey* with the action pressed.

    *on_hotkey* runs inside Qt's message dispatch, so it must return quickly: start a task,
    never await or open a dialog. It is guarded, since an exception escaping a native event
    filter takes the event loop with it.
    """

    def __init__(self, on_hotkey: Callable[[str], None], *, id_base: int = ID_BASE_AUTHORING) -> None:
        super().__init__()
        self._on_hotkey = on_hotkey
        self._id_base = id_base
        self._bindings: dict[str, tuple[int, int]] = {}
        # Registered id -> action, while armed.
        self._registered: dict[int, str] = {}
        self._armed = False
        self._user32 = ctypes.windll.user32
        # Warned once per binding a session, not on every focus change: another tool
        # holding F1 (Deimos, say) would otherwise log a line every time a tile is clicked.
        self._warned: set[tuple[str, tuple[int, int]]] = set()

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def registered_actions(self) -> list[str]:
        return sorted(self._registered.values())

    def set_bindings(self, bindings: dict[str, str]) -> list[str]:
        """Replace every binding; returns the actions whose binding could not be understood."""
        parsed: dict[str, tuple[int, int]] = {}
        invalid: list[str] = []
        for action, text in bindings.items():
            if not text:
                continue
            combo = parse_binding(text)
            if combo is None:
                invalid.append(action)
            else:
                parsed[action] = combo
        for action in invalid:
            logger.warning(
                "Hotkey for %s is %r, which is not a key %s can bind", action, bindings[action], current().name
            )
        self._bindings = parsed
        if self._armed:
            self._unregister_all()
            self._register_all()
        return invalid

    def set_armed(self, armed: bool) -> None:
        if armed == self._armed:
            return
        self._armed = armed
        if armed:
            self._register_all()
        else:
            self._unregister_all()

    def _register_all(self) -> None:
        # Two actions on one key are caught here rather than left to Windows, which refuses
        # the second the same way it refuses a key another application holds. Reporting that
        # as another application's sent a user looking for one when the clash was our own.
        owners: dict[tuple[int, int], str] = {}
        for offset, (action, combo) in enumerate(sorted(self._bindings.items())):
            hotkey_id = self._id_base + offset
            modifiers, vk = combo
            if (owner := owners.get(combo)) is not None:
                self._warn_once(
                    action, combo, "The hotkeys for %s and %s are the same key; only %s gets it", owner, action, owner
                )
            elif self._user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                self._registered[hotkey_id] = action
                owners[combo] = action
            else:
                self._warn_once(
                    action,
                    combo,
                    "The hotkey for %s is already registered elsewhere: by another application, "
                    "or by %s's other hotkey set if it is a digit speed-authoring uses",
                    action,
                    current().name,
                )

    def _warn_once(self, action: str, combo: tuple[int, int], message: str, *args) -> None:
        if (action, combo) not in self._warned:
            self._warned.add((action, combo))
            logger.warning(message, *args)

    def _unregister_all(self) -> None:
        for hotkey_id in self._registered:
            self._user32.UnregisterHotKey(None, hotkey_id)
        self._registered.clear()

    def nativeEventFilter(self, event_type, message):
        if not self._registered or bytes(event_type) != b"windows_generic_MSG":
            return False, 0
        msg = ctypes.wintypes.MSG.from_address(int(message))
        if msg.message != WM_HOTKEY:
            return False, 0
        action = self._registered.get(msg.wParam)
        if action is None:
            return False, 0
        try:
            self._on_hotkey(action)
        except Exception:
            logger.exception("Hotkey handler raised for %s", action)
        return True, 0

    def close(self) -> None:
        """Release every registration. Call before the filter is dropped."""
        self._armed = False
        self._unregister_all()
