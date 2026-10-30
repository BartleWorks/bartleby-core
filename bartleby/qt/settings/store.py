"""What the shared settings surfaces read and write of a product's settings.

The rows, the Theme and Game tabs, the account and profile editors and setup's shared
pages came from BartleBot, where each asked its ``SettingsManager`` singleton. Here they ask
:func:`store`, which is the product's settings object as :func:`configure` was handed it.
A function that gives it, not the object: BartleBot's tests replace its singleton, and a
captured instance would answer with the last test's accounts (as core's screen fit found,
``docs/ARCHITECTURE.md``).

The accounts and launch profiles are the vendor's (:mod:`bartleby.shared.account_store`),
so an account added in one product is in the other's next read. Everything else here is
the product's own file.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from bartleby.shared.account_store import AccountStore


class SettingsStore(Protocol):
    """A product's settings, as the shared settings surfaces use them."""

    #: The vendor's accounts and launch profiles: assigning one writes it through.
    accounts: list[dict]
    launch_profiles: list[dict]
    #: The resolution a launch uses when its profile names none.
    game_resolution: tuple[int, int]
    #: Whether a launched client is taken past character select on its most recent character.
    auto_select_character: bool
    #: Whether a launched client gets the game's quick friend teleport turned off.
    disable_quick_friend_teleport: bool
    #: ``system``, ``light``, ``dark`` or ``custom``.
    theme: str
    #: The colors a custom theme overrides, by token name.
    custom_theme: dict[str, str]
    #: Whether the Settings dialog shows every row's description: a reading preference.
    show_setting_descriptions: bool

    @property
    def account_store(self) -> AccountStore:
        """The store under ``accounts`` and ``launch_profiles``, for a change to both in one write."""
        ...

    def default_for(self, name: str) -> Any:
        """The shipped default of the setting called *name* above, in the control's own terms."""
        ...

    def save(self) -> None: ...


_store: Callable[[], SettingsStore] | None = None


def configure(settings: Callable[[], SettingsStore]) -> None:
    """Hand the settings surfaces what gives the product's settings. Called once, at startup."""
    global _store
    _store = settings


def store() -> SettingsStore:
    """The product's settings, as :func:`configure` was handed them."""
    if _store is None:
        raise RuntimeError("bartleby.qt.settings.store.configure() has not been called")
    return _store()
