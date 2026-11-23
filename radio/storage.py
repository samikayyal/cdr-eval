import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

DEMO = Path(__file__).resolve().parent / "demo"


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

        rows = connection.execute("""
            SELECT track.id, track.title, track.artist
            FROM playlist_entries AS entry
            JOIN library_tracks AS track ON track.id = entry.track_id
            ORDER BY entry.position
        """)
        return tuple(Track(*row) for row in rows)


@contextmanager
def open_audio(database: Path, track_id: int) -> Iterator[sqlite3.Blob]:
    with closing(sqlite3.connect(database)) as connection:
        with connection.blobopen("library_tracks", "mp3", track_id, readonly=True) as audio:
            yield audio
