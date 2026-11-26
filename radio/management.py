import logging
import re
import shutil
import sqlite3
from asyncio import CancelledError, Event, Lock, Task, create_task, gather, sleep, timeout
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from tempfile import TemporaryDirectory
from time import monotonic
from typing import Annotated, BinaryIO

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path as PathParameter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.requests import ClientDisconnect
from starlette.types import Receive, Scope, Send

from radio import storage, youtube
from radio.broadcast import Broadcast
from radio.preparation import (
    AUDIO_EXTENSIONS, DISK_RESERVE_BYTES, MAX_UPLOAD_BYTES, METADATA_LIMIT,
    InvalidAudio, prepare_audio,
)
from radio.staging import OnlinePreviews, StagedRecording, StagingBusy, StagingFull
from radio.storage import PlaylistEntry, PlaylistGap, Track, TrackUsage

LOGGER = logging.getLogger(__name__)
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
    gap: PlaylistGap | None = None


@dataclass(frozen=True)
class PlaylistOrder:
    entries: tuple[PlaylistEntry, ...]
    on_air_entry_id: int | None


@dataclass(frozen=True)
class PlaylistRemoval:
    order: PlaylistOrder
    undo_token: str


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


class AudioPreviewResponse[Reader: BinaryIO | sqlite3.Blob](StreamingResponse):
    def __init__(
        self, request: Request, size: int | None, audio: AbstractContextManager[Reader],
        *, media_type: str = "audio/mpeg",
        stream: Callable[[Reader, int, int | None], AsyncIterator[bytes]] | None = None,
    ) -> None:
        headers = dict(PRIVATE_AUDIO)
        range_header = request.headers.get("range")
        status = 200
        start, length = 0, size
        if size is not None:
            headers["Accept-Ranges"] = "bytes"
            if range_header is not None:
                start, end = preview_bounds(range_header, size)
                length = end - start + 1
                headers["Content-Range"] = f"bytes {start}-{end}/{size}"
                status = 206
            headers["Content-Length"] = str(length)
        self.audio = audio
        self.start = start
        self.length = length
        self.stream = stream
        super().__init__((), status_code=status, media_type=media_type, headers=headers)

    async def read_audio(self, audio: Reader) -> AsyncGenerator[bytes, None]:
        audio.seek(self.start)
        remaining = self.length
        while remaining:
            chunk = audio.read(min(remaining, storage.AUDIO_CHUNK_SIZE))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with ExitStack() as resources:
            try:
                audio = resources.enter_context(self.audio)
            except sqlite3.OperationalError:
                # A library track can be deleted between the size check and opening its BLOB.
                await JSONResponse({"detail": "track_not_found"}, status_code=404)(scope, receive, send)
                return
            self.body_iterator = self.stream(audio, self.start, self.length) if self.stream else self.read_audio(audio)
            await super().__call__(scope, receive, send)


def require_preparation_space(database: Path) -> None:
    # Allow for both a preview download and an import's source, MP3, WAL and checkpoint.
    if shutil.disk_usage(database.parent).free < DISK_RESERVE_BYTES + 5 * MAX_UPLOAD_BYTES:
        raise HTTPException(507, "insufficient_space")


@contextmanager
def report_online_errors() -> Iterator[None]:
    try:
        yield
    except StagingBusy as error:
        raise HTTPException(409, "preparation_busy") from error
    except StagingFull as error:
        raise HTTPException(409, "preview_storage_full") from error
    except youtube.RecordingTooLarge as error:
        raise HTTPException(413, "upload_too_large") from error
    except (youtube.YouTubeUnavailable, InvalidAudio) as error:
        raise HTTPException(502, "youtube_retrieval_failed") from error
    except FileNotFoundError as error:
        raise HTTPException(503, "preparation_unavailable") from error


def management_routes(database: Path, broadcast: Broadcast) -> APIRouter:
    sessions = ManagerSessions()
    preparation = Lock()
    playlist_edits = Lock()
    youtube_search = Lock()
    staging = OnlinePreviews(database)
    deletions: set[Task[None]] = set()

    async def finish_deletion(track_id: int, finished: Event) -> None:
        await finished.wait()
        try:
            await run_in_threadpool(storage.purge_pending_track, database, track_id)
        except sqlite3.Error:
            LOGGER.exception("Could not finish deleting track %s; will retry on restart", track_id)

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
            await staging.close()
            pending = tuple(deletions)
            for deletion in pending:
                deletion.cancel()
            await gather(*pending, return_exceptions=True)

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

    @manager.post("/broadcast/skip/{playback_id}", status_code=204)
    async def skip_playback(playback_id: int) -> None:
        if not await broadcast.skip(playback_id):
            raise HTTPException(409, "playback_changed")

    @manager.get("/playlist")
    async def playlist() -> tuple[Track, ...]:
        return tuple(entry.track for entry in broadcast.playlist)

    @manager.get("/playlist/order")
    async def playlist_order() -> PlaylistOrder:
        return PlaylistOrder(broadcast.playlist, broadcast.on_air_entry_id)

    async def save_playlist(
        edit: Callable[[], tuple[PlaylistEntry, ...] | None], missing: str,
    ) -> tuple[PlaylistEntry, ...]:
        async with playlist_edits:
            try:
                updated = await run_in_threadpool(edit)
            except storage.PlaylistGapChanged as error:
                order = PlaylistOrder(error.playlist, broadcast.on_air_entry_id)
                raise HTTPException(409, {"code": "playlist_gap_changed", "order": jsonable_encoder(order)}) from error
            except storage.LastPlayableEntry as error:
                raise HTTPException(409, "last_playable_entry") from error
            if updated is None:
                raise HTTPException(404, missing)
            transition = broadcast.update_playlist(updated)
            if transition is not None:
                await transition.wait()
            return updated

    @manager.post("/playlist", status_code=201)
    async def add_entry(addition: PlaylistAddition) -> tuple[Track, ...]:
        updated = await save_playlist(
            partial(storage.add_to_playlist, database, addition.track_id, addition.gap), "track_not_found",
        )
        return tuple(entry.track for entry in updated)

    @manager.patch("/playlist/{entry_id}", status_code=204)
    async def move_entry(entry_id: int, gap: PlaylistGap) -> None:
        await save_playlist(partial(storage.move_playlist_entry, database, entry_id, gap), "entry_not_found")

    @manager.delete("/playlist/{entry_id}")
    async def remove_entry(entry_id: int) -> PlaylistRemoval:
        token = token_urlsafe(24)
        updated = await save_playlist(
            partial(storage.remove_playlist_entry, database, entry_id, token), "entry_not_found",
        )
        return PlaylistRemoval(PlaylistOrder(updated, broadcast.on_air_entry_id), token)

    @manager.post("/playlist/undo/{token}")
    async def undo_removal(token: str) -> PlaylistOrder:
        updated = await save_playlist(partial(storage.undo_playlist_removal, database, token), "undo_unavailable")
        return PlaylistOrder(updated, broadcast.on_air_entry_id)

    @manager.get("/library")
    def library(q: Annotated[str, Query(max_length=METADATA_LIMIT)] = "") -> list[Track]:
        return [item.track for item in storage.search_library(database, q)]

    @manager.get("/library/usage")
    def library_usage(q: Annotated[str, Query(max_length=METADATA_LIMIT)] = "") -> list[TrackUsage]:
        return storage.search_library(database, q)

    @manager.delete("/library/{track_id}", status_code=204, responses={202: {"description": "Finishing on air"}})
    async def delete_track(track_id: int, confirm: bool = False) -> Response:
        if not confirm:
            raise HTTPException(400, "confirm_track_deletion")
        async with playlist_edits:
            finished = broadcast.on_air_completion(track_id)
            try:
                deleted = await run_in_threadpool(
                    storage.delete_unused_track, database, track_id, defer=finished is not None,
                )
            except storage.TrackInUse as error:
                raise HTTPException(409, "track_in_use") from error
            if not deleted:
                raise HTTPException(404, "track_not_found")
            if finished is not None:
                deletion = create_task(finish_deletion(track_id, finished))
                deletions.add(deletion)
                deletion.add_done_callback(deletions.discard)
                return Response(status_code=202)
            return Response(status_code=204)

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

    def stage_online(video_id: str) -> StagedRecording:
        staged = staging.find(video_id)
        if staged:
            return staged
        require_preparation_space(database)
        return staging.start(video_id)

    @manager.post("/youtube/{video_id}/preview", status_code=204)
    async def stage_preview(video_id: VideoId) -> None:
        if await run_in_threadpool(storage.find_youtube_track, database, video_id):
            return
        with report_online_errors():
            await stage_online(video_id).wait_for_start()

    @manager.get("/youtube/{video_id}/audio", response_class=StreamingResponse)
    async def online_audio(video_id: VideoId, request: Request, prepare: bool = False) -> StreamingResponse:
        if prepare:
            check_origin(request)
        staged = staging.find(video_id)
        if staged is None and prepare:
            imported = await run_in_threadpool(storage.find_youtube_track, database, video_id)
            if imported is not None:
                return await preview(imported.id, request)
            with report_online_errors():
                staged = stage_online(video_id)
        if staged:
            with report_online_errors():
                source = await staged.wait_for_start()
            with suppress(FileNotFoundError):
                audio = staged.path.open("rb")
                try:
                    size = staged.size if staged.fingerprint is not None else source.size
                    # Let browsers decode a growing stream. Explicit seeks can still use known ranges.
                    if staged.fingerprint is None and request.headers.get("range") in (None, "bytes=0-"):
                        size = None
                    return AudioPreviewResponse(
                        request, size, audio, media_type=source.media_type, stream=staged.stream,
                    )
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

            with report_online_errors():
                staged = stage_online(video_id)
                fingerprint = await staged.wait_for_completion()
            async with playlist_edits:
                existing = await run_in_threadpool(storage.find_uploaded_track, database, fingerprint)
                if existing:
                    await run_in_threadpool(storage.attach_youtube_source, database, video_id, existing.id)
                    response.status_code = 200
            if existing is None:
                require_preparation_space(database)
                source = await staged.wait_for_start()
                with report_online_errors(), TemporaryDirectory(prefix="import-", dir=database.parent) as directory:
                    prepared = Path(directory) / "prepared.mp3"
                    recording = source.recording
                    await run_in_threadpool(prepare_audio, staged.path, prepared, recording.id, allow_webm=True)
                    existing = await run_in_threadpool(
                        storage.save_prepared_track, database, prepared, fingerprint,
                        recording.title, recording.channel or "", video_id=video_id,
                    )
            staging.discard(staged)
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
