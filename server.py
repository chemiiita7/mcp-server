"""
server.py — Punto de entrada GENÉRICO del MCP del Oracle VPS Lab.

No contiene ninguna herramienta. Solo:
  1. Lee la configuración (core/config.py)
  2. Inicializa la base de datos (core/db.py)
  3. Descubre y registra los módulos de tools/ (core/loader.py)
  4. Arranca el servidor HTTP

Para añadir o mejorar herramientas NO hace falta tocar este archivo:
crea/edita un .py en tools/ con una función register_tools(mcp)
y reinicia el contenedor (docker compose restart mcp-lab-server).
"""
import logging
import os

from core.config import settings

# Mantener la corrección de compatibilidad: FastMCP lee host/puerto del entorno.
os.environ["FASTMCP_HOST"] = settings.host
os.environ["FASTMCP_PORT"] = str(settings.port)

try:  # SDK oficial 1.x (lo fijado en requirements.txt)
    from mcp.server.fastmcp import FastMCP
except ImportError:
    try:  # paquete fastmcp v2+
        from fastmcp import FastMCP
    except ImportError:  # SDK oficial 2.x: FastMCP pasó a llamarse MCPServer
        from mcp.server.mcpserver import MCPServer as FastMCP

from core import db
from core.loader import load_tools

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("server")


def build_server() -> FastMCP:
    mcp = FastMCP(settings.server_name)
    db.init_schema()
    report = load_tools(mcp, profile=settings.profile)
    log.info("Perfil '%s' | módulos cargados: %s", settings.profile, report["loaded"] or "-")
    if report["skipped"]:
        log.info("Módulos omitidos por permisos: %s", report["skipped"])
    if report["failed"]:
        log.error("Módulos con error (el resto funciona): %s", report["failed"])
    return mcp


def main() -> None:
    mcp = build_server()
    log.info("Arrancando en %s:%s (%s)", settings.host, settings.port, settings.transport)
    try:
        # fastmcp v2 acepta host/port como argumentos
        mcp.run(transport=settings.transport, host=settings.host, port=settings.port)
    except TypeError:
        # SDK oficial: los toma de FASTMCP_HOST / FASTMCP_PORT
        mcp.run(transport=settings.transport)


if __name__ == "__main__":
    main()
