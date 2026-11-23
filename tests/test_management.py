import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

IDLE = {"on_air": None, "up_next": None}


def sign_in(client: httpx.Client) -> None:
    assert client.post("/api/manager/session", json={
        "username": "admin", "password": "admin",
    }).status_code == 204


def test_demo_login_grants_and_revokes_management_access(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        assert client.get("/api/manager/playlist").status_code == 401
        assert client.post("/api/manager/session", json={
            "username": "admin", "password": "wrong",
        }).status_code == 401

        sign_in(client)
        assert [track["title"] for track in client.get("/api/manager/playlist").json()] == [
            "Harbor Lights", "Evening Tide",
        ]

        assert client.delete("/api/manager/session").status_code == 204
        assert client.get("/api/manager/playlist").status_code == 401


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare uploads",
)
def test_prepare_search_and_preview_a_recording_while_idle(
    station: str, tmp_path: Path,
) -> None:
    recording = tmp_path / "input.wav"
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-metadata", "title=Morning Coffee", "-metadata", "artist=ليلى",
        str(recording),
    ], check=True)
    with httpx.Client(base_url=station, trust_env=False) as client:
        upload = {
            "params": {"filename": "uploaded.wav"},
            "content": recording.read_bytes(),
            "headers": {"Content-Type": "application/octet-stream"},
        }
        assert client.post("/api/manager/library", **upload).status_code == 401
        sign_in(client)
        prepared = client.post("/api/manager/library", **upload)
        assert prepared.status_code == 201, prepared.text
        track = prepared.json()
        track_id = track["id"]
        assert (track["title"], track["artist"]) == ("Morning Coffee", "ليلى")
        assert client.post("/api/manager/library", **upload).json() == track

        corrected = client.patch(f"/api/manager/library/{track_id}", json={
            "title": "First Coffee", "artist": "ليلى",
        }).json()
        assert client.get("/api/manager/library", params={"q": "coffee"}).json() == [corrected]
        assert client.get("/api/manager/library", params={"q": "ليلى"}).json() == [corrected]

        preview = client.get(f"/api/manager/library/{track_id}/preview", headers={
            "Range": "bytes=0-1023",
        })
        assert preview.status_code == 206
        assert preview.headers["content-type"] == "audio/mpeg"
        assert preview.content[:3] in (b"\xff\xfb\x90", b"\xff\xfb\x92")
        assert len(preview.content) == 1024
        assert client.get("/api/broadcast").json() == IDLE
        assert len(client.get("/api/manager/playlist").json()) == 2


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare uploads",
)
def test_append_during_broadcast_reaches_the_existing_listener(station: str) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo/harbor-lights.mp3"
    with httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        headers = {"Content-Type": "application/octet-stream"}
        assert client.post(
            "/api/manager/library", params={"filename": "broken.wav"},
            content=b"Not audio", headers=headers,
        ).status_code == 422
        assert len(client.get("/api/manager/library").json()) == 2
        prepared = client.post(
            "/api/manager/library", params={"filename": "Night Shift.mp3"},
            content=demo.read_bytes(), headers=headers,
        )
        assert prepared.status_code == 201, prepared.text
        track = prepared.json()
        assert track["title"] == "Night Shift"
        audio = client.get(f"/api/manager/library/{track['id']}/preview").content

        with client.stream("GET", "/live.mp3") as response:
            started = time.monotonic()
            packets = response.iter_raw()
            assert demo.read_bytes().startswith(next(packets))
            with httpx.Client(base_url=station, trust_env=False) as guest:
                assert guest.post("/api/manager/playlist", json={"track_id": track["id"]}).status_code == 401
                assert guest.get(f"/api/manager/library/{track['id']}/preview").status_code == 401
            assert client.post("/api/manager/playlist", json={"track_id": track["id"]}).status_code == 201
            assert client.get("/api/broadcast").json()["on_air"]["title"] == "Harbor Lights"
            assert client.get("/api/manager/playlist").json()[-1] == track
            assert track in client.get("/api/credits").json()

            for packet in packets:
                assert time.monotonic() - started < 30
                if audio.startswith(packet):
                    assert client.get("/api/broadcast").json() == {
                        "on_air": {"title": "Night Shift", "artist": ""},
                        "up_next": {"title": "Harbor Lights", "artist": "Station demo"},
                    }
                    break
            else:
                pytest.fail("The existing listener did not hear the appended track")
