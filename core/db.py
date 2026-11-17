"""Acceso a personal_data.db en SOLO LECTURA. Las tools importan de aquí, nunca abren la BD por su cuenta.

El esquema no vive aquí: lo crea, lo migra y lo rellena el repo vps-didac/ingesta
(Hevy y Garmin), que monta la misma carpeta /data. Este servidor nunca escribe en la BD.
Si allí cambia una tabla, actualiza la docstring de query_personal_db en tools/health_db.py.
"""
import sqlite3
from contextlib import contextmanager

from core.config import settings


@contextmanager
def connect():
    """Conexión con filas tipo dict, abierta en modo solo lectura a nivel de SQLite."""
    if not settings.db_path.exists():
        raise FileNotFoundError(
            f"No existe {settings.db_path}: la crea el servicio de ingesta (vps-didac/ingesta) en su primera pasada."
        )
    conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def fetch_all(sql: str, params: tuple = (), limit: int | None = None) -> list[dict]:
    limit = limit or settings.max_rows
    with connect() as conn:
        rows = conn.execute(sql, params).fetchmany(limit)
        return [dict(r) for r in rows]
