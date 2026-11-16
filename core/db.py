"""Gestión de SQLite: conexiones y esquema. Las tools importan de aquí, nunca abren la BD por su cuenta."""
import sqlite3
from contextlib import contextmanager

from core.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS hevy_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id TEXT,
    date TEXT,
    workout_title TEXT,
    exercise_title TEXT,
    set_index INTEGER,
    weight_kg REAL,
    reps INTEGER,
    rpe REAL,
    UNIQUE(workout_id, exercise_title, set_index)
);

CREATE TABLE IF NOT EXISTS garmin_daily (
    date TEXT PRIMARY KEY,
    sleep_score INTEGER,
    sleep_hours REAL,
    hrv_avg REAL,
    resting_hr INTEGER,
    body_battery_max INTEGER,
    steps INTEGER,
    stress_avg INTEGER
);

CREATE TABLE IF NOT EXISTS cardio_activities (
    activity_id TEXT PRIMARY KEY,
    date TEXT,
    sport_type TEXT,
    duration_min REAL,
    distance_km REAL,
    avg_hr INTEGER,
    tss REAL
);
"""


def init_schema() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.db_path) as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect(readonly: bool = True):
    """Conexión con filas tipo dict. readonly=True abre la BD en modo solo lectura a nivel de SQLite."""
    if readonly:
        conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        if not readonly:
            conn.commit()
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = (), limit: int | None = None) -> list[dict]:
    limit = limit or settings.max_rows
    with connect(readonly=True) as conn:
        rows = conn.execute(sql, params).fetchmany(limit)
        return [dict(r) for r in rows]
