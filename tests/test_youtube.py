import shutil
from pathlib import Path

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
def test_import_saves_the_private_preview_even_after_youtube_becomes_unavailable(station, monkeypatch):
    video_id = "nvr4lPoKqzI"

    def retrieve(selected, destination):
        assert selected == video_id
        shutil.copyfile(DEMO_RECORDING, destination)
        return youtube.Recording(selected, "Selected recording", "Test channel", 12)

    def unavailable(*args):
        raise youtube.YouTubeUnavailable

    monkeypatch.setattr(youtube, "retrieve", retrieve)
    root = f"/api/manager/youtube/{video_id}"
    with httpx.Client(base_url=station) as client:
        assert client.get(f"{root}/audio").status_code == 401
        client.post("/api/manager/session", json={"username": "admin", "password": "admin"})
        assert client.get(f"{root}/audio").status_code == 404
        assert client.post(f"{root}/preview").status_code == 204
        preview = client.get(f"{root}/audio")
        assert preview.status_code == 200
        assert preview.headers["content-type"] == "audio/mpeg"
        assert preview.headers["cache-control"] == "private, no-store"
        assert client.get(f"{root}/audio", headers={"Range": "bytes=10-19"}).content == preview.content[10:20]
        assert client.get("/api/broadcast").json() == {"on_air": None, "up_next": None}
        assert client.get("/api/manager/library", params={"q": "Selected recording"}).json() == []

        monkeypatch.setattr(youtube, "retrieve", unavailable)
        imported = client.post(f"{root}/import")
        assert imported.status_code == 201
        track = imported.json()
        assert (track["title"], track["artist"]) == ("Selected recording", "Test channel")
        duplicate = client.post(f"{root}/import")
        assert duplicate.status_code == 200
        assert duplicate.json() == track
        assert client.get(f"/api/manager/library/{track['id']}/preview").content == preview.content
        assert client.post("/api/manager/playlist", json={"track_id": track["id"]}).json()[-1] == track


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare recordings",
)
def test_failed_youtube_retrieval_leaves_search_and_file_upload_available(station, monkeypatch):
    recording = youtube.Recording("nvr4lPoKqzI", "فيروز - كيفك إنت", "UMMENAVEVO", 214)
    monkeypatch.setattr(youtube, "search", lambda query: [recording])

    def retrieve(video_id, destination):
        raise youtube.YouTubeUnavailable

    monkeypatch.setattr(youtube, "retrieve", retrieve)
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
