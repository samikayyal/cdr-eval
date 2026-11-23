import logging
import os
from asyncio import CancelledError, Event, Task, create_task, timeout
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from tempfile import mkstemp
from time import monotonic, time
from typing import BinaryIO

from radio import youtube
from radio.preparation import MAX_UPLOAD_BYTES
from radio.storage import AUDIO_CHUNK_SIZE

PREVIEW_SECONDS = 15 * 60
PREVIEW_LIMIT = 8
logger = logging.getLogger(__name__)


class StagingFull(Exception):
    pass


class StagingBusy(Exception):
    pass


@dataclass
class StagedRecording:
    video_id: str
    path: Path
    source: youtube.AudioSource | None = None
    size: int = 0
    fingerprint: str | None = None
    failure: youtube.YouTubeUnavailable | youtube.RecordingTooLarge | None = None
    last_access: float = field(default_factory=monotonic)
    changed: Event = field(default_factory=Event)

    def notify(self) -> None:
        self.changed.set()
        self.changed = Event()

    def check_failure(self) -> None:
        if self.failure:
            raise type(self.failure) from None

    async def wait_for_data(self, offset: int) -> None:
        while self.size <= offset and self.fingerprint is None:
            self.check_failure()
            await self.changed.wait()

    async def wait_for_start(self) -> youtube.AudioSource:
        await self.wait_for_data(0)
        self.check_failure()
        assert self.source is not None
        return self.source

    async def wait_for_completion(self) -> str:
        while self.fingerprint is None:
            self.check_failure()
            await self.changed.wait()
        return self.fingerprint

    async def stream(self, file: BinaryIO, start: int, length: int | None) -> AsyncIterator[bytes]:
        file.seek(start)
        offset = start
        end = start + length if length is not None else None
        while end is None or offset < end:
            await self.wait_for_data(offset)
            self.check_failure()
            available = min(self.size, end) if end is not None else self.size
            if offset >= available:
                break
            chunk = file.read(min(AUDIO_CHUNK_SIZE, available - offset))
            if not chunk:
                raise youtube.YouTubeUnavailable
            offset += len(chunk)
            yield chunk


class OnlinePreviews:
    def __init__(self, database: Path) -> None:
        self.directory = database.with_name(f"{database.name}.youtube")
        self.recordings: dict[str, StagedRecording] = {}
        self.download: Task[None] | None = None

    def clean(self, *, startup: bool = False) -> None:
        for staged in list(self.recordings.values()):
            if staged.fingerprint and monotonic() - staged.last_access >= PREVIEW_SECONDS:
                self.discard(staged)
        live_paths = {staged.path for staged in self.recordings.values()}
        for path in self.directory.iterdir() if self.directory.is_dir() else ():
            if path not in live_paths and path.is_file() and (
                startup or time() - path.stat().st_mtime >= PREVIEW_SECONDS
            ):
                self.remove_file(path)

    @staticmethod
    def remove_file(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            # An open browser response can keep a file on Windows until the next sweep.
            pass
        except OSError:
            logger.warning("Could not remove a temporary YouTube recording", exc_info=True)

    def discard(self, staged: StagedRecording) -> None:
        if self.recordings.get(staged.video_id) is staged:
            del self.recordings[staged.video_id]
        self.remove_file(staged.path)

    def find(self, video_id: str) -> StagedRecording | None:
        staged = self.recordings.get(video_id)
        if staged:
            staged.last_access = monotonic()
        return staged

    def start(self, video_id: str) -> StagedRecording:
        if self.download and not self.download.done():
            raise StagingBusy
        if len(self.recordings) >= PREVIEW_LIMIT:
            raise StagingFull

        self.directory.mkdir(mode=0o700, exist_ok=True)
        descriptor, filename = mkstemp(prefix=f"{video_id}-", suffix=".source", dir=self.directory)
        os.close(descriptor)
        staged = StagedRecording(video_id, Path(filename))
        self.recordings[video_id] = staged
        self.download = create_task(self._download(staged))
        return staged

    async def _download(self, staged: StagedRecording) -> None:
        digest = sha256()
        try:
            async with timeout(120), youtube.open_audio(staged.video_id) as (source, chunks):
                staged.source = source
                if source.size and source.size > MAX_UPLOAD_BYTES:
                    raise youtube.RecordingTooLarge
                with staged.path.open("wb") as file:
                    async for chunk in chunks:
                        size = staged.size + len(chunk)
                        if size > MAX_UPLOAD_BYTES:
                            raise youtube.RecordingTooLarge
                        if source.size is not None and size > source.size:
                            raise youtube.YouTubeUnavailable
                        file.write(chunk)
                        file.flush()
                        digest.update(chunk)
                        staged.size = size
                        staged.notify()
                if not staged.size or source.size is not None and staged.size != source.size:
                    raise youtube.YouTubeUnavailable
            staged.fingerprint = digest.hexdigest()
        except (youtube.YouTubeUnavailable, youtube.RecordingTooLarge) as error:
            staged.failure = error
        except CancelledError:
            staged.failure = youtube.YouTubeUnavailable()
            raise
        except TimeoutError:
            staged.failure = youtube.YouTubeUnavailable()
        except OSError:
            logger.warning("Could not stage the YouTube recording", exc_info=True)
            staged.failure = youtube.YouTubeUnavailable()
        finally:
            if staged.fingerprint is None:
                if staged.failure is None:
                    staged.failure = youtube.YouTubeUnavailable()
                self.discard(staged)
            staged.notify()

    async def close(self) -> None:
        if self.download:
            self.download.cancel()
            with suppress(CancelledError):
                await self.download
