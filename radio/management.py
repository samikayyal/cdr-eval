import re
import shutil
from asyncio import Lock, timeout
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.requests import ClientDisconnect

from radio import storage
from radio.broadcast import Broadcast
from radio.preparation import (
    AUDIO_EXTENSIONS, DISK_RESERVE_BYTES, MAX_UPLOAD_BYTES, METADATA_LIMIT,
    InvalidAudio, prepare_audio,
)
from radio.storage import Track

SESSION_COOKIE = "radio_manager"
SESSION_SECONDS = 8 * 60 * 60


class Credentials(BaseModel):
    username: str
    password: str


class TrackMetadata(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=METADATA_LIMIT)]
    artist: Annotated[str, StringConstraints(strip_whitespace=True, max_length=METADATA_LIMIT)]


class PlaylistAddition(BaseModel):
    track_id: int


@dataclass
class ManagerSessions:
    expires: dict[str, float] = field(default_factory=dict)

    def signed_in(self, request: Request) -> bool:
        token = request.cookies.get(SESSION_COOKIE, "")
        return self.expires.get(token, 0) > monotonic()

    def sign_in(self, request: Request, response: Response) -> None:
        now = monotonic()
        self.expires = {token: end for token, end in self.expires.items() if end > now}
        self.expires.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        token = token_urlsafe(32)
        self.expires[token] = now + SESSION_SECONDS
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=SESSION_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
        )

    def sign_out(self, request: Request, response: Response) -> None:
        self.expires.pop(request.cookies.get(SESSION_COOKIE, ""), None)
        response.delete_cookie(
            SESSION_COOKIE, httponly=True, secure=request.url.scheme == "https", samesite="strict",
        )


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "foreign_origin")


def preview_bounds(range_header: str, size: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=(\d{0,19})-(\d{0,19})", range_header, flags=re.ASCII)
    if match:
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
            if start <= end:
                return start, end
        elif last and int(last) > 0:
            return max(0, size - int(last)), size - 1
    raise HTTPException(416, "invalid_range", headers={"Content-Range": f"bytes */{size}"})


async def preview_audio(database: Path, track_id: int, start: int, length: int) -> AsyncIterator[bytes]:
    with storage.open_audio(database, track_id) as audio:
        audio.seek(start)
        while length:
            chunk = audio.read(min(length, storage.AUDIO_CHUNK_SIZE))
            length -= len(chunk)
            yield chunk


def management_routes(database: Path, broadcast: Broadcast) -> APIRouter:
    sessions = ManagerSessions()
    preparation = Lock()
    playlist_edits = Lock()
    router = APIRouter(prefix="/api/manager")

    async def require_manager(request: Request) -> None:
        if not sessions.signed_in(request):
            raise HTTPException(401, "sign_in_required")
        if request.method != "GET":
            check_origin(request)

    manager = APIRouter(dependencies=[Depends(require_manager)])

    @router.get("/session")
    async def session(request: Request, response: Response) -> dict[str, bool]:
        response.headers["Cache-Control"] = "no-store"
        return {"signed_in": sessions.signed_in(request)}

    @router.post("/session", status_code=204)
    async def sign_in(credentials: Credentials, request: Request, response: Response) -> None:
        check_origin(request)
        if (credentials.username, credentials.password) != ("admin", "admin"):
            raise HTTPException(401, "invalid_credentials")
        sessions.sign_in(request, response)

    @router.delete("/session", status_code=204)
    async def sign_out(request: Request, response: Response) -> None:
        check_origin(request)
        sessions.sign_out(request, response)

    @manager.get("/playlist")
    async def playlist() -> tuple[Track, ...]:
        return broadcast.playlist

    @manager.post("/playlist", status_code=201)
    async def append(addition: PlaylistAddition) -> tuple[Track, ...]:
        async with playlist_edits:
            updated = await run_in_threadpool(storage.append_to_playlist, database, addition.track_id)
            if updated is None:
                raise HTTPException(404, "track_not_found")
            broadcast.update_playlist(updated)
            return updated

    @manager.get("/library")
    def library(q: Annotated[str, Query(max_length=METADATA_LIMIT)] = "") -> list[Track]:
        return storage.search_library(database, q)

    @manager.post("/library", status_code=201)
    async def upload(
        request: Request, response: Response, filename: Annotated[str, Query(max_length=METADATA_LIMIT)],
    ) -> Track:
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
            raise HTTPException(415, "unsupported_format")
        if request.headers.get("content-type") != "application/octet-stream":
            raise HTTPException(415, "expected_recording")
        if int(request.headers.get("content-length", "0")) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "upload_too_large")
        if preparation.locked():
            raise HTTPException(409, "preparation_busy")

        async with preparation:
            # Allow for the source, prepared file, SQLite write-ahead log and checkpoint.
            if shutil.disk_usage(database.parent).free < DISK_RESERVE_BYTES + 4 * MAX_UPLOAD_BYTES:
                raise HTTPException(507, "insufficient_space")
            with TemporaryDirectory(prefix="upload-", dir=database.parent) as directory:
                source = Path(directory) / "recording"
                prepared = Path(directory) / "prepared.mp3"
                fingerprint = sha256()
                size = 0
                try:
                    async with timeout(120):
                        with source.open("wb") as file:
                            async for chunk in request.stream():
                                size += len(chunk)
                                if size > MAX_UPLOAD_BYTES:
                                    raise HTTPException(413, "upload_too_large")
                                file.write(chunk)
                                fingerprint.update(chunk)
                except (ClientDisconnect, TimeoutError) as error:
                    raise HTTPException(408, "upload_interrupted") from error
                if not size:
                    raise HTTPException(422, "invalid_audio")

                digest = fingerprint.hexdigest()
                existing = await run_in_threadpool(storage.find_uploaded_track, database, digest)
                if existing:
                    response.status_code = 200
                    return existing
                try:
                    title, artist = await run_in_threadpool(prepare_audio, source, prepared, filename)
                except InvalidAudio as error:
                    raise HTTPException(422, "invalid_audio") from error
                except FileNotFoundError as error:
                    raise HTTPException(503, "preparation_unavailable") from error
                return await run_in_threadpool(
                    storage.save_prepared_track, database, prepared, digest, title, artist,
                )

    @manager.patch("/library/{track_id}")
    async def edit_track(track_id: int, metadata: TrackMetadata) -> Track:
        async with playlist_edits:
            updated = await run_in_threadpool(
                storage.update_track, database, track_id, metadata.title, metadata.artist,
            )
            if updated is None:
                raise HTTPException(404, "track_not_found")
            track, playlist = updated
            broadcast.update_playlist(playlist)
            return track

    @manager.get("/library/{track_id}/preview", response_class=StreamingResponse)
    async def preview(track_id: int, request: Request) -> StreamingResponse:
        size = await run_in_threadpool(storage.audio_size, database, track_id)
        if size is None:
            raise HTTPException(404, "track_not_found")

        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store"}
        range_header = request.headers.get("range")
        start, end = 0, size - 1
        if range_header is not None:
            start, end = preview_bounds(range_header, size)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1
        headers["Content-Length"] = str(length)
        return StreamingResponse(
            preview_audio(database, track_id, start, length),
            status_code=206 if range_header is not None else 200,
            media_type="audio/mpeg",
            headers=headers,
        )

    router.include_router(manager)
    return router
