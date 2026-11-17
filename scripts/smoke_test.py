"""Comprobación de humo para CI: el servidor arranca y todos los módulos cargan.

Construye el servidor MCP completo (configuración + carga de tools/)
sobre directorios temporales, sin abrir ningún puerto. Si un módulo de tools/ está
roto, el cargador lo apunta en report["failed"] y aquí falla el build, así que un
.py mal escrito nunca llega al VPS.

Comprueba además que MCP_HOST se respeta de verdad: mcp 1.30 dejó de leer la
variable FASTMCP_HOST y el servidor se quedó escuchando en 127.0.0.1 en silencio.
Si una versión futura vuelve a cambiar la API, esto lo caza antes del despliegue.

Uso:  python scripts/smoke_test.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import os

    tmp = tempfile.mkdtemp(prefix="mcp-smoke-")
    os.environ.setdefault("MCP_DB_PATH", str(Path(tmp) / "personal_data.db"))
    os.environ.setdefault("MCP_VAULT_PATH", str(Path(tmp) / "vault"))
    # Valores raros a propósito: si el servidor acaba escuchando en otra cosa,
    # es que la configuración no llega.
    os.environ.setdefault("MCP_HOST", "127.0.0.2")
    os.environ.setdefault("MCP_PORT", "8123")
    Path(os.environ["MCP_VAULT_PATH"]).mkdir(parents=True, exist_ok=True)

    from core.config import settings
    from core.loader import discover, load_tools
    from server import build_server

    descubiertos = [n.removeprefix("tools.") for n in discover()]
    print(f"módulos descubiertos: {descubiertos}")
    if not descubiertos:
        print("ERROR: el cargador no ha encontrado ningún módulo en tools/")
        return 1

    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        from fastmcp import FastMCP

    fallos = {}
    for perfil in ("admin", "agent"):
        report = load_tools(FastMCP(f"smoke-{perfil}"), profile=perfil)
        print(f"perfil {perfil}: cargados={report['loaded']} omitidos={report['skipped']}")
        fallos.update(report["failed"])

    if fallos:
        print("ERROR: módulos que no cargan:")
        for nombre, error in fallos.items():
            print(f"  - {nombre}: {error}")
        return 1

    servidor = build_server()
    conf = getattr(servidor, "settings", None)
    if conf is None:
        print("AVISO: esta versión de FastMCP no expone .settings; MCP_HOST sin comprobar")
    else:
        if (conf.host, conf.port) != (settings.host, settings.port):
            print(
                f"ERROR: MCP_HOST/MCP_PORT piden {settings.host}:{settings.port} "
                f"pero el servidor escucharía en {conf.host}:{conf.port}. "
                "La API de FastMCP ha vuelto a cambiar: pásale host/port como toque ahora."
            )
            return 1
        print(f"OK: el servidor escucharía en {conf.host}:{conf.port}, como pide la configuración.")

    print("OK: todos los módulos cargan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
