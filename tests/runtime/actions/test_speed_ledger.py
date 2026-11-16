"""One record of each wizard's own speed, for every writer of its speed field."""

from types import SimpleNamespace

from bartleby.runtime.actions.speed_ledger import SpeedLedger

CHARACTER = 7


def _client(hwnd: int = 1):
    return SimpleNamespace(window_handle=hwnd)


def test_the_value_read_before_a_write_is_the_one_given_back():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert ledger.own_speed(client) == 40
    assert ledger.to_give_back(client, CHARACTER, 100) == 40
    assert ledger.own_speed(client) is None, "given back, it is forgotten"


def test_a_value_we_wrote_is_never_taken_for_the_games_own():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    # A second writer sharing the ledger reads the field and finds our 100 there.
    ledger.observe(client, CHARACTER, 100)
    assert ledger.own_speed(client) == 40


def test_a_value_the_game_wrote_since_replaces_the_own_one():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    ledger.observe(client, CHARACTER, 55)  # a mount or a zone change rewrote it
    assert ledger.own_speed(client) == 55


def test_nothing_is_given_back_once_the_game_has_written_over_ours():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert ledger.to_give_back(client, CHARACTER, 55) is None


def test_nothing_is_given_back_to_another_character():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert not ledger.wrote_to(client, CHARACTER + 1)
    assert ledger.to_give_back(client, CHARACTER + 1, 100) is None


def test_it_lists_the_clients_written_and_not_given_back():
    ledger, a, b = SpeedLedger(), _client(1), _client(2)
    for client in (a, b):
        ledger.observe(client, CHARACTER, 0)
        ledger.wrote(client, CHARACTER, 100)
    assert ledger.wrote_to(a, CHARACTER)
    ledger.forget(a)
    assert ledger.clients() == [b]
    assert not ledger.wrote_to(a, CHARACTER)
