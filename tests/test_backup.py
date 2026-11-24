import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from conftest import serve_station
from radio.storage import save_restart_position
from test_management import sign_in


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="FFmpeg and ffprobe are required to prepare uploads",
)
def test_restore_keeps_recordings_order_and_saved_slot(tmp_path: Path) -> None:
    source = tmp_path / "live.sqlite3"
    snapshot = tmp_path / "snapshot.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    order_url = "/api/manager/playlist/order"
    playlist_url = "/api/manager/playlist"
    library_url = "/api/manager/library"
    status_url = "/api/broadcast"
    demo = Path(__file__).resolve().parents[1] / "radio/demo/harbor-lights.mp3"

    with serve_station(source) as station, httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        harbor, evening = client.get(order_url).json()["entries"]
        uploaded = client.post(
            library_url, params={"filename": "recovery.mp3"},
            headers={"Content-Type": "application/octet-stream"}, content=demo.read_bytes(),
        )
        assert uploaded.status_code == 201
        track_url = f"{library_url}/{uploaded.json()['id']}"
        metadata = {"title": "Recovery recording", "artist": "ليلى"}
        track = client.patch(track_url, json=metadata).json()
        preview = client.get(f"{track_url}/preview")
        assert preview.status_code == 200
        assert client.post(playlist_url, json={"track_id": track["id"]}).status_code == 201
        recovery = client.get(order_url).json()["entries"][-1]

        assert client.patch(f"{playlist_url}/{harbor['id']}", json={
            "after_entry_id": recovery["id"], "before_entry_id": None,
        }).status_code == 204

    save_restart_position(source, 1)
    with serve_station(source) as station, httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        assert client.get(status_url).json()["starts_with"] == metadata

        subprocess.run([sys.executable, "-m", "radio.backup", source, snapshot], check=True)
        assert client.patch(track_url, json={**metadata, "title": "Later edit"}).status_code == 200

    subprocess.run([sys.executable, "-m", "radio.backup", snapshot, restored], check=True)
    with serve_station(restored) as station, httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        assert client.get(library_url, params={"q": "Recovery"}).json() == [track]
        assert client.get(f"{track_url}/preview").content == preview.content
        assert client.get(order_url).json()["entries"] == [evening, recovery, harbor]
        assert client.get(status_url).json()["starts_with"] == metadata
        with client.stream("GET", "/live.mp3") as stream:
            packets = stream.iter_raw()
            first_packet = next(packets)
            assert first_packet and preview.content.startswith(first_packet)
            assert client.get(status_url).json()["on_air"] == metadata
