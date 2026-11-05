"""Each client's own speed, kept once for every writer of its speed field.

Two things write a wizard's speed field in BartleBuddy: the Speed toggle
(:class:`~bartleby.runtime.actions.helpers.SpeedBoost`) and a follower walking
(:class:`~bartleby.runtime.movement.follow.Follower`). Each gives the field back when it's
done, to the value the game had put there, which it takes to be any value it didn't write
itself. With two writers that's wrong: the boost would read a follower's 2.0x as the game's
own and give it back as the wizard's speed. So the writers of one team share a ledger, which
holds the last value any of them wrote, per client and character, and the value the game
wrote, and a value is the game's own only if none of them wrote it.

A writer on its own, as BartleBot's authoring Speed is, keeps a ledger of its own, and nothing
changes for it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Own:
    client: object
    character: int
    value: int


class SpeedLedger:
    """What each client's speed field held before we wrote it, and what we last wrote, by window."""

    def __init__(self) -> None:
        self._own: dict[int, _Own] = {}
        self._written: dict[int, tuple[int, int]] = {}

    def observe(self, client, character: int, current: int) -> None:
        """*current* was read from *client*'s field for *character*, just before a write.

        Anything but the value we last wrote to that character was written by the game, on a
        zone change, a mount or a new character, and is the one to give back.
        """
        if self._written.get(client.window_handle) != (character, current):
            self._own[client.window_handle] = _Own(client, character, current)

    def wrote(self, client, character: int, value: int) -> None:
        """*value* was just written to *client*'s field for *character*."""
        self._written[client.window_handle] = (character, value)

    def own_speed(self, client) -> int | None:
        """The game's own value for *client*'s field, as last seen, or None if we never wrote it."""
        own = self._own.get(client.window_handle)
        return None if own is None else own.value

    def wrote_to(self, client, character: int) -> bool:
        """Whether we wrote *client*'s field for *character*, and know its own value to give back.

        Asked before the field is read again: a logged-out character's field is freed memory.
        """
        own, written = self._own.get(client.window_handle), self._written.get(client.window_handle)
        return own is not None and written is not None and own.character == character == written[0]

    def to_give_back(self, client, character: int, current: int) -> int | None:
        """The value to write back to *client*, and forget it: its own, if the field still holds ours.

        None when there's nothing to give back: we never wrote it, the game has written since,
        or it's another character now, whose field holds our value only by chance.
        """
        hwnd = client.window_handle
        own, written = self._own.pop(hwnd, None), self._written.pop(hwnd, None)
        if own is None or written is None or own.character != character:
            return None
        return own.value if written == (character, current) else None

    def clients(self) -> list:
        """Every client whose field we've written and not given back."""
        return [own.client for hwnd, own in self._own.items() if hwnd in self._written]

    def forget(self, client) -> None:
        self._own.pop(client.window_handle, None)
        self._written.pop(client.window_handle, None)
