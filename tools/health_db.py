"""Herramientas de consulta sobre personal_data.db (solo lectura)."""
from core import db
from core.config import settings


def register_tools(mcp):

    @mcp.tool()
    def query_personal_db(sql_query: str) -> list[dict] | str:
        """Ejecuta una consulta SELECT de solo lectura sobre personal_data.db.
        Tablas: hevy_sets, garmin_daily, cardio_activities. Devuelve hasta 100 filas."""
        sql = sql_query.strip().rstrip(";")
        first = sql.split(None, 1)[0].upper() if sql else ""
        if first not in ("SELECT", "WITH"):
            return "Error: solo se permiten consultas SELECT."
        if ";" in sql:
            return "Error: una sola sentencia por consulta."
        try:
            # Doble protección: además del filtro, la conexión es mode=ro
            return db.fetch_all(sql, limit=settings.max_rows)
        except Exception as exc:
            return f"Error SQL: {exc}"
