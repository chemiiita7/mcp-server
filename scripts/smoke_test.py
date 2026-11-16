"""Comprobación de humo para CI: el servidor arranca y todos los módulos cargan.

Construye el servidor MCP completo (configuración + esquema SQLite + carga de tools/)
sobre directorios temporales, sin abrir ningún puerto. Si un módulo de tools/ está
roto, el cargador lo apunta en report["failed"] y aquí falla el build, así que un
.py mal escrito nunca llega al VPS.

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
    Path(os.environ["MCP_VAULT_PATH"]).mkdir(parents=True, exist_ok=True)

    from core.config import settings
    from core.loader import discover, load_tools
    from core import db
    from server import build_server  # noqa: F401  (verifica que el entrypoint importa)

    descubiertos = [n.removeprefix("tools.") for n in discover()]
    print(f"módulos descubiertos: {descubiertos}")
    if not descubiertos:
        print("ERROR: el cargador no ha encontrado ningún módulo en tools/")
        return 1

    db.init_schema()
    print(f"esquema SQLite creado en {settings.db_path}")

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

    print("OK: todos los módulos cargan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
