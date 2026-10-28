"""The part of BartleBot's node_helpers that needs no bot: the game's own windows.

Moved from BartleBot's ``bartlebot/nodes/node_helpers.py`` (a0c51a71) with everything it
uses and nothing else: finding, reading and clicking UI windows by path, pressing through
dialogue (declining side quests), recognizing and answering modals, the friends list and a
friend's profile, and porting clients to a friend. None of it reads a bot, an option or a
machine, which is what let it move whole.

Every function here that presses or clicks is input, so in a product it is called with a
lease's client (:mod:`bartleby.runtime.arbiter`); the actions beside this module
(:mod:`~bartleby.runtime.actions.dialogue`, :mod:`~bartleby.runtime.actions.friends`) take
the lease itself.
"""

import asyncio
import logging
import math
import re
import weakref
from collections.abc import Collection
from typing import NamedTuple

from wizwalker import Keycode

from bartleby.runtime.clients import game_keys, sightings
from bartleby.runtime.game import dialog_captions
from bartleby.runtime.game.key_bindings import GameAction, action_label
from bartleby.runtime.paths import (
    ADVANCE_DIALOG_PATH,
    CHARACTER_PROFILE_CLOSE_PATH,
    CHARACTER_PROFILE_PATH,
    CROWNS_SHOP_WINDOW_PATH,
    DECLINE_QUEST_PATH,
    FRIENDS_LIST_BUTTON_PATH,
    FRIENDS_LIST_PATH,
    FRIENDS_LIST_PATHS,
    GO_TO_FRIEND_BUTTON_PATH,
    LARGE_FRIENDS_LIST_PATH,
    MESSAGE_BOX_CAPTION_PATH,
    MESSAGE_BOX_CENTER,
    MESSAGE_BOX_RIGHT,
)
from bartleby.runtime.timing import (
    DIALOG_FAST_POLL_INTERVAL,
    DIALOG_PAGE_SETTLE,
    FRIEND_PROFILE_SETTLE_TIMEOUT,
    FRIEND_PROFILE_TIMEOUT,
    FRIEND_TELEPORT_ATTEMPTS,
    FRIEND_TELEPORT_CONFIRM_TIMEOUT,
    POLL_INTERVAL,
    PORT_QUIET_GRACE,
    TRIGGER_TIMEOUT,
    WINDOW_CLOSE_CONFIRM_INTERVAL,
    WINDOW_CLOSE_RETRY_INTERVAL,
    WINDOW_CLOSE_TIMEOUT,
    ZONE_CHANGE_TIMEOUT,
)

logger = logging.getLogger(__name__)


# One lock per client around a dialogue page's read and its press. See DIALOG_PAGE_SETTLE.
_dialog_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _dialog_lock(client) -> asyncio.Lock:
    lock = _dialog_locks.get(client)
    if lock is None:
        lock = _dialog_locks[client] = asyncio.Lock()
    return lock


async def advance_dialog_once(client) -> bool:
    """Advance *client*'s open dialogue by one page, declining a side quest rather than taking it.

    The one place dialogue is pressed. A side quest's offer page puts ACCEPT on the right
    button, the same one SPACEBAR presses to advance every other page, so pressing through
    blind takes every side quest an NPC offers. The offer page is the only one with a left
    button (DECLINE), so while that shows it is clicked instead, and nothing else is pressed.

    DECLINE is only read once the right button shows: a child's visibility is its own flag
    (see is_visible_by_path), and the right button is the one already trusted to say a
    dialogue is open.

    Returns True while dialogue is open -- pressed, or a DECLINE that could not be clicked,
    which the next call tries again rather than falling back to SPACEBAR.
    """
    async with _dialog_lock(client):
        if not await is_visible_by_path(client, ADVANCE_DIALOG_PATH):
            return False
        if await is_visible_by_path(client, DECLINE_QUEST_PATH):
            if await click_by_path(client, DECLINE_QUEST_PATH):
                logger.info("Declined a side quest offer")
            else:
                logger.debug("A side quest offer's DECLINE could not be clicked yet")
        else:
            await client.send_key(Keycode.SPACEBAR, 0.1)
        await asyncio.sleep(DIALOG_PAGE_SETTLE)
        return True


async def advance_dialogue_until_clear(
    clients: list,
    safety_delay: float,
    timeout: float | None,
    first_wait: float | None = None,
) -> bool:
    """Advance dialogue on all clients until all are dialogue-free for safety_delay seconds.

    Advances any client that has an open dialogue banner (advance_dialog_once, which declines
    a side quest offer), then resets the quiet timer. Polls at DIALOG_FAST_POLL_INTERVAL
    after a dismissal and POLL_INTERVAL during the quiet phase (no dialogue detected). Returns True when all clients have been dialogue-free
    for safety_delay seconds. Returns False if timeout expires while clients are still in
    dialogue.

    `first_wait`, when given, is how long each client may take to open its first dialogue,
    counted from the call — the time an animation may take to open it. It is per client: one
    player's window opening early says nothing about another's animation, which is still
    playing. A client that has shown dialogue needs only the safety_delay quiet period after
    the last page.
    """
    loop = asyncio.get_event_loop()
    overall_start = loop.time()
    quiet_since = overall_start
    # The clients whose first dialogue has not been seen yet, while first_wait still covers them.
    unseen = set(range(len(clients))) if first_wait is not None else set()

    while True:
        now = loop.time()
        dialogs = await asyncio.gather(*[advance_dialog_once(c) for c in clients])
        unseen -= {i for i, shown in enumerate(dialogs) if shown}
        animating = bool(unseen) and (now - overall_start) < (first_wait or 0.0)

        if not any(dialogs) and (now - quiet_since) >= safety_delay and not animating:
            return True

        if timeout is not None and (now - overall_start) >= timeout:
            return False

        if any(dialogs):
            quiet_since = loop.time()
            await asyncio.sleep(DIALOG_FAST_POLL_INTERVAL)
        else:
            await asyncio.sleep(POLL_INTERVAL)


async def identify_modal(client) -> str | None:
    """Return the lang key of the modal currently on screen, or None.

    Every in-game modal is the same MessageBoxModalWindow tree, so "a modal is up" says
    nothing about which one it is. Its caption does: see bartleby.runtime.game.dialog_captions for the
    key constants to compare against (INSTANCE_EXIT, FRIEND_BUSY, …) and for how the match
    survives markup, substitution slots, and all seven locales.

    None means *no recognized dialog* — either nothing is showing, or something the
    snapshot has not seen. The two are deliberately not distinguished: callers should have
    a button-based fallback for both, so a caption we do not know degrades to the previous
    behavior rather than hanging. Use modal_showing() to tell them apart when it matters.
    """
    return dialog_captions.identify_caption(await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH))


async def modal_showing(client) -> bool:
    """Return True when any modal message box is on screen, recognized or not.

    Reads the caption rather than the modal window itself: a window's is_visible() flag is
    its own and ignores its parents (see is_visible_by_path), whereas CaptionText only
    holds text while a dialog is actually up.
    """
    return bool(await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH))


class ModalOutcome(NamedTuple):
    """What resolve_modal() did about the dialog on screen.

    `answered` says a dialog was there and has been clicked — a caller in a retry loop
    should skip its retry this tick, because the click it would send lands on a modal that
    is still closing rather than on the button it means to press.

    `refusal` is set when that dialog was one the game will not be talked out of. Pair it
    with dialog_captions.describe() to log what the player would have read.
    """

    answered: bool
    refusal: str | None = None


_NOTHING = ModalOutcome(answered=False)


async def resolve_modal(client, *, recognized_only: bool = False) -> ModalOutcome:
    """Answer whatever modal is on screen.

    The one call a node needs while waiting for something the game may be holding behind a
    dialog. Three outcomes:

      * a question (dialog_captions.CONFIRM_TO_PROCEED) — confirmed, so the node's wait
        carries on and will now succeed;
      * a refusal (dialog_captions.REFUSALS) — dismissed, and returned, so the node can
        fail immediately: the game has said no, and waiting out a timeout only delays the
        same answer while reporting it as the wrong cause;
      * nothing recognized — falls back to the button layout, which still separates the
        two kinds: a center button offers a way to proceed, so it is confirmed; a right
        button with no center offers only a way out, so it is dismissed and reported as
        dialog_captions.UNRECOGNIZED_REFUSAL. Without that second half a dialog reworded
        by a game patch would stop failing fast and start timing out instead.

    Refusals are dismissed with the right button where it exists and the center otherwise:
    a one-button refusal puts OK in the center, and a two-button one puts cancel on the
    right, which is the answer that leaves the game alone.

    `recognized_only` answers only a dialog the snapshot knows, and leaves any other alone.
    It is for a wait that goes on answering for as long as it lasts, which would otherwise
    confirm or decline whatever the game raises meanwhile: an invitation, a request.
    """
    key = await identify_modal(client)

    if key is None:
        if recognized_only:
            return _NOTHING
        if await is_visible_by_path(client, MESSAGE_BOX_CENTER):
            await click_by_path(client, MESSAGE_BOX_CENTER)
            return ModalOutcome(answered=True)
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
            return ModalOutcome(answered=True, refusal=dialog_captions.UNRECOGNIZED_REFUSAL)
        return _NOTHING

    if key in dialog_captions.CONFIRM_TO_PROCEED:
        logger.debug("resolve_modal: confirming %s", key)
        await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True)

    if key in dialog_captions.REFUSALS:
        logger.debug("resolve_modal: dismissing %s", key)
        if await is_visible_by_path(client, MESSAGE_BOX_RIGHT):
            await click_by_path(client, MESSAGE_BOX_RIGHT)
        else:
            await click_by_path(client, MESSAGE_BOX_CENTER)
        return ModalOutcome(answered=True, refusal=key)

    # Recognized, but somebody else's dialog — leave it alone rather than click blind.
    logger.debug("resolve_modal: leaving %s to its own handler", key)
    return _NOTHING


async def diagnose_path(client, path: list[str]) -> str:
    """Walk path step-by-step and return a diagnostic string.

    If a segment is missing, reports the name that was expected, the names of the
    children that actually exist at that level, and how far traversal got.
    If the full path exists, reports whether is_visible() is True or False.
    Intended for DEBUG logging when is_visible_by_path unexpectedly returns False.
    """
    node = client.root_window
    for i, segment in enumerate(path):
        children = await node.children()
        names = [await c.name() for c in children]
        match = next((c for c, n in zip(children, names) if n == segment), None)
        if match is None:
            prefix = "→".join(path[:i]) if i > 0 else "(root)"
            return f"missing {segment!r} under {prefix!r}; actual children: {names}"
        node = match
    visible = await node.is_visible()
    return f"path found, is_visible()={visible}"


async def is_visible_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is currently visible.

    IMPORTANT: a window's is_visible() flag is its OWN flag — it does not account for its
    parents. A button inside a hidden window commonly still reports visible=True, so
    reading a child to infer whether its container is open gives a constant answer.
    Observed directly: wndFriendsList reads visible=False while its btnFriendListClose
    reads visible=True, before and after the friends list is ever opened
    (tools/probe_friends_list.py).

    So: to ask "is this panel open?", read the panel's own path, not a control inside it.
    """
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return await window.is_visible()


async def iter_windows_by_path(parent, path: list[str]):
    """Yield every window reachable from parent by this sequence of child names, in tree order.

    Lazy on purpose: the single-result callers below take the first yielded window and stop,
    so an unambiguous path costs exactly what a first-match walk always cost. Only a caller
    that asks for every match pays to explore the remaining branches.

    An empty path yields the parent itself.
    """
    if not path:
        yield parent
        return
    for child in await parent.children():
        if await child.name() == path[0]:
            async for window in iter_windows_by_path(child, path[1:]):
                yield window


async def _get_window_from_path(parent, path: list[str]):
    """Traverse the UI window tree by a sequence of child names.

    Returns the matching Window object, or None if the path cannot be followed.

    Backtracks across same-named siblings: if two children share a name and only the
    second one leads to the rest of the path, the second is returned. This is what makes
    ambiguous names like Wizard101's duplicate `ButtonLayout` windows safe to path
    through, as long as one branch alone completes the path.

    When SEVERAL branches complete the path — a list menu whose rows are structurally
    identical — the first in tree order wins silently. Use find_windows_by_path() or
    resolve_path_match() where that choice matters.
    """
    async for window in iter_windows_by_path(parent, path):
        return window
    return None


async def find_windows_by_path(parent, path: list[str]) -> list:
    """Return every window reachable by this name path, in tree order.

    Unlike _get_window_from_path(), this does not stop at the first branch that completes
    the path, so the caller can see that a path is ambiguous and pick among the matches.
    """
    return [window async for window in iter_windows_by_path(parent, path)]


def strip_html(text: str) -> str:
    """Remove HTML tags from a string (e.g. '<center>Unicorn' → 'Unicorn')."""
    return re.sub(r"<[^>]+>", "", text).strip()


async def _window_text(window) -> str:
    """Read one window's displayed text, stripped of the game's HTML wrapping.

    Returns '' for any window that has no text or whose text cannot be decoded — a routine
    outcome, since maybe_text() reads a different offset per class and fails on many of them.
    """
    try:
        return strip_html(await window.maybe_text() or "")
    except Exception:
        logger.debug("Could not read window text", exc_info=True)
        return ""


async def window_text_matches(window, needle: str, *, exact: bool = False, deep: bool = True) -> bool:
    """Whether this window's text — or, when deep, any descendant's — matches needle.

    Case-insensitive either way; `exact` switches equality for substring. Deep by default
    because a menu row is usually a container whose label lives on a child text window.
    """
    target = needle.strip().casefold()
    text = (await _window_text(window)).casefold()
    if (text == target) if exact else (target in text):
        return True
    if not deep:
        return False
    try:
        children = await window.children()
    except Exception:
        logger.debug("Could not read window children while text matching", exc_info=True)
        return False
    for child in children:
        if await window_text_matches(child, needle, exact=exact, deep=True):
            return True
    return False


async def resolve_path_match(
    client,
    path: list[str],
    *,
    match_text: str = "",
    match_text_exact: bool = False,
    match_index: int = 0,
) -> tuple[object | None, int]:
    """Pick one window among everything `path` matches.

    Returns ``(window, candidate_count)``, where the count is how many windows the path
    matched before `match_text` filtered them — so a caller can report "matched 6 rows" even
    when its text filter then rejected all of them. The window is None when nothing matched,
    when no match carried the text, or when match_index is out of range.

    With the defaults this is `_get_window_from_path` plus a count: first match, no filter.
    """
    candidates = await find_windows_by_path(client.root_window, path)
    count = len(candidates)
    if match_text:
        matched = []
        for window in candidates:
            if await window_text_matches(window, match_text, exact=match_text_exact):
                matched.append(window)
        candidates = matched
    if not candidates or not (-len(candidates) <= match_index < len(candidates)):
        return None, count
    return candidates[match_index], count


async def poll_until(condition_coro, timeout: float | None, poll_interval: float = POLL_INTERVAL) -> bool:
    """Poll condition_coro every poll_interval until it returns True or timeout expires.

    Always evaluates condition_coro at least once before the timeout clock starts, so a
    condition that is already True never races against a tight deadline.
    """
    if await condition_coro():
        return True

    async def _poll():
        while not await condition_coro():
            await asyncio.sleep(poll_interval)

    try:
        await asyncio.wait_for(_poll(), timeout=timeout)
        return True
    except TimeoutError:
        return False


async def get_text_by_path(client, path: list[str]) -> str:
    """Return the displayed text of the UI window at path, or '' if not found."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return ""
    return await window.maybe_text() or ""


async def click_until_gone(
    client,
    path: list[str],
    is_open=None,
    timeout: float | None = None,
    poll_interval: float | None = None,
) -> bool:
    """Click the button at `path` until the thing it closes is gone. Returns True once it is.

    A single click is not reliable: another window can sit on top of the button and swallow
    the click, and the game can drop clicks sent during an animation. Retrying until the
    target is actually gone turns "we clicked it" into "it actually closed".

    `is_open` is an async predicate for "the thing is still open". It MUST test the panel
    itself, not the close button, because a control inside a hidden window still reports
    visible=True — polling the button gives a constant answer and guarantees a timeout.
    See is_visible_by_path() for the observed evidence.

    It defaults to the button's own visibility, which is only correct for controls that
    genuinely disappear with their window; pass an explicit predicate otherwise.

    The predicate is evaluated before each click, so an already-closed panel costs one read
    and no clicks — important because these buttons often sit on toggles, where a stray
    click would reopen what was just closed.

    `poll_interval` is the floor on how fast we may click AGAIN, not on how fast we may
    notice the click worked: the gap is spent polling the predicate, so a close that took
    effect immediately returns immediately instead of after a full interval.

    Returns False if the panel is still open when the timeout expires. The warning then says
    how many of the clicks were never sent, because the button was missing or grayed. That
    separates the two ways a close can fail: none sent means a disabled button, all sent
    means something on top took them.

    `timeout` and `poll_interval` default to WINDOW_CLOSE_TIMEOUT and
    WINDOW_CLOSE_RETRY_INTERVAL, read when called, so a test can compress them.
    """
    if timeout is None:
        timeout = WINDOW_CLOSE_TIMEOUT
    if poll_interval is None:
        poll_interval = WINDOW_CLOSE_RETRY_INTERVAL
    if is_open is None:

        async def is_open():
            return await is_visible_by_path(client, path)

    async def _gone():
        return not await is_open()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    attempts = unsent = 0
    while True:
        if await _gone():
            return True
        attempts += 1
        if not await click_by_path(client, path):
            unsent += 1
        if loop.time() >= deadline:
            break
        if await poll_until(_gone, timeout=poll_interval, poll_interval=WINDOW_CLOSE_CONFIRM_INTERVAL):
            return True

    still_open = await is_open()
    if still_open:
        logger.warning(
            "Window closed via %s is still open after %.0fs of retries (%d of %d clicks not sent: button missing or grayed)",
            "/".join(path),
            timeout,
            unsent,
            attempts,
        )
    return not still_open


async def click_window(client, window) -> bool:
    """Click an already-resolved UI window if it is enabled (not grayed out).

    Separate from click_by_path so a caller that had to disambiguate among several
    same-named windows can click the exact one it chose, rather than re-walking the path
    and landing on whichever match comes first.
    """
    if window is None:
        return False
    if await window.is_control_grayed():
        return False
    async with client.mouse_handler:
        await client.mouse_handler.click_window(window)
    return True


async def click_by_path(client, path: list[str]) -> bool:
    """Click the UI window at path if it exists and is enabled (not grayed out).
    Returns True if found, enabled, and clicked. Returns False if not found or grayed."""
    window = await _get_window_from_path(client.root_window, path)
    return await click_window(client, window)


async def open_friends_list(client):
    """Open the compact friends list if it is not already open, and return its window.

    Opens NewFriendsListWindow specifically. The game's other friends list, wndFriendsList,
    is a separate window reached from this one — see paths.FRIENDS_LIST_PATHS. The
    name/profile code reads the compact list, so this deliberately opens only that one.

    Raises ValueError if the window cannot be found even after clicking the button.
    """
    from wizwalker.extensions.scripting.utils import _maybe_get_window_at_path  # noqa: PLC0415

    # The large list has to be cleared, not clicked past. Visibility is judged below from
    # NewFriendsListWindow alone, so from the large list this reads "closed" and clicks
    # btnFriends anyway — and what the game does with that click was never established.
    # Closing first replaces an unknown with the known-good closed state, at
    # the cost of one extra toggle in a case that only arises when a list was left open by
    # hand.
    if await is_visible_by_path(client, LARGE_FRIENDS_LIST_PATH):
        logger.debug("Large friends list is open; closing it before opening the compact one")
        await close_friends_list(client)

    friends_window = await _get_window_from_path(client.root_window, FRIENDS_LIST_PATH)
    if friends_window is not None and await friends_window.is_visible():
        return friends_window
    btn = await _maybe_get_window_at_path(client.root_window, FRIENDS_LIST_BUTTON_PATH)
    await client.mouse_handler.click_window(btn)
    if friends_window is not None:
        return friends_window
    # The window does not exist at all until the list has been opened once.
    return await _maybe_get_window_at_path(client.root_window, FRIENDS_LIST_PATH)


async def press_key_until_gone(
    client,
    action: GameAction | Keycode,
    is_open,
    timeout: float | None = None,
    poll_interval: float | None = None,
) -> bool:
    """Press `action`'s key until `is_open()` reports the panel has closed. Returns True once gone.

    A GameAction is pressed by the key the player's account has it on; a Keycode as itself,
    which is only for a key no player can rebind, such as ESC.

    The keyboard counterpart to click_until_gone, and preferable for panels that have a
    keyboard toggle: a keypress goes to the client as a whole, so unlike a click it cannot
    land on the wrong window — which is the failure mode every close bug here has had.

    `is_open` must test the panel itself; see click_until_gone for why a close button's own
    visibility is useless for this.

    The predicate is checked before each press, because these keys are toggles: pressing
    one on an already-closed panel reopens it.

    As in click_until_gone, `poll_interval` bounds how soon the next press may go out, and
    the gap is spent polling rather than slept through, and both default when called.
    """
    if timeout is None:
        timeout = WINDOW_CLOSE_TIMEOUT
    if poll_interval is None:
        poll_interval = WINDOW_CLOSE_RETRY_INTERVAL

    async def _gone():
        return not await is_open()

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        if await _gone():
            return True
        if isinstance(action, GameAction):
            await game_keys.press(client, action, 0.1)
        else:
            await client.send_key(action, 0.1)
        if loop.time() >= deadline:
            break
        if await poll_until(_gone, timeout=poll_interval, poll_interval=WINDOW_CLOSE_CONFIRM_INTERVAL):
            return True

    still_open = await is_open()
    if still_open:
        label = action_label(action) if isinstance(action, GameAction) else action.name
        logger.warning("Panel toggled by %s is still open after %.0fs of retries", label, timeout)
    return not still_open


class ShopClosure(NamedTuple):
    """What close_crowns_shop() found, and did: whether the shop was open, and whether it is shut now."""

    was_open: bool
    closed: bool


async def close_crowns_shop(client, appear_timeout: float = 0.0, poll_interval: float = POLL_INTERVAL) -> ShopClosure:
    """Close the crowns shop if it is open, with ESC. Never raises.

    The shop is a modal over everything, so while it is up it takes every click: the
    spellbook's, the friends list's, every close button's. It opens by itself on the first
    login of the day, and from the wishlist button on a friend's profile, which an invite
    click can land on (#280). ESC rather than a click, since a keypress goes to the client as
    a whole and nothing over the shop can take it. A shop that is not open costs a read and
    no ESC, which on an empty screen would open the game menu instead.

    `appear_timeout` watches that long for a shop that is on its way, as one is just after
    a login; the default checks once. The shop is not in the window tree at all while it is
    closed, so a read that finds it is one that is up.

    A read that fails reports the shop as not open, so the caller carries on as it would have.
    """

    async def _shown() -> bool:
        return await is_visible_by_path(client, CROWNS_SHOP_WINDOW_PATH)

    try:
        shown = (
            await poll_until(_shown, timeout=appear_timeout, poll_interval=poll_interval)
            if appear_timeout > 0
            else await _shown()
        )
        if not shown:
            return ShopClosure(was_open=False, closed=True)
        closed = await press_key_until_gone(client, Keycode.ESC, is_open=_shown)
        return ShopClosure(was_open=True, closed=closed)
    except Exception:
        logger.debug("Could not close the crowns shop", exc_info=True)
        return ShopClosure(was_open=False, closed=True)


async def is_friends_list_open(client) -> bool:
    """Return True if EITHER friends-list window is showing.

    There are two independent friends-list windows (see paths.FRIENDS_LIST_PATHS):
    the compact NewFriendsListWindow and the larger wndFriendsList. Only one is on screen
    at a time, and F cycles between them, so a check against a single one will report the
    list closed while the other one is still up.
    """
    for path in FRIENDS_LIST_PATHS:
        if await is_visible_by_path(client, path):
            return True
    return False


async def close_friends_list(client) -> bool:
    """Close the friends list, retrying until it is gone. Never raises.

    Closed with the Friends toggle (F by default) rather than a close button. F cycles
    closed -> NewFriendsListWindow -> wndFriendsList -> closed, so pressing it until
    neither window is visible handles whichever list the client happens to be showing.

    A close button cannot do this job: btnFriendListClose belongs to wndFriendsList and
    does not close NewFriendsListWindow. It appeared to work earlier only because the two
    windows put their close buttons in the same place, so the click sometimes landed on
    whichever list was actually up.
    """
    try:
        return await press_key_until_gone(
            client,
            GameAction.FRIENDS,
            is_open=lambda: is_friends_list_open(client),
        )
    except Exception:
        logger.debug("Could not close the friends list", exc_info=True)
        return False


async def list_online_friends(client) -> list[str]:
    """Return the exact-case name of every friend currently online, in list order.

    Opens the online friends list (if not already open) and reads the whole list window
    in one text read. That text contains every online friend regardless of the current
    scroll position, so pagination is not a concern here.

    Raises ValueError if no friends are online, which is what callers have to act on —
    an empty list and a failed read are not distinguishable from the window text alone.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()

    if not list_text:
        raise ValueError("No friends online")

    return [entry.group("name") for entry in _friend_list_entry.finditer(list_text)]


async def resolve_friend_name(client, friend_name_lower: str) -> str:
    """Return the exact-case friend name matching the lowercased search term.

    WizWalker's friend lookups compare names case-sensitively, so callers must resolve the
    exact casing through this function before handing a name to them.

    Raises ValueError if no friends are online or no entry matches the search term.
    """
    for name in await list_online_friends(client):
        if name.lower() == friend_name_lower:
            return name

    # The name is not quoted: callers log this message, and a wizard name must not reach a log.
    raise ValueError("Could not find friend in the online friends list")


_GO_TO_FRIEND = GO_TO_FRIEND_BUTTON_PATH[-1]


async def teleport_via_friends_list(client, friend_name_lower: str) -> None:
    """Teleport to a friend through the in-game friends list: their profile, then Go To Friend.

    WizWalker's teleport_to_friend_from_list did this, and clicked Go To Friend the moment
    the profile showed, before its buttons had settled. Here open_friend_profile opens the
    profile, matching the name case-insensitively, and _click_go_to_friend waits for the
    buttons. A click that brings up no box missed: the profile (and the crowns shop, should
    the click have opened it) is closed, and the whole is tried again, up to
    FRIEND_TELEPORT_ATTEMPTS times.

    Raises ValueError if no friends are online, the name is not found, or no click brought
    up the box. The message never names the friend.
    """
    async with client.mouse_handler:
        for attempt in range(1, FRIEND_TELEPORT_ATTEMPTS + 1):
            await open_friend_profile(client, friend_name_lower)
            if await _click_go_to_friend(client):
                return
            logger.debug("Go To Friend missed (attempt %d of %d)", attempt, FRIEND_TELEPORT_ATTEMPTS)
            await close_friend_profile(client)
    raise ValueError(f"Go To Friend brought up no box in {FRIEND_TELEPORT_ATTEMPTS} attempts")


TRIGGER_MARGIN = 5.0


def distance(a, b) -> float:
    """Straight-line distance between two XYZ-shaped points."""
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


async def _position_or_none(client):
    """The client's position, or None when it cannot be read -- a body mid-load, say."""
    try:
        return await client.body.position()
    except Exception:
        logger.debug("Could not read a client's position", exc_info=True)
        return None


async def _or_none(awaitable):
    """*awaitable*'s result, or None if it raised. For reads a check can do without."""
    try:
        return await awaitable
    except Exception:
        return None


async def _entity_gids(client) -> frozenset[int] | None:
    """The global ids *client* has loaded, or None if its entity list could not be read."""
    try:
        entities = await client.get_base_entity_list()
    except Exception:
        logger.debug("Could not read a client's entity list", exc_info=True)
        return None
    gids = await asyncio.gather(*(_or_none(e.global_id_full()) for e in entities))
    return frozenset(g for g in gids if g is not None)


async def _reading(client) -> sightings.Reading:
    gid, zone, position, loading, seen = await asyncio.gather(
        _or_none(client.client_object.global_id_full()),
        _or_none(client.zone_name()),
        _position_or_none(client),
        _or_none(client.is_loading()),
        _entity_gids(client),
    )
    # An unreadable loading flag is treated as loading: nothing else it reports can be trusted.
    return sightings.Reading(gid=gid, zone=zone, position=position, loading=loading is not False, seen=seen)


async def read_sightings(clients: list, machine=None) -> sightings.Sightings:
    """Check which of *clients* share a zone, realm and instance. See :mod:`bartleby.runtime.clients.sightings`.

    One id read, one entity-list read and one set intersection per client, about 5 ms each.
    Nothing here raises: a read that fails makes its pairs Unknown.

    With *machine*, the check is also folded into its record of who has been seen together
    (``BotMachine.note_sightings``), which is what lets a later check answer for players
    who have since walked out of each other's sight.
    """
    readings = await asyncio.gather(*(_reading(c) for c in clients))
    result = sightings.Sightings(clients, readings)
    note = getattr(machine, "note_sightings", None)
    if note is not None:
        note(result)
    return result


class PortToFriendResult(NamedTuple):
    """Outcome of port_clients()'s wait for every port to start.

    Indices are into the clients_to_move list the caller passed in. A port shows itself one of
    three ways, and each has its own set:

    * `seen_loading` -- the client entered a loading screen: another zone, or the same zone on
      another realm.
    * `jumped` -- its position moved with no loading screen: the same zone and realm.
    * `presumed` -- neither, through a re-issue, for a client the caller said may already be
      where it is going. A port onto a player you are standing beside does nothing visible.

    `refusal` is set when the game refused the port outright for one client — pair it with
    dialog_captions.describe(); `refused_index` says which client hit it. `unstarted` holds
    the clients whose port could not begin (their `initiate` raised), which are not waited
    for. `missing()` is what is left over: those, and clients that showed nothing at all by
    the ceiling.
    """

    seen_loading: set[int]
    refusal: str | None = None
    refused_index: int | None = None
    jumped: frozenset[int] = frozenset()
    presumed: frozenset[int] = frozenset()
    unstarted: frozenset[int] = frozenset()

    def missing(self, count: int) -> list[int]:
        """Indices below `count` that neither loaded, jumped, nor were presumed already there."""
        started = self.seen_loading | self.jumped | self.presumed
        return [i for i in range(count) if i not in started]


async def port_to_friend(
    clients_to_move: list,
    friend_name_lower: str,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
    maybe_there: Collection[int] = (),
    target=None,
) -> PortToFriendResult:
    """Teleport clients_to_move to friend_name_lower via the friends list, and wait for each
    to enter a loading screen.

    The mechanics shared by everything that ports players to a friend — teleport_to_friend
    and node_helpers' own zone-arrival recovery: initiate,
    watch for whatever modal a not-yet-loading client is showing (a dungeon exit or
    cross-realm warning is confirmed so the port proceeds; a friend-busy dialog is a refusal),
    retry a straggler once trigger_timeout's grace period has passed, and cap the whole wait
    at `ceiling`. Only how the target and the surrounding zone check are resolved differs
    between callers, and that stays with them.

    Raises ValueError if no client's teleport could begin (the friend not found, not online,
    or Go To Friend bringing up no box) — the error teleport_via_friends_list raises. A
    client whose own start or re-issue fails while others' begin is left out of the wait and
    reported in the result's `unstarted`.

    None of the waiting here is bounded by Safety Delay: a caller using this as a fast
    fallback rather than as the whole point of the node should size `ceiling` accordingly
    rather than assume this returns quickly.
    """
    return await port_clients(
        clients_to_move,
        lambda client: teleport_via_friends_list(client, friend_name_lower),
        trigger_timeout=trigger_timeout,
        ceiling=ceiling,
        poll_interval=poll_interval,
        maybe_there=maybe_there,
        target=target,
    )


async def port_clients(
    clients_to_move: list,
    initiate,
    *,
    trigger_timeout: float = TRIGGER_TIMEOUT,
    ceiling: float = ZONE_CHANGE_TIMEOUT,
    poll_interval: float = POLL_INTERVAL,
    maybe_there: Collection[int] = (),
    quiet_grace: float | None = None,
    target=None,
) -> PortToFriendResult:
    """Start a port on every client with `initiate`, and wait for each port to show itself.

    `initiate(client)` is how one client's port is started: the friends list for port_to_friend,
    the group roster for group_manager.port_to_group_member. Everything after the start is the
    same whichever way it began -- the dungeon exit and cross-realm warnings are confirmed, a
    friend-busy dialog is a refusal -- so it lives here once. See port_to_friend for the rest.

    What a successful port looks like depends on where the client starts, and the realm it is on
    cannot be read without opening the spellbook, so every sign is accepted from every client:

    * another zone, or the same zone on another realm -- a loading screen;
    * the same zone and realm, standing elsewhere -- no loading screen, but the client's position
      jumps onto the target (more than TRIGGER_MARGIN);
    * the same zone and realm, already beside the target -- nothing at all.

    Only the last is ambiguous, so it is only ever concluded for the indices in `maybe_there`:
    clients the caller has reason to think are already there. Such a client that shows nothing
    for `quiet_grace` has its port re-issued once, in case the first never registered, and
    after another `quiet_grace` is presumed there. Every other quiet client is re-issued once
    per `trigger_timeout` -- once per window, not every poll -- until `ceiling`.

    With `target`, the hooked client being ported to, a presumption has to be seen: the
    client must have the target loaded (#130), which proves the same zone, realm and
    instance where standing beside it proves only the zone. One that does not is on another
    realm, where the port would have loaded, so it goes back to being re-issued like any
    other quiet client.

    Each client's port starts on its own: one whose `initiate` raises is left out of the wait
    (the result's `unstarted`), and the others' ports carry on. Raises what `initiate` raised
    only when no client's port began, since then there is nothing to wait for; both starters
    raise ValueError when they cannot begin. A re-issue that raises joins `unstarted` too,
    and is not re-issued again.
    """
    if quiet_grace is None:
        quiet_grace = PORT_QUIET_GRACE  # read here, not in the signature, so it stays tunable
    maybe_there = set(maybe_there)
    count = len(clients_to_move)
    loop = asyncio.get_event_loop()

    # Positions are read before the port, or a jump that happens during initiate goes unseen.
    start_positions = await asyncio.gather(*[_position_or_none(c) for c in clients_to_move])
    starts = await _start_each(initiate, clients_to_move)
    unstarted = {i for i, error in enumerate(starts) if error is not None}
    if count and len(unstarted) == count:
        raise starts[0]
    for i in sorted(unstarted):
        logger.debug("Client %d of %d: its port could not start (%s)", i + 1, count, type(starts[i]).__name__)

    seen_loading: set[int] = set()
    jumped: set[int] = set()
    presumed: set[int] = set()
    loop_start = loop.time()
    last_attempt = [loop_start] * count
    reissued = [False] * count

    def result(refusal: str | None = None, refused_index: int | None = None) -> PortToFriendResult:
        return PortToFriendResult(
            seen_loading, refusal, refused_index, frozenset(jumped), frozenset(presumed), frozenset(unstarted)
        )

    while True:
        for i in range(count):
            if i in seen_loading or i in jumped or i in presumed or i in unstarted:
                continue
            outcome = await resolve_modal(clients_to_move[i])
            if outcome.refusal:
                return result(outcome.refusal, i)
            if outcome.answered:
                # The port only starts once the dialog is gone, so its quiet window starts then too.
                last_attempt[i] = loop.time()

        loading_flags = await asyncio.gather(*[c.is_loading() for c in clients_to_move])
        for i, is_now_loading in enumerate(loading_flags):
            if is_now_loading:
                seen_loading.add(i)

        # A loading client still reports the position it left, so only the rest are read.
        done = seen_loading | jumped | presumed | unstarted
        unsettled = [i for i in range(count) if i not in done]
        positions = await asyncio.gather(*[_position_or_none(clients_to_move[i]) for i in unsettled])
        quiet: list[int] = []
        for i, pos in zip(unsettled, positions):
            start = start_positions[i]
            if pos is not None and start is not None and distance(pos, start) > TRIGGER_MARGIN:
                jumped.add(i)
            else:
                quiet.append(i)

        now = loop.time()
        due: list[int] = []
        for i in quiet:
            grace = quiet_grace if i in maybe_there else trigger_timeout
            if now - last_attempt[i] < grace:
                continue
            if i in maybe_there and reissued[i]:
                if target is None or await _sees_each_other(target, clients_to_move[i]):
                    presumed.add(i)
                    continue
                maybe_there.discard(i)
            due.append(i)

        if len(seen_loading | jumped | presumed | unstarted) == count or now - loop_start > ceiling:
            return result()

        if due:
            errors = await _start_each(initiate, [clients_to_move[i] for i in due])
            unstarted.update(i for i, error in zip(due, errors) if error is not None)
            after = loop.time()
            for i in due:
                last_attempt[i] = after
                reissued[i] = True

        await asyncio.sleep(poll_interval)


async def _start_each(initiate, clients: list) -> list[Exception | None]:
    """Start every client's port at once; what each raised, or None for one that began.

    One client's failure must not cut the others' ports short: a plain gather raises the
    first error, and the caller never learns which ports had begun. Only an Exception is
    caught; a cancel goes through.
    """
    outcomes = await asyncio.gather(*[initiate(c) for c in clients], return_exceptions=True)
    for outcome in outcomes:
        if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
            raise outcome
    return [outcome if isinstance(outcome, Exception) else None for outcome in outcomes]


async def _sees_each_other(a, b) -> bool:
    """Whether a check proves *a* and *b* share a zone, realm and instance."""
    return (await read_sightings([a, b])).pair(0, 1) is sightings.Sight.TOGETHER


async def open_friend_profile(client, name: str):
    """Open a friend's profile popup from the friends list and return the wndCharacter window.

    This is the first half of WizWalker's teleport_to_friend_from_list — it locates the
    friend, paginates to their row and clicks it, but stops at the profile popup instead of
    going on to teleport. That popup carries the per-player action buttons (go to friend,
    add/remove friend, add to group).

    `name` is matched case-insensitively. WizWalker's own lookup compares names with ==,
    so the exact casing is resolved here first, from the list text this function already
    reads — no extra UI work. Without that, a name whose casing differs even slightly from
    the friends list (e.g. one detected from the character page) silently fails to match.

    Raises ValueError if no friends are online or the name is not in the list.
    """
    from wizwalker.extensions.scripting.utils import (  # noqa: PLC0415
        _click_on_friend,
        _cycle_friends_list,
        _cycle_to_online_friends,
        _friend_list_entry,
        _maybe_get_named_window,
        _maybe_get_window_at_path,
    )

    friends_window = await open_friends_list(client)
    await _cycle_to_online_friends(client, friends_window)

    friends_list = await _maybe_get_named_window(friends_window, "listFriends")
    list_text = await friends_list.maybe_text()
    if not list_text:
        raise ValueError("No friends online")

    wanted = name.lower()
    exact_name = next(
        (e.group("name") for e in _friend_list_entry.finditer(list_text) if e.group("name").lower() == wanted),
        name,
    )
    if exact_name != name:
        logger.debug("Resolved the friend's name to its spelling in the friends list")

    right_button = await _maybe_get_named_window(friends_window, "btnArrowDown")
    page_number = await _maybe_get_named_window(friends_window, "PageNumber")
    page_text = await page_number.maybe_text()
    current_page, _ = map(
        int,
        page_text.replace("<center>", "").replace("</center>", "").replace(" ", "").split("/"),
    )

    friend, friend_index = await _cycle_friends_list(
        client, right_button, friends_list, None, None, exact_name, current_page
    )
    if friend is None:
        # The name is not quoted: callers log this message, and a wizard name must not reach a log.
        raise ValueError("Could not find friend in the online friends list")

    await _click_on_friend(client, friends_list, friend_index)
    return await _maybe_get_window_at_path(client.root_window, CHARACTER_PROFILE_PATH)


# The friend profile popup's buttons as (name, shown, (x1, y1, x2, y2)), the popup itself
# first under the name "popup". The names are the game's control names, never a player's.
ProfileLayout = tuple[tuple[str, bool, tuple[int, int, int, int]], ...]


async def read_friend_profile(client) -> tuple[ProfileLayout | None, str]:
    """Read where each of the friend profile popup's buttons is and whether it shows. Never raises.

    Returns the layout, None when the popup is not open or could not be read, and the text
    the logs show for it. The buttons sit in ButtonLayouts, and the layout can still be
    moving when the popup first reads as visible (#280): two reads that disagree show it
    moved, and one shows which button covered the point that was clicked.
    """
    try:
        popups = await find_windows_by_path(client.root_window, CHARACTER_PROFILE_PATH)
        if not popups:
            return None, profile_layout_text(None)
        popup = popups[0]
        entries = [("popup", True, _rect_tuple(await popup.scale_to_client()))]
        for child in await popup.children():
            if await child.name() != "ButtonLayout":
                continue
            for button in await child.children():
                entries.append(
                    (await button.name(), await button.is_visible(), _rect_tuple(await button.scale_to_client()))
                )
        layout = tuple(entries)
        return layout, profile_layout_text(layout)
    except Exception as exc:
        return None, f"<unreadable: {exc!r}>"


async def await_settled_friend_profile(client) -> tuple[ProfileLayout | None, int]:
    """Wait for the profile popup's buttons to stop moving, and return where they settled.

    Returns the settled layout, or None if no two reads in a row agreed within
    FRIEND_PROFILE_SETTLE_TIMEOUT, with the number of reads taken. A popup already laid out
    settles in two. The reads are spaced a POLL_INTERVAL apart, because two taken back to
    back can both come before the game's next layout pass and agree while still wrong.
    An unreadable popup never counts as settled.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + FRIEND_PROFILE_SETTLE_TIMEOUT
    previous, _ = await read_friend_profile(client)
    reads = 1
    while loop.time() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        current, _ = await read_friend_profile(client)
        reads += 1
        if current is not None and current == previous:
            return current, reads
        previous = current
    return None, reads


def profile_button_rect(layout: ProfileLayout | None, name: str) -> tuple[int, int, int, int] | None:
    """Where the named button is in a layout read, or None if the read has no such button."""
    return next((rect for button, _shown, rect in layout or () if button == name), None)


def profile_layout_text(layout: ProfileLayout | None) -> str:
    if layout is None:
        return "popup missing"
    return ", ".join(f"{name}{'' if shown else ' hidden'}@{rect_text(rect)}" for name, shown, rect in layout)


def _rect_tuple(rect) -> tuple[int, int, int, int]:
    return (rect.x1, rect.y1, rect.x2, rect.y2)


def rect_text(rect: tuple[int, int, int, int]) -> str:
    x1, y1, x2, y2 = rect
    return f"({x1},{y1},{x2},{y2})"


async def close_friend_profile(client) -> bool:
    """Close the friend profile popup, and the crowns shop a click on its wishlist button opens.

    Returns True once neither is up. Never raises. The shop goes first: it is a modal over
    everything, and the profile's close button cannot be clicked under it.
    """
    try:
        shop = await close_crowns_shop(client)
        closed = await click_until_gone(
            client,
            CHARACTER_PROFILE_CLOSE_PATH,
            is_open=lambda: is_visible_by_path(client, CHARACTER_PROFILE_PATH),
        )
        return shop.closed and closed
    except Exception:
        logger.debug("Could not close the friend profile popup", exc_info=True)
        return False


async def _click_go_to_friend(client) -> bool:
    """Click Go To Friend on the open profile popup and answer the box that asks. True if one came.

    The click waits for the popup's buttons to settle, as Invite to Group's does (#280):
    sent the moment the popup showed, it landed on Friend Finder below, or on nothing, and
    no box came. A refusal (dialog_captions.REFUSALS) is left up for port_clients to
    report; anything else is confirmed, and the profile closed during the teleport's
    animation, which takes about a second.
    """
    if not await poll_until(
        lambda: is_visible_by_path(client, CHARACTER_PROFILE_PATH),
        timeout=FRIEND_PROFILE_TIMEOUT,
        poll_interval=POLL_INTERVAL,
    ):
        logger.debug("The friend profile did not appear within %.0fs", FRIEND_PROFILE_TIMEOUT)
        return False
    settled, reads = await await_settled_friend_profile(client)
    if settled is None:
        logger.debug("Profile buttons still moving after %.0fs; clicking anyway", FRIEND_PROFILE_SETTLE_TIMEOUT)
    elif reads > 2:
        logger.debug("Profile buttons moved after the popup showed; settled after %d reads", reads)
    before = settled or (await read_friend_profile(client))[0]
    if not await click_by_path(client, GO_TO_FRIEND_BUTTON_PATH):
        logger.debug("Go To Friend unavailable: %s", await diagnose_path(client, GO_TO_FRIEND_BUTTON_PATH))
        return False

    if not await poll_until(
        lambda: modal_showing(client), timeout=FRIEND_TELEPORT_CONFIRM_TIMEOUT, poll_interval=POLL_INTERVAL
    ):
        _, after_text = await read_friend_profile(client)
        logger.debug(
            "No box asked to teleport within %.0fs of Go To Friend, aimed at %s; profile buttons then: %s",
            FRIEND_TELEPORT_CONFIRM_TIMEOUT,
            rect_text(aimed) if (aimed := profile_button_rect(before, _GO_TO_FRIEND)) else "?",
            after_text,
        )
        return False
    if await identify_modal(client) in dialog_captions.REFUSALS:
        return True
    await click_by_path(client, MESSAGE_BOX_CENTER)
    await click_by_path(client, CHARACTER_PROFILE_CLOSE_PATH)
    return True


async def is_named_window_visible(client, name: str) -> bool:
    """Return True if any window with this name is visible.

    A search of the whole UI, about 30 ms of reads on the event loop, so only for a window
    whose path is not known or that lives in several places, such as MessageBoxModalWindow,
    and never in a poll. A window with a known path is read by is_visible_by_path.
    """
    try:
        windows = await client.root_window.get_windows_with_name(name)
    except Exception:
        logger.debug("Could not search for window %r", name, exc_info=True)
        return False
    for window in windows:
        if await window.is_visible():
            return True
    return False


async def none_loading(clients: list) -> bool:
    """Return True when no client is on a loading screen."""
    loading = await asyncio.gather(*[c.is_loading() for c in clients])
    return not any(loading)


async def click_until_dialog_gone(client, button: list[str]) -> bool:
    """Click `button` on the dialog showing until that dialog has gone. True once it has.

    Gone means the caption changed: the box closed, or another one came up in its place.
    Every modal is the same window, so the caption is the only way to tell the dialog
    answered from the one queued behind it, which this leaves for the caller to read and
    answer. A second box with the very same caption gets the same click, which is the
    answer the caller chose for that caption. Nothing showing costs one read and no click.
    """
    caption = await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH)
    if not caption:
        return True

    async def still_up() -> bool:
        return await get_text_by_path(client, MESSAGE_BOX_CAPTION_PATH) == caption

    return await click_until_gone(client, button, is_open=still_up)


async def is_enabled_by_path(client, path: list[str]) -> bool:
    """Return True if the UI window at path exists and is not grayed out (i.e., enabled)."""
    window = await _get_window_from_path(client.root_window, path)
    if window is None:
        return False
    return not await window.is_control_grayed()
