import os
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Send

from radio.broadcast import Broadcast
from radio.management import management_routes
from radio.storage import Track, prepare_station

STATIC = Path(__file__).resolve().parent / "static"


class LiveAudioResponse(StreamingResponse):
    def __init__(self, audio: AsyncGenerator[bytes, None]) -> None:
        super().__init__(
            audio,
            media_type="audio/mpeg",
            headers={
                "Cache-Control": "no-store, no-transform",
                "X-Accel-Buffering": "no",
            },
        )
        self._audio = audio

    async def stream_response(self, send: Send) -> None:
        # Also close the listener if the client disconnects during an ASGI send.
        async with aclosing(self._audio):
            await super().stream_response(send)


def create_app(database: Path | None = None) -> FastAPI:
    database = database or Path(os.environ.get("RADIO_DATABASE", "data/station.sqlite3"))
    broadcast = Broadcast(database, prepare_station(database))
    app = FastAPI(title="Al Qubtan Restaurants Radio")
    app.include_router(management_routes(database, broadcast))
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def listen_page() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/broadcast")
    async def broadcast_status() -> JSONResponse:
        headers = {"Cache-Control": "no-store"}
        if broadcast.on_air_entry_id is not None:
            headers["X-On-Air-Entry-Id"] = str(broadcast.on_air_entry_id)
        return JSONResponse(broadcast.status(), headers=headers)

    @app.get("/api/credits")
    async def music_credits() -> list[Track]:
        return list(dict.fromkeys(entry.track for entry in broadcast.playlist))

    @app.get("/live.mp3", response_class=StreamingResponse)
    async def live_audio() -> StreamingResponse:
        if not broadcast.playlist:
            raise HTTPException(503, "The station playlist is empty")
        if broadcast.unavailable:
            raise HTTPException(503, "Station temporarily unavailable")
        return LiveAudioResponse(broadcast.listen())

    return app
