"""Connection management and the migration runner.

The database is local-first and single-writer: one desktop app, several worker
processes, one SQLite file. That shape drives every pragma set here.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Four digits, an underscore, then lowercase words separated by single
# underscores. Deliberately tight: the point is to reject a near-miss like
# "0002_proxy_index 2.sql", not to describe every name someone might pick.
MIGRATION_FILENAME = re.compile(r"[0-9]{4}_[a-z0-9]+(?:_[a-z0-9]+)*\.sql")


class MigrationError(RuntimeError):
    """A migration could not be applied, or the file on disk is from the future."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def discover_migrations() -> list[Migration]:
    """Load migrations in version order.

    Filenames are `NNNN_name.sql`. Version numbers must be contiguous from 1 --
    a gap means a migration was deleted or never committed, and applying the
    remainder would leave a database nobody can reproduce.

    The name must match MIGRATION_FILENAME exactly, and anything else in this
    directory is refused rather than skipped. That is not pedantry: this repo
    has an environment that periodically drops byte-identical copies named
    `0002_proxy_index 2.sql` beside the original, and a loader globbing `*.sql`
    swallowed one, producing a duplicate version 2 and a contiguity error that
    read as a broken migration set rather than as a stray file. Refusing by
    name says which file is wrong; skipping unknown names would have hidden it
    entirely, which is worse -- a shadow copy of a migration is a shadow copy
    of the schema.
    """
    migrations: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        stem = path.stem
        if not MIGRATION_FILENAME.fullmatch(path.name):
            raise MigrationError(
                f"{path.name} is not a migration filename (expected NNNN_name.sql). "
                "Delete it if it is a stray copy; rename it if it is real."
            )
        number, _, name = stem.partition("_")
        if not number.isdigit():
            raise MigrationError(f"migration {path.name} does not start with a version number")
        migrations.append(
            Migration(version=int(number), name=name, sql=path.read_text(encoding="utf-8"))
        )

    for index, migration in enumerate(migrations, start=1):
        if migration.version != index:
            raise MigrationError(
                f"migration versions are not contiguous: expected {index}, "
                f"found {migration.version} ({migration.name})"
            )
    return migrations


SCHEMA_VERSION = len(discover_migrations())


def connect(path: str | Path, *, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection with the pragmas this workload needs.

    WAL because analysis workers read continuously while ingest writes; the
    default rollback journal would have them blocking each other for the entire
    length of a scan.

    `foreign_keys` is ON because the cascade rules are load-bearing: deleting a
    media row must take its faces, moments, sources and vectors with it, and
    "no silent data loss" cuts both ways -- orphans are a kind of loss too.
    """
    path = Path(path)
    if read_only and path != Path(":memory:"):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        connection = sqlite3.connect(path)

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    # NORMAL rather than FULL: with WAL this is durable across application
    # crashes, only losing the most recent commits on an OS-level crash. Every
    # job is resumable and every step idempotent, so re-running the tail of a
    # scan is cheap; fsyncing every commit during a 300k-file import is not.
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA mmap_size = 268435456")
    return connection


def current_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def migrate(connection: sqlite3.Connection) -> int:
    """Apply every migration the database has not yet seen.

    Idempotent: running it against an up-to-date database is a no-op. Each
    migration runs inside a transaction, so a failure leaves the database at the
    last good version rather than half-migrated.
    """
    migrations = discover_migrations()
    version = current_version(connection)

    if version > len(migrations):
        raise MigrationError(
            f"database is at schema version {version} but only {len(migrations)} "
            "migrations exist; this file was written by a newer build. Refusing to "
            "open it rather than silently misreading its contents."
        )

    for migration in migrations:
        if migration.version <= version:
            continue
        try:
            with connection:
                connection.executescript(migration.sql)
                connection.execute(f"PRAGMA user_version = {migration.version}")
        except sqlite3.Error as error:
            raise MigrationError(
                f"migration {migration.version:04d}_{migration.name} failed: {error}"
            ) from error
        version = migration.version

    return version


def has_fts5(connection: sqlite3.Connection) -> bool:
    try:
        connection.execute("CREATE VIRTUAL TABLE temp.__fts_probe USING fts5(x)")
        connection.execute("DROP TABLE temp.__fts_probe")
        return True
    except sqlite3.Error:
        return False
