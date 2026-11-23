import re
import shutil
import sqlite3
from asyncio import CancelledError, Lock, create_task, sleep, timeout
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import AbstractContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field
from hashlib import sha256
from os import fstat
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path as PathParameter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from radio import storage, youtube
from radio.broadcast import Broadcast
from radio.preparation import (
    AUDIO_EXTENSIONS, DISK_RESERVE_BYTES, MAX_UPLOAD_BYTES, METADATA_LIMIT,
    InvalidAudio, prepare_audio,
)
from radio.staging import OnlinePreviews, StagedRecording, StagingFull
from radio.storage import Track

SESSION_COOKIE = "radio_manager"
SESSION_SECONDS = 8 * 60 * 60
PRIVATE_AUDIO = {"Cache-Control": "private, no-store"}
VideoId = Annotated[str, PathParameter(pattern=youtube.VIDEO_ID_PATTERN)]


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


class AudioPreviewResponse(StreamingResponse):
    def __init__(
        self, request: Request, size: int, audio: AbstractContextManager[BinaryIO | sqlite3.Blob],
    ) -> None:
        headers = {**PRIVATE_AUDIO, "Accept-Ranges": "bytes"}
        range_header = request.headers.get("range")
        start, end = 0, size - 1
        if range_header is not None:
            start, end = preview_bounds(range_header, size)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        length = end - start + 1
        headers["Content-Length"] = str(length)
        self.audio = audio
        self.start = start
        self.length = length
        super().__init__(
            (), status_code=206 if range_header is not None else 200,
            media_type="audio/mpeg", headers=headers,
        )

    async def read_audio(self, audio: BinaryIO | sqlite3.Blob) -> AsyncGenerator[bytes, None]:
        audio.seek(self.start)
        remaining = self.length
        while remaining:
            chunk = audio.read(min(remaining, storage.AUDIO_CHUNK_SIZE))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with self.audio as audio:
            self.body_iterator = self.read_audio(audio)
            await super().__call__(scope, receive, send)


def require_preparation_space(database: Path) -> None:
    # Allow for the source, prepared file, SQLite write-ahead log and checkpoint.
    if shutil.disk_usage(database.parent).free < DISK_RESERVE_BYTES + 4 * MAX_UPLOAD_BYTES:
        raise HTTPException(507, "insufficient_space")


def management_routes(database: Path, broadcast: Broadcast) -> APIRouter:
    sessions = ManagerSessions()
    preparation = Lock()
    playlist_edits = Lock()
    youtube_search = Lock()
    staging = OnlinePreviews(database)

    async def clean_previews() -> None:
        while True:
            await sleep(60)
            async with preparation:
                staging.clean()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        staging.clean(startup=True)
        cleaner = create_task(clean_previews())
        try:
            yield
        finally:
            cleaner.cancel()
            with suppress(CancelledError):
                await cleaner

    router = APIRouter(prefix="/api/manager", lifespan=lifespan)

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

    @manager.get("/youtube")
    async def search_youtube(q: Annotated[str, Query(max_length=METADATA_LIMIT)]) -> list[youtube.Recording]:
        if not q.strip():
            return []
        if youtube_search.locked():
            raise HTTPException(429, "youtube_search_busy")
        async with youtube_search:
            try:
                return await run_in_threadpool(youtube.search, q.strip())
            except youtube.YouTubeUnavailable as error:
                raise HTTPException(502, "youtube_search_failed") from error

    async def stage_online(video_id: str) -> StagedRecording:
        try:
            return await run_in_threadpool(staging.stage, video_id)
        except StagingFull as error:
            raise HTTPException(409, "preview_storage_full") from error
        except youtube.RecordingTooLarge as error:
            raise HTTPException(413, "upload_too_large") from error
        except (youtube.YouTubeUnavailable, InvalidAudio) as error:
            raise HTTPException(502, "youtube_retrieval_failed") from error
        except FileNotFoundError as error:
            raise HTTPException(503, "preparation_unavailable") from error

    @manager.post("/youtube/{video_id}/preview", status_code=204)
    async def stage_preview(video_id: VideoId) -> None:
        if preparation.locked():
            raise HTTPException(409, "preparation_busy")
        async with preparation:
            if await run_in_threadpool(storage.find_youtube_track, database, video_id) or staging.find(video_id):
                return
            require_preparation_space(database)
            await stage_online(video_id)

    @manager.get("/youtube/{video_id}/audio", response_class=StreamingResponse)
    async def online_audio(video_id: VideoId, request: Request) -> StreamingResponse:
        staged = staging.find(video_id)
        if staged:
            audio = staged.path.open("rb")
            try:
                return AudioPreviewResponse(request, fstat(audio.fileno()).st_size, audio)
            except BaseException:
                audio.close()
                raise
        imported = await run_in_threadpool(storage.find_youtube_track, database, video_id)
        if imported is None:
            raise HTTPException(404, "track_not_found")
        return await preview(imported.id, request)

    @manager.post("/youtube/{video_id}/import", status_code=201)
    async def import_online(video_id: VideoId, response: Response) -> Track:
        if preparation.locked():
            raise HTTPException(409, "preparation_busy")
        async with preparation:
            existing = await run_in_threadpool(storage.find_youtube_track, database, video_id)
            if existing:
                response.status_code = 200
                return existing

            require_preparation_space(database)
            staged = staging.find(video_id) or await stage_online(video_id)
            existing = await run_in_threadpool(storage.find_uploaded_track, database, staged.fingerprint)
            if existing:
                await run_in_threadpool(storage.attach_youtube_source, database, video_id, existing.id)
                response.status_code = 200
            else:
                existing = await run_in_threadpool(
                    storage.save_prepared_track, database, staged.path, staged.fingerprint,
                    staged.recording.title, staged.recording.channel or "", video_id=video_id,
                )
            staging.discard(staged.path)
            return existing

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
            require_preparation_space(database)
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

        return AudioPreviewResponse(request, size, storage.open_audio(database, track_id))

    router.include_router(manager)
    return router
