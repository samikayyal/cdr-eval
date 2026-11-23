import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

DEMO = Path(__file__).resolve().parent / "demo"
AUDIO_CHUNK_SIZE = 64 * 1024


@dataclass(frozen=True)
class Track:
    id: int
    title: str
    artist: str


def prepare_station(database: Path) -> tuple[Track, ...]:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute('PRAGMA journal_mode = WAL')
        connection.execute('PRAGMA foreign_keys = ON')
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS library_tracks (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                mp3 BLOB NOT NULL CHECK (length(mp3) > 0)
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
        """)
        columns = connection.execute("PRAGMA table_info(library_tracks)")
        if "original_sha256" not in {row[1] for row in columns}:
            connection.execute("ALTER TABLE library_tracks ADD COLUMN original_sha256 TEXT")
        connection.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS library_original_sha256
            ON library_tracks (original_sha256)
        """)
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

        return read_playlist(connection)


def read_playlist(connection: sqlite3.Connection) -> tuple[Track, ...]:
    rows = connection.execute("""
        SELECT track.id, track.title, track.artist
        FROM playlist_entries AS entry
        JOIN library_tracks AS track ON track.id = entry.track_id
        ORDER BY entry.position
    """)
    return tuple(Track(*row) for row in rows)


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
        entry = connection.execute(
            """INSERT INTO library_tracks (title, artist, mp3, original_sha256)
               VALUES (?, ?, zeroblob(?), ?)""",
            (title, artist, prepared.stat().st_size, fingerprint),
        )
        track_id = entry.lastrowid
        assert track_id is not None
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


def search_library(database: Path, query: str) -> list[Track]:
    with closing(sqlite3.connect(database)) as connection:
        connection.create_function("casefold", 1, str.casefold, deterministic=True)
        rows = connection.execute("""
            SELECT id, title, artist
            FROM library_tracks
            WHERE instr(casefold(title), :query) OR instr(casefold(artist), :query)
            ORDER BY casefold(title), casefold(artist), id
        """, {"query": query.strip().casefold()})
        return [Track(*row) for row in rows]


def update_track(
    database: Path, track_id: int, title: str, artist: str,
) -> tuple[Track, tuple[Track, ...]] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        row = connection.execute("""
            UPDATE library_tracks SET title = ?, artist = ? WHERE id = ?
            RETURNING id, title, artist
        """, (title, artist, track_id)).fetchone()
        return (Track(*row), read_playlist(connection)) if row else None


def append_to_playlist(database: Path, track_id: int) -> tuple[Track, ...] | None:
    with closing(sqlite3.connect(database)) as connection, connection:
        entry = connection.execute("""
            INSERT INTO playlist_entries (track_id, position)
            SELECT id, (SELECT coalesce(max(position), -1) + 1 FROM playlist_entries)
            FROM library_tracks WHERE id = ?
        """, (track_id,))
        return read_playlist(connection) if entry.rowcount else None


def audio_size(database: Path, track_id: int) -> int | None:
    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT length(mp3) FROM library_tracks WHERE id = ?", (track_id,),
        ).fetchone()
        return row[0] if row else None


@contextmanager
def open_audio(database: Path, track_id: int) -> Iterator[sqlite3.Blob]:
    with closing(sqlite3.connect(database)) as connection:
        with connection.blobopen("library_tracks", "mp3", track_id, readonly=True) as audio:
            yield audio
