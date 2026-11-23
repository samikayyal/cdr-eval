import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Iterator
from itertools import cycle
from pathlib import Path

from radio.storage import Track, open_audio

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
    def __init__(self, database: Path, playlist: tuple[Track, ...]) -> None:
        self._database = database
        self._playlist = playlist
        self._on_air: int | None = None
        self._listeners: set[asyncio.Queue[bytes | None]] = set()
        self._producer: asyncio.Task[None] | None = None

    def status(self) -> dict[str, dict[str, str] | None]:
        if self._on_air is None:
            return {"on_air": None, "up_next": None}

        current = self._playlist[self._on_air]
        following = self._playlist[(self._on_air + 1) % len(self._playlist)]
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
                self._on_air = None

    async def _produce(self) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        try:
            for position in cycle(range(len(self._playlist))):
                with open_audio(self._database, self._playlist[position].id) as audio:
                    self._on_air = position
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
        except Exception:
            LOGGER.exception("Broadcast stopped while reading prepared audio")
        finally:
            if self._producer is asyncio.current_task():
                for listener in tuple(self._listeners):
                    self._end(listener)
                self._producer = None
                self._on_air = None

    def _end(self, listener: asyncio.Queue[bytes | None]) -> None:
        self._listeners.remove(listener)
        while not listener.empty():
            listener.get_nowait()
        listener.put_nowait(None)
