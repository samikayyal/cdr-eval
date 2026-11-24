import asyncio
import logging
from collections.abc import AsyncGenerator
from pathlib import Path

from radio.preparation import mp3_packets
from radio.storage import PlaylistEntry, open_audio

LOGGER = logging.getLogger(__name__)
LISTENER_QUEUE_SIZE = 10


class Broadcast:
    def __init__(self, database: Path, playlist: tuple[PlaylistEntry, ...]) -> None:
        self._database = database
        self._playlist = playlist
        self._on_air: PlaylistEntry | None = None
        self._up_next: PlaylistEntry | None = None
        self._on_air_finished: asyncio.Event | None = None
        self._skip_on_air = False
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._producer: asyncio.Task[None] | None = None

    @property
    def playlist(self) -> tuple[PlaylistEntry, ...]:
        return self._playlist

    @property
    def on_air_entry_id(self) -> int | None:
        return self._on_air.id if self._on_air else None

    def on_air_completion(self, track_id: int) -> asyncio.Event | None:
        if self._on_air and self._on_air.track.id == track_id:
            return self._on_air_finished
        return None

    def update_playlist(self, playlist: tuple[PlaylistEntry, ...]) -> asyncio.Event | None:
        previous = self._playlist
        self._playlist = playlist
        if self._on_air is None:
            return None
        for position, entry in enumerate(playlist):
            if entry.id == self._on_air.id:
                self._on_air = entry
                self._up_next = playlist[(position + 1) % len(playlist)]
                return None

        # Advance the broadcast to the removed entry's first surviving successor.
        by_id = {entry.id: entry for entry in playlist}
        start = previous.index(self._up_next) if self._up_next else 0
        self._up_next = next(
            (by_id[entry.id] for entry in previous[start:] + previous[:start] if entry.id in by_id),
            playlist[0] if playlist else None,
        )
        self._skip_on_air = True
        for listener in self._listeners:
            while not listener.empty():
                listener.get_nowait()
        return self._on_air_finished

    def status(self) -> dict[str, dict[str, str] | None]:
        return {
            label: {"title": entry.track.title, "artist": entry.track.artist} if entry else None
            for label, entry in (("on_air", self._on_air), ("up_next", self._up_next))
        }

    def _clear_on_air(self) -> None:
        self._on_air = self._up_next = self._on_air_finished = None

    async def listen(self) -> AsyncGenerator[bytes, None]:
        listener: asyncio.Queue[bytes | None] = asyncio.Queue(LISTENER_QUEUE_SIZE)
        self._listeners.add(listener)
        if self._producer is None:
            self._producer = asyncio.create_task(self._produce())
        try:
            while (packet := await listener.get()) is not None:
                yield packet
        finally:
            self._listeners.discard(listener)
            if not self._listeners and self._producer is not None:
                self._producer.cancel()
                self._producer = None
                self._clear_on_air()

    async def _produce(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            entry = self._playlist[0] if self._playlist else None
            while entry is not None:
                finished = asyncio.Event()
                try:
                    with open_audio(self._database, entry.track.id) as audio:
                        self._on_air = entry
                        self._on_air_finished = finished
                        self._skip_on_air = False
                        position = self._playlist.index(entry)
                        self._up_next = self._playlist[(position + 1) % len(self._playlist)]
                        for packet, seconds in mp3_packets(audio.read):
                            for listener in tuple(self._listeners):
                                if listener.full():
                                    self._end(listener)
                                else:
                                    listener.put_nowait(packet)
                            if not self._listeners:
                                return
                            deadline = max(deadline + seconds, loop.time())
                            await asyncio.sleep(deadline - loop.time())
                            if self._skip_on_air:
                                deadline = loop.time()
                                break
                finally:
                    finished.set()
                entry = self._up_next
        except Exception:
            LOGGER.exception("Broadcast stopped while reading prepared audio")
        finally:
            if self._producer is asyncio.current_task():
                for listener in tuple(self._listeners):
                    self._end(listener)
                self._producer = None
                self._clear_on_air()

    def _end(self, listener: asyncio.Queue[bytes | None]) -> None:
        self._listeners.remove(listener)
        while not listener.empty():
            listener.get_nowait()
        listener.put_nowait(None)
