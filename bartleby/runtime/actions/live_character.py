"""Whether a client's client object is safe to write to.

The client hook keeps the last address the game handed it, and nothing clears it when a
character logs out: the game frees the object, and the hook goes on pointing at it until
the next character loads. A read through it is harmless -- it comes back as a garbage id,
which ``wizard_names`` simply drops -- but a write lands in freed heap memory, and the game
crashes some time later, with a corrupted heap (0xc0000374) or an access violation in
ntdll. BartleBot's Speed, writing every tick, did exactly that to clients switching
characters.

So every write to a client object is preceded by :func:`live_character_id`, and one that
repeats by :class:`CharacterWatch`.
"""

from __future__ import annotations

from bartleby.runtime.game import wizard_names
from bartleby.runtime.launcher.auto_select import at_character_select

# The end of a player's client object's type name: ClientObject, or the game's own
# WizClientObject.
_CLIENT_OBJECT_TYPE = "ClientObject"


async def live_character_id(client) -> int:
    """The id of the character *client*'s client object holds, or NO_CHARACTER if writing to it is unsafe.

    The game is neither loading nor at character select, and the object still reads as a
    client object, which a freed one's vtable seldom leads to.
    """
    if await client.is_loading() or await at_character_select(client):
        return wizard_names.NO_CHARACTER
    if not (await client.client_object.maybe_read_type_name()).endswith(_CLIENT_OBJECT_TYPE):
        return wizard_names.NO_CHARACTER
    return await wizard_names.read_character_id(client)


class CharacterWatch:
    """The same live character on two looks running, per client.

    A freed address the heap has since handed to something else seldom reads as the same id
    twice.
    """

    def __init__(self) -> None:
        self._seen: dict[int, int] = {}

    async def steady_character(self, client) -> int:
        """The id of *client*'s character if the last look saw it too, else NO_CHARACTER."""
        handle = client.window_handle
        seen = self._seen.pop(handle, None)
        character = await live_character_id(client)
        if character == wizard_names.NO_CHARACTER:
            return wizard_names.NO_CHARACTER
        self._seen[handle] = character
        return character if character == seen else wizard_names.NO_CHARACTER

    def forget(self, client) -> None:
        """Start *client*'s two looks afresh."""
        self._seen.pop(client.window_handle, None)

    def clear(self) -> None:
        self._seen.clear()
