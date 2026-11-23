import logging
import os
from dataclasses import dataclass
from hashlib import file_digest
from pathlib import Path
from time import time

from radio import youtube
from radio.preparation import prepare_audio

PREVIEW_SECONDS = 15 * 60
PREVIEW_LIMIT = 8
logger = logging.getLogger(__name__)


class StagingFull(Exception):
    pass


@dataclass(frozen=True)
class StagedRecording:
    path: Path
    recording: youtube.Recording
    fingerprint: str


class OnlinePreviews:
    def __init__(self, database: Path) -> None:
        self.directory = database.with_name(f"{database.name}.youtube")
        self.recordings: dict[str, StagedRecording] = {}

    def clean(self, *, startup: bool = False) -> None:
        for path in self.directory.glob("*"):
            if not path.is_file():
                continue
            if startup or path.suffix == ".mp3" and time() - path.stat().st_mtime >= PREVIEW_SECONDS:
                self.discard(path)

    def discard(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove a temporary YouTube recording", exc_info=True)
        else:
            self.recordings.pop(path.stem, None)

    def find(self, video_id: str) -> StagedRecording | None:
        staged = self.recordings.get(video_id)
        if staged:
            try:
                os.utime(staged.path)
            except FileNotFoundError:
                self.recordings.pop(video_id, None)
                return None
        return staged

    def stage(self, video_id: str) -> StagedRecording:
        if len(self.recordings) >= PREVIEW_LIMIT:
            raise StagingFull

        self.directory.mkdir(mode=0o700, exist_ok=True)
        source = self.directory / f"{video_id}.source"
        partial = source.with_suffix(".source.part")
        preparing = source.with_suffix(".preparing.mp3")
        ready = source.with_suffix(".mp3")
        try:
            recording = youtube.retrieve(video_id, source)
            with source.open("rb") as file:
                fingerprint = file_digest(file, "sha256").hexdigest()
            prepare_audio(source, preparing, f"{video_id}.m4a", allow_webm=True)
            preparing.replace(ready)
        finally:
            for path in (source, partial, preparing):
                path.unlink(missing_ok=True)

        staged = StagedRecording(ready, recording, fingerprint)
        self.recordings[video_id] = staged
        return staged
