"""
PLANTILLA para un nuevo módulo de herramientas.
Los archivos que empiezan por "_" NO se cargan. Para usarla:
  cp tools/_plantilla.py tools/mi_dominio.py
  (añadir "mi_dominio" a los perfiles de permissions.json que deban verlo)
  docker compose restart mcp-lab-server
"""
from core import db              # si necesitas la BD (solo lectura)
from core.config import settings  # rutas, límites...


def register_tools(mcp):

    @mcp.tool()
    def ejemplo_saludo(nombre: str) -> str:
        """Docstring = descripción que verá el LLM. Sé claro sobre qué hace y qué devuelve."""
        return f"Hola {nombre}"
