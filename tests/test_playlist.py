import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Barrier
from time import monotonic

import httpx


def sign_in(client: httpx.Client) -> None:
    assert client.post("/api/manager/session", json={
        "username": "admin", "password": "admin",
    }).status_code == 204


def entry_ids(client: httpx.Client) -> list[int]:
    return [entry["id"] for entry in client.get("/api/manager/playlist/order").json()["entries"]]


def gap(after: int | None, before: int | None) -> dict[str, int | None]:
    return {"after_entry_id": after, "before_entry_id": before}


def test_remove_and_undo_one_occurrence_without_losing_its_neighbors(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        a, b = client.get("/api/manager/playlist/order").json()["entries"]
        assert client.post("/api/manager/playlist", json={
            "track_id": a["track"]["id"], "gap": gap(a["id"], b["id"]),
        }).status_code == 201
        first_id, duplicate_id, last_id = entry_ids(client)
        usage = "/api/manager/library/usage?q=Harbor+Lights"
        assert client.get(usage).json() == [{"track": a["track"], "entry_count": 2}]
        assert client.delete(f"/api/manager/library/{a['track']['id']}?confirm=true").status_code == 409

        removed = client.delete(f"/api/manager/playlist/{duplicate_id}")
        assert removed.status_code == 200
        assert entry_ids(client) == [first_id, last_id]
        assert client.get(usage).json() == [{"track": a["track"], "entry_count": 1}]
        undo = f"/api/manager/playlist/undo/{removed.json()['undo_token']}"
        assert client.post(undo).status_code == 200
        assert entry_ids(client) == [first_id, duplicate_id, last_id]
        assert client.post(undo).status_code == 404

        assert client.delete(f"/api/manager/playlist/{first_id}").status_code == 200
        assert client.delete(f"/api/manager/playlist/{duplicate_id}").status_code == 200
        protected = client.delete(f"/api/manager/playlist/{last_id}")
        assert protected.status_code == 409
        assert protected.json()["detail"] == "last_playable_entry"
        assert entry_ids(client) == [last_id]
        assert client.delete(f"/api/manager/library/{a['track']['id']}?confirm=true").status_code == 204
        assert client.get(usage).json() == []


def test_cannot_remove_the_last_playable_entry_when_another_recording_is_unreadable(
    station: str, tmp_path: Path,
) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        sign_in(client)
        a, b = client.get("/api/manager/playlist/order").json()["entries"]
        with closing(sqlite3.connect(tmp_path / "station.sqlite3")) as connection, connection:
            connection.execute(
                "UPDATE library_tracks SET mp3 = ? WHERE id = ?", (b"Unreadable", b["track"]["id"]),
            )
        response = client.delete(f"/api/manager/playlist/{a['id']}")
        assert response.status_code == 409
        assert entry_ids(client) == [a["id"], b["id"]]
        assert client.delete(f"/api/manager/playlist/{b['id']}").status_code == 200
        assert entry_ids(client) == [a["id"]]


def test_removing_and_deleting_the_on_air_recording_lets_its_audio_finish(station: str) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (demo / "harbor-lights.mp3").read_bytes()
    evening_tide = (demo / "evening-tide.mp3").read_bytes()
    with (
        httpx.Client(base_url=station, trust_env=False) as manager,
        httpx.Client(base_url=station, trust_env=False) as listener,
    ):
        sign_in(manager)
        a, b = manager.get("/api/manager/playlist/order").json()["entries"]
        track_id = a["track"]["id"]
        entry = f"/api/manager/playlist/{a['id']}"
        recording = f"/api/manager/library/{track_id}"
        deletion = f"{recording}?confirm=true"

        assert listener.delete(deletion).status_code == 401
        assert manager.delete(recording).status_code == 400
        assert manager.delete(deletion).status_code == 409
        assert manager.get("/api/manager/library/usage", params={"q": "Harbor Lights"}).json() == [
            {"track": a["track"], "entry_count": 1},
        ]

        with listener.stream("GET", "/live.mp3") as response:
            packets = response.iter_raw()
            played = bytearray(next(packets))
            assert harbor_lights.startswith(played)

            removed = manager.delete(entry).json()
            assert entry_ids(manager) == [b["id"]]
            status = listener.get("/api/broadcast")
            assert status.headers["X-On-Air-Entry-Id"] == str(a["id"])
            assert status.json()["up_next"]["title"] == b["track"]["title"]

            assert manager.post(f"/api/manager/playlist/undo/{removed['undo_token']}").status_code == 200
            assert entry_ids(manager) == [a["id"], b["id"]]
            assert listener.get("/api/broadcast").headers["X-On-Air-Entry-Id"] == str(a["id"])

            removed_again = manager.delete(entry).json()
            assert manager.get("/api/manager/library/usage", params={"q": "Harbor Lights"}).json() == [
                {"track": a["track"], "entry_count": 0},
            ]
            assert manager.delete(deletion).status_code == 202
            assert manager.get("/api/manager/library/usage", params={"q": "Harbor Lights"}).json() == []
            assert manager.get(f"{recording}/preview").status_code == 404
            assert manager.post("/api/manager/playlist", json={"track_id": track_id}).status_code == 404
            assert manager.post(f"/api/manager/playlist/undo/{removed_again['undo_token']}").status_code == 404

            deadline = monotonic() + 18
            while len(played) < len(harbor_lights):
                assert monotonic() < deadline
                played.extend(next(packets))
            assert played == harbor_lights
            assert evening_tide.startswith(next(packets))
            assert listener.get("/api/broadcast").headers["X-On-Air-Entry-Id"] == str(b["id"])
            assert entry_ids(manager) == [b["id"]]


def test_same_recording_can_be_placed_twice_and_one_occurrence_moved(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        assert client.get("/api/manager/playlist/order").status_code == 401
        sign_in(client)
        order = client.get("/api/manager/playlist/order").json()
        first, second = order["entries"]
        a, b = first["id"], second["id"]
        track_id = first["track"]["id"]
        assert order["on_air_entry_id"] is None

        assert client.post("/api/manager/playlist", json={
            "track_id": track_id, "gap": gap(a, b),
        }).status_code == 201
        assert client.post("/api/manager/playlist", json={"track_id": track_id}).status_code == 201
        _, placed, _, appended = entry_ids(client)
        assert len({a, b, placed, appended}) == 4

        move = f"/api/manager/playlist/{appended}"
        assert client.patch(move, json=gap(None, a)).status_code == 204
        assert entry_ids(client) == [appended, a, placed, b]
        assert [entry["track"]["id"] for entry in client.get("/api/manager/playlist/order").json()["entries"]] == [
            track_id, track_id, track_id, second["track"]["id"],
        ]
        assert client.get("/api/broadcast").json() == {"on_air": None, "up_next": None}


def test_only_one_manager_can_claim_the_same_gap(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as first, httpx.Client(
        base_url=station, trust_env=False,
    ) as second:
        sign_in(first)
        sign_in(second)
        a, b = entry_ids(first)
        track_id = first.get("/api/manager/playlist/order").json()["entries"][0]["track"]["id"]
        both_ready = Barrier(2)

        def place(client: httpx.Client) -> httpx.Response:
            both_ready.wait(timeout=5)
            return client.post("/api/manager/playlist", json={"track_id": track_id, "gap": gap(a, b)})

        with ThreadPoolExecutor(2) as pool:
            results = sorted(pool.map(place, (first, second)), key=lambda result: result.status_code)
        assert [result.status_code for result in results] == [201, 409]
        refreshed = results[1].json()["detail"]["order"]["entries"]
        placed = refreshed[1]["id"]
        assert [entry["id"] for entry in refreshed] == [a, placed, b]

        assert second.post("/api/manager/playlist", json={"track_id": track_id}).status_code == 201
        appended = entry_ids(second)[-1]
        assert entry_ids(first) == [a, placed, b, appended]

        conflict = first.patch(f"/api/manager/playlist/{a}", json=gap(b, None))
        assert conflict.status_code == 409
        assert [entry["id"] for entry in conflict.json()["detail"]["order"]["entries"]] == [
            a, placed, b, appended,
        ]
        assert second.patch(f"/api/manager/playlist/{a}", json=gap(b, appended)).status_code == 204
        assert entry_ids(first) == [placed, b, a, appended]


def test_moving_the_on_air_occurrence_wraps_both_live_streams_to_the_first_entry(station: str) -> None:
    demo = Path(__file__).resolve().parents[1] / "radio/demo"
    harbor_lights = (demo / "harbor-lights.mp3").read_bytes()
    evening_tide = (demo / "evening-tide.mp3").read_bytes()
    with (
        httpx.Client(base_url=station, trust_env=False) as first,
        httpx.Client(base_url=station, trust_env=False) as second,
    ):
        sign_in(first)
        a, b = first.get("/api/manager/playlist/order").json()["entries"]
        for _ in range(2):
            assert first.post("/api/manager/playlist", json={"track_id": b["track"]["id"]}).status_code == 201
        a_id, b_id, c_id, d_id = entry_ids(first)

        with first.stream("GET", "/live.mp3") as first_stream:
            first_packets = first_stream.iter_raw()
            assert harbor_lights.startswith(next(first_packets))
            with second.stream("GET", "/live.mp3") as second_stream:
                second_packets = second_stream.iter_raw()
                joined = next(second_packets)
                deadline = monotonic() + 35
                while next(first_packets) != joined:
                    assert monotonic() < deadline

                packet = joined
                while not evening_tide.startswith(packet):
                    assert monotonic() < deadline
                    packet = next(first_packets)
                    assert packet == next(second_packets)
                played = bytearray(packet)

                assert second.patch(f"/api/manager/playlist/{b_id}", json=gap(d_id, None)).status_code == 401
                assert first.patch(f"/api/manager/playlist/{b_id}", json=gap(d_id, None)).status_code == 204
                order = first.get("/api/manager/playlist/order").json()
                assert [entry["id"] for entry in order["entries"]] == [a_id, c_id, d_id, b_id]
                assert order["on_air_entry_id"] == b_id
                expected = {
                    "on_air": {"title": b["track"]["title"], "artist": b["track"]["artist"]},
                    "up_next": {"title": a["track"]["title"], "artist": a["track"]["artist"]},
                }
                assert first.get("/api/broadcast").json() == second.get("/api/broadcast").json() == expected

                while len(played) < len(evening_tide):
                    assert monotonic() < deadline
                    packet = next(first_packets)
                    assert packet == next(second_packets)
                    played.extend(packet)
                assert played == evening_tide
                following = next(first_packets)
                assert following == next(second_packets)
                assert harbor_lights.startswith(following)
