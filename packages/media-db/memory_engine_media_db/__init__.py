"""media-db: the local store the whole system reads and writes.

Owns the SQLite schema, migrations, full-text index, vector index and the query
API that both agents call. Records go in and come out as contract-shaped
dictionaries; this package never invents a field the contract does not define.
"""

from .database import Database, MediaSummary, QueryError
from .db import MigrationError, SCHEMA_VERSION, connect, current_version, migrate
from .vectors import Neighbour, VectorError, VectorIndex, cosine_distance

__all__ = [
    "Database",
    "MediaSummary",
    "MigrationError",
    "Neighbour",
    "QueryError",
    "SCHEMA_VERSION",
    "VectorError",
    "VectorIndex",
    "connect",
    "cosine_distance",
    "current_version",
    "migrate",
]
