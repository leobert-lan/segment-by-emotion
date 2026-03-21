import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def session(self):
        connection = self.connect()
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self, schema_path: Path) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.session() as connection:
            schema_sql = schema_path.read_text(encoding="utf-8")
            connection.executescript(schema_sql)

