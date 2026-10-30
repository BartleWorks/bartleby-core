"""Test doubles for a game client, and the isolation every test gets.

FakeClient and its helpers came from BartleBot's tests/conftest.py with the modules they
stand in for; isolated_appdata is the library's part of BartleBot's isolated_settings.
"""

import asyncio
import ctypes
import dataclasses
import itertools
import logging
import os
import sys
from contextlib import ExitStack
from unittest.mock import DEFAULT, AsyncMock, MagicMock, patch

import pytest
from wizwalker import XYZ
from wizwalker.extensions.wizsprinter import SprintyClient

from bartleby.qt.testing.widget_lifetime import (  # noqa: F401 - registers them with pytest
    _free_widgets_this_test_created,
    parentless_shows,
    pytest_runtest_call,
)
from bartleby.runtime.clients import game_keys, sightings
from bartleby.runtime.game.game_sizing import RIGHT, RIGHT_SIDEBAR_MIN_WIDTH, Pane
from bartleby.runtime.game.key_bindings import KeyMap
from bartleby.shared import product
from bartleby.shared.account_store import AccountStore
from bartleby.shared.product import Product

# ---------------------------------------------------------------------------
# API drift guard
#
# Validates that every method mocked in FakeClient still exists on the real
# SprintyClient (which inherits from WizWalker's Client).  When the WizSprinter
# pin in pyproject.toml is bumped, any renamed or removed method will cause an
# immediate ImportError here rather than silently passing stale tests.
#
# The MagicMock attributes — body, root_window, stats, mouse_handler,
# combat_handler — are instance attributes set in WizWalker's Client.__init__,
# not class-level attributes, so hasattr(SprintyClient, name) cannot see them.
# They are excluded from this check and must be verified manually when the pin
# changes.
# ---------------------------------------------------------------------------
_FAKED_METHODS = frozenset(
    {
        "zone_name",
        "teleport",
        "is_loading",
        "is_in_npc_range",
        "is_in_dialog",
        "send_key",
        "find_closest_mob",
        "find_closest_by_vague_name",
        "find_closest_by_predicate",
        "tp_to",
        "in_battle",
        "calc_health_ratio",
        "calc_mana_ratio",
        "has_potion",
        "use_potion_if_needed",
        "use_potion",
        "get_health_wisps",
        "get_mana_wisps",
        "get_mobs",
        "backpack_space",
        "get_base_entities_with_vague_name",
        "get_base_entities_with_predicate",
        "get_base_entity_list",
    }
)
for _method in _FAKED_METHODS:
    if not hasattr(SprintyClient, _method):
        raise AttributeError(
            f"FakeClient mocks {_method!r} but SprintyClient has no such attribute. "
            "Update FakeClient to match the current WizSprinter API."
        )
del _method


#: Every FakeClient the running test has made. See FakeClient.get_base_entity_list.
_clients_in_this_test: list = []


async def _everyone_in_this_test() -> list:
    return [FakeGidEntity(c.gid) for c in _clients_in_this_test]


@pytest.fixture(autouse=True)
def _forget_fake_clients():
    """Start each test with no FakeClients sharing its realm. A list clear: costs nothing."""
    _clients_in_this_test.clear()
    yield
    _clients_in_this_test.clear()


class FakeClient:
    """AsyncMock-backed stub for a WizSprinter SprintyClient.

    Each method is an AsyncMock so tests can configure return values and
    side_effect sequences, or assert call counts without any Wizard101 process.
    """

    _next_handle = 1000

    def __init__(self):
        # Distinct per instance so wizard_names' handle-keyed cache treats each FakeClient
        # as a separate client, the way real windows behave.
        FakeClient._next_handle += 1
        self.window_handle = FakeClient._next_handle
        self.combat_handler = MagicMock()
        self.combat_handler.get_members = AsyncMock(return_value=[])
        self.zone_name = AsyncMock(return_value="TestZone")
        self.teleport = AsyncMock()
        self.is_loading = AsyncMock(return_value=False)
        self.is_in_npc_range = AsyncMock(return_value=False)
        self.is_in_dialog = AsyncMock(return_value=False)
        self.send_key = AsyncMock()
        # No game behind it to read key bindings from: the shipped defaults, so X is Interact.
        game_keys.use_map(self, KeyMap.defaults())
        self.find_closest_mob = AsyncMock(return_value=None)
        self.find_closest_by_vague_name = AsyncMock(return_value=None)
        self.find_closest_by_predicate = AsyncMock(return_value=None)
        self.tp_to = AsyncMock()
        self.in_battle = AsyncMock(return_value=False)
        # The center of the fight this client is in, away from FakeEntity's location so a
        # teleport to one is never mistaken for a teleport to the other.
        self.duel = MagicMock()
        self.duel.position = AsyncMock(return_value=XYZ(500.0, 600.0, 0.0))
        self.body = MagicMock()
        self.body.position = AsyncMock(return_value=XYZ(0.0, 0.0, 0.0))
        self.root_window = MagicMock()
        self.root_window.children = AsyncMock(return_value=[])
        self.calc_health_ratio = AsyncMock(return_value=1.0)
        self.calc_mana_ratio = AsyncMock(return_value=1.0)
        self.has_potion = AsyncMock(return_value=True)
        self.use_potion_if_needed = AsyncMock()
        self.use_potion = AsyncMock(return_value=True)
        self.stats = MagicMock()
        self.stats.potion_max = AsyncMock(return_value=10)
        self.stats.potion_charge = AsyncMock(return_value=10)
        # A level 180 Fire wizard, which is what Challenge Mode's stat caps read (scaled_ratios).
        self.stats.reference_level = AsyncMock(return_value=180)
        self.stats.school_id = AsyncMock(return_value=2343174)
        self.get_health_wisps = AsyncMock(return_value=[])
        self.get_mana_wisps = AsyncMock(return_value=[])
        self.get_mobs = AsyncMock(return_value=[])
        self.backpack_space = AsyncMock(return_value=(0, 100))  # (used, total)
        self.mouse_handler = MagicMock()
        self.mouse_handler.__aenter__ = AsyncMock(return_value=None)
        self.mouse_handler.__aexit__ = AsyncMock(return_value=False)
        self.mouse_handler.click_window = AsyncMock()
        self.get_base_entities_with_vague_name = AsyncMock(return_value=[])
        self.get_base_entities_with_predicate = AsyncMock(return_value=[])
        # Distinct per instance, like window_handle, so each FakeClient looks like a
        # different character to wizard_names' switch detection. Tests that care set
        # read_value_from_offset.return_value directly to simulate a character switch.
        self.client_object = MagicMock()
        self.client_object.read_value_from_offset = AsyncMock(return_value=FakeClient._next_handle * 1000)
        # No name behavior by default, so the memory name route answers None and callers
        # fall through to the character-page route. Tests that want the memory route give
        # this a behavior — see tests/game/test_wizard_names.py.
        self.client_object.inactive_behaviors = AsyncMock(return_value=[])
        # This character's global id, and what it has loaded. By default every FakeClient
        # a test makes has every other loaded -- one zone, realm and instance, which is
        # what node tests written before realm checks assume. see_each_other() splits them.
        self.gid = FakeClient._next_handle * 1000 + 1
        self.client_object.global_id_full = AsyncMock(return_value=self.gid)
        self.client_object.object_name = AsyncMock(return_value=FAKE_PLAYER_TEMPLATE)
        self.get_base_entity_list = AsyncMock(side_effect=_everyone_in_this_test)
        _clients_in_this_test.append(self)


class FakeEntity:
    """Minimal stub for a WizWalker entity (mob / NPC / reagent)."""

    def __init__(self, name: str = "TestEnemy/TestEnemy"):
        self.object_name = AsyncMock(return_value=name)
        self.location = AsyncMock(return_value=XYZ(100.0, 200.0, 0.0))
        # Real entities expose their name through the template, not directly; predicate
        # searches read it that way, so the two must report the same name.
        template = MagicMock()
        template.object_name = self.object_name
        self.object_template = AsyncMock(return_value=template)


#: The template every player is made from, a FakeClient's own character included. The real
#: name is not needed: the code compares an entity's template with the client's own.
FAKE_PLAYER_TEMPLATE = "Player Object"


class FakeGidEntity:
    """An entity-list entry that is only a global id: another player, as a client sees it.

    *template* other than the player's makes it an NPC or a mob instead.
    """

    def __init__(self, gid: int, template: str = FAKE_PLAYER_TEMPLATE):
        self.global_id_full = AsyncMock(return_value=gid)
        self.object_name = AsyncMock(return_value=template)


def prompt_at(client: "FakeClient", *points, initially: bool = False) -> None:
    """Make *client*'s interact prompt show only while its last teleport was to one of *points*.

    That is how the game behaves, so a test using this does not depend on how many times a
    node reads the prompt, only on where it sends the client. *initially* is the prompt
    before the first teleport: True for a client that starts beside some other object.
    Points are XYZs or the ``{"x", "y", "z"}`` dicts node params hold, compared by
    coordinates, since XYZ has no equality of its own.
    """
    wanted = [(p["x"], p["y"], p["z"]) if isinstance(p, dict) else tuple(p) for p in points]

    async def _showing() -> bool:
        if not client.teleport.call_args_list:
            return initially
        return tuple(client.teleport.call_args_list[-1].args[0]) in wanted

    client.is_in_npc_range.side_effect = _showing


def zone_unreadable_at_first(client: "FakeClient", zone: str = "TestZone", reads: int = 1) -> None:
    """Make *client*'s zone read None for its next *reads* reads, then *zone*.

    That is a client just off a loading screen: the zone turns readable a moment after the
    screen is gone (#282). After that the read is ``zone_name.return_value``, so a fake that
    moves the client by setting it still does.
    """
    client.zone_name = AsyncMock(
        return_value=zone, side_effect=lambda: None if client.zone_name.await_count <= reads else DEFAULT
    )


def see_each_other(*clients: "FakeClient") -> None:
    """Make *clients* have each other loaded, and nobody else: one zone, realm and instance.

    By default a test's FakeClients all see each other; calling this once per group is how
    a test splits them, e.g. ``see_each_other(a, b); see_each_other(c)``.
    """
    for viewer in clients:
        viewer.get_base_entity_list.side_effect = None
        viewer.get_base_entity_list.return_value = [FakeGidEntity(c.gid) for c in clients]


class FakeMachine:
    """Minimal stub for BotMachine that records logs and drops."""

    def __init__(self, bot_config=None, options: dict | None = None):
        self.bot_config = bot_config
        self._options = options or {}
        self.logs: list[tuple] = []
        self.drops: list[tuple] = []
        self.chat_baselines: dict[object, str] = {}
        self.drop_poll: asyncio.Task | None = None
        # Which client hwnds are currently exempt from ClientHealthMonitor, and which have
        # been put back. A node that suspends and forgets to restore leaves a real crash
        # unnoticed for the rest of the run, so both sides are recorded.
        self.health_checks_suspended: set[int] = set()
        self.health_checks_resumed: set[int] = set()
        # Every abandon_client call, as (client, reason). ``recovers`` is what it answers:
        # False by default, which is the node tester's run with no recovery behind it.
        self.abandoned: list[tuple[object, str]] = []
        self.recovers = False
        self.is_stopped = False
        # Mirrors BotMachine.port_map: None until something surveys who can port to whom.
        self.port_map = None
        # Mirrors BotMachine.node_scratch: what a node keeps for its own rerun.
        self.node_scratch: dict[str, object] = {}
        # The real record, retaining as it does mid-run, so node tests exercise its rules.
        self.together = sightings.TogetherRecord()

    def note_sightings(self, result) -> None:
        self.together.note(result)

    def are_together(self, clients) -> bool:
        return self.together.are_together(clients)

    def together_with(self, client, clients) -> list:
        return self.together.together_with(client, clients)

    def forget_sightings(self, client) -> None:
        self.together.forget(client)

    def log(self, msg: str, level: int = logging.INFO) -> None:
        self.logs.append((msg, level))

    def note_flee(self) -> bool:
        self.flees = getattr(self, "flees", 0) + 1
        return False

    def note_fight_fled(self, state_id, level) -> None:
        """Mirror BotMachine: a node's fight was fled, on *level*. Recorded as (state_id, level)."""
        self.fights_fled = [*getattr(self, "fights_fled", []), (state_id, level)]

    def on_drop(self, item: str, qty: int, client_index: int = 0) -> None:
        self.drops.append((item, qty, client_index))

    def chat_baseline(self, client) -> str | None:
        """Mirror BotMachine: None means never seeded, "" is a real empty baseline."""
        return self.chat_baselines.get(client)

    def set_chat_baseline(self, client, text: str) -> None:
        self.chat_baselines[client] = text

    def start_drop_poll(self, task: asyncio.Task) -> None:
        """Mirror BotMachine: a new fight's poll cancels the previous one."""
        if self.drop_poll is not None:
            self.drop_poll.cancel()
        self.drop_poll = task

    async def wait_for_drop_poll(self) -> None:
        if self.drop_poll is not None and not self.drop_poll.done():
            await asyncio.wait({self.drop_poll})

    def suspend_health_checks_for(self, hwnds, *, watch_disconnects: bool = False) -> None:
        """Record a health-check suspension. Real BotMachine flags them on its monitor."""
        self.health_checks_suspended.update(hwnds)
        self.watching_disconnects = watch_disconnects

    def is_handed_to_recovery(self, client) -> bool:
        """Mirror BotMachine: set ``lost`` to the hwnds a watcher has handed to recovery."""
        return client.window_handle in getattr(self, "lost", set())

    def resume_health_checks_for(self, hwnds) -> None:
        self.health_checks_suspended.difference_update(hwnds)
        self.health_checks_resumed.update(hwnds)

    def note_logged_in(self, hwnds) -> None:
        """Mirror BotMachine: these players are back from a login and nothing has moved them."""
        self.fresh_logins = {*getattr(self, "fresh_logins", set()), *hwnds}

    def is_freshly_logged_in(self, client) -> bool:
        return client.window_handle in getattr(self, "fresh_logins", set())

    def abandon_client(self, client, reason: str) -> bool:
        """Record a handover to crash recovery. Real BotMachine kills the client, or stops the run."""
        self.abandoned.append((client, reason))
        return self.recovers

    def get_option(self, name: str, default=None):
        """Return configured option value, or a sensible test default.

        Safety Delay uses 0.01s (10ms): small enough to be negligible but reliable.
        timeout=0 and timeout=0.001 can race against asyncio.wait_for's own scheduling
        overhead in Python 3.12+ and fire before the condition coroutine runs even once.
        10ms gives the event loop plenty of room while still being 10× faster than the
        original 0.1s default.
        Tests that need a longer window (e.g. late-appearing dialog tests) should pass
        options={"Safety Delay": <value>} to the constructor.
        """
        return self._options.get(name, 0.01 if name == "Safety Delay" else 0)


def message_box_patches(visible_buttons=()):
    """Patch node_helpers' UI reads to stand in for an on-screen modal message box.

    `visible_buttons` is the set of MESSAGE_BOX_* paths the modal shows; clicks land only
    on those. Which buttons are showing is the ONLY way to tell the game's modals apart —
    they all share the MessageBoxModalWindow tree — so tests state a dialog by naming its
    buttons, the way the nodes identify it:

        (MESSAGE_BOX_CENTER, MESSAGE_BOX_RIGHT)  the dungeon exit warning (confirm + cancel)
        (MESSAGE_BOX_RIGHT,)                     teleport_to_friend's friend-busy dialog
        ()                                       no modal

    Returns (patches, clicked_paths): apply the patches with an ExitStack or a `with`
    tuple, and assert against clicked_paths afterwards.
    """
    import bartleby.runtime.actions.node_helpers as nh

    visible = [list(p) for p in visible_buttons]
    clicked: list[list[str]] = []

    async def _is_visible(_client, path):
        return list(path) in visible

    async def _click(_client, path):
        clicked.append(path)
        return list(path) in visible

    return (
        patch.object(nh, "is_visible_by_path", new=AsyncMock(side_effect=_is_visible)),
        patch.object(nh, "click_by_path", new=AsyncMock(side_effect=_click)),
    ), clicked


class ModalScreen:
    """A stack of modals over a client, each a (caption, buttons) pair, closed by a click on one of its buttons.

    Stands in for the game's modal message box: `patches()` replaces the three reads and the click
    node_helpers makes, and `stubborn` makes a click that does nothing. `clicked` records every click.
    """

    def __init__(self, *modals, stubborn: bool = False):
        self.modals = list(modals)
        self.stubborn = stubborn
        self.clicked: list[list[str]] = []

    def patches(self):
        import bartleby.runtime.actions.node_helpers as nh_mod
        from bartleby.runtime.paths import MESSAGE_BOX_CAPTION_PATH

        async def _text(_client, path):
            return self.modals[0][0] if self.modals and list(path) == MESSAGE_BOX_CAPTION_PATH else ""

        async def _visible(_client, path):
            return bool(self.modals) and list(path) in self.modals[0][1]

        async def _click(_client, path):
            self.clicked.append(list(path))
            if self.modals and list(path) in self.modals[0][1] and not self.stubborn:
                self.modals.pop(0)

        return (
            patch.object(nh_mod, "get_text_by_path", new=_text),
            patch.object(nh_mod, "is_visible_by_path", new=_visible),
            patch.object(nh_mod, "click_by_path", new=_click),
        )

    def apply(self) -> ExitStack:
        """Enter every patch; use as `with screen.apply():`."""
        stack = ExitStack()
        for patcher in self.patches():
            stack.enter_context(patcher)
        return stack


# ---------------------------------------------------------------------------
# Timer resolution
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _fine_grained_timers():
    """Raise the Windows timer resolution for the duration of the test session.

    Windows schedules waits on a ~15.6 ms tick by default, so every ``asyncio.sleep``
    shorter than that still costs a full tick. The suite is mostly nodes polling on
    short intervals, which means thousands of 1-10 ms sleeps each rounding up to 15.6 ms
    — well over half the wall time, and none of it doing anything.

    ``timeBeginPeriod(1)`` drops the tick to ~1 ms. It cannot make a sleep return early,
    so nothing that asserts a lower bound on elapsed time can be affected; it only stops
    the short ones overshooting. The app itself does not do this — its own cadences are
    tenths of seconds, where the tick does not matter.

    Session-scoped and paired with ``timeEndPeriod`` because the setting is process-wide
    and refcounted by the OS.
    """
    if sys.platform != "win32":
        yield
        return
    winmm = ctypes.WinDLL("winmm")
    winmm.timeBeginPeriod(1)
    try:
        yield
    finally:
        winmm.timeEndPeriod(1)


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------

#: Where the install key, the file types and the compatibility layers go in a test that does
#: not choose a key of its own: one throwaway key per worker, never the vendor's.
UNGUARDED_REGISTRY_KEY = rf"Software\Bartleby-test-unguarded-{os.getpid()}"

_APPDATA_SERIAL = itertools.count()


@pytest.fixture(scope="session")
def _appdata_root(tmp_path_factory):
    """The parent of every test's private ``%APPDATA%``, made once per worker."""
    return tmp_path_factory.getbasetemp() / "appdata"


@pytest.fixture(scope="session", autouse=True)
def _remove_unguarded_registry_key():
    """Remove whatever a test wrote under :data:`UNGUARDED_REGISTRY_KEY`, at the end of the session."""
    yield
    if sys.platform == "win32":
        import winreg  # noqa: PLC0415

        from bartleby.shared.install_key import delete_tree  # noqa: PLC0415

        delete_tree(winreg.HKEY_CURRENT_USER, UNGUARDED_REGISTRY_KEY)


@pytest.fixture(autouse=True)
def isolated_appdata(_appdata_root, monkeypatch):
    """Point %APPDATA% at a per-test directory, named but not created, as BartleBot's suite does."""
    serial = next(_APPDATA_SERIAL)
    monkeypatch.setenv("APPDATA", str(_appdata_root / f"t{serial}"))
    monkeypatch.setenv("LOCALAPPDATA", str(_appdata_root / f"t{serial}-local"))
    monkeypatch.delenv("BARTLEBOT_DEV", raising=False)
    monkeypatch.delenv("BARTLEBUDDY_DEV", raising=False)

    from bartleby.shared import file_types, game_dpi, game_install, install_key, official_set  # noqa: PLC0415

    monkeypatch.setattr(official_set, "_registry", official_set.NoRegistry())
    official_set.reset_shipped_root()
    monkeypatch.setattr(install_key, "REGISTRY_KEY", UNGUARDED_REGISTRY_KEY)
    monkeypatch.setattr(file_types, "CLASSES_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Classes")
    monkeypatch.setattr(file_types, "notify", lambda: None)
    monkeypatch.setattr(game_dpi, "LAYERS_KEY", rf"{UNGUARDED_REGISTRY_KEY}\Layers")
    monkeypatch.setattr(game_install, "_places", game_install.NoPlaces())


#: The product every test runs as, named for no real one, so a product's name written into core fails a test.
SAMPLE_PRODUCT = Product(
    name="Sample",
    version="1.2.3",
    accent="#3366bb",
    accent_dark="#5b95d6",
    logo_background="#204060",
)


@pytest.fixture(autouse=True)
def sample_product(monkeypatch):
    """Run every test as :data:`SAMPLE_PRODUCT`, as an app's entry point would with ``product.use``."""
    monkeypatch.setattr(product, "_current", SAMPLE_PRODUCT)
    return SAMPLE_PRODUCT


@dataclasses.dataclass
class SampleSettings:
    """A product's settings as the game view, the launch screen and the screen fit read them.

    ``GameViewSettings`` and ``FitSettings`` both. *panes* is what the fit is handed as the
    window's panes: one right sidebar at the shared floor, as a product with a Log tab has.
    """

    accounts: list = dataclasses.field(default_factory=list)
    launch_profiles: list = dataclasses.field(default_factory=list)
    recent_launches: list = dataclasses.field(default_factory=list)
    game_resolution: tuple = (800, 600)
    sizes: dict = dataclasses.field(default_factory=dict)
    panes: list = dataclasses.field(default_factory=lambda: [Pane("sidebar", RIGHT_SIDEBAR_MIN_WIDTH, RIGHT)])

    def pane_sizes(self) -> dict:
        return dict(self.sizes)

    def add_recent_launch(self, entry_type: str, identifier: str) -> None:
        self.recent_launches.insert(0, {"type": entry_type, "id": identifier})


@pytest.fixture(autouse=True)
def sample_settings() -> SampleSettings:
    """A fresh :class:`SampleSettings` per test, handed to the screen fit as a product would."""
    from bartleby.qt import screen_fit  # noqa: PLC0415 - Qt, kept off the runtime tests' collection

    settings = SampleSettings()
    screen_fit.configure(settings, lambda: settings.panes)
    return settings


class SampleStore:
    """A product's settings as the settings surfaces read them (``bartleby.qt.settings.store``).

    The accounts and launch profiles are the vendor's real store, in the test's own
    %APPDATA%, so a card action's write lands on disk as it would in either product.
    """

    DEFAULTS: dict = {
        "theme": "system",
        "game_resolution": (800, 600),
        "auto_select_character": True,
        "disable_quick_friend_teleport": True,
    }

    def __init__(self) -> None:
        self.account_store = AccountStore()
        self.game_resolution = self.DEFAULTS["game_resolution"]
        self.auto_select_character = self.DEFAULTS["auto_select_character"]
        self.disable_quick_friend_teleport = self.DEFAULTS["disable_quick_friend_teleport"]
        self.theme = self.DEFAULTS["theme"]
        self.custom_theme: dict = {}
        self.show_setting_descriptions = False
        #: How many times :meth:`save` was called.
        self.saves = 0

    @property
    def accounts(self) -> list[dict]:
        return self.account_store.accounts

    @accounts.setter
    def accounts(self, value: list[dict]) -> None:
        self.account_store.set_accounts(value)

    @property
    def launch_profiles(self) -> list[dict]:
        return self.account_store.launch_profiles

    @launch_profiles.setter
    def launch_profiles(self, value: list[dict]) -> None:
        self.account_store.set_launch_profiles(value)

    def default_for(self, name: str):
        return self.DEFAULTS[name]

    def save(self) -> None:
        self.saves += 1


@pytest.fixture(autouse=True)
def sample_store(isolated_appdata) -> SampleStore:
    """A fresh :class:`SampleStore` per test, handed to the settings surfaces as a product would."""
    from bartleby.qt.settings import store  # noqa: PLC0415 - kept off the runtime tests' collection

    settings = SampleStore()
    store.configure(lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# Qt: one QApplication per worker, at the app's font (BartleBot's conftest)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def qapp_cls():
    """Build the one QApplication with the theme's font, and the app's Qt attributes set first."""
    from PyQt6.QtWidgets import QApplication  # noqa: PLC0415 - keeps Qt out of non-GUI runs

    from bartleby.qt.native_widgets import keep_siblings_alien  # noqa: PLC0415
    from bartleby.qt.theming.theme import THEME  # noqa: PLC0415

    keep_siblings_alien()

    class _AppFontApplication(QApplication):
        def __init__(self, argv):
            super().__init__(argv)
            self.setFont(THEME.ui_font())

    return _AppFontApplication


@pytest.fixture(autouse=True)
def _restore_app_stylesheet():
    """Undo any ``QApplication.setStyleSheet`` a test leaves behind, as BartleBot's suite does."""
    qtwidgets = sys.modules.get("PyQt6.QtWidgets")
    app = qtwidgets.QApplication.instance() if qtwidgets else None
    before = app.styleSheet() if app is not None else None
    yield
    app = qtwidgets.QApplication.instance() if qtwidgets else None
    if app is not None and before is not None and app.styleSheet() != before:
        app.setStyleSheet(before)


@pytest.fixture
def streamer_mode_on(monkeypatch):
    """Streamer Mode on for this test, as a product's setting would turn it on."""
    from bartleby.qt import streamer_mode  # noqa: PLC0415

    monkeypatch.setattr(streamer_mode, "_setting", lambda: True)
