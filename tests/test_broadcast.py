import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, closing
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest

from radio.storage import save_prepared_track


def wait_until_idle(client: httpx.Client) -> None:
    deadline = time.monotonic() + 3
    while client.get("/api/broadcast").json()["on_air"] is not None:
        assert time.monotonic() < deadline, "Broadcast stayed on air after the last Stop"
        time.sleep(0.03)


def test_listen_page_leaves_the_station_idle(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'lang="en"' in page.text
        assert '<audio id="player" preload="none"></audio>' in page.text
        assert ">Play</button>" in page.text
        assert client.get("/api/broadcast").json() == {
            "on_air": None,
            "up_next": None,
            "starts_with": {"title": "Harbor Lights", "artist": "Station demo"},
            "unavailable": False,
            "failed_entry_ids": [],
        }


def test_idle_start_uses_the_saved_slot_after_playlist_edits_and_restart(
    run_station: Callable[[], AbstractContextManager[str]],
) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (demo / "harbor-lights.mp3").read_bytes()
    evening_tide = (demo / "evening-tide.mp3").read_bytes()
    harbor = {"title": "Harbor Lights", "artist": "Station demo"}
    evening = {"title": "Evening Tide", "artist": "Station demo"}
    playlist = "/api/manager/playlist"
    with run_station() as station, httpx.Client(base_url=station, trust_env=False) as client:
        assert client.post("/api/manager/session", json={
            "username": "admin", "password": "admin",
        }).status_code == 204
        first, second = client.get(f"{playlist}/order").json()["entries"]
        assert client.get("/api/broadcast").json()["starts_with"] == harbor

        with client.stream("GET", "/live.mp3") as response:
            packets = response.iter_raw()
            assert harbor_lights.startswith(next(packets))
            assert client.patch(f"{playlist}/{first['id']}", json={
                "after_entry_id": second["id"], "before_entry_id": None,
            }).status_code == 204
            assert client.get("/api/broadcast").headers["X-On-Air-Entry-Id"] == str(first["id"])
            # A new worker sees the saved slot even before the original listener disconnects.
            with run_station() as fresh_station, httpx.Client(base_url=fresh_station, trust_env=False) as fresh:
                assert fresh.get("/api/broadcast").json() == {
                    "on_air": None, "up_next": None, "starts_with": harbor,
                    "unavailable": False,
                    "failed_entry_ids": [],
                }

        wait_until_idle(client)
        with client.stream("GET", "/live.mp3") as resumed:
            assert harbor_lights.startswith(next(resumed.iter_raw()))

        wait_until_idle(client)
        assert client.delete(f"{playlist}/{first['id']}").status_code == 200
        assert client.get("/api/broadcast").json()["starts_with"] == evening
        assert client.post(playlist, json={"track_id": first["track"]["id"]}).status_code == 201
        assert client.get("/api/broadcast").json()["starts_with"] == harbor

        _, appended = client.get(f"{playlist}/order").json()["entries"]
        assert client.patch(f"{playlist}/{appended['id']}", json={
            "after_entry_id": None, "before_entry_id": second["id"],
        }).status_code == 204
        assert client.get("/api/broadcast").json()["starts_with"] == evening

    with run_station() as station, httpx.Client(base_url=station, trust_env=False) as restarted:
        assert restarted.get("/api/broadcast").json()["starts_with"] == evening
        with restarted.stream("GET", "/live.mp3") as response:
            assert evening_tide.startswith(next(response.iter_raw()))

        wait_until_idle(restarted)
        assert restarted.post("/api/manager/session", json={
            "username": "admin", "password": "admin",
        }).status_code == 204
        assert restarted.delete(f"{playlist}/{second['id']}").status_code == 200
        assert restarted.get("/api/broadcast").json()["starts_with"] == harbor
        with restarted.stream("GET", "/live.mp3") as response:
            assert harbor_lights.startswith(next(response.iter_raw()))

def test_concurrent_first_listeners_share_one_program_and_stop_independently(
    station: str,
) -> None:
    with httpx.Client(base_url=station, trust_env=False) as observer:
        with ThreadPoolExecutor(2) as pool:
            first_play = Barrier(2)
            first_audio = Barrier(2)
            finished = Event()
            survivor_kept_playing = Event()

            def listen(stop_first: bool) -> bytes:
                try:
                    with httpx.Client(base_url=station, trust_env=False) as client:
                        first_play.wait(timeout=5)
                        with client.stream("GET", "/live.mp3") as response:
                            assert response.status_code == 200
                            assert response.headers["content-type"] == "audio/mpeg"
                            packets = response.iter_raw()
                            beginning = next(packets)
                            first_audio.wait(timeout=5)
                            if not stop_first:
                                assert finished.wait(timeout=5)
                                assert next(packets)
                                survivor_kept_playing.set()
                                assert ready_to_stop.wait(timeout=5)
                            return beginning
                finally:
                    if stop_first:
                        finished.set()

            ready_to_stop = Event()
            first = pool.submit(listen, True)
            second = pool.submit(listen, False)
            try:
                first_packet = first.result(timeout=5)
                assert survivor_kept_playing.wait(timeout=5)
                assert observer.get("/api/broadcast").json() == {
                    "on_air": {"title": "Harbor Lights", "artist": "Station demo"},
                    "up_next": {"title": "Evening Tide", "artist": "Station demo"},
                    "starts_with": None,
                    "unavailable": False,
                    "failed_entry_ids": [],
                }
            finally:
                ready_to_stop.set()
            second_packet = second.result(timeout=5)

        first_track = (Path(__file__).resolve().parents[1] / "radio/demo/harbor-lights.mp3").read_bytes()
        assert 0 <= first_track.find(first_packet) < 4096
        assert 0 <= first_track.find(second_packet) < 4096

        wait_until_idle(observer)

def test_late_listener_shares_the_transition_and_wraparound(station: str) -> None:
    audio = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (audio / "harbor-lights.mp3").read_bytes()
    evening_tide = (audio / "evening-tide.mp3").read_bytes()
    with (
        httpx.Client(base_url=station, trust_env=False) as first,
        httpx.Client(base_url=station, trust_env=False) as late,
        first.stream("GET", "/live.mp3") as first_response,
    ):
        first_packets = first_response.iter_raw()
        started = time.monotonic()
        assert harbor_lights.startswith(next(first_packets))
        for _ in range(8):
            assert next(first_packets)

        with late.stream("GET", "/live.mp3") as late_response:
            late_packets = late_response.iter_raw()
            joined_at = next(late_packets)
            assert 4096 < harbor_lights.find(joined_at) < len(harbor_lights) - 4096
            while next(first_packets) != joined_at:
                assert time.monotonic() - started < 5, "Couldn't locate the late listener's passage"

            for packet in first_packets:
                assert packet == next(late_packets)
                if evening_tide.startswith(packet):
                    assert 10 <= time.monotonic() - started <= 16
                    break
            else:
                pytest.fail("Stream ended before the transition")

            assert first.get("/api/broadcast").json() == {
                "on_air": {"title": "Evening Tide", "artist": "Station demo"},
                "up_next": {"title": "Harbor Lights", "artist": "Station demo"},
                "starts_with": None,
                "unavailable": False,
                "failed_entry_ids": [],
            }

            for packet in first_packets:
                assert packet == next(late_packets)
                if harbor_lights.startswith(packet):
                    assert 20 <= time.monotonic() - started <= 30
                    break
            else:
                pytest.fail("Stream ended before wraparound")


def test_manager_can_skip_the_current_playback_for_all_listeners(station: str) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (demo / "harbor-lights.mp3").read_bytes()
    evening_tide = (demo / "evening-tide.mp3").read_bytes()
    with (
        httpx.Client(base_url=station, trust_env=False) as manager,
        httpx.Client(base_url=station, trust_env=False) as first,
        httpx.Client(base_url=station, trust_env=False) as second,
    ):
        assert manager.post("/api/manager/session", json={
            "username": "admin", "password": "admin",
        }).status_code == 204
        order_url = "/api/manager/playlist/order"
        original_order = manager.get(order_url).json()["entries"]
        idle = manager.get("/api/broadcast")
        assert "X-Playback-Id" not in idle.headers
        assert manager.post("/api/manager/broadcast/skip/1").status_code == 409
        assert manager.get("/api/broadcast").json() == idle.json()

        with first.stream("GET", "/live.mp3") as first_response, second.stream("GET", "/live.mp3") as second_response:
            first_packets = first_response.iter_raw()
            second_packets = second_response.iter_raw()
            assert harbor_lights.startswith(next(first_packets))
            assert next(second_packets) in harbor_lights
            active = manager.get("/api/broadcast")
            skip_url = f"/api/manager/broadcast/skip/{active.headers['X-Playback-Id']}"
            assert first.post(skip_url).status_code == 401
            assert manager.post(skip_url).status_code == 204

            for packets in (first_packets, second_packets):
                deadline = time.monotonic() + 2
                for packet in packets:
                    assert time.monotonic() < deadline, "Skip did not advance the shared stream"
                    if evening_tide.startswith(packet):
                        break
                else:
                    pytest.fail("Skip disconnected a listener")

            assert manager.post(skip_url).status_code == 409
            assert manager.get("/api/broadcast").json()["on_air"]["title"] == "Evening Tide"
            assert manager.get(order_url).json()["entries"] == original_order


def test_listeners_skip_a_broken_recording_together(station: str, tmp_path: Path) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (demo / "harbor-lights.mp3").read_bytes()
    with (
        httpx.Client(base_url=station, trust_env=False) as manager,
        httpx.Client(base_url=station, trust_env=False) as first,
        httpx.Client(base_url=station, trust_env=False) as second,
    ):
        assert manager.post("/api/manager/session", json={
            "username": "admin", "password": "admin",
        }).status_code == 204
        order_url = "/api/manager/playlist/order"
        harbor, evening = manager.get(order_url).json()["entries"]
        with closing(sqlite3.connect(tmp_path / "station.sqlite3")) as database, database:
            database.execute("UPDATE library_tracks SET mp3 = ? WHERE id = ?", (b"broken", evening["track"]["id"]))

        with first.stream("GET", "/live.mp3") as first_response, second.stream("GET", "/live.mp3") as second_response:
            packets = first_response.iter_raw()
            late_packets = second_response.iter_raw()
            assert harbor_lights.startswith(next(packets))
            joined_at = next(late_packets)
            deadline = time.monotonic() + 16
            while next(packets) != joined_at:
                assert time.monotonic() < deadline
            for packet in packets:
                assert time.monotonic() < deadline
                assert packet == next(late_packets)
                if harbor_lights.startswith(packet):
                    break
            else:
                pytest.fail("The shared stream ended at the broken recording")

            status = manager.get("/api/broadcast")
            assert status.headers["X-On-Air-Entry-Id"] == str(harbor["id"])
            assert status.json()["failed_entry_ids"] == [evening["id"]]
            assert status.json()["up_next"]["title"] == "Harbor Lights"
            assert manager.get(order_url).json()["entries"] == [harbor, evening]


def test_unplayable_rotation_waits_for_a_ready_playlist_entry(station: str, tmp_path: Path) -> None:
    database_path = tmp_path / "station.sqlite3"
    demo = Path(__file__).resolve().parents[1] / "radio/demo/evening-tide.mp3"
    ready = save_prepared_track(database_path, demo, "recovery-demo", "Recovery", "Station demo")
    with httpx.Client(base_url=station, trust_env=False) as client:
        with closing(sqlite3.connect(database_path)) as database, database:
            database.execute("UPDATE library_tracks SET mp3 = ? WHERE id != ?", (b"broken", ready.id))

        assert client.post("/api/manager/session", json={
            "username": "admin", "password": "admin",
        }).status_code == 204
        playlist = "/api/manager/playlist"
        harbor, evening = client.get(f"{playlist}/order").json()["entries"]

        with client.stream("GET", "/live.mp3") as response:
            assert list(response.iter_raw()) == []

        unavailable = client.get("/api/broadcast").json()
        assert unavailable["unavailable"] is True
        assert unavailable["on_air"] is unavailable["starts_with"] is None
        assert unavailable["failed_entry_ids"] == [harbor["id"], evening["id"]]
        assert client.get("/live.mp3").status_code == 503

        assert client.post(playlist, json={"track_id": harbor["track"]["id"]}).status_code == 201
        assert client.get("/api/broadcast").json()["unavailable"] is True
        assert client.get("/live.mp3").status_code == 503

        assert client.post(playlist, json={"track_id": ready.id}).status_code == 201
        status = client.get("/api/broadcast").json()
        assert status["unavailable"] is False
        assert status["on_air"] is None
        assert status["starts_with"] == {"title": "Recovery", "artist": "Station demo"}
        original, second, repeat, recovered = client.get(f"{playlist}/order").json()["entries"]
        assert status["failed_entry_ids"] == [original["id"], second["id"], repeat["id"]]
        with client.stream("GET", "/live.mp3") as resumed:
            packets = resumed.iter_raw()
            assert demo.read_bytes().startswith(next(packets))
            assert client.get("/api/broadcast").headers["X-On-Air-Entry-Id"] == str(recovered["id"])
