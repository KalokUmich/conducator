"""TODOService — DuckDB-backed room-scoped task tracking."""
import logging
import uuid
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS todos (
    id          VARCHAR PRIMARY KEY,
    room_id     VARCHAR NOT NULL,
    title       VARCHAR NOT NULL,
    description VARCHAR,
    type        VARCHAR NOT NULL DEFAULT 'task',
    priority    VARCHAR NOT NULL DEFAULT 'medium',
    status      VARCHAR NOT NULL DEFAULT 'open',
    file_path   VARCHAR,
    line_number INTEGER,
    created_by  VARCHAR NOT NULL DEFAULT '',
    assignee    VARCHAR,
    created_at  TIMESTAMP NOT NULL,
    source      VARCHAR NOT NULL DEFAULT 'manual',
    source_id   VARCHAR
)
"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_todos_room ON todos(room_id)"


class TODOService:
    """Singleton service for managing room-scoped TODOs in DuckDB.

    All writes are synchronous (DuckDB is embedded and very fast for this
    volume of data).
    """

    _instance: Optional["TODOService"] = None
    _default_db_path: str = "todos.duckdb"

    def __init__(self, db_path: Optional[str] = None) -> None:
        import duckdb
        self._db_path = db_path or self._default_db_path
        self._conn = duckdb.connect(self._db_path)
        self._conn.execute(_CREATE_TABLE)
        self._conn.execute(_INDEX)
        logger.info("[TODOService] Initialized with db=%s", self._db_path)

    @classmethod
    def get_instance(cls, db_path: Optional[str] = None) -> "TODOService":
        if cls._instance is None:
            cls._instance = cls(db_path)
        return cls._instance

    # -----------------------------------------------------------------------
    # CRUD
    # -----------------------------------------------------------------------

    def create(
        self,
        room_id: str,
        title: str,
        description: Optional[str] = None,
        type_: str = "task",
        priority: str = "medium",
        file_path: Optional[str] = None,
        line_number: Optional[int] = None,
        created_by: str = "",
        assignee: Optional[str] = None,
        source: str = "manual",
        source_id: Optional[str] = None,
    ) -> dict:
        todo_id = str(uuid.uuid4())
        now = datetime.utcnow()
        self._conn.execute(
            """
            INSERT INTO todos
              (id, room_id, title, description, type, priority, status,
               file_path, line_number, created_by, assignee, created_at,
               source, source_id)
            VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                todo_id, room_id, title, description, type_, priority,
                file_path, line_number, created_by, assignee, now,
                source, source_id,
            ],
        )
        return self._row_to_dict(self._conn.execute(
            "SELECT * FROM todos WHERE id = ?", [todo_id]
        ).fetchone())

    def list_by_room(self, room_id: str) -> List[dict]:
        rows = self._conn.execute(
            "SELECT * FROM todos WHERE room_id = ? ORDER BY created_at ASC",
            [room_id],
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(self, todo_id: str, **kwargs) -> Optional[dict]:
        allowed = {
            "title", "description", "priority", "status",
            "file_path", "line_number", "assignee",
        }
        fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not fields:
            return self.get(todo_id)

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [todo_id]
        self._conn.execute(
            f"UPDATE todos SET {set_clause} WHERE id = ?", values
        )
        return self.get(todo_id)

    def get(self, todo_id: str) -> Optional[dict]:
        row = self._conn.execute(
            "SELECT * FROM todos WHERE id = ?", [todo_id]
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def delete(self, todo_id: str) -> bool:
        result = self._conn.execute(
            "DELETE FROM todos WHERE id = ? RETURNING id", [todo_id]
        ).fetchone()
        return result is not None

    def delete_by_room(self, room_id: str) -> int:
        result = self._conn.execute(
            "DELETE FROM todos WHERE room_id = ? RETURNING id", [room_id]
        ).fetchall()
        return len(result)

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    _COLUMNS = [
        "id", "room_id", "title", "description", "type", "priority", "status",
        "file_path", "line_number", "created_by", "assignee", "created_at",
        "source", "source_id",
    ]

    def _row_to_dict(self, row) -> dict:
        d = dict(zip(self._COLUMNS, row))
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
        return d
