import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from radio.preparation import mp3_packets

DEMO = Path(__file__).resolve().parent / "demo"
AUDIO_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Track:
    id: int
    title: str
    artist: str


@dataclass(frozen=True)
class TrackUsage:
    track: Track
    entry_count: int


@dataclass(frozen=True)
class PlaylistEntry:
    id: int
    track: Track


@dataclass(frozen=True)
class PlaylistGap:
    after_entry_id: int | None
    before_entry_id: int | None


class PlaylistGapChanged(Exception):
    def __init__(self, playlist: tuple[PlaylistEntry, ...]) -> None:
        self.playlist = playlist


class LastPlayableEntry(Exception):
    pass


class TrackInUse(Exception):
    pass


def prepare_station(database: Path) -> tuple[PlaylistEntry, ...]:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS library_tracks (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                mp3 BLOB NOT NULL CHECK (length(mp3) > 0),
                pending_deletion INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS playlist_entries (
                id INTEGER PRIMARY KEY,
                track_id INTEGER NOT NULL REFERENCES library_tracks (id),
                position INTEGER NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS youtube_imports (
                video_id TEXT PRIMARY KEY,
                track_id INTEGER NOT NULL REFERENCES library_tracks (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS playlist_removals (
                token TEXT PRIMARY KEY,
                entry_id INTEGER NOT NULL UNIQUE,
                track_id INTEGER NOT NULL REFERENCES library_tracks (id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                after_entry_id INTEGER,
                before_entry_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS station_ids (
                kind TEXT PRIMARY KEY,
                last_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS station_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                restart_position INTEGER NOT NULL CHECK (restart_position >= 0)
            );

            INSERT OR IGNORE INTO station_state (id, restart_position) VALUES (1, 0);

            CREATE INDEX IF NOT EXISTS playlist_entries_by_track ON playlist_entries (track_id);
        """)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(library_tracks)")}
        if "original_sha256" not in columns:
            connection.execute("ALTER TABLE library_tracks ADD COLUMN original_sha256 TEXT")
        if "pending_deletion" not in columns:
            connection.execute("ALTER TABLE library_tracks ADD COLUMN pending_deletion INTEGER NOT NULL DEFAULT 0")
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS library_original_sha256
            ON library_tracks (original_sha256)
        """)
        connection.execute("DELETE FROM library_tracks WHERE pending_deletion = 1")
        if connection.execute("SELECT 1 FROM library_tracks LIMIT 1").fetchone() is None:
            for position, (filename, title) in enumerate((
                ("harbor-lights.mp3", "Harbor Lights"),
                ("evening-tide.mp3", "Evening Tide"),
            )):
                track = connection.execute(
                    "INSERT INTO library_tracks (title, artist, mp3) VALUES (?, ?, ?)",
                    (title, "Station demo", (DEMO / filename).read_bytes()),
                )
                connection.execute(
                    "INSERT INTO playlist_entries (track_id, position) VALUES (?, ?)",
                    (track.lastrowid, position),
                )

        # Preserve identity across deletion, including a restart after running an older release.
        connection.execute("""
            INSERT INTO station_ids (kind, last_id) VALUES
                ('entry', (SELECT coalesce(max(id), 0) FROM playlist_entries)),
                ('track', (SELECT coalesce(max(id), 0) FROM library_tracks))
            ON CONFLICT (kind) DO UPDATE SET last_id = max(station_ids.last_id, excluded.last_id)
        """)
        return read_playlist(connection)


def read_restart_position(database: Path) -> int:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute("SELECT restart_position FROM station_state WHERE id = 1").fetchone()[0]


def save_restart_position(database: Path, position: int) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE station_state SET restart_position = ? WHERE id = 1", (position,))


def allocate_id(connection: sqlite3.Connection, kind: str) -> int:
    return connection.execute("""
        UPDATE station_ids SET last_id = last_id + 1 WHERE kind = ? RETURNING last_id
    """, (kind,)).fetchone()[0]


def read_playlist(connection: sqlite3.Connection) -> tuple[PlaylistEntry, ...]:
    rows = connection.execute("""
        SELECT entry.id, track.id, track.title, track.artist
        FROM playlist_entries AS entry
        JOIN library_tracks AS track ON track.id = entry.track_id
        ORDER BY entry.position
    """)
    return tuple(PlaylistEntry(entry_id, Track(track_id, title, artist)) for entry_id, track_id, title, artist in rows)


def find_uploaded_track(database: Path, fingerprint: str) -> Track | None:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT id, title, artist FROM library_tracks WHERE original_sha256 = ?",
            (fingerprint,),
        ).fetchone()
        return Track(*row) if row else None


def save_prepared_track(
    database: Path, prepared: Path, fingerprint: str, title: str, artist: str,
    *, video_id: str | None = None,
) -> Track:
    with closing(sqlite3.connect(database)) as connection, connection:
        track_id = allocate_id(connection, "track")
        connection.execute(
            """INSERT INTO library_tracks (id, title, artist, mp3, original_sha256)
               VALUES (?, ?, ?, zeroblob(?), ?)""",
            (track_id, title, artist, prepared.stat().st_size, fingerprint),
        )
        with connection.blobopen("library_tracks", "mp3", track_id) as audio, prepared.open("rb") as file:
            while chunk := file.read(AUDIO_CHUNK_SIZE):
                audio.write(chunk)
        if video_id is not None:
            connection.execute("INSERT INTO youtube_imports VALUES (?, ?)", (video_id, track_id))
        return Track(track_id, title, artist)


def find_youtube_track(database: Path, video_id: str) -> Track | None:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute("""
            SELECT track.id, track.title, track.artist
            FROM youtube_imports AS source
            JOIN library_tracks AS track ON track.id = source.track_id
            WHERE source.video_id = ?
        """, (video_id,)).fetchone()
        return Track(*row) if row else None


def attach_youtube_source(database: Path, video_id: str, track_id: int) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO youtube_imports VALUES (?, ?)", (video_id, track_id))


def search_library(database: Path, query: str) -> list[TrackUsage]:
    with closing(sqlite3.connect(database)) as connection:
        connection.create_function("casefold", 1, str.casefold, deterministic=True)
        rows = connection.execute("""
            SELECT track.id, track.title, track.artist,
                   (SELECT count(*) FROM playlist_entries WHERE track_id = track.id)
            FROM library_tracks AS track
            WHERE pending_deletion = 0
              AND (instr(casefold(title), :query) OR instr(casefold(artist), :query))
            ORDER BY casefold(title), casefold(artist), id
        """, {"query": query.strip().casefold()})
        return [TrackUsage(Track(track_id, title, artist), count) for track_id, title, artist, count in rows]


def update_track(
    database: Path, track_id: int, title: str, artist: str,
) -> tuple[Track, tuple[PlaylistEntry, ...]] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute("""
            UPDATE library_tracks SET title = ?, artist = ? WHERE id = ? AND pending_deletion = 0
            RETURNING id, title, artist
        """, (title, artist, track_id)).fetchone()
        return (Track(*row), read_playlist(connection)) if row else None


def delete_unused_track(database: Path, track_id: int, *, defer: bool) -> bool:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM playlist_entries WHERE track_id = ?", (track_id,)).fetchone():
            raise TrackInUse
        if defer:
            updated = connection.execute("""
                UPDATE library_tracks SET pending_deletion = 1, original_sha256 = NULL
                WHERE id = ? AND pending_deletion = 0
            """, (track_id,))
            if updated.rowcount:
                connection.execute("DELETE FROM playlist_removals WHERE track_id = ?", (track_id,))
                connection.execute("DELETE FROM youtube_imports WHERE track_id = ?", (track_id,))
            return bool(updated.rowcount)
        deleted = connection.execute(
            "DELETE FROM library_tracks WHERE id = ? AND pending_deletion = 0", (track_id,),
        )
        return bool(deleted.rowcount)


def purge_pending_track(database: Path, track_id: int) -> None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("DELETE FROM library_tracks WHERE id = ? AND pending_deletion = 1", (track_id,))


def gap_position(
    entries: list[int], gap: PlaylistGap, playlist: tuple[PlaylistEntry, ...],
) -> int:
    neighbors = zip((None, *entries), (*entries, None), strict=True)
    for position, pair in enumerate(neighbors):
        if pair == (gap.after_entry_id, gap.before_entry_id):
            return position
    raise PlaylistGapChanged(playlist)


def write_playlist_positions(connection: sqlite3.Connection, entries: list[int]) -> None:
    # Park existing positions away from their final values to satisfy UNIQUE throughout a reorder.
    connection.execute("UPDATE playlist_entries SET position = -position - 1")
    connection.executemany("UPDATE playlist_entries SET position = ? WHERE id = ?", enumerate(entries))


def add_to_playlist(
    database: Path, track_id: int, gap: PlaylistGap | None,
) -> tuple[PlaylistEntry, ...] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        playlist = read_playlist(connection)
        entries = [entry.id for entry in playlist]
        position = len(entries) if gap is None else gap_position(entries, gap, playlist)
        entry_id = allocate_id(connection, "entry")
        entry = connection.execute("""
            INSERT INTO playlist_entries (id, track_id, position)
            SELECT ?, id, (SELECT coalesce(max(position), -1) + 1 FROM playlist_entries)
            FROM library_tracks WHERE id = ? AND pending_deletion = 0
        """, (entry_id, track_id))
        if not entry.rowcount:
            return None
        if position != len(entries):
            entries.insert(position, entry_id)
            write_playlist_positions(connection, entries)
        return read_playlist(connection)


def move_playlist_entry(
    database: Path, entry_id: int, gap: PlaylistGap,
) -> tuple[PlaylistEntry, ...] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        playlist = read_playlist(connection)
        entries = [entry.id for entry in playlist]
        if entry_id not in entries:
            return None
        original_position = entries.index(entry_id)
        entries.pop(original_position)
        position = gap_position(entries, gap, playlist)
        if position == original_position:
            return playlist
        entries.insert(position, entry_id)
        write_playlist_positions(connection, entries)
        return read_playlist(connection)


def playable(connection: sqlite3.Connection, track_id: int) -> bool:
    try:
        with connection.blobopen("library_tracks", "mp3", track_id, readonly=True) as audio:
            has_audio = False
            for _ in mp3_packets(audio.read):
                has_audio = True
            return has_audio
    except (ValueError, sqlite3.OperationalError):
        return False


def remove_playlist_entry(
    database: Path, entry_id: int, token: str,
) -> tuple[PlaylistEntry, ...] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        playlist = read_playlist(connection)
        entries = [entry.id for entry in playlist]
        if entry_id not in entries:
            return None
        position = entries.index(entry_id)
        track_id = playlist[position].track.id
        remaining_tracks = dict.fromkeys(entry.track.id for entry in playlist if entry.id != entry_id)
        if not remaining_tracks or (
            not any(playable(connection, other) for other in remaining_tracks)
            and playable(connection, track_id)
        ):
            raise LastPlayableEntry

        connection.execute("""
            INSERT INTO playlist_removals (token, entry_id, track_id, position, after_entry_id, before_entry_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (token, entry_id, track_id, position,
              entries[position - 1] if position else None,
              entries[position + 1] if position + 1 < len(entries) else None))
        connection.execute("DELETE FROM playlist_entries WHERE id = ?", (entry_id,))
        entries.pop(position)
        write_playlist_positions(connection, entries)
        return read_playlist(connection)


def undo_playlist_removal(database: Path, token: str) -> tuple[PlaylistEntry, ...] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        removal = connection.execute("""
            DELETE FROM playlist_removals WHERE token = ?
            RETURNING entry_id, track_id, position, after_entry_id, before_entry_id
        """, (token,)).fetchone()
        if removal is None:
            return None

        entry_id, track_id, original_position, after, before = removal
        entries = [entry.id for entry in read_playlist(connection)]
        if before in entries:
            position = entries.index(before)
        elif after in entries:
            position = entries.index(after) + 1
        else:
            position = min(original_position, len(entries))
        connection.execute("""
            INSERT INTO playlist_entries (id, track_id, position)
            SELECT ?, ?, coalesce(max(position), -1) + 1 FROM playlist_entries
        """, (entry_id, track_id))
        entries.insert(position, entry_id)
        write_playlist_positions(connection, entries)
        return read_playlist(connection)


def audio_size(database: Path, track_id: int) -> int | None:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT length(mp3) FROM library_tracks WHERE id = ? AND pending_deletion = 0", (track_id,),
        ).fetchone()
        return row[0] if row else None


@contextmanager
def open_audio(database: Path, track_id: int) -> Iterator[sqlite3.Blob]:
    with closing(sqlite3.connect(database)) as connection:
        with connection.blobopen("library_tracks", "mp3", track_id, readonly=True) as audio:
            yield audio
