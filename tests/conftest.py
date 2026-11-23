import socket
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Thread

import pytest
import uvicorn


@pytest.fixture
def station(tmp_path: Path) -> Iterator[str]:
    from radio.web import create_app

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        app = create_app(tmp_path / "station.sqlite3")
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    pytest.fail("Station did not start")
                time.sleep(0.01)
            yield url
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "Station did not shut down"
