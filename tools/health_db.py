"""Herramientas de consulta sobre personal_data.db (solo lectura)."""
from core import db
from core.config import settings


def register_tools(mcp):

    @mcp.tool()
    def query_personal_db(sql_query: str) -> list[dict] | str:
        """Ejecuta una consulta SELECT de solo lectura sobre personal_data.db. Devuelve hasta 100 filas.

        Tablas (las crea y rellena vps-didac/ingesta cada pocas horas):
        - hevy_sets: una fila por serie de fuerza. workout_id, date (local, YYYY-MM-DD),
          start_time (UTC), workout_title, exercise_index, exercise_title, exercise_template_id,
          set_index, set_type (normal/warmup/failure/dropset), weight_kg, reps, rpe, distance_m, duration_s.
        - garmin_daily: una fila por día. date, sleep_score, sleep_hours, hrv_avg (ms, noche),
          resting_hr, body_battery_max, body_battery_min, steps, stress_avg.
        - cardio_activities: actividades de Garmin salvo fuerza. activity_id, date, start_time (local),
          name, sport_type, duration_min, distance_km, avg_hr, max_hr, calories, elevation_gain_m, tss.
        - sync_state: última sincronización de cada fuente (source, last_ok, last_error).
        """
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

    @mcp.tool()
    def ingest_status() -> list[dict] | str:
        """Estado de la ingesta de Hevy y Garmin: última pasada correcta, último error y
        rango de fechas que hay en la BD. Úsalo antes de sacar conclusiones si faltan datos."""
        try:
            estado = {r["source"]: r for r in db.fetch_all("SELECT * FROM sync_state")}
            rangos = db.fetch_all(
                "SELECT 'hevy' AS source, MIN(date) AS desde, MAX(date) AS hasta, COUNT(*) AS filas FROM hevy_sets "
                "UNION ALL SELECT 'garmin', MIN(date), MAX(date), COUNT(*) FROM garmin_daily "
                "UNION ALL SELECT 'garmin_cardio', MIN(date), MAX(date), COUNT(*) FROM cardio_activities"
            )
        except Exception as exc:
            return f"Error leyendo el estado: {exc}"
        for r in rangos:
            fuente = "garmin" if r["source"].startswith("garmin") else r["source"]
            s = estado.get(fuente) or {}
            r.update(last_ok=s.get("last_ok"), last_error=s.get("last_error"))
        return rangos
