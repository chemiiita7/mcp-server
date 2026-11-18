"""Prueba sin red del parte diario y de la carpeta privada de la bóveda.

Monta una bóveda y una BD temporales (con las tablas que crea vps-didac/ingesta) y
comprueba:
  - que 99_Private no se lista, no se lee y no se escribe, ni con trucos de ruta;
  - que el parte sale con el semáforo correcto en cada caso, en la carpeta del sistema,
    y que nunca pisa una nota que no ha escrito él.

Los datos de garmin_daily son reales (noviembre de 2026).
Uso:  python scripts/test_parte_diario.py
"""
import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

tmp = Path(tempfile.mkdtemp(prefix="parte-test-"))
VAULT = tmp / "vault"
os.environ["MCP_VAULT_PATH"] = str(VAULT)
os.environ["MCP_DB_PATH"] = str(tmp / "personal_data.db")
os.environ["MCP_TZ"] = "Europe/Madrid"

ESQUEMA = """
CREATE TABLE garmin_daily (date TEXT PRIMARY KEY, sleep_score INTEGER, sleep_hours REAL, hrv_avg REAL,
    resting_hr INTEGER, body_battery_max INTEGER, body_battery_min INTEGER, steps INTEGER,
    stress_avg INTEGER, synced_at TEXT);
CREATE TABLE garmin_activities (activity_id TEXT PRIMARY KEY, date TEXT, start_time TEXT, start_time_utc TEXT,
    end_time_utc TEXT, name TEXT, sport_type TEXT, duration_min REAL, distance_km REAL, avg_hr INTEGER,
    max_hr INTEGER, calories REAL, elevation_gain_m REAL, tss REAL);
CREATE TABLE hevy_workouts (workout_id TEXT PRIMARY KEY, date TEXT, start_time TEXT, end_time TEXT,
    duration_min REAL, title TEXT, garmin_activity_id TEXT);
CREATE TABLE sesiones_fuerza (workout_id TEXT, date TEXT, start_time TEXT, title TEXT, duration_min REAL,
    ejercicios INTEGER, series INTEGER, volumen_kg REAL, garmin_activity_id TEXT, garmin_sport_type TEXT,
    avg_hr INTEGER, max_hr INTEGER, calories REAL);
CREATE TABLE sync_state (source TEXT PRIMARY KEY, cursor TEXT, last_run TEXT, last_ok TEXT,
    last_error TEXT, last_rows INTEGER);
INSERT INTO sync_state (source, last_ok) VALUES ('garmin', '2026-11-18T08:44:55Z');
"""

# date, sleep_score, sleep_hours, hrv, resting_hr, bb_max, bb_min, steps, stress
REALES = [
    ("2026-11-09", 83, 8.23, 65, 46, 100, 32, 4010, 24),
    ("2026-11-10", 64, 6.32, 68, 47, 92, 13, 10258, 28),
    ("2026-11-11", 79, 7.33, 62, 48, 76, 13, 3544, 26),
    ("2026-11-12", 79, 6.83, 62, 48, 86, 25, 1708, 22),
    ("2026-11-13", 73, 7.39, 63, 48, 91, 5, 7397, 37),
    ("2026-11-14", 52, 5.98, 43, 52, 20, 5, 6295, 45),
    ("2026-11-15", 87, 8.7, 64, 47, 90, 5, 2383, 23),
    ("2026-11-16", 62, 8.6, 59, 50, 92, 23, 2627, 32),
    ("2026-11-17", 74, 7.63, 53, 50, 72, 12, 3946, 41),
    ("2026-11-18", None, None, None, 53, 34, 18, None, 22),   # a las 03:44, antes de despertar
]


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print(f"  ok  {msg}")


def bd(sql: str, params: tuple = ()):
    with sqlite3.connect(os.environ["MCP_DB_PATH"]) as c:
        c.execute(sql, params)


def privado():
    from core.vault import RutaNoPermitida, listar_md, safe_path

    (VAULT / "99_Private").mkdir(parents=True)
    (VAULT / "99_Private" / "diario.md").write_text("secreto")
    (VAULT / "02_Daily").mkdir()
    (VAULT / "02_Daily" / "2026-11-18.md").write_text("mi día")
    (VAULT / "02_Daily" / "atajo").symlink_to(VAULT / "99_Private")

    vistas = sorted(p.relative_to(VAULT).as_posix() for p in listar_md(VAULT))
    check(vistas == ["02_Daily/2026-11-18.md"], f"la lista no enseña 99_Private ni el atajo ({vistas})")
    for ruta in ("99_Private/diario.md", "99_private/diario.md", "99_Private", "02_Daily/../99_Private/diario.md",
                 "02_Daily/atajo/diario.md", "../etc/passwd", "99_Private/nueva.md"):
        try:
            safe_path(ruta)
        except RutaNoPermitida as exc:
            check(str(exc) == "Ruta fuera de la bóveda", f"vetada: {ruta}")
        else:
            raise AssertionError(f"debería estar vetada: {ruta}")
    check(safe_path("02_Daily/2026-11-18.md").read_text() == "mi día", "el resto de la bóveda se lee normal")
    check(safe_path("") == VAULT.resolve(), "la raíz de la bóveda se puede listar")


def parte():
    with sqlite3.connect(os.environ["MCP_DB_PATH"]) as c:
        c.executescript(ESQUEMA)
        c.executemany("INSERT INTO garmin_daily VALUES (?,?,?,?,?,?,?,?,?,NULL)", REALES)

    from core import parte_diario as pd

    # 18/11 a las 03:44: aún sin sueño
    rel, res, txt = pd.escribir(date(2026, 11, 18))
    check(rel == "02_Daily/_sistema/2026-11-18.md" and res == "creada", "la nota va a 02_Daily/_sistema")
    check("estado: pendiente" in txt and "[!info] ⏳" in txt, "sin sueño todavía → pendiente")
    check(pd.escribir(date(2026, 11, 18))[1] == "sin cambios", "si nada cambia no se reescribe")

    # Llega el sueño del 18: noche corta tras... no, la del 17 fue buena → amarillo, no rojo
    bd("UPDATE garmin_daily SET sleep_score=55, sleep_hours=5.5, hrv_avg=50 WHERE date='2026-11-18'")
    rel, res, txt = pd.escribir(date(2026, 11, 18))
    check(res == "actualizada", "cuando llega el sueño, la nota se actualiza")
    check("estado: rojo" in txt and "HRV a la baja 3 días seguidos (64 → 59 → 53 → 50)" in txt,
          "HRV bajando 3 días → rojo (guardarraíl)")
    check("# Parte del día" in txt and "## Sueño y recuperación" in txt and "## Entrenos" in txt
          and "## Actividad del día" in txt and "## Ayer" not in txt, "encabezados del embed")

    # 17/11 con los datos reales: HRV 53 frente a ~60 de media y 2 días bajando → amarillo
    _, _, txt = pd.escribir(date(2026, 11, 17))
    check("estado: amarillo" in txt, "17/11 real → amarillo")
    check("HRV a la baja 2 días seguidos (64 → 59 → 53)" in txt, "aviso de HRV bajando 2 días")
    check("| Sueño | 7 h 38 min · 74 |" in txt, "sueño en horas y minutos")
    check("pasos: 3946" in txt and "- 3.946 pasos · estrés medio 41 · Body Battery mínimo 12" in txt,
          "pasos y estrés del mismo día, con punto de miles")

    # 15/11: durmió 5,98 h el 14 pero 8,7 el 15 → una sola noche corta no es rojo
    _, _, txt = pd.escribir(date(2026, 11, 15))
    check("estado: verde" in txt, "15/11 real → verde")

    # Dos noches por debajo de 6 h → rojo
    bd("UPDATE garmin_daily SET sleep_hours=5.8 WHERE date='2026-11-13'")
    _, _, txt = pd.escribir(date(2026, 11, 14))
    check("estado: rojo" in txt and "Dos noches seguidas por debajo de 6 h" in txt, "dos noches < 6 h → rojo")

    # Entrenos del día: sesión de Hevy con Garmin cruzado + carrera suelta, en la nota del 16
    bd("INSERT INTO hevy_workouts VALUES ('w1','2026-11-16','2026-11-16T17:00:00Z',NULL,63,'Push','g1')")
    bd("INSERT INTO sesiones_fuerza VALUES ('w1','2026-11-16','2026-11-16T17:00:00Z','Push',63,6,18,8450,"
       "'g1','strength_training',112,150,450)")
    bd("INSERT INTO garmin_activities (activity_id,date,start_time,sport_type,duration_min,avg_hr) VALUES "
       "('g1','2026-11-16','2026-11-16 18:00:00','strength_training',70,112)")
    bd("INSERT INTO garmin_activities (activity_id,date,start_time,sport_type,duration_min,distance_km,avg_hr)"
       " VALUES ('g2','2026-11-16','2026-11-16 08:00:00','running',45,8.12,149)")
    _, _, txt17 = pd.escribir(date(2026, 11, 17))
    check("- Sin entrenos registrados por ahora" in txt17, "el entreno del 16 no sale en la nota del 17")
    _, _, txt = pd.escribir(date(2026, 11, 16))
    check("- **Push** · 63 min · 6 ejercicios · 18 series · 8.450 kg · ❤️ 112 lpm · 450 kcal" in txt,
          "sesión de fuerza en la nota de su mismo día")
    check("- **Carrera** · 8,1 km · 45 min · ❤️ 149 lpm" in txt and txt.count("Fuerza") == 0,
          "la carrera sale; la fuerza de Garmin no se repite")
    check("entrenos: 2" in txt, "frontmatter cuenta los entrenos del día")

    # Hoy no se crea antes de las 9:00; ayer sí se sigue completando
    from datetime import datetime
    from zoneinfo import ZoneInfo
    madrid = ZoneInfo("Europe/Madrid")
    check(pd.fechas_a_generar(datetime(2026, 11, 18, 8, 59, tzinfo=madrid)) == [date(2026, 11, 17)],
          "a las 8:59 solo se refresca la de ayer")
    check(pd.fechas_a_generar(datetime(2026, 11, 18, 9, 0, tzinfo=madrid)) == [date(2026, 11, 17), date(2026, 11, 18)],
          "desde las 9:00, también la de hoy")

    # Una nota del usuario en la carpeta del sistema no se pisa nunca
    propia = VAULT / "02_Daily" / "_sistema" / "2026-11-10.md"
    propia.write_text("---\norigen: yo\n---\nmía\n")
    check(pd.escribir(date(2026, 11, 10))[1] == "omitida" and propia.read_text().endswith("mía\n"),
          "una nota sin la marca del sistema no se sobrescribe")
    check(not list((VAULT / "02_Daily" / "_sistema").glob(".syncthing.*")), "no quedan temporales")

    print("\n----- 02_Daily/_sistema/2026-11-16.md -----\n" + txt)


def main() -> int:
    privado()
    parte()
    print("OK: el parte diario y la carpeta privada se comportan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
