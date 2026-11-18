"""Herramientas de la bóveda de Obsidian (/vault).

Las rutas pasan por core/vault.py: nada fuera de la bóveda y nada de las carpetas
vetadas (MCP_VAULT_DENY, por defecto 99_Private).
"""
from core.config import settings
from core.vault import listar_md, safe_path


def _nota(note_path: str):
    return safe_path(note_path if note_path.endswith(".md") else f"{note_path}.md")


def register_tools(mcp):

    @mcp.tool()
    def list_obsidian_notes(subfolder: str = "") -> list[str]:
        """Lista las notas .md de la bóveda (o de una subcarpeta), con rutas relativas."""
        base = safe_path(subfolder)
        if not base.is_dir():
            return []
        vault = settings.vault_path.resolve()
        return sorted(p.relative_to(vault).as_posix() for p in listar_md(base))

    @mcp.tool()
    def read_obsidian_note(note_path: str) -> str:
        """Devuelve el contenido íntegro de una nota. note_path es relativo a la bóveda."""
        path = _nota(note_path)
        if not path.is_file():
            return f"No existe la nota: {note_path}"
        return path.read_text(encoding="utf-8")

    @mcp.tool()
    def append_to_obsidian_note(note_path: str, content: str) -> str:
        """Añade contenido al final de una nota; la crea (y sus carpetas) si no existe."""
        path = _nota(note_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if path.exists() and path.stat().st_size > 0 else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(prefix + content.rstrip("\n") + "\n")
        return f"OK: escrito en {path.relative_to(settings.vault_path.resolve()).as_posix()}"
