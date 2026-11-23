import shutil
from asyncio import to_thread
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Event

import httpx
import pytest

from radio import youtube

DEMO_RECORDING = Path(__file__).resolve().parents[1] / "radio" / "demo" / "harbor-lights.mp3"


def test_manager_searches_youtube_without_changing_local_results(station, monkeypatch):
    monkeypatch.setattr(youtube, "search", lambda query: [
        youtube.Recording("nvr4lPoKqzI", f"{query} - كيفك إنت", "UMMENAVEVO", 214),
    ])
    with httpx.Client(base_url=station) as client:
        assert client.get("/api/manager/youtube", params={"q": "فيروز"}).status_code == 401
        client.post("/api/manager/session", json={"username": "admin", "password": "admin"})
        assert client.get("/api/manager/library", params={"q": "فيروز"}).json() == []
        assert client.get("/api/manager/youtube", params={"q": "فيروز"}).json() == [{
            "id": "nvr4lPoKqzI", "title": "فيروز - كيفك إنت",
            "channel": "UMMENAVEVO", "duration": 214,
        }]


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare recordings",
)
def test_preview_plays_before_download_finishes_and_import_reuses_the_recording(station, monkeypatch):
    video_id = "nvr4lPoKqzI"
    raw = DEMO_RECORDING.read_bytes()
    first, rest = raw[:16_384], raw[16_384:]
    finish_download = Event()
    served_complete = Event()

    @asynccontextmanager
    async def open_audio(selected):
        assert selected == video_id

        async def chunks():
            yield first
            assert await to_thread(finish_download.wait, 8), "test did not release the download"
            yield rest
            served_complete.set()

        recording = youtube.Recording(selected, "Selected recording", "Test channel", 12)
        yield youtube.AudioSource(recording, "audio/mpeg", len(raw)), chunks()

    @asynccontextmanager
    async def unavailable(_video_id):
        raise youtube.YouTubeUnavailable
        yield

    monkeypatch.setattr(youtube, "open_audio", open_audio)
    root = f"/api/manager/youtube/{video_id}"
    with httpx.Client(base_url=station) as client, ThreadPoolExecutor() as executor:
        assert client.get(f"{root}/audio").status_code == 401
        client.post("/api/manager/session", json={"username": "admin", "password": "admin"})
        assert client.get(f"{root}/audio").status_code == 404
        try:
            assert client.post(f"{root}/preview").status_code == 204
            with client.stream("GET", f"{root}/audio", headers={"Range": "bytes=0-"}) as preview:
                assert preview.status_code == 200
                assert "content-length" not in preview.headers
                assert preview.headers["content-type"] == "audio/mpeg"
                assert preview.headers["cache-control"] == "private, no-store"
                audio = preview.iter_bytes(chunk_size=len(first))
                assert next(audio) == first
                assert not served_complete.is_set()
                small_range = client.get(f"{root}/audio", headers={"Range": "bytes=10-19"})
                assert small_range.status_code == 206
                assert small_range.content == first[10:20]
                assert client.get("/api/broadcast").json() == {"on_air": None, "up_next": None}
                assert client.get("/api/manager/library", params={"q": "Selected recording"}).json() == []

                monkeypatch.setattr(youtube, "open_audio", unavailable)
                importing = executor.submit(client.post, f"{root}/import", timeout=15)
                finish_download.set()
                assert b"".join(audio) == rest
                imported = importing.result()
        finally:
            finish_download.set()
        assert imported.status_code == 201
        track = imported.json()
        assert (track["title"], track["artist"]) == ("Selected recording", "Test channel")
        duplicate = client.post(f"{root}/import")
        assert duplicate.status_code == 200
        assert duplicate.json() == track
        ready = client.get(f"/api/manager/library/{track['id']}/preview")
        assert ready.headers["content-type"] == "audio/mpeg"
        assert ready.content != raw
        assert client.get(f"{root}/audio").content == ready.content
        assert client.post("/api/manager/playlist", json={"track_id": track["id"]}).json()[-1] == track


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare recordings",
)
def test_failed_youtube_retrieval_leaves_search_and_file_upload_available(station, monkeypatch):
    recording = youtube.Recording("nvr4lPoKqzI", "فيروز - كيفك إنت", "UMMENAVEVO", 214)
    monkeypatch.setattr(youtube, "search", lambda query: [recording])

    @asynccontextmanager
    async def open_audio(_video_id):
        raise youtube.YouTubeUnavailable
        yield

    monkeypatch.setattr(youtube, "open_audio", open_audio)
    with httpx.Client(base_url=station) as client:
        client.post("/api/manager/session", json={"username": "admin", "password": "admin"})
        results = client.get("/api/manager/youtube", params={"q": "فيروز"}).json()
        assert client.post(f"/api/manager/youtube/{recording.id}/preview").status_code == 502
        assert client.get("/api/manager/youtube", params={"q": "فيروز"}).json() == results
        uploaded = client.post(
            "/api/manager/library", params={"filename": "fallback.mp3"},
            headers={"Content-Type": "application/octet-stream"}, content=DEMO_RECORDING.read_bytes(),
        )
        assert uploaded.status_code == 201


def test_interrupted_preview_can_retry_the_same_recording(station, monkeypatch):
    recording = youtube.Recording("nvr4lPoKqzI", "Interrupted recording", None, 12)
    raw = DEMO_RECORDING.read_bytes()
    cut = Event()

    @asynccontextmanager
    async def open_audio(_video_id):
        async def chunks():
            if cut.is_set():
                yield raw
            else:
                yield raw[:8192]
                assert await to_thread(cut.wait, 8), "test did not interrupt the download"

        yield youtube.AudioSource(recording, "audio/mpeg", len(raw)), chunks()

    monkeypatch.setattr(youtube, "open_audio", open_audio)
    root = f"/api/manager/youtube/{recording.id}"
    with httpx.Client(base_url=station) as client:
        client.post("/api/manager/session", json={"username": "admin", "password": "admin"})
        try:
            assert client.post(f"{root}/preview").status_code == 204
            with client.stream("GET", f"{root}/audio") as preview:
                stream = preview.iter_bytes(chunk_size=8192)
                assert next(stream) == raw[:8192]
                cut.set()
                with pytest.raises(httpx.RemoteProtocolError):
                    next(stream)
        finally:
            cut.set()
        assert client.get("/api/manager/library", params={"q": recording.title}).json() == []
        assert client.post(f"{root}/preview").status_code == 204
        assert client.get(f"{root}/audio").content == raw
