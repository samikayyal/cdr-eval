import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Iterator
from pathlib import Path

from radio.storage import PlaylistEntry, open_audio

LOGGER = logging.getLogger(__name__)
FRAMES_PER_PACKET = 4
SECONDS_PER_FRAME = 1152 / 44_100
LISTENER_QUEUE_SIZE = 10


def mp3_packets(read: Callable[[int], bytes]) -> Iterator[tuple[bytes, float]]:
    # Prepared tracks contain only 44.1 kHz, 128 kbps MPEG-1 Layer III frames.
    frames: list[bytes] = []
    while header := read(4):
        if (
            len(header) != 4
            or header[0] != 0xFF
            or header[1] & 0xFE != 0xFA
            or header[2] & 0xFC != 0x90
        ):
            raise ValueError("Unsupported prepared MP3 frame")
        frame_size = 144 * 128_000 // 44_100 + ((header[2] >> 1) & 1)
        body = read(frame_size - len(header))
        if len(body) != frame_size - len(header):
            raise ValueError("Truncated prepared MP3 frame")
        frames.append(header + body)
        if len(frames) == FRAMES_PER_PACKET:
            yield b"".join(frames), len(frames) * SECONDS_PER_FRAME
            frames.clear()
    if frames:
        yield b"".join(frames), len(frames) * SECONDS_PER_FRAME


class Broadcast:
    def __init__(self, database: Path, playlist: tuple[PlaylistEntry, ...]) -> None:
        self._database = database
        self._playlist = playlist
        self._on_air_entry_id: int | None = None
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._producer: asyncio.Task[None] | None = None

    @property
    def playlist(self) -> tuple[PlaylistEntry, ...]:
        return self._playlist

    @property
    def on_air_entry_id(self) -> int | None:
        return self._on_air_entry_id

    def update_playlist(self, playlist: tuple[PlaylistEntry, ...]) -> None:
        self._playlist = playlist

    def _position_of(self, entry_id: int) -> int:
        return next(position for position, entry in enumerate(self._playlist) if entry.id == entry_id)

    def _following_position(self, position: int) -> int:
        return (position + 1) % len(self._playlist)

    def status(self) -> dict[str, dict[str, str] | None]:
        if self._on_air_entry_id is None:
            return {"on_air": None, "up_next": None}

        position = self._position_of(self._on_air_entry_id)
        current = self._playlist[position].track
        following = self._playlist[self._following_position(position)].track
        return {
            "on_air": {"title": current.title, "artist": current.artist},
            "up_next": {"title": following.title, "artist": following.artist},
        }

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
                self._on_air_entry_id = None

    async def _produce(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            position = 0
            while self._playlist:
                entry = self._playlist[position]
                with open_audio(self._database, entry.track.id) as audio:
                    self._on_air_entry_id = entry.id
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
                position = self._following_position(self._position_of(entry.id))
        except Exception:
            LOGGER.exception("Broadcast stopped while reading prepared audio")
        finally:
            if self._producer is asyncio.current_task():
                for listener in tuple(self._listeners):
                    self._end(listener)
                self._producer = None
                self._on_air_entry_id = None

    def _end(self, listener: asyncio.Queue[bytes | None]) -> None:
        self._listeners.remove(listener)
        while not listener.empty():
            listener.get_nowait()
        listener.put_nowait(None)
