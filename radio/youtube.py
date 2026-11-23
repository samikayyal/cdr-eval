import json
import logging
import re
import subprocess
import sys
from asyncio import to_thread
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from radio.preparation import MAX_UPLOAD_BYTES, METADATA_LIMIT

RESULT_LIMIT = 30
VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
logger = logging.getLogger(__name__)
RESULT_FIELDS = ("id", "title", "channel", "uploader", "duration")
AUDIO_TYPES = {"m4a": "audio/mp4", "webm": "audio/webm"}
AUDIO_HEADERS = {"user-agent", "referer", "origin", "accept", "accept-language"}
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


def _extract(*arguments: str, timeout: int) -> dict:
    try:
        process = subprocess.run(
            [sys.executable, "-m", "radio.youtube", *arguments],
            capture_output=True, timeout=timeout,
        )
        process.check_returncode()
        result = json.loads(process.stdout)
        if not isinstance(result, dict):
            raise YouTubeUnavailable
        return result
    except subprocess.CalledProcessError as error:
        diagnostic = error.stderr.decode("utf-8", errors="replace").strip().splitlines()
        if diagnostic:
            logger.warning("yt-dlp: %s", diagnostic[-1][:500])
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


async def _require_youtube_cdn(request: httpx.Request) -> None:
    url = request.url
    if url.scheme != "https" or not url.host.endswith(".googlevideo.com") or url.port not in (None, 443):
        raise YouTubeUnavailable


@asynccontextmanager
async def open_audio(video_id: str) -> AsyncIterator[tuple[AudioSource, AsyncIterator[bytes]]]:
    result = await to_thread(_extract, f"https://www.youtube.com/watch?v={video_id}", "--audio", timeout=25)
    media_type = AUDIO_TYPES.get(result.get("ext", ""))
    if result.get("id") != video_id or not media_type or not result.get("url"):
        raise YouTubeUnavailable
    filesize = result.get("filesize")
    size_hint = filesize if isinstance(filesize, int) and filesize > 0 else None
    if size_hint is not None and size_hint > MAX_UPLOAD_BYTES:
        raise RecordingTooLarge
    headers = {
        key: value for key, value in (result.get("http_headers") or {}).items()
        if key.lower() in AUDIO_HEADERS
    }
    headers["Accept-Encoding"] = "identity"
    expected_range = None
    if size_hint is not None:
        # YouTube serves byte ranges at download speed. A plain GET can pace audio for playback.
        headers["Range"] = f"bytes=0-{size_hint - 1}"
        expected_range = f"bytes 0-{size_hint - 1}/{size_hint}"
    try:
        async with httpx.AsyncClient(
            timeout=10, follow_redirects=True, max_redirects=3, event_hooks={"request": [_require_youtube_cdn]},
        ) as client:
            async with client.stream("GET", result["url"], headers=headers) as response:
                if response.status_code == 206:
                    if expected_range is None or response.headers.get("content-range") != expected_range:
                        raise YouTubeUnavailable
                elif response.status_code != 200:
                    raise YouTubeUnavailable
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise YouTubeUnavailable
                content_type = response.headers.get("content-type", "")
                if not content_type.startswith("audio/") and content_type != "application/octet-stream":
                    raise YouTubeUnavailable
                length = response.headers.get("content-length")
                size = int(length) if length is not None else size_hint
                if size is not None and not 0 < size <= MAX_UPLOAD_BYTES:
                    if size > MAX_UPLOAD_BYTES:
                        raise RecordingTooLarge
                    raise YouTubeUnavailable
                if size_hint is not None and size != size_hint:
                    raise YouTubeUnavailable
                yield AudioSource(Recording.from_result(result), media_type, size), response.aiter_raw()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as error:
        raise YouTubeUnavailable from error


def _run_yt_dlp(target: str, mode: str = "--search") -> None:
    from yt_dlp import YoutubeDL
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
        })
    try:
        with YoutubeDL(options) as client:
            result = client.extract_info(target, download=False)
        if not result:
            raise SystemExit(1)
        if mode == "--search":
            output = {"entries": [
                {key: entry.get(key) for key in RESULT_FIELDS}
                for entry in (result.get("entries") or []) if entry
            ]}
        else:
            output = {key: result.get(key) for key in (*RESULT_FIELDS, "ext", "url", "filesize", "http_headers")}
        print(json.dumps(output))
    except DownloadError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    _run_yt_dlp(*sys.argv[1:])
