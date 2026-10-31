"""What makes a client object safe to write to: a logged-out character's is freed memory."""

from unittest.mock import AsyncMock

from bartleby.runtime.actions import live_character
from bartleby.runtime.actions.live_character import CharacterWatch, live_character_id
from bartleby.runtime.game.wizard_names import NO_CHARACTER
from bartleby.runtime.testing.sim_client import SimClient


async def test_a_loaded_character_is_live():
    assert await live_character_id(SimClient(character=7)) == 7


async def test_a_loading_client_is_not():
    assert await live_character_id(SimClient(character=7, loading=True)) == NO_CHARACTER


async def test_a_client_at_character_select_is_not(monkeypatch):
    monkeypatch.setattr(live_character, "at_character_select", AsyncMock(return_value=True))
    assert await live_character_id(SimClient(character=7)) == NO_CHARACTER


async def test_a_freed_object_is_not():
    client = SimClient()
    client.log_out()
    assert await live_character_id(client) == NO_CHARACTER


async def test_the_watch_answers_on_the_second_look_at_one_character():
    client = SimClient(character=7)
    watch = CharacterWatch()
    assert await watch.steady_character(client) == NO_CHARACTER
    assert await watch.steady_character(client) == 7
    assert await watch.steady_character(client) == 7


async def test_the_watch_starts_over_when_the_character_changes():
    client = SimClient(character=7)
    watch = CharacterWatch()
    await watch.steady_character(client)
    client.character = 8
    assert await watch.steady_character(client) == NO_CHARACTER
    assert await watch.steady_character(client) == 8


async def test_the_watch_starts_over_after_a_look_at_a_freed_object():
    client = SimClient(character=7)
    watch = CharacterWatch()
    await watch.steady_character(client)
    client.type_name = ""
    assert await watch.steady_character(client) == NO_CHARACTER
    client.type_name = "WizClientObject"
    assert await watch.steady_character(client) == NO_CHARACTER
    assert await watch.steady_character(client) == 7


async def test_forget_starts_one_client_over():
    client, other = SimClient(character=7), SimClient(character=8)
    watch = CharacterWatch()
    await watch.steady_character(client)
    await watch.steady_character(other)
    watch.forget(client)
    assert await watch.steady_character(client) == NO_CHARACTER
    assert await watch.steady_character(other) == 8
