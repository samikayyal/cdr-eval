import json
import logging
import re
import subprocess
import sys
from asyncio import create_task
from asyncio.subprocess import PIPE, create_subprocess_exec
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

from radio.preparation import METADATA_LIMIT

RESULT_LIMIT = 30
VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
logger = logging.getLogger(__name__)
RESULT_FIELDS = ("id", "title", "channel", "uploader", "duration")
AUDIO_TYPES = {"m4a": "audio/mp4", "webm": "audio/webm"}
AUDIO_FORMAT = (
    "bestaudio[protocol=https][ext=m4a][audio_channels<=2]/"
    "bestaudio[protocol=https][ext=webm][audio_channels<=2]/"
    "bestaudio[protocol=https][ext=m4a]/bestaudio[protocol=https][ext=webm]"
)


class YouTubeUnavailable(Exception):
    pass


class RecordingTooLarge(Exception):
    pass


@dataclass(frozen=True)
class Recording:
    id: str
    title: str
    channel: str | None
    duration: int | None

    @classmethod
    def from_result(cls, result: dict) -> "Recording":
        video_id = result["id"]
        duration = result.get("duration")
        return cls(
            video_id,
            (result.get("title") or video_id).strip()[:METADATA_LIMIT] or video_id,
            (result.get("channel") or result.get("uploader") or "").strip()[:METADATA_LIMIT] or None,
            int(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
        )


@dataclass(frozen=True)
class AudioSource:
    recording: Recording
    media_type: str
    size: int | None


def _extract(target: str, *, timeout: int) -> dict:
    try:
        process = subprocess.run(
            [sys.executable, "-m", "radio.youtube", target],
            capture_output=True, timeout=timeout,
        )
        process.check_returncode()
        result = json.loads(process.stdout)
        if not isinstance(result, dict):
            raise YouTubeUnavailable
        return result
    except subprocess.CalledProcessError as error:
        _log_failure(error.stderr)
        raise YouTubeUnavailable from error
    except (TimeoutError, subprocess.TimeoutExpired, OSError, ValueError) as error:
        raise YouTubeUnavailable from error


def search(query: str) -> list[Recording]:
    result = _extract(f"ytsearch{RESULT_LIMIT}:{query}", timeout=25)
    recordings = {}
    for entry in result.get("entries") or []:
        if isinstance(entry, dict) and re.fullmatch(VIDEO_ID_PATTERN, entry.get("id") or ""):
            recordings.setdefault(entry["id"], Recording.from_result(entry))
        if len(recordings) == RESULT_LIMIT:
            break
    return list(recordings.values())


def _log_failure(stderr: bytes) -> None:
    diagnostic = stderr.decode("utf-8", errors="replace").strip().splitlines()
    if diagnostic:
        logger.warning("yt-dlp: %s", diagnostic[-1][:500])


@asynccontextmanager
async def open_audio(video_id: str) -> AsyncIterator[tuple[AudioSource, AsyncIterator[bytes]]]:
    try:
        process = await create_subprocess_exec(
            sys.executable, "-m", "radio.youtube", f"https://www.youtube.com/watch?v={video_id}", "--audio",
            stdout=PIPE, stderr=PIPE,
        )
    except OSError as error:
        raise YouTubeUnavailable from error
    stdout, stderr = process.stdout, process.stderr
    assert stdout is not None and stderr is not None
    errors = create_task(stderr.read())

    async def chunks() -> AsyncIterator[bytes]:
        while chunk := await stdout.read(64 * 1024):
            yield chunk
        if await process.wait() != 0:
            raise YouTubeUnavailable

    try:
        # The child sends one metadata line, then yt-dlp writes the original audio to stdout.
        result = json.loads(await stdout.readline())
        if not isinstance(result, dict) or result.get("id") != video_id:
            raise YouTubeUnavailable
        media_type = AUDIO_TYPES.get(result.get("ext", ""))
        if media_type is None:
            raise YouTubeUnavailable
        filesize = result.get("filesize")
        size = filesize if isinstance(filesize, int) and filesize > 0 else None
        yield AudioSource(Recording.from_result(result), media_type, size), chunks()
    except (OSError, ValueError) as error:
        raise YouTubeUnavailable from error
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            # Drain the pipe after terminating so a paused pipe transport can close.
            await stdout.read()
        status = await process.wait()
        diagnostic = await errors
        if status:
            _log_failure(diagnostic)


def _run_yt_dlp(target: str, mode: str = "--search") -> None:
    from yt_dlp import YoutubeDL
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError

    options: Any = {
        "quiet": True,
        "noprogress": True,
        "cachedir": False,
        "socket_timeout": 10,
        "retries": 0,
        "extractor_retries": 0,
    }
    if mode == "--search":
        options.update({"extract_flat": "in_playlist", "playlistend": RESULT_LIMIT})
    else:
        options.update({
            # Prefer a source that already matches the station's stereo output.
            "format": AUDIO_FORMAT,
            "noplaylist": True,
            "fixup": "never",
            "outtmpl": "-",
            "logtostderr": True,
            "buffersize": 64 * 1024,
            "noresizebuffer": True,
        })

    class PreviewMetadata(PostProcessor):
        def run(self, information):
            fields = (*RESULT_FIELDS, "ext", "filesize")
            payload = {key: information.get(key) for key in fields}
            sys.stdout.buffer.write(json.dumps(payload).encode("ascii") + b"\n")
            sys.stdout.buffer.flush()
            return [], information

    try:
        with YoutubeDL(options) as client:
            if mode == "--audio":
                client.add_post_processor(PreviewMetadata(client), when="before_dl")
            result = client.extract_info(target, download=mode == "--audio")
        if not result:
            raise SystemExit(1)
        if mode == "--search":
            output = {"entries": [
                {key: entry.get(key) for key in RESULT_FIELDS}
                for entry in (result.get("entries") or []) if entry
            ]}
            print(json.dumps(output))
    except DownloadError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    _run_yt_dlp(*sys.argv[1:])
