"""Parte diario del sistema (sueño, recuperación y entrenos del día) en 02_Daily/_sistema.

El servicio nota-diaria lo regenera solo cuando la ingesta trae datos (ver
docker-compose.yml); esta tool sirve para forzarlo a mano.
"""
from datetime import date

from core import parte_diario


def register_tools(mcp):

    @mcp.tool()
    def generar_parte_diario(fecha: str = "") -> str:
        """Genera o regenera la nota del sistema de un día (sueño, HRV, FC en reposo, Body
        Battery con su media de 7 días, semáforo de recuperación, entrenos y actividad de ese
        mismo día) en 02_Daily/_sistema/YYYY-MM-DD.md, y devuelve su contenido. fecha:
        YYYY-MM-DD; vacío = hoy. Se genera sola desde las 9:00 cada vez que la ingesta trae
        datos: úsala para forzarla a mano. Nunca toca las notas del usuario."""
        try:
            dia = date.fromisoformat(fecha) if fecha else parte_diario.hoy()
        except ValueError:
            return "Error: la fecha va en formato YYYY-MM-DD."
        try:
            rel, resultado, texto = parte_diario.escribir(dia)
        except Exception as exc:
            return f"Error generando el parte: {type(exc).__name__}: {exc}"
        return f"{rel}: {resultado}\n\n{texto}"
