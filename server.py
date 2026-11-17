"""
server.py — Punto de entrada GENÉRICO del MCP del Oracle VPS Lab.

No contiene ninguna herramienta. Solo:
  1. Lee la configuración (core/config.py)
  2. Descubre y registra los módulos de tools/ (core/loader.py)
  3. Arranca el servidor HTTP

La base de datos no se toca aquí: la crea y la rellena vps-didac/ingesta, y este
servidor solo la lee (core/db.py).

Para añadir o mejorar herramientas NO hace falta tocar este archivo:
crea/edita un .py en tools/ con una función register_tools(mcp)
y reinicia el contenedor (docker compose restart mcp-lab-server).
"""
import logging
import os

from core.config import settings

# Cinturón y tirantes: las versiones viejas de FastMCP leían host/puerto de aquí.
# Desde mcp 1.30 esto se ignora, así que lo de verdad es pasárselo al constructor
# (ver _nuevo_servidor); se deja porque no estorba y cubre versiones antiguas.
os.environ["FASTMCP_HOST"] = settings.host
os.environ["FASTMCP_PORT"] = str(settings.port)

try:  # SDK oficial 1.x (lo fijado en requirements.txt)
    from mcp.server.fastmcp import FastMCP
except ImportError:
    try:  # paquete fastmcp v2+
        from fastmcp import FastMCP
    except ImportError:  # SDK oficial 2.x: FastMCP pasó a llamarse MCPServer
        from mcp.server.mcpserver import MCPServer as FastMCP

from core.loader import load_tools

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")


def _nuevo_servidor() -> FastMCP:
    """Construye el FastMCP pasándole host y puerto explícitamente.

    mcp 1.30 ignora FASTMCP_HOST, así que sin esto el servidor escucha siempre en
    127.0.0.1 y MCP_HOST no sirve de nada. El SDK oficial acepta host/port como
    settings del constructor; si alguna versión no los acepta, se cae al
    comportamiento de antes en vez de reventar.
    """
    try:
        return FastMCP(settings.server_name, host=settings.host, port=settings.port)
    except TypeError:
        log.warning(
            "Esta versión de FastMCP no acepta host/port en el constructor: "
            "el servidor puede ignorar MCP_HOST=%s", settings.host,
        )
        return FastMCP(settings.server_name)


def build_server() -> FastMCP:
    mcp = _nuevo_servidor()
    report = load_tools(mcp, profile=settings.profile)
    log.info("Perfil '%s' | módulos cargados: %s", settings.profile, report["loaded"] or "-")
    if report["skipped"]:
        log.info("Módulos omitidos por permisos: %s", report["skipped"])
    if report["failed"]:
        log.error("Módulos con error (el resto funciona): %s", report["failed"])
    return mcp


def main() -> None:
    mcp = build_server()
    efectivo = getattr(getattr(mcp, "settings", None), "host", settings.host)
    log.info("Arrancando en %s:%s (%s)", efectivo, settings.port, settings.transport)
    if efectivo != settings.host:
        log.warning("MCP_HOST pide %s pero el servidor escuchará en %s", settings.host, efectivo)
    try:
        # fastmcp v2 acepta host/port como argumentos
        mcp.run(transport=settings.transport, host=settings.host, port=settings.port)
    except TypeError:
        # SDK oficial: los toma de FASTMCP_HOST / FASTMCP_PORT
        mcp.run(transport=settings.transport)


if __name__ == "__main__":
    main()
