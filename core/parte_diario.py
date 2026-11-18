"""Parte diario: la nota del sistema con el sueño, la recuperación y los entrenos del día.

Se escribe en <bóveda>/<MCP_DAILY_DIR>/YYYY-MM-DD.md (por defecto 02_Daily/_sistema) y
tu nota de diario, que es tuya, solo la incrusta:

    ![[02_Daily/_sistema/2026-11-18#Parte del día]]

Todo es determinista: medias, deltas y semáforo salen de reglas fijas en Python, sin
LLM. El frontmatter lleva las métricas como propiedades para poder hacer después
calendarios y heatmaps con Dataview.

Fechas: garmin_daily.date = D guarda el sueño y el HRV de la noche que TERMINA la
mañana de D (así lo da Garmin), y el pulso en reposo, el Body Battery, los pasos y el
estrés del día D. Los entrenos y actividades de D van en la nota de D.

Cuándo se escribe: la nota de hoy no existe antes de MCP_DAILY_DESDE (09:00, la primera
franja de la ingesta). A partir de ahí el servicio la regenera cada MCP_DAILY_INTERVAL_MIN
(2) y solo la reescribe si algo ha cambiado, o sea, cuando la ingesta trae datos: a las
9, en los reintentos del sueño, a las 12, 17 y 22, y al llegar un entreno de Hevy. La de
ayer se sigue refrescando para recoger lo último del día (la franja de las 22 y lo que
la ingesta repasa a la mañana siguiente).

Solo lee la BD (core/db.py, mode=ro) y solo escribe en MCP_DAILY_DIR. Si en esa
carpeta hay una nota sin la marca "origen: sistema" en el frontmatter, no la toca.

CLI (en el VPS, dentro del contenedor):
    python -m core.parte_diario                       # la de hoy, una vez
    python -m core.parte_diario --fecha 2026-11-17
    python -m core.parte_diario loop                  # servicio: ayer y (desde las 9) hoy
"""
import argparse
import logging
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core import db
from core.config import settings
from core.vault import atomic_write, safe_path

log = logging.getLogger("parte")

TITULO = "Parte del día"           # el ancla del embed: no cambiarla sin cambiar la plantilla
MARCA = "origen: sistema"

# Umbrales del semáforo. Rojo = guardarraíles fisiológicos del proyecto.
SUENO_MIN_H = 6.0                  # < 6 h dos noches seguidas → rojo; una noche → amarillo
SUENO_PUNTUACION_BAJA = 60
HRV_CAIDA_PCT = 10                 # HRV un 10 % por debajo de la media de 7 días → amarillo
FC_REPOSO_SUBIDA = 5               # lpm por encima de la media → amarillo
MIN_DIAS_MEDIA = 3                 # con menos días de historia no se calcula media

ESTADOS = {
    "verde": ("success", "🟢 Recuperación buena", "Luz verde: entrena según el plan."),
    "amarillo": ("warning", "🟡 Recuperación moderada", "Entrena, pero sin buscar marcas: RPE máximo 7–8."),
    "rojo": ("danger", "🔴 Recuperación baja",
             "Guardarraíl: día de recuperación activa, o la sesión prevista con el RPE rebajado."),
    "pendiente": ("info", "⏳ Sueño pendiente de sincronizar", ""),
}

DEPORTES = {
    "running": "Carrera", "treadmill_running": "Cinta", "trail_running": "Trail",
    "cycling": "Bici", "indoor_cycling": "Rodillo", "road_biking": "Bici de carretera",
    "lap_swimming": "Natación (piscina)", "open_water_swimming": "Natación (aguas abiertas)",
    "strength_training": "Fuerza", "walking": "Caminata", "hiking": "Senderismo",
    "yoga": "Yoga", "cardio": "Cardio",
}


def hoy() -> date:
    return datetime.now(ZoneInfo(settings.tz)).date()


# ---------------------------------------------------------------- formato
def _num(v, dec: int = 0) -> str:
    """Número a la española: 8.450 / 48,6."""
    if v is None:
        return "—"
    s = f"{v:,.{dec}f}"
    return s.replace(",", "_").replace(".", ",").replace("_", ".")


def _horas(h) -> str:
    if h is None:
        return "—"
    minutos = round(h * 60)
    return f"{minutos // 60} h {minutos % 60:02d} min"


def _flecha(d: float, umbral: float, texto: str) -> str:
    if abs(d) < umbral:
        return "≈"
    return f"{'▲' if d > 0 else '▼'} {texto}"


def _yaml(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{round(v, 2):g}"
    return str(v)


# ---------------------------------------------------------------- datos
def _seguro(sql: str, params: tuple = ()) -> list[dict]:
    """Consulta que no tumba el parte si una tabla aún no existe (BD recién creada)."""
    try:
        return db.fetch_all(sql, params)
    except Exception as exc:
        log.warning("Consulta fallida (%s): %s", exc, sql.split()[3] if len(sql.split()) > 3 else sql)
        return []


def _media(filas: list[dict], campo: str):
    v = [f[campo] for f in filas if f.get(campo) is not None]
    return sum(v) / len(v) if len(v) >= MIN_DIAS_MEDIA else None


def contexto(fecha: date) -> dict:
    iso = lambda k: (fecha - timedelta(days=k)).isoformat()  # noqa: E731
    dias = {f["date"]: f for f in _seguro(
        "SELECT * FROM garmin_daily WHERE date BETWEEN ? AND ? ORDER BY date", (iso(7), iso(0)))}
    previos = [dias[iso(k)] for k in range(7, 0, -1) if iso(k) in dias]
    vacio = {}
    dia = iso(0)
    sync = _seguro("SELECT last_ok FROM sync_state WHERE source = 'garmin'")
    return {
        "fecha": fecha,
        "hoy": dias.get(iso(0)),
        "media": {c: _media(previos, c) for c in ("sleep_hours", "sleep_score", "hrv_avg", "resting_hr",
                                                   "body_battery_max")},
        "hrv_serie": [dias.get(iso(k), vacio).get("hrv_avg") for k in (3, 2, 1, 0)],
        "sueno_ayer": dias.get(iso(1), vacio).get("sleep_hours"),
        "sesiones": _seguro("SELECT * FROM sesiones_fuerza WHERE date = ? ORDER BY start_time", (dia,)),
        # Las actividades de Garmin que ya van con un entreno de Hevy salen en la sesión
        "actividades": _seguro(
            "SELECT * FROM garmin_activities WHERE date = ? AND activity_id NOT IN"
            " (SELECT garmin_activity_id FROM hevy_workouts WHERE garmin_activity_id IS NOT NULL)"
            " ORDER BY start_time", (dia,)),
        "garmin_ok": sync[0]["last_ok"] if sync else None,
    }


# ---------------------------------------------------------------- reglas
def evaluar(ctx: dict) -> tuple[str, list[str], list[str]]:
    """(estado, motivos que lo explican, avisos que no cambian el estado)."""
    h = ctx["hoy"] or {}
    m = ctx["media"]
    if h.get("sleep_score") is None and h.get("sleep_hours") is None:
        return "pendiente", [], []

    rojo, amarillo, avisos = [], [], []
    horas = h.get("sleep_hours")
    if horas is not None and horas < SUENO_MIN_H:
        if ctx["sueno_ayer"] is not None and ctx["sueno_ayer"] < SUENO_MIN_H:
            rojo.append(f"Dos noches seguidas por debajo de {SUENO_MIN_H:g} h")
        else:
            amarillo.append(f"Menos de {SUENO_MIN_H:g} h de sueño")
    if h.get("sleep_score") is not None and h["sleep_score"] < SUENO_PUNTUACION_BAJA:
        amarillo.append(f"Puntuación de sueño baja ({h['sleep_score']})")

    serie = ctx["hrv_serie"]
    if all(v is not None for v in serie):
        if serie[0] > serie[1] > serie[2] > serie[3]:
            rojo.append("HRV a la baja 3 días seguidos (" + " → ".join(_num(v) for v in serie) + ")")
        elif serie[1] > serie[2] > serie[3]:
            avisos.append("HRV a la baja 2 días seguidos (" + " → ".join(_num(v) for v in serie[1:])
                          + "). Si mañana vuelve a bajar → recuperación activa.")
    hrv, hrv_m = h.get("hrv_avg"), m["hrv_avg"]
    if hrv is not None and hrv_m and hrv < hrv_m * (1 - HRV_CAIDA_PCT / 100):
        amarillo.append(f"HRV un {_num((1 - hrv / hrv_m) * 100)} % por debajo de tu media")
    fc, fc_m = h.get("resting_hr"), m["resting_hr"]
    if fc is not None and fc_m is not None and fc >= fc_m + FC_REPOSO_SUBIDA:
        amarillo.append(f"FC en reposo {_num(fc - fc_m)} lpm por encima de tu media")

    if rojo:
        return "rojo", rojo + amarillo, avisos
    if amarillo:
        return "amarillo", amarillo, avisos
    return "verde", [], avisos


# ---------------------------------------------------------------- render
def _tabla(h: dict, m: dict) -> list[str]:
    filas = ["| Métrica | Hoy | Media 7 d | Δ |", "|---|---|---|---|"]

    sh, ss, mh, ms = h.get("sleep_hours"), h.get("sleep_score"), m["sleep_hours"], m["sleep_score"]
    d = _flecha((sh - mh) * 60, 10, f"{abs(round((sh - mh) * 60))} min") if sh is not None and mh is not None else ""
    filas.append(f"| Sueño | {_horas(sh)} · {_num(ss)} | {_horas(mh)} · {_num(ms)} | {d} |")

    v, mv = h.get("hrv_avg"), m["hrv_avg"]
    d = _flecha((v - mv) / mv * 100, 3, f"{_num(abs(v - mv) / mv * 100)} %") if v is not None and mv else ""
    filas.append(f"| HRV | {_num(v)} ms | {_num(mv)} ms | {d} |")

    v, mv = h.get("resting_hr"), m["resting_hr"]
    d = _flecha(v - mv, 1, _num(abs(v - mv), 1)) if v is not None and mv is not None else ""
    filas.append(f"| FC reposo | {_num(v)} | {_num(mv, 1)} | {d} |")

    v, mv = h.get("body_battery_max"), m["body_battery_max"]
    d = _flecha(v - mv, 3, _num(abs(v - mv))) if v is not None and mv is not None else ""
    filas.append(f"| Body Battery | {_num(v)} | {_num(mv)} | {d} |")
    return filas


def _entrenos(ctx: dict) -> list[str]:
    lineas = []
    for s in ctx["sesiones"]:
        partes = [f"**{s.get('title') or 'Fuerza'}**", f"{_num(s.get('duration_min'))} min"]
        if s.get("ejercicios"):
            partes.append(f"{s['ejercicios']} ejercicios · {_num(s.get('series'))} series")
        if s.get("volumen_kg"):
            partes.append(f"{_num(s['volumen_kg'])} kg")
        if s.get("avg_hr"):
            partes.append(f"❤️ {s['avg_hr']} lpm")
        if s.get("calories"):
            partes.append(f"{_num(s['calories'])} kcal")
        lineas.append("- " + " · ".join(partes))
    for a in ctx["actividades"]:
        nombre = DEPORTES.get(a.get("sport_type") or "", a.get("name") or a.get("sport_type") or "Actividad")
        partes = [f"**{nombre}**"]
        if a.get("distance_km"):
            partes.append(f"{_num(a['distance_km'], 1)} km")
        partes.append(f"{_num(a.get('duration_min'))} min")
        if a.get("avg_hr"):
            partes.append(f"❤️ {a['avg_hr']} lpm")
        if a.get("tss"):
            partes.append(f"TSS {_num(a['tss'])}")
        lineas.append("- " + " · ".join(partes))
    if not lineas:
        lineas.append("- Sin entrenos registrados por ahora")
    return lineas


def _actividad(ctx: dict) -> list[str]:
    h = ctx["hoy"] or {}
    partes = []
    if h.get("steps") is not None:
        partes.append(f"{_num(h['steps'])} pasos")
    if h.get("stress_avg") is not None:
        partes.append(f"estrés medio {h['stress_avg']}")
    if h.get("body_battery_min") is not None:
        partes.append(f"Body Battery mínimo {h['body_battery_min']}")
    return ["- " + " · ".join(partes)] if partes else ["- Sin datos del reloj todavía"]


def _hora_local(ts_utc: str | None) -> str | None:
    if not ts_utc:
        return None
    dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00")).astimezone(ZoneInfo(settings.tz))
    return dt.strftime("%d/%m a las %H:%M")


def render(ctx: dict) -> str:
    fecha = ctx["fecha"]
    h = ctx["hoy"] or {}
    m = ctx["media"]
    estado, motivos, avisos = evaluar(ctx)
    tipo, titulo, consejo = ESTADOS[estado]
    sync = _hora_local(ctx["garmin_ok"])

    props = {
        "fecha": fecha.isoformat(),
        "origen": "sistema",
        "estado": estado,
        "sueno_puntuacion": h.get("sleep_score"),
        "sueno_horas": h.get("sleep_hours"),
        "hrv": h.get("hrv_avg"),
        "hrv_media7": round(m["hrv_avg"], 1) if m["hrv_avg"] is not None else None,
        "fc_reposo": h.get("resting_hr"),
        "body_battery": h.get("body_battery_max"),
        "pasos": h.get("steps"),
        "entrenos": len(ctx["sesiones"]) + len(ctx["actividades"]),
    }
    out = ["---"] + [f"{k}: {_yaml(v)}".rstrip() for k, v in props.items()]
    out += ["tags:", "  - sistema/parte", "---", f"# {TITULO}", "", "## Sueño y recuperación"]

    callout = [f"[!{tipo}] {titulo}"]
    if estado == "pendiente":
        callout.append("El reloj aún no ha subido la noche"
                       + (f" (última sincronización de Garmin: {sync})" if sync else "")
                       + ". Se vuelve a mirar cada 15 min, o cada hora si ya hay actividad de hoy;"
                         " esta nota se completa sola.")
        if h:
            callout += ["", f"Por ahora: FC reposo {_num(h.get('resting_hr'))} · "
                            f"Body Battery {_num(h.get('body_battery_max'))}"]
    else:
        callout += _tabla(h, m)
        if motivos or avisos:
            callout.append("")
            callout += [f"- {x}" for x in motivos] + [f"- ⚠️ {x}" for x in avisos]
        callout += ["", f"**Hoy:** {consejo}"]
    out += ["> " + linea if linea else ">" for linea in callout]

    out += ["", "## Entrenos"] + _entrenos(ctx)
    out += ["", "## Actividad del día"] + _actividad(ctx)
    if sync:
        out += ["", f"_Datos de Garmin sincronizados el {sync}. Esta nota la genera el sistema: "
                    "no la edites, se sobrescribe._"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- escritura
def ruta_relativa(fecha: date) -> str:
    return f"{settings.daily_dir.strip('/')}/{fecha.isoformat()}.md"


def _es_del_sistema(texto: str) -> bool:
    """La marca tiene que estar en el frontmatter, no en cualquier parte del texto."""
    if not texto.startswith("---"):
        return False
    partes = texto.split("---", 2)
    return len(partes) == 3 and MARCA in partes[1].splitlines()


def escribir(fecha: date) -> tuple[str, str, str]:
    """Genera la nota de `fecha`. Devuelve (ruta relativa, resultado, texto).

    resultado: "creada", "actualizada", "sin cambios" o "omitida" (existe y no es del sistema).
    Solo reescribe si cambia algo, para no despertar a Syncthing cada 10 minutos.
    """
    rel = ruta_relativa(fecha)
    path = safe_path(rel)
    texto = render(contexto(fecha))
    if path.exists():
        actual = path.read_text(encoding="utf-8")
        if actual == texto:
            return rel, "sin cambios", texto
        if not _es_del_sistema(actual):
            log.warning("%s existe y no la ha escrito el sistema: no la toco", rel)
            return rel, "omitida", actual
        atomic_write(path, texto)
        return rel, "actualizada", texto
    atomic_write(path, texto)
    return rel, "creada", texto


def fechas_a_generar(ahora: datetime) -> list[date]:
    """Ayer siempre (se completa tarde); hoy, solo desde MCP_DAILY_DESDE."""
    dia = ahora.date()
    fechas = [dia - timedelta(days=1)]
    if ahora.time() >= settings.daily_desde:
        fechas.append(dia)
    return fechas


def loop() -> None:
    log.info("Parte diario en %s: hoy desde las %s, comprobando cada %s min (zona %s)", settings.daily_dir,
             settings.daily_desde.strftime("%H:%M"), settings.daily_interval_min, settings.tz)
    while True:
        for fecha in fechas_a_generar(datetime.now(ZoneInfo(settings.tz))):
            try:
                rel, resultado, _ = escribir(fecha)
                if resultado != "sin cambios":
                    log.info("%s: %s", rel, resultado)
            except Exception:
                log.exception("No he podido generar el parte de %s", fecha)
        time.sleep(settings.daily_interval_min * 60)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m core.parte_diario")
    p.add_argument("modo", nargs="?", choices=["una", "loop"], default="una")
    p.add_argument("--fecha", type=date.fromisoformat, help="YYYY-MM-DD (por defecto, hoy)")
    args = p.parse_args(argv)
    logging.basicConfig(level=settings.log_level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.modo == "loop":
        loop()
        return 0
    rel, resultado, _ = escribir(args.fecha or hoy())
    print(f"{rel}: {resultado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
