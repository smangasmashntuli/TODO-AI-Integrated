from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Additive and nullable so existing rows remain valid.
_TASK_AI_COLUMNS: dict[str, str] = {
    "due_date": "DATETIME",
    "assignees": "TEXT",
    "subtasks": "TEXT",
    "context": "VARCHAR(2000)",
    "effort_hours": "FLOAT",
    "category": "VARCHAR(100)",
    "priority_score": "FLOAT",
    "confidence_level": "FLOAT",
    "created_from": "VARCHAR(50) NOT NULL DEFAULT 'ui'",
}


def run_migrations() -> None:
    """Apply idempotent schema upgrades to a pre-existing SQLite database.

    SQLAlchemy's create_all() does not alter existing tables, so columns added
    to the Todo model are applied here with ALTER TABLE when the table already
    exists. Fresh databases already contain every column (no-op).
    """
    inspector = inspect(engine)
    if "todos" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("todos")}
    with engine.begin() as connection:
        for name, column_ddl in _TASK_AI_COLUMNS.items():
            if name not in existing:
                connection.execute(text(f"ALTER TABLE todos ADD COLUMN {name} {column_ddl}"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()