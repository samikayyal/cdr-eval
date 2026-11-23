import socket
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import httpx
import pytest
import uvicorn


def test_listen_page_leaves_the_station_idle(station: str) -> None:
    with httpx.Client(base_url=station, trust_env=False) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert 'lang="en"' in page.text
        assert '<audio id="player" preload="none"></audio>' in page.text
        assert ">Play</button>" in page.text
        assert client.get("/api/broadcast").json() == {"on_air": None, "up_next": None}

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
                }
            finally:
                ready_to_stop.set()
            second_packet = second.result(timeout=5)

        first_track = (Path(__file__).resolve().parents[1] / "radio/demo/harbor-lights.mp3").read_bytes()
        assert 0 <= first_track.find(first_packet) < 4096
        assert 0 <= first_track.find(second_packet) < 4096

        deadline = time.monotonic() + 3
        while observer.get("/api/broadcast").json()["on_air"] is not None:
            assert time.monotonic() < deadline, "Broadcast stayed on air after the last Stop"
            time.sleep(0.03)

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
            }

            for packet in first_packets:
                assert packet == next(late_packets)
                if harbor_lights.startswith(packet):
                    assert 20 <= time.monotonic() - started <= 30
                    break
            else:
                pytest.fail("Stream ended before wraparound")
