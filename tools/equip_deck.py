"""List each client's decks, and equip one by its name, on live clients.

Calls the app's own ``bartleby.runtime.decks``: ``owned_decks`` with nothing pressed, and
``equip_deck`` under an arbiter lease, as a product calls it, timed and with every client at
once. It is how an equip is checked in game: BartleBuddy's ``tools/probe_combat_facts.py``
measured the route by hand at 1.7 to 2.1 s a wizard.

USAGE
    uv run python tools/equip_deck.py                                  # list, every client
    uv run python tools/equip_deck.py "C1=Boss Deck" "C2=Boss Deck"    # equip, then list
    uv run python tools/equip_deck.py "C1=Type_2"                      # by key: Class_N+Type_N+Num_N

    Clients are C1, C2... in the order of their window titles. A name is as the backpack shows
    it, in any locale, among the decks any client chosen owns. Two names can share their text
    ("Healer"), and one nobody owns has no text to find it by: name either by its keys.

    It changes which deck is on, so put each wizard's own back after. Close BartleBuddy and
    BartleBot first: this installs wizwalker's hooks, and two hookers on one client collide.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartleby.runtime.arbiter import Arbiter, Owner  # noqa: E402
from bartleby.runtime.decks import DecksUnreadable, EquipResult, OwnedDeck, equip_deck, owned_decks  # noqa: E402
from bartleby.shared.deck_names import DeckName, Wheel, matches  # noqa: E402

# A deck's name can be Greek, which a cp1252 console cannot encode.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def target(text: str) -> tuple[str, str]:
    """``C1=Boss Deck`` as the client and the name."""
    label, _, name = text.partition("=")
    if not name.strip():
        raise argparse.ArgumentTypeError("a target is CLIENT=DECK NAME, as C1='Boss Deck'")
    return label.strip().upper(), name.strip()


def name_by_keys(text: str) -> DeckName | None:
    """``Class_7+Type_1`` as a name, or None for text that is not keys."""
    found: dict[str, str] = {}
    for key in text.split("+"):
        wheel = next((w for w in Wheel if key.strip().startswith(f"{w}_")), None)
        if wheel is None:
            return None
        found[f"{wheel.lower()}_key"] = key.strip()
    return DeckName(**found)


def find_name(text: str, owned: Iterable[OwnedDeck]) -> DeckName:
    """The name *text* says: by its keys, or as a deck in *owned* reads. ValueError for none, or two that read alike."""
    if (name := name_by_keys(text)) is not None:
        return name
    names = {deck.name for deck in owned if deck.name is not None and matches(deck.name, text)}
    if len(names) == 1:
        return names.pop()
    if not names:
        raise ValueError(f"no client chosen owns a deck named {text!r}: name it by its keys, as Type_2")
    raise ValueError(f"{text!r} is {' or '.join('+'.join(filter(None, n.keys)) for n in names)}: name it by its keys")


def deck_line(deck: OwnedDeck) -> str:
    name = deck.name.text() if deck.name is not None else "(never named)"
    keys = "+".join(filter(None, deck.name.keys)) if deck.name is not None else ""
    return f"      {name:<28} {keys:<24} {deck.cards:>3} cards{'  EQUIPPED' if deck.equipped else ''}"


def result_line(label: str, result: EquipResult, seconds: float) -> str:
    on = (
        result.on.name.text() if result.on is not None and result.on.name is not None else "a deck never named, or none"
    )
    parts = [f"{label}  {result.name.text()!r}: {result.outcome} in {seconds:.2f} s", f"on: {on}"]
    parts += [result.reason] if result.reason else []
    parts += ["THE BACKPACK WAS LEFT OPEN"] if result.left_open else []
    return "; ".join(parts)


async def run(targets: list[tuple[str, str]]) -> int:
    from wizwalker import ClientHandler  # noqa: PLC0415
    from wizwalker.utils import get_window_title  # noqa: PLC0415

    clients = ClientHandler().get_new_clients()
    clients.sort(key=lambda c: get_window_title(c.window_handle) or "")
    by_label = {f"C{i}": client for i, client in enumerate(clients, start=1)}
    if missing := sorted({label for label, _ in targets} - by_label.keys()):
        print(f"no client {', '.join(missing)}; running: {', '.join(by_label) or 'none'}")
        return 1
    chosen = {label: by_label[label] for label, _ in targets} or by_label
    if not chosen:
        print("No client is running: start them, each in the world, and run this again.")
        return 1

    print(f"Hooking {len(chosen)} client(s)...")
    await asyncio.gather(*(client.activate_hooks() for client in chosen.values()))
    try:
        owned = await _list(chosen)
        if not targets:
            return 0
        try:
            wanted = [
                (label, find_name(text, [d for decks in owned.values() for d in decks])) for label, text in targets
            ]
        except ValueError as e:
            print(e)
            return 1
        arbiter = Arbiter()

        async def one(label: str, name: DeckName) -> tuple[str, EquipResult, float]:
            async with arbiter.acquire(chosen[label], Owner.HOTKEY, detail="decks") as lease:
                started = time.perf_counter()
                result = await equip_deck(lease.client, name)
                return label, result, time.perf_counter() - started

        started = time.perf_counter()
        results = await asyncio.gather(*(one(label, name) for label, name in wanted))
        total = time.perf_counter() - started
        print()
        for label, result, seconds in results:
            print(result_line(label, result, seconds))
        print(f"All {len(results)} at once: {total:.2f} s\n")
        await _list(chosen)
        return 0 if all(result.ok for _, result, _ in results) else 1
    finally:
        await asyncio.gather(*(client.close() for client in chosen.values()), return_exceptions=True)


async def _list(clients: dict[str, object]) -> dict[str, list[OwnedDeck]]:
    """Print each client's decks, and how long reading them took; the decks by client."""
    owned: dict[str, list[OwnedDeck]] = {}
    for label, client in clients.items():
        started = time.perf_counter()
        try:
            owned[label] = await owned_decks(client)
        except DecksUnreadable as e:
            print(f"{label}  decks unreadable: {e}")
            continue
        print(f"{label}  {len(owned[label])} deck(s), read in {time.perf_counter() - started:.2f} s")
        for deck in owned[label]:
            print(deck_line(deck))
    return owned


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("targets", nargs="*", type=target, help="CLIENT=DECK NAME, e.g. C1='Boss Deck'; none lists only")
    return asyncio.run(run(ap.parse_args(argv).targets))


if __name__ == "__main__":
    sys.exit(main())
