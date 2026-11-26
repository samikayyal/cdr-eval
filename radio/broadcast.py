import asyncio
import logging
import sqlite3
from collections.abc import AsyncGenerator, Generator
from contextlib import closing
from itertools import chain
from pathlib import Path

from radio.preparation import mp3_packets
from radio.storage import PlaylistEntry, open_audio, read_restart_position, save_restart_position

LOGGER = logging.getLogger(__name__)
LISTENER_QUEUE_SIZE = 10


class Broadcast:
    def __init__(self, database: Path, playlist: tuple[PlaylistEntry, ...]) -> None:
        self._database = database
        self._playlist = playlist
        self._failed_tracks: set[int] = set()
        self._restart_position = read_restart_position(database)
        self._on_air: PlaylistEntry | None = None
        self._up_next: PlaylistEntry | None = None
        self._on_air_finished: asyncio.Event | None = None
        self._skip_on_air = False
        self._playback_id = 0
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._producer: asyncio.Task[None] | None = None

    @property
    def playlist(self) -> tuple[PlaylistEntry, ...]:
        return self._playlist

    @property
    def on_air_entry_id(self) -> int | None:
        return self._on_air.id if self._on_air else None

    @property
    def playback_id(self) -> int | None:
        return self._playback_id if self._on_air else None

    @property
    def unavailable(self) -> bool:
        return all(entry.track.id in self._failed_tracks for entry in self._playlist)

    def on_air_completion(self, track_id: int) -> asyncio.Event | None:
        if self._on_air and self._on_air.track.id == track_id:
            return self._on_air_finished
        return None

    async def skip(self, playback_id: int) -> bool:
        finished = self._on_air_finished
        if finished is None or playback_id != self._playback_id:
            return False
        if not self._skip_on_air:
            self._interrupt_on_air()
        await finished.wait()
        return True

    def update_playlist(self, playlist: tuple[PlaylistEntry, ...]) -> asyncio.Event | None:
        previous = self._playlist
        self._playlist = playlist
        if self._on_air is None:
            return None
        for position, entry in enumerate(playlist):
            if entry.id == self._on_air.id:
                self._on_air = entry
                self._up_next = playlist[(position + 1) % len(playlist)]
                self._save_position(position)
                return None

        # Advance the broadcast to the removed entry's first surviving successor.
        by_id = {entry.id: entry for entry in playlist}
        start = previous.index(self._up_next) if self._up_next else 0
        self._up_next = next(
            (by_id[entry.id] for entry in previous[start:] + previous[:start] if entry.id in by_id),
            playlist[0] if playlist else None,
        )
        self._interrupt_on_air()
        return self._on_air_finished

    def _interrupt_on_air(self) -> None:
        self._skip_on_air = True
        for listener in self._listeners:
            while not listener.empty():
                listener.get_nowait()

    def status(self) -> dict[str, dict[str, str] | list[int] | bool | None]:
        starts_with = self._playable_from(self._starting_position()) if self._on_air is None else None
        up_next = self._playable_from(self._playlist.index(self._up_next)) if self._up_next else None
        return {
            label: {"title": entry.track.title, "artist": entry.track.artist} if entry else None
            for label, entry in (("on_air", self._on_air), ("up_next", up_next), ("starts_with", starts_with))
        } | {
            "unavailable": self.unavailable,
            "failed_entry_ids": [entry.id for entry in self._playlist if entry.track.id in self._failed_tracks],
        }

    def _playable_from(self, position: int) -> PlaylistEntry | None:
        return next(
            (entry for entry in self._playlist[position:] + self._playlist[:position]
             if entry.track.id not in self._failed_tracks),
            None,
        )

    def _starting_position(self) -> int:
        return self._restart_position if self._restart_position < len(self._playlist) else 0

    def _save_position(self, position: int) -> None:
        if position != self._restart_position:
            save_restart_position(self._database, position)
            self._restart_position = position

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

    def _read_packets(self, track_id: int) -> Generator[tuple[bytes, float], None, None]:
        try:
            with open_audio(self._database, track_id) as audio:
                if not len(audio):
                    raise ValueError("Empty prepared MP3")
                yield from mp3_packets(audio.read)
        except (ValueError, sqlite3.Error) as error:
            self._failed_tracks.add(track_id)
            LOGGER.warning("Skipping unreadable recording %s: %s", track_id, error)

    async def _produce(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            position = self._starting_position()
            while (entry := self._playable_from(position)) is not None:
                position = self._playlist.index(entry)
                self._up_next = self._playlist[(position + 1) % len(self._playlist)]
                finished = asyncio.Event()
                try:
                    with closing(self._read_packets(entry.track.id)) as packets:
                        first = next(packets, None)
                        if first is None:
                            position = (position + 1) % len(self._playlist)
                            continue
                        # Save before sending audio so a process crash also resumes this track.
                        self._save_position(position)
                        self._on_air = entry
                        self._on_air_finished = finished
                        self._skip_on_air = False
                        self._playback_id += 1
                        for packet, seconds in chain((first,), packets):
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
                position = self._playlist.index(self._up_next) if self._up_next else 0
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
