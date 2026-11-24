"""Copy a station database to a new, standalone SQLite file."""

import argparse
import sqlite3
from contextlib import closing
from os.path import lexists
from pathlib import Path
from tempfile import TemporaryDirectory


def copy_station_database(source: Path, destination: Path) -> None:
    """Take an online snapshot or restore one into a fresh location."""
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = f"{destination}{suffix}"
        if lexists(path):
            raise FileExistsError(f"Destination already has a database file: {path}")

    with TemporaryDirectory(dir=destination.parent) as directory:
        staged = Path(directory) / "station.sqlite3"
        with (
            closing(sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)) as live,
            closing(sqlite3.connect(staged)) as copy,
        ):
            # Pin one read snapshot. WAL writers can continue committing during the copy.
            live.execute("BEGIN")
            tables = {
                name for (name,) in live.execute(
                    "SELECT name FROM sqlite_schema WHERE type = 'table'"
                )
            }
            if not {"library_tracks", "playlist_entries"} <= tables:
                raise ValueError("Source is missing the station library or playlist")

            live.backup(copy)
            copy.execute("PRAGMA journal_mode = DELETE").fetchone()
            if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Snapshot failed the SQLite integrity check")
            if copy.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("Snapshot has broken database references")

        staged.chmod(0o600)
        # Publish the complete file atomically. A competing copy cannot overwrite it.
        destination.hardlink_to(staged)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path, help="new file in an existing directory")
    args = parser.parse_args()
    try:
        copy_station_database(args.source, args.destination)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")


if __name__ == "__main__":
    main()
