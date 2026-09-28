import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

# Override with DATABASE_URL (the test suite points this at a throwaway database so
# it can never touch local development data).
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./test.db")
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Additive and nullable so existing rows remain valid.
_TASK_AI_COLUMNS: dict[str, str] = {
    "due_date": "DATETIME",
    "assignees": "TEXT",
    "subtasks": "TEXT",
    "context": "VARCHAR(2000)",
    "effort_hours": "FLOAT",
    "category": "VARCHAR(100)",
    "project": "VARCHAR(200)",
    "area_of_focus": "VARCHAR(200)",
    "skills_required": "TEXT",
    "urgency": "VARCHAR(10)",
    "priority_score": "FLOAT",
    "impact_score": "FLOAT",
    "feasibility_score": "FLOAT",
    "confidence_level": "FLOAT",
    "created_from": "VARCHAR(50) NOT NULL DEFAULT 'ui'",
    # Actual behaviour (Phase 2 prerequisite): a timestamped completion and the real
    # effort, stored alongside - never replacing - the AI estimate in effort_hours.
    "completed_at": "DATETIME",
    "actual_effort_hours": "FLOAT",
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