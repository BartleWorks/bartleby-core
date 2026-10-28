"""One state reader per client, publishing a live snapshot of it.

Followers, the GUI and the recovery watchers all want the same few facts about a client,
some of them several times a second. Each reading memory for itself would multiply the
reads by the number of watchers, so one reader per client reads them on three cadences
and publishes a :class:`Snapshot`; everything else subscribes.

* position and yaw, several times a second (:data:`~bartleby.runtime.timing.POSITION_READ_INTERVAL`);
* zone, loading and battle, about once a second (:data:`~bartleby.runtime.timing.ZONE_READ_INTERVAL`);
* the speed multiplier, less often (:data:`~bartleby.runtime.timing.STATS_READ_INTERVAL`).

A snapshot is immutable, and is the only thing about a client that crosses to the GUI
thread. A failed read keeps the last value: a client mid-zone-change fails every read for a
moment, and the next tick gets it.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from bartleby.runtime.timing import POSITION_READ_INTERVAL, STATS_READ_INTERVAL, ZONE_READ_INTERVAL

logger = logging.getLogger(__name__)

Point = tuple[float, float, float]


@dataclass(frozen=True)
class Snapshot:
    """What one client looked like at *time* (``time.monotonic``)."""

    key: int
    time: float = 0.0
    position: Point | None = None
    yaw: float | None = None
    zone: str | None = None
    loading: bool = False
    in_battle: bool = False
    speed: int | None = None

    @property
    def readable(self) -> bool:
        """Whether there is a position and zone to act on: not loading, and read at least once."""
        return self.position is not None and self.zone is not None and not self.loading


Listener = Callable[[Snapshot], None]


class StateReader:
    """Reads one client on its cadences and publishes each new snapshot to its listeners."""

    def __init__(
        self,
        client,
        *,
        position_interval: float = POSITION_READ_INTERVAL,
        zone_interval: float = ZONE_READ_INTERVAL,
        stats_interval: float = STATS_READ_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self._intervals = (position_interval, zone_interval, stats_interval)
        self._clock = clock
        self._listeners: list[Listener] = []
        self.latest = Snapshot(key=client.window_handle)
        self._task: asyncio.Task | None = None

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name=f"state-reader-{self.client.window_handle}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def run(self) -> None:
        position_every, zone_every, stats_every = self._intervals
        next_zone = next_stats = 0.0
        while True:
            now = self._clock()
            changes: dict = {}
            if now >= next_zone:
                changes |= await self._read_zone()
                next_zone = now + zone_every
            if now >= next_stats:
                changes |= await self._read_stats()
                next_stats = now + stats_every
            if not changes.get("loading", self.latest.loading):
                changes |= await self._read_position()
            self.publish(**changes)
            await asyncio.sleep(position_every)

    def publish(self, **changes) -> Snapshot:
        """Make a new snapshot from the latest with *changes*, and hand it to every listener."""
        self.latest = dataclasses.replace(self.latest, time=self._clock(), **changes)
        for listener in list(self._listeners):
            try:
                listener(self.latest)
            except Exception:
                logger.exception("StateReader listener raised")
        return self.latest

    async def _read_position(self) -> dict:
        try:
            pos = await self.client.body.position()
            yaw = await self.client.body.yaw()
        except Exception:
            return {}
        return {"position": (pos.x, pos.y, pos.z), "yaw": yaw}

    async def _read_zone(self) -> dict:
        try:
            loading = await self.client.is_loading()
        except Exception:
            return {}
        if loading:
            return {"loading": True, "zone": None}
        changes: dict = {"loading": False}
        with contextlib.suppress(Exception):
            changes["zone"] = await self.client.zone_name()
        with contextlib.suppress(Exception):
            changes["in_battle"] = await self.client.in_battle()
        return changes

    async def _read_stats(self) -> dict:
        try:
            return {"speed": await self.client.client_object.speed_multiplier()}
        except Exception:
            return {}
