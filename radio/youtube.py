import json
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from radio.preparation import MAX_UPLOAD_BYTES, METADATA_LIMIT

RESULT_LIMIT = 8
VIDEO_ID_PATTERN = r"^[A-Za-z0-9_-]{11}$"
TOO_LARGE_EXIT = 3
logger = logging.getLogger(__name__)
RESULT_FIELDS = ("id", "title", "channel", "uploader", "duration")
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


def _extract(*arguments: str, timeout: int) -> dict:
    try:
        process = subprocess.run(
            [sys.executable, "-m", "radio.youtube", *arguments],
            capture_output=True, timeout=timeout,
        )
        if process.returncode == TOO_LARGE_EXIT:
            raise RecordingTooLarge
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


def retrieve(video_id: str, destination: Path) -> Recording:
    result = _extract(f"https://www.youtube.com/watch?v={video_id}", str(destination), timeout=120)
    if result.get("id") != video_id or not destination.is_file():
        raise YouTubeUnavailable
    size = destination.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise RecordingTooLarge
    if not size:
        raise YouTubeUnavailable
    return Recording.from_result(result)


def _limit_download(progress: dict) -> None:
    if max(progress.get(key) or 0 for key in ("downloaded_bytes", "total_bytes")) > MAX_UPLOAD_BYTES:
        raise RecordingTooLarge


def _run_yt_dlp(target: str, destination: str | None = None) -> None:
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
    if destination is None:
        options.update({"extract_flat": "in_playlist", "playlistend": RESULT_LIMIT})
    else:
        options.update({
            # Prefer a source that already matches the station's stereo output.
            "format": AUDIO_FORMAT,
            "noplaylist": True,
            "fixup": "never",
            "outtmpl": destination.replace("%", "%%"),
            "progress_hooks": [_limit_download],
            "buffersize": 64 * 1024,
            "noresizebuffer": True,
        })
    try:
        with YoutubeDL(options) as client:
            result = client.extract_info(target, download=destination is not None)
        if destination is None:
            output = {"entries": [
                {key: entry.get(key) for key in RESULT_FIELDS}
                for entry in (result.get("entries") or []) if entry
            ]}
        else:
            output = {key: result.get(key) for key in RESULT_FIELDS}
        print(json.dumps(output))
    except RecordingTooLarge:
        raise SystemExit(TOO_LARGE_EXIT) from None
    except DownloadError:
        raise SystemExit(1) from None


if __name__ == "__main__":
    _run_yt_dlp(*sys.argv[1:])
