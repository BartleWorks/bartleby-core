"""The game clients at run time: found, hooked, named by account and kept in slot order.

The Qt-free half of BartleBot's ``ClientManager``, which every product needs: it polls
wizwalker's handler for new windows, hooks each in the background, ties each window to an
account (by the title the launcher gives it, or by the user id it reports), learns each
account's characters, tracks launches, holds the slot order, and unhooks and rehooks on
request. What changes is published through :class:`~bartleby.shared.listeners.Listeners`;
a product with a Qt GUI may hand its own signals in their place.

What it does not do is bring a lost client back. BartleBot's recovery, relogging and
the bot they serve stay in BartleBot, which subclasses this manager and answers
``clients_removed``; BartleBuddy's recovery arrives with its phase 3. A slot held for a
client being brought back is still this manager's business, since the ordering is
(``_pending_recovery``, which only such a product fills).

Three rules carried over, each the cost of a bug (``bartleby/runtime/CLAUDE.md`` has them
as rules):

* **Hooking runs in the background.** ``activate_hooks()`` waits for a character to be in
  the world, with no timeout, so a window at character select would stall the loop for
  good. See :meth:`ClientManager._start_activation`.
* **A failed activation is never retried.** It leaves jumps into wizwalker's scratch
  region, which a fresh attempt would zero out from under them.
* **A failed read keeps the list as it was.** The next pass compares against it, and that
  comparison is what notices a client that died (#154).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from wizwalker.errors import MemoryReadError
from wizwalker.utils import get_window_title

from bartleby.runtime.clients import account_identity, game_keys
from bartleby.runtime.clients.credentials import CredentialStore, account_label
from bartleby.runtime.clients.launch_tracker import LaunchStage, LaunchTracker
from bartleby.runtime.clients.tile_slots import TileSlot, build_tile_slots
from bartleby.runtime.clients.window_kill import force_close_client
from bartleby.runtime.launcher import auto_select
from bartleby.runtime.timing import (
    CLIENT_DETECT_ERROR_BACKOFF,
    CLIENT_DETECT_INTERVAL,
    CLIENT_DETECT_INTERVAL_IDLE,
)
from bartleby.shared.listeners import Listeners
from bartleby.shared.ordering import apply_order

logger = logging.getLogger(__name__)


class ClientSettings(Protocol):
    """What the client manager reads from an app's settings: the accounts it knows.

    Settable, because the manager writes an account's identity fingerprint back the first
    time it sees a window it launched (see ``_refresh_account_identity``). Both products'
    settings objects keep the accounts in the vendor's shared store
    (:mod:`bartleby.shared.account_store`), so a write lands there.
    """

    @property
    def accounts(self) -> list[dict]: ...

    @accounts.setter
    def accounts(self, value: list[dict]) -> None: ...


class TileView(Protocol):
    """The game view the manager hands its tiles to: one tile per window on screen."""

    def sync_tiles(self, wanted: list[TileSlot]) -> None: ...

    def unembed_all(self) -> None: ...


class _NoView:
    """For a product that shows no game view of this manager's: tiles go nowhere."""

    def sync_tiles(self, wanted: list[TileSlot]) -> None:
        pass

    def unembed_all(self) -> None:
        pass


@dataclass(frozen=True)
class Removal:
    """One hooked client that disappeared, as ``clients_removed`` reports it.

    Gathered before anything about the window is forgotten, so whoever answers it still
    has the account and the slot the client held, and its title for a window that never
    had an account. The slot is already empty in the ordering by the time this is sent,
    and remembered as the account's vacated slot.
    """

    hwnd: int
    #: The account the window was tied to, or None for one that never was.
    username: str | None
    #: The slot it held in the ordering, or None.
    slot: int | None
    #: The last title read from the window, or None.
    title: str | None


def _titled(clients, title: str):
    """The first client in *clients* whose window title matches, or None.

    Titles are read live rather than cached because a window is renamed after login and
    a relaunched client comes back under a new handle with the same title.
    """
    for c in clients:
        try:
            if get_window_title(c.window_handle) == title:
                return c
        except Exception:
            pass
    return None


def _slot_order_for_log(order: list[str | None]) -> str:
    """A slot ordering as a log line can show it: each slot's label, never a username."""
    return ", ".join(f"P{i + 1} {'(empty)' if u is None else repr(account_label(u))}" for i, u in enumerate(order))


class ClientManager:
    """Owns client detection, hooking, account identity, launches and slot ordering.

    Listeners, each a :class:`~bartleby.shared.listeners.Listeners` a product may replace
    with a Qt signal of the same arguments:
        clients_changed: The hooked clients, in slot order, whenever they are published.
        all_clients_changed: ``(hooked, unhooked)`` whenever either list changes.
        wizard_names_changed: The cached wizard names, aligned with the hooked clients.
        clients_reordered: The permutation a swap or reorder applied, ``order[i]`` being
            the old index now at *i*.
        clients_removed: A list of :class:`Removal`, when hooked clients disappear.
    """

    def __init__(self, handler=None, settings: ClientSettings | None = None, *, game_view: TileView | None = None):
        if handler is None:
            from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

            handler = WizSprinter()
        if settings is None:
            raise TypeError("ClientManager needs the app's settings, for the accounts it knows")
        self._handler = handler
        self._settings = settings
        self._game_view = game_view if game_view is not None else _NoView()

        self.clients_changed = Listeners("clients_changed")
        self.all_clients_changed = Listeners("all_clients_changed")
        self.wizard_names_changed = Listeners("wizard_names_changed")
        self.clients_reordered = Listeners("clients_reordered")
        self.clients_removed = Listeners("clients_removed")

        self._clients: list = []
        # Last announced wizard names, so the signal fires on change rather than per tick.
        self._last_wizard_names: list = []
        # Maps window_handle → username for all clients seen this session.
        self._hwnd_account_map: dict[int, str] = {}
        # Last title seen per unmapped window, so a client that disconnects can still be
        # named in the log after its window is gone and get_window_title returns nothing.
        self._hwnd_titles: dict[int, str] = {}
        # Account-level user id per window, once one has been read. Identifies a client
        # this app did not launch, whose title carries no account tag.
        self._hwnd_user_ids: dict[int, int] = {}
        # Character roster per account, loaded from keyring on first use. Cached because
        # the learning pass runs every client-loop tick and a keyring read is not free;
        # this process is the only writer, so the cache cannot go stale under us.
        self._rosters: dict[str, list[dict]] = {}
        # Desired slot ordering; None marks a slot currently being recovered.
        self._client_order: list[str | None] = []
        # Accounts with a launch running right now. Separate from the ordering above
        # because a reservation outlives the launch that made it -- see launch_tracker.
        self._launches = LaunchTracker()
        # Slots held for a client being brought back: original_position → username. Only a
        # product with recovery fills it (BartleBot's); the slot code here honors it.
        self._pending_recovery: dict[int, str] = {}
        # Slot each username last occupied, remembered across a disconnect so a client
        # that comes back outside the recovery flow reclaims its original position.
        self._vacated_slots: dict[str, int] = {}
        # Last known window resolution per username; used by recovery to restore original size.
        self._client_resolutions: dict[str, tuple[int, int]] = {}
        self._unhooked_clients: list = []
        self._unhooked_hwnds: set[int] = set()
        # Windows whose hooks are still being installed. activate_hooks() blocks until the
        # character is in-game, so activation runs as a background task per client and the
        # hwnd sits here meanwhile — see _start_activation for why this can't be awaited inline.
        self._activating_hwnds: set[int] = set()
        # Windows whose hook activation raised. Kept out of the published client list for
        # good: retrying would re-prepare the autobot region the failed hooks jump into.
        self._failed_hook_hwnds: set[int] = set()
        self._activation_tasks: set[asyncio.Task] = set()
        self._client_loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def clients(self) -> list:
        return self._clients

    @property
    def unhooked_clients(self) -> list:
        """Clients that were unhooked but whose game windows are still open."""
        return self._unhooked_clients

    def start(self) -> None:
        """Create the client detection loop task. Call once after the event loop is running."""
        self._client_loop_task = asyncio.create_task(self._client_loop())

    def stop(self) -> None:
        """Cancel the detection loop and any in-flight hook activations.

        Hook activation can block indefinitely (a client parked at character select never
        writes the player struct), so those tasks would otherwise outlive the manager.
        """
        if self._client_loop_task is not None:
            self._client_loop_task.cancel()
            self._client_loop_task = None
        for task in list(self._activation_tasks):
            task.cancel()
        self._activation_tasks.clear()
        self._activating_hwnds.clear()
        self._failed_hook_hwnds.clear()

    def has_client_titled(self, title: str) -> bool:
        """Whether a fully hooked client with this window title is currently live.

        This is the signal that a launched instance has finished loading in: a client only
        reaches self._clients once activate_hooks() succeeds, which requires a character
        to be in a game zone. Used as the completion check for auto-select and recovery.
        """
        return self.client_titled(title) is not None

    def client_titled(self, title: str):
        """The live client whose window title matches, or None."""
        return _titled(self._clients, title)

    def _alias_for(self, username: str) -> str | None:
        """The configured alias for *username*, or None. Keeps real usernames out of the GUI."""
        for account in self._settings.accounts:
            if account.get("username") == username:
                return account.get("alias") or None
        return None

    def pending_client_titled(self, title: str):
        """The client for this window whether or not its hooks have come up yet.

        Auto-select reads the character-select screen through this. It must be the Client
        this manager already registered — building a second one for the same process
        re-prepares WizWalker's autobot region and corrupts the hooks the first one
        installed, so there is deliberately no way to ask for a fresh one.
        """
        return _titled(self._handler.clients, title)

    async def await_client_in(self, expected_title: str) -> bool:
        """Wait for a window to reach in-game under its own steam, pressing nothing."""
        return await auto_select.wait_for_client_in(expected_title, lambda: self.has_client_titled(expected_title))

    def swap_clients(self, idx_a: int, idx_b: int) -> None:
        """Swap the positions of two clients in the live list.

        Changes which client index a product routes to, updates game-view tile labels,
        and republishes — without unhooking or re-hooking any WizWalker clients.
        """
        if idx_a == idx_b:
            return
        if self._refuse_reorder():
            return
        if idx_a >= len(self._clients) or idx_b >= len(self._clients):
            return
        self._clients[idx_a], self._clients[idx_b] = (self._clients[idx_b], self._clients[idx_a])
        # Keep _client_order in sync so recovery preserves the user's reordering.
        if idx_a < len(self._client_order) and idx_b < len(self._client_order):
            self._client_order[idx_a], self._client_order[idx_b] = (
                self._client_order[idx_b],
                self._client_order[idx_a],
            )
        order = list(range(len(self._clients)))
        order[idx_a], order[idx_b] = idx_b, idx_a
        self.clients_reordered.emit(order)
        # Both numbers moved — the slots the tiles sit in and the player indices their
        # captions read from — so the view is re-derived rather than relabeled. The tiles
        # themselves are re-keyed, not rebuilt, so no game window is re-parented.
        self._sync_tiles()
        self.clients_changed.emit(self._clients)
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

    def reorder_clients(self, order: list[int]) -> None:
        """Rearrange the whole live client list at once, where ``order[i]`` is the old index now at *i*.

        A sibling to :meth:`swap_clients`, not a replacement -- that one keeps its own
        callers and its own meaning. What this adds is *one update per gesture*: a drag
        decomposed into transpositions would rebuild the sidebar, re-derive the tile layout
        and emit both signals once per transposition, putting intermediate orders on screen
        that nobody asked for. Everything below the permutation happens exactly once.

        A permutation that does not describe the current list is refused rather than
        applied. The column that emitted it was built from a list read some moments
        earlier, and an unhook racing the drag can invalidate it; refusing costs the user
        one drag, applying reorders a list that is not the one they dragged.
        """
        reordered = apply_order(self._clients, order)
        if reordered is None or order == list(range(len(order))):
            return
        if self._refuse_reorder():
            return
        self._clients[:] = reordered
        # Keep _client_order in sync so recovery preserves the user's reordering. Positional,
        # exactly as swap_clients is: _clients is hooked-only and contiguous while
        # _client_order can hold None for a slot awaiting recovery, so position i in one is
        # not necessarily slot i in the other. That misalignment belongs to recovery and is
        # deliberately not corrected here.
        if len(self._client_order) >= len(order):
            head = [self._client_order[i] for i in order]
            self._client_order[: len(order)] = head
        self.clients_reordered.emit(list(order))
        # Both numbers moved -- the slots the tiles sit in and the player indices their
        # captions read from -- so the view is re-derived rather than relabeled.
        self._sync_tiles()
        self.clients_changed.emit(self._clients)
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

    def _refuse_reorder(self) -> bool:
        """Whether to refuse a swap or reorder asked for now. Never, here.

        A product overrides this when its players must keep their order for a while:
        BartleBot's, while a bot is running, since the running node holds the very list a
        reorder rewrites in place.
        """
        return False

    def account_for(self, client) -> str | None:
        """The account username *client* was launched under, or None when unknown.

        Two things a caller has to plan for:

        - **None is normal, especially early.** The map behind this is filled by
          ``_refresh_account_identity`` from inside the client loop, so a client hooked a
          moment ago has no account yet, and a game window this app did not launch may
          never be identified at all.
        - **Nothing signals the answer changing.** ``all_clients_changed`` fires when the
          set of hooked clients changes, not when an identity is merely *learned*, and a
          signal for that is more machinery than the answer needs. Re-ask on each refresh
          tick instead -- this is a dict lookup, the same bargain the Wizard row already
          makes with ``wizard_names.cached_name``.
        """
        return self._hwnd_account_map.get(client.window_handle)

    async def unhook_client(self, index: int) -> None:
        """Unhook WizWalker from the client at the given index without closing its game window."""
        if index >= len(self._clients):
            return
        prev_clients = list(self._clients)
        await self._unhook_one(self._clients[index])
        self._finish_hook_change(prev_clients)

    async def unhook_all(self) -> None:
        """Unhook every active client at once, leaving all game windows open."""
        prev_clients = list(self._clients)
        if not prev_clients:
            return
        await asyncio.gather(*[self._unhook_one(c) for c in prev_clients])
        self._finish_hook_change(prev_clients)

    async def rehook_client(self, hwnd: int) -> None:
        """Re-activate WizWalker hooks on a previously unhooked client."""
        client = next((c for c in self._unhooked_clients if c.window_handle == hwnd), None)
        if client is None:
            return
        prev_clients = list(self._clients)
        if await self._rehook_one(client):
            self._assign_rehooked_slots([client])
            self._finish_hook_change(prev_clients)

    async def rehook_all(self) -> None:
        """Re-activate WizWalker hooks on every previously unhooked client."""
        candidates = list(self._unhooked_clients)
        if not candidates:
            return
        prev_clients = list(self._clients)
        results = await asyncio.gather(*[self._rehook_one(c) for c in candidates])
        rehooked = [c for c, ok in zip(candidates, results) if ok]
        if not rehooked:
            return
        self._assign_rehooked_slots(rehooked)
        self._finish_hook_change(prev_clients)

    async def shutdown(self, *, close_windows: bool) -> list[int]:
        """Release every client on the way out, optionally closing their game windows.

        Quitting with clients still embedded destroys them: ``ClientTile`` re-parents each
        game window under the Qt window with ``SetParent``, so Qt takes its children down
        with it when the window is torn down. Unhooking is what releases them —
        the same path the Unhook All button already runs — so this unhooks whichever way
        the user answered, and only then closes the windows it was told to close.

        Returns the hwnds whose process survived the close sequence; always empty when
        *close_windows* is False.
        """
        # The detection loop re-hooks and re-embeds on every tick, so it goes first:
        # anything it did after the unhook would be a window embedded on the way out.
        self.stop()
        # pids are read while the clients still exist — force_close_client falls back to
        # reading one from the window, which fails once the window is gone.
        targets = (
            [(c.window_handle, c.process_id) for c in self._clients + self._unhooked_clients] if close_windows else []
        )
        try:
            await self.unhook_all()
        except Exception:
            logger.exception("Error while unhooking clients on shutdown")
        # Belt and braces: a tile left behind by a failed unhook would still take its
        # game window down with the Qt window, which is the whole bug.
        self._game_view.unembed_all()
        if not targets:
            return []
        results = await asyncio.gather(*[self._close_game_window(h, pid) for h, pid in targets])
        return [hwnd for (hwnd, _), closed in zip(targets, results) if not closed]

    @staticmethod
    async def _close_game_window(hwnd: int, pid: int | None) -> bool:
        """Close one game window, reporting rather than raising when it will not go."""
        try:
            return await force_close_client(hwnd, pid=pid)
        except Exception:
            logger.exception("Failed to close game window hwnd=%d", hwnd)
            return False

    # ------------------------------------------------------------------
    # Hook toggling internals
    # ------------------------------------------------------------------

    async def _unhook_one(self, client) -> None:
        """Close hooks on *client* and move it from the active list to the unhooked list."""
        hwnd = client.window_handle
        self._unhooked_hwnds.add(hwnd)

        try:
            await client.close()
        except Exception:
            logger.exception("Error while unhooking client hwnd=%d", hwnd)

        username = self._hwnd_account_map.pop(hwnd, None)
        if username:
            try:
                self._client_order.remove(username)
            except ValueError:
                pass

        self._clients = [c for c in self._clients if c.window_handle != hwnd]
        self._unhooked_clients.append(client)
        logger.info("Unhooked client hwnd=%d — game window left open", hwnd)

    async def _rehook_one(self, client) -> bool:
        """Activate hooks on *client* and move it back into the active list. Returns success."""
        hwnd = client.window_handle
        try:
            await client.activate_hooks()
        except Exception:
            logger.exception("Error while rehooking client hwnd=%d", hwnd)
            return False

        self._unhooked_clients.remove(client)
        self._unhooked_hwnds.discard(hwnd)
        self._clients.append(client)
        logger.info("Rehooked client hwnd=%d", hwnd)
        return True

    def _assign_rehooked_slots(self, rehooked: list) -> None:
        """Restore ordering slots for clients that just came back under hooks."""
        self._refresh_account_map(self._clients)
        for client in rehooked:
            username = self._hwnd_account_map.get(client.window_handle)
            if username:
                self._assign_slot(username)

    def _finish_hook_change(self, prev_clients: list) -> None:
        """Reconcile embedding and listeners after a hook add/remove."""
        self._sync_tiles()
        self.clients_changed.emit(list(self._clients))
        self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

    # ------------------------------------------------------------------
    # Client loop
    # ------------------------------------------------------------------

    async def _client_loop(self) -> None:
        while True:
            try:
                self._handler.remove_dead_clients()
                for c in self._handler.get_new_clients():
                    self._start_activation(c)

                clients = list(self._handler.clients)

                # Exclude unhooked clients — window still open but hooks deactivated —
                # plus clients whose hooks are still coming up or failed to install.
                excluded = self._unhooked_hwnds | self._activating_hwnds | self._failed_hook_hwnds
                clients = [c for c in clients if c.window_handle not in excluded]

                # Identify windows by title, over *every* live window rather than only the
                # hooked ones. The launcher titles a window right after login, so this is
                # what lets a client still at character select be matched to its account,
                # given a slot below, and put on screen — which is the whole point of
                # embedding before a character is in game. It is title reads and a window
                # rect, no memory, so an unhooked window costs nothing here.
                self._refresh_account_map(self._handler.clients)

                # Clean up _unhooked_clients whose game windows have since closed.
                handler_hwnds = {c.window_handle for c in self._handler.clients}
                self._failed_hook_hwnds &= handler_hwnds
                closed_unhooked = [c for c in self._unhooked_clients if c.window_handle not in handler_hwnds]
                if closed_unhooked:
                    for c in closed_unhooked:
                        self._unhooked_clients.remove(c)
                        self._unhooked_hwnds.discard(c.window_handle)
                    self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

                if not clients:
                    # Detect removals before clearing the list — handles the case where the
                    # last remaining client(s) crash, which would otherwise skip _on_clients_removed.
                    if self._clients:
                        removed_hwnds = {c.window_handle for c in self._clients}
                        self._on_clients_removed(removed_hwnds)
                    self._clients = []
                    self._forget_closed_windows(handler_hwnds)
                    # Not a return to the launch screen: this branch is also the whole of a
                    # launch, from the first window appearing until the first character is
                    # in game, and those windows have tiles.
                    self._place_pending_accounts()
                    self._sync_tiles()
                    self.clients_changed.emit([])
                    self.all_clients_changed.emit([], list(self._unhooked_clients))
                    await asyncio.sleep(CLIENT_DETECT_INTERVAL_IDLE)
                    continue

                # Place whatever the titles could not: a window this app did not launch
                # carries no account tag, and is identified by what it reports in memory
                # instead. Runs before the comparison below so an adopted client is
                # already placed when its slot is assigned.
                await self._refresh_account_identity(clients)

                # Compare by HWND set to detect additions and removals.
                new_hwnd_set = {c.window_handle for c in clients}
                cur_hwnd_set = {c.window_handle for c in self._clients}
                if new_hwnd_set != cur_hwnd_set:
                    # Detect removals before rebuilding the list.
                    removed_hwnds = cur_hwnd_set - new_hwnd_set
                    if removed_hwnds:
                        self._on_clients_removed(removed_hwnds)

                    # Register any newly added known clients in _client_order.
                    #
                    # Sorting by window position puts clients that appear in the same poll
                    # cycle into visual layout order rather than EnumWindows Z-order. It only
                    # decides anything for windows nobody reserved a slot for — a client
                    # opened outside this app. Anything launched from here is already placed
                    # by reserve_slots, and could not be ordered by position anyway: a launch
                    # spawns the whole batch at one shared anchor, so the keys are all equal.
                    new_additions = sorted(
                        [c for c in clients if c.window_handle not in cur_hwnd_set],
                        key=lambda c: (c.window_rectangle.y1, c.window_rectangle.x1),
                    )
                    for c in new_additions:
                        username = self._hwnd_account_map.get(c.window_handle)
                        if username:
                            self._place_account(username)

                    self._clients = self._build_ordered_clients(clients)
                    self.clients_changed.emit(self._clients)
                    self.all_clients_changed.emit(list(self._clients), list(self._unhooked_clients))

                # Only now that the removals above have been handled, because everything
                # they report a disconnect with is what this drops.
                self._forget_closed_windows(handler_hwnds)

                # Windows still logging in or choosing a character reach neither branch
                # above — they are not clients yet — so they are placed and drawn here.
                self._place_pending_accounts()
                self._sync_tiles()

                # Keep wizard names current: notice character switches, and read the name
                # for any client that does not have one. One uint64 read per client per
                # cycle, plus a behavior walk only on a genuine miss.
                #
                # Runs *after* the client list is rebuilt above, and over self._clients
                # rather than the handler's list, for two reasons: the game tiles are keyed
                # by position in self._clients, so emitting the handler's order would put
                # names on the wrong tiles once a user reorders players; and tiles for
                # newly appeared clients are only created by the sync above, so emitting
                # first would announce names to tiles that do not exist yet — and the next
                # tick would see no change and stay quiet.
                await self._refresh_wizard_names(self._clients)
                self._learn_character_roster(self._clients)

                await asyncio.sleep(CLIENT_DETECT_INTERVAL)

            # Both keep the client list as it was. The next pass compares against it, and that
            # comparison is what reports a client that died as removed and queues its recovery:
            # emptying the list here, as both once did, lost the removal of a client whose last
            # reads were what failed, and popped every game window out of its tile (#154).
            except MemoryReadError:
                logger.debug("Client loop: a memory read failed; trying again", exc_info=True)
                await asyncio.sleep(CLIENT_DETECT_INTERVAL)
            except Exception:
                logger.exception("Client loop error")
                await asyncio.sleep(CLIENT_DETECT_ERROR_BACKOFF)

    def _start_activation(self, client) -> None:
        """Begin hooking a newly detected client, without blocking the detection loop.

        activate_hooks() waits for the *player struct* address to be written, and that only
        happens once a character is in a game zone — a window sitting on the login or
        character select screen never writes it, and WizWalker passes no timeout, so the
        await simply never returns. Awaiting that inline stalls the whole loop: no zone
        refresh, no embedding, no crash detection, for as long as any one window is parked
        at character select. Auto-select makes that the normal case on every launch.

        Timing out and retrying is not the fix. activate_all_hooks() installs every hook
        before it waits, so a client abandoned mid-wait leaves jmps in the game process
        pointing into WizWalker's autobot scratch region; building a fresh Client for the
        same process re-runs _prepare_autobot, which zeroes that region out from under
        them. So a failed activation is left alone — the client stays registered with the
        handler and is reaped by remove_dead_clients() when its window closes.
        """
        hwnd = client.window_handle
        if hwnd in self._activating_hwnds:
            return
        self._activating_hwnds.add(hwnd)

        async def _activate() -> None:
            try:
                await client.activate_hooks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Client activation failed for hwnd %s", hwnd)
                self._failed_hook_hwnds.add(hwnd)
            else:
                # The hooks wait for a character in the world, so the account's key bindings
                # are in memory now: find them here, a scan of a few seconds, rather than when
                # the first run starts. A task of its own, so publishing the client never waits
                # on it; the pre-run check takes its answer, and warns if they cannot be read.
                warm = asyncio.create_task(game_keys.key_map(client, refresh=True, warn=False))
                self._activation_tasks.add(warm)
                warm.add_done_callback(self._activation_tasks.discard)
            finally:
                self._activating_hwnds.discard(hwnd)

        task = asyncio.create_task(_activate())
        self._activation_tasks.add(task)
        task.add_done_callback(self._activation_tasks.discard)

    async def _refresh_wizard_names(self, clients: list) -> None:
        """Keep cached wizard names current, and announce them when they change.

        Two jobs. Character switches invalidate a cached name: the window handle the cache
        is keyed by survives a switch, so without this a cached name goes on naming the
        previous wizard for the rest of the session — which is what sends a group invite to
        the wrong character. And any client without a name gets one read from memory, so
        the sidebar and the game tiles have something to show without anything having asked.

        Memory only — never the character page. Filling a label is not worth opening the
        character UI on a live client.

        Never raises: a stale name degrades grouping, but an exception here would take down
        the client loop that owns hook lifecycle and crash recovery.
        """
        from bartleby.runtime.game import wizard_names  # noqa: PLC0415

        try:
            changed = await wizard_names.fill_names_from_memory(clients)
        except Exception:
            logger.debug("Could not refresh wizard names", exc_info=True)
            return

        names = [wizard_names.cached_name(c) for c in clients]
        if changed or names != self._last_wizard_names:
            self._last_wizard_names = names
            self.wizard_names_changed.emit(list(names))

    def _learn_character_roster(self, clients: list) -> None:
        """Record which characters each identified account has, for the launch screen.

        The launch screen has to describe an account that is not running, and nothing
        else in the app remembers a character past the life of its window. This is the
        one moment both halves are in hand: a live client whose account the map has
        placed, whose name the cache holds, and whose character id says which character
        that name belongs to.

        Cheap by construction. Both reads are dict lookups into caches the loop has
        already filled — no memory access of its own — and a keyring write only happens
        when the roster actually changes, which for a stable set of characters is once
        each, ever.

        Never raises: this runs inside the client loop, where an exception escaping here
        would cost the rest of the pass, tiles and names included. character_roster's own calls swallow their failures,
        so the remaining risk is a client object going away mid-pass.
        """
        from bartleby.runtime.clients import character_roster  # noqa: PLC0415
        from bartleby.runtime.game import wizard_names  # noqa: PLC0415

        for client in clients:
            try:
                username = self._hwnd_account_map.get(client.window_handle)
                if not username:
                    continue  # unidentified — an adopted window we have never launched
                name = wizard_names.cached_name(client)
                gid = wizard_names.cached_character_id(client)
                if not name or gid == wizard_names.NO_CHARACTER:
                    continue  # at character select, mid-login, or the name is not read yet
                roster = self._rosters.get(username)
                if roster is None:
                    roster = character_roster.load(username)
                    self._rosters[username] = roster
                updated = character_roster.upsert(roster, gid, name)
                if updated == roster:
                    continue
                self._rosters[username] = updated
                if character_roster.save(username, updated):
                    # No name and no id in the message: a log line naming a wizard is the
                    # thing the roster's keyring storage exists to avoid, and the count is
                    # what makes this line useful anyway.
                    logger.info(
                        "Learned a character for account '%s' (%d known)",
                        account_label(username),
                        len(updated),
                    )
            except Exception:
                logger.debug("Could not update the character roster for a client", exc_info=True)

    def characters_for(self, username: str) -> list[dict]:
        """The known characters for *username*, newest last. Never raises.

        Public because the launch screen asks about accounts that are not running, which
        is precisely the case no client object can answer.
        """
        from bartleby.runtime.clients import character_roster  # noqa: PLC0415

        roster = self._rosters.get(username)
        if roster is None:
            roster = character_roster.load(username)
            self._rosters[username] = roster
        return list(roster)

    async def _refresh_account_identity(self, live_clients: list) -> None:
        """Tie each client to an account by the user id it reports, learning or adopting.

        Two directions, and which one runs depends on whether the title already placed
        the client:

        - **Learn.** A client the title matched was launched by this app, so its account
          is certain — we typed those credentials. Its fingerprint is written to that
          account, which is the only moment the association can be established at all.
        - **Adopt.** A client the title could not place is matched by fingerprint against
          the configured accounts. This is what makes a window launched elsewhere and
          adopted here recoverable.

        Never raises: this runs inside the client loop, where an exception escaping here
        would cost the rest of the pass, the client list's update included.
        """
        settings = self._settings
        accounts = settings.accounts
        if not accounts:
            return

        # An account already answering for a live window cannot also be another one's.
        # Checked against live clients rather than the account map: the map is pruned of
        # closed windows each tick (see _forget_closed_windows) but still names windows
        # that are not clients here -- unhooked, or not yet through character select.
        claimed = {
            self._hwnd_account_map[c.window_handle] for c in live_clients if c.window_handle in self._hwnd_account_map
        }

        for client in live_clients:
            hwnd = client.window_handle
            user_id = await self._read_user_id(client)
            if not user_id:
                continue  # not logged in yet; nothing to identify it by

            try:
                fp = account_identity.fingerprint(user_id, account_identity.account_key())
            except Exception:
                logger.exception("Could not fingerprint the user id for hwnd=%d", hwnd)
                return  # the key is broken, so the whole pass is pointless

            known = self._hwnd_account_map.get(hwnd)
            if known:
                if account_identity.stored_fingerprint(accounts, known) != fp:
                    accounts = account_identity.with_fingerprint(accounts, known, fp)
                    # The setter writes the shared account store through, so no explicit
                    # save follows. The association has to outlive the process: it exists
                    # so a later session can recognize a window this one launched.
                    settings.accounts = accounts
                    logger.info(
                        "Learned the identity of account '%s' (user id %s) — clients adopted from "
                        "another launcher can now be recognized as this account",
                        account_label(known),
                        account_identity.mask(user_id),
                    )
                continue

            username = account_identity.match_account(accounts, fp)
            if username is None:
                continue
            if username in claimed:
                logger.warning(
                    "Client hwnd=%d reports account '%s', which another live client is already "
                    "answering for — leaving it unidentified rather than guessing",
                    hwnd,
                    account_label(username),
                )
                continue

            self._hwnd_account_map[hwnd] = username
            claimed.add(username)
            self._assign_slot(username)
            logger.info(
                "Adopted client hwnd=%d as account '%s' by its user id (%s)",
                hwnd,
                account_label(username),
                account_identity.mask(user_id),
            )

    async def _read_user_id(self, client) -> int:
        """This client's account-level user id, cached once a real one has been read.

        Zero means the client has not finished logging in; that is re-read every cycle
        until it resolves. A non-zero id cannot change without a new process, so it is
        read exactly once per window — the first read also resolves GameClient's base
        address by pattern scan, which is far too expensive to repeat per second.
        """
        cached = self._hwnd_user_ids.get(client.window_handle)
        if cached:
            return cached
        try:
            user_id = await client.game_client.user_id()
        except Exception:
            return 0
        if user_id:
            self._hwnd_user_ids[client.window_handle] = user_id
        return user_id or 0

    def _refresh_account_map(self, live_clients: list) -> None:
        """For each live client with an unrecognized HWND, try to match window title to an account."""
        import ctypes  # noqa: PLC0415
        import ctypes.wintypes  # noqa: PLC0415

        settings = self._settings
        for client in live_clients:
            if client.window_handle in self._hwnd_account_map:
                continue  # already known
            try:
                title = get_window_title(client.window_handle)
            except Exception:
                continue
            # Remembered whether or not it matches an account: the ones that never match
            # are exactly the ones a removal has to explain itself about.
            self._hwnd_titles[client.window_handle] = title
            for account in settings.accounts:
                username = account["username"]
                alias = account.get("alias") or None
                display = CredentialStore.display_name(username, alias)
                if title == f"[{display}] Wizard101":
                    self._hwnd_account_map[client.window_handle] = username
                    try:
                        rect = ctypes.wintypes.RECT()
                        ctypes.windll.user32.GetClientRect(client.window_handle, ctypes.byref(rect))
                        w, h = rect.right - rect.left, rect.bottom - rect.top
                        if w > 0 and h > 0:
                            self._client_resolutions[username] = (w, h)
                    except Exception:
                        pass
                    break

    def _forget_closed_windows(self, live_hwnds: set[int]) -> None:
        """Drop everything remembered about a window whose game process is gone.

        The account map was written once per window and never unwritten, so an account
        stayed in ``active_accounts`` for the rest of the session after its window closed
        — and ``begin_launch`` reads that to reject an account that is "already running".
        Closing a client and launching it again therefore did nothing but put up a message
        box saying it was already up, while the tile it was talking about had gone from the
        grid on the tick the window closed.

        Called from the client loop *after* the removal handling, never before: the
        username here is the same one ``_on_clients_removed`` reports a removal with, and
        ``_hwnd_titles`` is what names a lost window once its title can no longer be
        read. Dropping either first would turn a recoverable disconnect into an
        unrecoverable one.

        Everything dropped here is re-learned for free if the window turns out to be alive
        after all: the map is rebuilt from window titles on every tick, and the user id is
        re-read from the client. So a handler that briefly loses a window costs one tick,
        not a session.
        """
        for hwnd in [h for h in self._hwnd_account_map if h not in live_hwnds]:
            username = self._hwnd_account_map.pop(hwnd)
            logger.info(
                "Window hwnd=%d for account '%s' has closed — the account can be launched again",
                hwnd,
                account_label(username),
            )
        # Both of these are keyed by window too, and a window that never carried an account
        # leaves an entry in them just the same.
        for hwnd in [h for h in self._hwnd_titles if h not in live_hwnds]:
            del self._hwnd_titles[hwnd]
        for hwnd in [h for h in self._hwnd_user_ids if h not in live_hwnds]:
            del self._hwnd_user_ids[hwnd]

    def _on_clients_removed(self, removed_hwnds: set) -> None:
        """Empty the slots of hooked clients that disappeared, then report each one.

        The slot is remembered as the account's vacated slot, so a client that comes back
        outside any recovery reclaims it. Everything about the window that a report needs
        is gathered here, before ``_forget_closed_windows`` drops it, and handed to
        ``clients_removed`` while the pass is still under way: a product that brings
        clients back (BartleBot's recovery) holds the slot in ``_pending_recovery`` from
        there, before anything else can take it.
        """
        removals: list[Removal] = []
        for hwnd in removed_hwnds:
            # Dropped first: nothing reports the user id.
            self._hwnd_user_ids.pop(hwnd, None)
            title = self._hwnd_titles.pop(hwnd, None)
            username = self._hwnd_account_map.get(hwnd)
            slot = None
            if username:
                slot = next((i for i, u in enumerate(self._client_order) if u == username), None)
                if slot is not None:
                    self._client_order[slot] = None
                    # Remember the slot even when a recovery holds it: recovery can be
                    # abandoned (launch failure, login timeout) while the client still
                    # shows up later, and it should reclaim this slot rather than be
                    # appended past the end of the ordering.
                    self._vacated_slots[username] = slot
            removals.append(Removal(hwnd=hwnd, username=username, slot=slot, title=title))
        self.clients_removed.emit(removals)

    def begin_launch(self, usernames: list[str], resolution: tuple[int, int] | None = None) -> list[str]:
        """Claim slots and open a launch for *usernames*, returning the ones accepted.

        The single door every launch route goes through. In BartleBot they used to reserve
        by different paths -- a single account called ``reserve_slots`` from the window, a
        profile handed the reserver down into ``launch_profile`` -- and neither could see the
        other, so a click during a launch reserved nothing new and spawned a second batch
        anyway. Rejecting an account already in flight is the whole point, and it can only
        be done where every route meets.

        An account is rejected for either of two reasons, and they are different states:
        a launch is already running for it, or it is already *here* -- a window open and
        recognized, whether hooked, embedded, or set aside unhooked. The second is the one
        a profile hits: a profile lists accounts, not windows, so starting a four-account
        profile with two of them already up would spawn a second window for each of those
        two, logged into the same account as the one on screen. The in-flight tracker
        cannot see that, because those launches finished -- successfully -- some time ago.

        The rejected ones are dropped rather than failing the batch: a profile of four with
        one account already launching should bring up the other three. Callers pair this
        with ``end_launch`` in a ``finally`` -- an attempt that is never ended leaves the
        account unlaunchable for the rest of the session.

        *resolution* is what the windows will open at, so their pending tiles hold room for
        that grid rather than for the global resolution, which a profile may not use.
        """
        active = set(self.active_accounts)
        wanted = [u for u in usernames if u not in active]
        accepted = self._launches.begin(wanted, resolution)
        if accepted:
            self.reserve_slots(accepted)
            self._sync_tiles()
        already_here = [u for u in usernames if u in active]
        if already_here:
            logger.info(
                "Ignoring launch for account(s) already running: %s", ", ".join(map(account_label, already_here))
            )
        rejected = [u for u in wanted if u not in accepted]
        if rejected:
            logger.info("Ignoring launch for account(s) already launching: %s", ", ".join(map(account_label, rejected)))
        return accepted

    def end_launch(self, usernames: list[str]) -> None:
        """Close the launches for *usernames*, freeing the slot of any that never arrived.

        Called from the caller's ``finally``, so it runs on the failure paths too -- and
        those are the ones that matter. Nothing else takes a reservation back: a launch
        that dies at the login screen would otherwise hold its slot, and the grid would
        keep a place for a window that is not coming until the app restarts.

        A slot is only released if no window turned up for it. An account that reached the
        login screen keeps its slot whether or not the drive that followed succeeded --
        there is a real window sitting in it, and moving it would be the tile shuffle the
        reserved ordering exists to prevent.

        A launch that failed keeps its attempt open so its tile can say so, but only where
        there is a tile to say it on: an account whose window came up and whose *drive*
        then failed is closed here like any other, because its slot holds a real client
        tile and a failure state with nowhere to be drawn is a launch the user can never
        dismiss.
        """
        arrived = set(self._hwnd_account_map.values())
        for username in usernames:
            if username in arrived:
                self._launches.dismiss(username)
            else:
                self._launches.end([username])
        # Only the launches that actually closed give their slots back. One left open
        # because it failed is still holding a tile, and the tile has to stay where it is
        # for the user to read it and click it away.
        self._release_unarrived([u for u in usernames if u not in self._launches])
        self._sync_tiles()

    def dismiss_launch(self, username: str) -> None:
        """Clear a failed launch the user has acknowledged, giving its slot back.

        The other half of leaving a failed attempt open. Nothing else closes one -- that is
        the point of the terminal stage -- so without this a failure is exactly the
        permanent artifact the pending tile was built to avoid.
        """
        if not self._launches.dismiss(username):
            return
        self._release_unarrived([username])
        self._sync_tiles()

    def report_launch_failed(self, username: str) -> None:
        """Mark a launch as having given up, and redraw its tile to say so."""
        if self._launches.fail(username):
            self._sync_tiles()

    def report_launch_stage(self, username: str, stage: LaunchStage) -> None:
        """Record which part of its launch *username* has reached, and redraw its tile.

        Handed to the launcher as its progress callback. Syncing only on a real change
        keeps this off the tick path: the launcher reports a stage per account per phase,
        which is a handful of calls per launch, and a repeated report costs a dict lookup.
        """
        if self._launches.set_stage(username, stage):
            self._sync_tiles()

    @property
    def launching(self) -> list[str]:
        """Accounts with a launch running, for a caller deciding whether to offer another."""
        return list(self._launches.in_flight())

    @property
    def active_accounts(self) -> list[str]:
        """Accounts that already have a game window open under this app.

        Read off the account map rather than ``clients``, because the question is about
        windows and not about hooks. ``_refresh_account_map`` runs over every live window
        each tick -- hooked, unhooked, and still at character select -- so the map names
        an account from the moment the launcher titles its window until that window
        closes, which is exactly the span during which launching it again would produce a
        duplicate. The closing half of that span is ``_forget_closed_windows``, which the
        loop runs every tick -- without it this property is the reason an account that has
        been shut down cannot be launched again.

        An adopted window this app has never launched is in here too, once
        ``_refresh_account_identity`` has fingerprinted it. One belonging to an account
        with no fingerprint on file stays unidentified and so cannot be guarded -- the
        same known limitation ``account_identity`` documents, and the same fix: launching
        that account from here once teaches it permanently.
        """
        return list(dict.fromkeys(self._hwnd_account_map.values()))

    def _release_unarrived(self, usernames: list[str]) -> None:
        """Give back the reserved slots of accounts that never produced a window.

        Trailing empties are trimmed as well, so a failed launch does not leave the
        ordering longer than the fleet it describes. That length is what ``_assign_slot``
        hands out from, and letting it grow is the slot drift its docstring describes.
        """
        arrived = set(self._hwnd_account_map.values())
        released = False
        for username in usernames:
            if username in arrived:
                continue
            for pos, held in enumerate(self._client_order):
                if held == username:
                    self._client_order[pos] = None
                    self._vacated_slots.pop(username, None)
                    released = True
        if not released:
            return
        while self._client_order and self._client_order[-1] is None:
            self._client_order.pop()
        logger.info(
            "Released slot(s) for launch(es) that produced no window: %s", _slot_order_for_log(self._client_order)
        )

    def reserve_slots(self, usernames: list[str]) -> None:
        """Pin the slot order for a batch of accounts about to be launched.

        Slot order is otherwise inferred from the windows themselves, and nothing about a
        window carries the order the user asked for. A launch profile is the one place that
        order is stated explicitly, so it is stated here, before the windows exist.

        Three things were deciding the order instead, none of them the profile:

        - Clients are published as each one's own hooks come up, so _assign_slot ran in
          whatever sequence the characters finished loading in — a 3-15 s race, different
          every run.
        - launch_profile pairs accounts to windows through a set difference, so account 0's
          credentials go to an arbitrary one of the new windows.
        - The positional sort in _client_loop cannot break the tie, because the whole batch
          is spawned at one shared anchor and every window has the same coordinates.

        Reserving up front makes all three irrelevant: _assign_slot returns early for an
        account already in the ordering, so each client keeps the slot named for it here
        however late it loads in. Accounts already running keep their slots, and slots
        being held for a client in recovery are left alone.
        """
        held_for_recovery = set(self._pending_recovery)
        for username in usernames:
            if username in self._client_order:
                continue
            # An explicit launch overrides whatever slot this account last sat in: the
            # profile is a statement of intent, the vacated slot is only a memory.
            self._vacated_slots.pop(username, None)
            free = next(
                (i for i, u in enumerate(self._client_order) if u is None and i not in held_for_recovery),
                None,
            )
            if free is not None:
                self._client_order[free] = username
            else:
                self._client_order.append(username)
        logger.info("Reserved slot order for launch: %s", _slot_order_for_log(self._client_order))

    def _place_account(self, username: str) -> None:
        """Give *username* its slot, honoring a recovery that is holding one for it.

        A client coming back from a crash goes to the slot it left, not to the first free
        one — the ordering is the user's, and recovery is meant to be invisible in it.
        Everything else falls through to _assign_slot's preference order.
        """
        recovery_pos = next((pos for pos, u in self._pending_recovery.items() if u == username), None)
        if recovery_pos is not None:
            self._client_order[recovery_pos] = username
            self._vacated_slots.pop(username, None)
        else:
            self._assign_slot(username)

    def _place_pending_accounts(self) -> None:
        """Give every identified window a slot, whether or not its hooks are up.

        A window is identified by its *title*, which the launcher sets immediately after
        login — long before a character is in game. Claiming the slot then is what lets the
        tile appear at character select and then *stay where it is* when the client finally
        hooks: the slot it is drawn in is already the slot it will keep.

        A launch profile has reserved these itself (see reserve_slots), so this changes
        nothing there. It is what covers an account launched on its own from a card, which
        states no order and so has nothing to reserve.
        """
        for hwnd in sorted(self._embeddable_hwnds()):
            username = self._hwnd_account_map.get(hwnd)
            if username and username not in self._client_order:
                self._place_account(username)

    def _assign_slot(self, username: str) -> None:
        """Place *username* into _client_order without growing the ordering needlessly.

        Preference order: the slot this client last occupied, then any slot left empty
        by a client that disconnected, and only then a new slot at the end.

        Appending unconditionally is what caused slot drift: a disconnect always nulls
        the client's slot, but recovery does not always run (bot stopped) or always
        succeed (login timeout). Each such round left a permanent hole and appended the
        returning client past the end, so the ordering grew past the client count —
        four clients reporting slots 0-4.
        """
        if username in self._client_order:
            return
        prev_slot = self._vacated_slots.pop(username, None)
        if prev_slot is not None and prev_slot < len(self._client_order) and self._client_order[prev_slot] is None:
            self._client_order[prev_slot] = username
            return
        free = next((i for i, u in enumerate(self._client_order) if u is None), None)
        if free is not None:
            self._client_order[free] = username
        else:
            self._client_order.append(username)

    def _build_ordered_clients(self, live_clients: list) -> list:
        """Build the client list in _client_order order; unknown-account clients go at the end."""
        username_to_client = {
            self._hwnd_account_map.get(c.window_handle): c
            for c in live_clients
            if self._hwnd_account_map.get(c.window_handle)
        }
        unknown_clients = [c for c in live_clients if self._hwnd_account_map.get(c.window_handle) is None]
        # Clients in desired order; skip None slots (awaiting recovery)
        ordered = [username_to_client[u] for u in self._client_order if u is not None and u in username_to_client]
        # Append manually-launched clients whose accounts are not tracked
        for c in unknown_clients:
            if c not in ordered:
                ordered.append(c)
        return ordered

    def _embeddable_hwnds(self) -> set[int]:
        """Every live window that may hold a tile, hooked or not.

        A window is embeddable as soon as it exists: ``ClientTile`` needs an hwnd and
        nothing else, so there is no reason to wait for a character to be in game. Two
        exclusions, both deliberate. A client the user unhooked by hand is meant to be left
        alone. A client whose hooks *failed* can never become a player and is never retried
        (see ``_start_activation``), so giving it a tile would promise something the app
        cannot deliver.
        """
        live = {c.window_handle for c in self._handler.clients}
        return live - self._unhooked_hwnds - self._failed_hook_hwnds

    def _account_label(self, username: str) -> str:
        """What to call a window that has an account but no player index yet."""
        meta = next((a for a in self._settings.accounts if a.get("username") == username), {})
        return CredentialStore.display_name(username, meta.get("alias") or None)

    def tile_slots(self) -> list[TileSlot]:
        """Every window that should be on screen right now, in slot order.

        Public because it is the whole contract with the game view, and because a test
        that cannot ask this question has to reach into the ordering by hand.
        """
        return build_tile_slots(
            client_order=self._client_order,
            hwnd_accounts=self._hwnd_account_map,
            embeddable_hwnds=self._embeddable_hwnds(),
            client_hwnds=[c.window_handle for c in self._clients],
            account_label=self._account_label,
            launching=self._launches.in_flight(),
        )

    def _sync_tiles(self) -> None:
        """Hand the game view the current tile set.

        Called on every client-loop tick rather than only when the client list changes: a
        window reaching character select changes no client list at all — it is not hooked —
        and it is exactly the window this exists to put on screen. ``sync_tiles`` is a
        no-op when nothing moved, so the common tick costs one dict comparison.
        """
        try:
            self._game_view.sync_tiles(self.tile_slots())
        except Exception:
            logger.exception("Syncing the game view tiles failed")


#: What answers ``clients_removed``: called with every hooked client that disappeared in a pass.
RemovalHook = Callable[[list[Removal]], None]
