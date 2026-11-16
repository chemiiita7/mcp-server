"""Herramientas de la bóveda de Obsidian (/vault)."""
from pathlib import Path

from core.config import settings


def _safe_path(relative: str) -> Path:
    """Resuelve una ruta dentro de la bóveda e impide salir de ella (../../etc)."""
    vault = settings.vault_path.resolve()
    target = (vault / relative).resolve()
    if target != vault and vault not in target.parents:
        raise ValueError("Ruta fuera de la bóveda")
    return target


def register_tools(mcp):

    @mcp.tool()
    def list_obsidian_notes(subfolder: str = "") -> list[str]:
        """Lista las notas .md de la bóveda (o de una subcarpeta), con rutas relativas."""
        base = _safe_path(subfolder)
        if not base.is_dir():
            return []
        vault = settings.vault_path.resolve()
        return sorted(
            str(p.relative_to(vault))
            for p in base.rglob("*.md")
            if ".obsidian" not in p.parts and ".trash" not in p.parts
        )

    @mcp.tool()
    def read_obsidian_note(note_path: str) -> str:
        """Devuelve el contenido íntegro de una nota. note_path es relativo a la bóveda."""
        path = _safe_path(note_path if note_path.endswith(".md") else f"{note_path}.md")
        if not path.is_file():
            return f"No existe la nota: {note_path}"
        return path.read_text(encoding="utf-8")

    @mcp.tool()
    def append_to_obsidian_note(note_path: str, content: str) -> str:
        """Añade contenido al final de una nota; la crea (y sus carpetas) si no existe."""
        path = _safe_path(note_path if note_path.endswith(".md") else f"{note_path}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = "\n" if path.exists() and path.stat().st_size > 0 else ""
        with path.open("a", encoding="utf-8") as f:
            f.write(prefix + content.rstrip("\n") + "\n")
        return f"OK: escrito en {path.relative_to(settings.vault_path.resolve())}"
