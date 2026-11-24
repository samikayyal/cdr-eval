import json
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

MAX_UPLOAD_BYTES = 100_000_000
DISK_RESERVE_BYTES = 256 * 1024 * 1024
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}
METADATA_LIMIT = 300
INPUT_OPTIONS = ["-protocol_whitelist", "file", "-format_whitelist", "mp3,wav,mov"]


class InvalidAudio(ValueError):
    pass


def prepare_audio(
    source: Path, prepared: Path, filename: str, *, allow_webm: bool = False,
) -> tuple[str, str]:
    """Read recording tags and prepare independent MP3 frames for preview and live audio."""
    input_options = (
        ["-protocol_whitelist", "file", "-format_whitelist", "mp3,wav,mov,matroska,webm"]
        if allow_webm else INPUT_OPTIONS
    )
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error", *input_options,
            "-select_streams", "a:0", "-show_entries", "format_tags:stream=index:stream_tags",
            "-of", "json", str(source),
        ], check=True, capture_output=True, timeout=30)
        metadata = json.loads(probe.stdout)
        streams = metadata["streams"]
        if not streams:
            raise InvalidAudio

        tags = {key.casefold(): value.strip() for entries in (
            streams[0].get("tags", {}), metadata["format"].get("tags", {}),
        ) for key, value in entries.items()}
        title = tags.get("title") or Path(filename).stem.strip() or filename
        artist = tags.get("artist", "")

        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-filter_threads", "1",
            "-threads", "1", "-err_detect", "explode", *input_options, "-i", str(source),
            "-map", "0:a:0", "-map_metadata", "-1",
            "-af", "loudnorm=I=-16:LRA=11:TP=-1.5",
            "-ac", "2", "-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k",
            "-reservoir", "0", "-id3v2_version", "0", "-write_xing", "0",
            "-fs", str(MAX_UPLOAD_BYTES + 1), str(prepared),
        ], check=True, capture_output=True, timeout=300)
        if not 0 < prepared.stat().st_size <= MAX_UPLOAD_BYTES:
            raise InvalidAudio
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise InvalidAudio from error

    return title[:METADATA_LIMIT], artist[:METADATA_LIMIT]


FRAMES_PER_PACKET = 4
SECONDS_PER_FRAME = 1152 / 44_100


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
