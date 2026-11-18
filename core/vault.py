"""Acceso a la bóveda de Obsidian: rutas seguras, carpetas vetadas y escritura atómica.

Toda tool que toque /vault pasa por aquí. Dos reglas:
  1. Nada fuera de la bóveda (../../etc, enlaces simbólicos que salen de ella).
  2. Nada dentro de las carpetas de MCP_VAULT_DENY (por defecto 99_Private). Ni se
     listan ni se leen ni se escriben, y el error es el mismo que para una ruta fuera
     de la bóveda: no se revela qué hay ni si existe.

Esto es la segunda capa. La primera es de Docker: en docker-compose.yml un tmpfs vacío
tapa /vault/99_Private, así que el contenedor ni siquiera tiene esos archivos delante
(y deploy.sh comprueba que sigue tapado). Si alguna vez una tool ejecuta comandos o un
módulo nuevo se salta esta función, sigue sin poder verlos.
"""
import os
from pathlib import Path

from core.config import settings

IGNORADAS = {".obsidian", ".trash", ".stfolder", ".stversions"}


class RutaNoPermitida(ValueError):
    pass


def _vault() -> Path:
    return settings.vault_path.resolve()


def _relativa(path: Path) -> str | None:
    """Ruta relativa a la bóveda en minúsculas, o None si está fuera."""
    try:
        return path.resolve().relative_to(_vault()).as_posix().casefold()
    except ValueError:
        return None


def vetada(path: Path) -> bool:
    """True si la ruta está fuera de la bóveda o dentro de una carpeta vetada.

    Se compara sin distinguir mayúsculas: en Windows y macOS "99_private" es la misma
    carpeta, y Syncthing puede traerla con cualquiera de las dos grafías.
    """
    rel = _relativa(path)
    if rel is None:
        return True
    for carpeta in settings.vault_deny:
        c = carpeta.strip("/").casefold()
        if c and (rel == c or rel.startswith(c + "/")):
            return True
    return False


def safe_path(relative: str) -> Path:
    """Resuelve una ruta relativa a la bóveda. Lanza RutaNoPermitida si sale de ella
    o cae en una carpeta vetada (mismo mensaje en los dos casos, a propósito)."""
    vault = _vault()
    target = (vault / relative).resolve()
    if (target != vault and vault not in target.parents) or (target != vault and vetada(target)):
        raise RutaNoPermitida("Ruta fuera de la bóveda")
    return target


def listar_md(base: Path) -> list[Path]:
    """Todas las .md bajo base, sin entrar en carpetas vetadas ni en las de sistema.
    Se podan los directorios antes de recorrerlos: lo vetado ni se abre."""
    encontradas = []
    for raiz, dirs, archivos in os.walk(base, followlinks=False):
        r = Path(raiz)
        dirs[:] = sorted(d for d in dirs if d not in IGNORADAS and not vetada(r / d))
        for nombre in archivos:
            p = r / nombre
            if nombre.endswith(".md") and not vetada(p):
                encontradas.append(p)
    return encontradas


def atomic_write(path: Path, texto: str) -> None:
    """Escribe de golpe: archivo temporal + rename. Syncthing nunca ve una nota a medias.

    El temporal usa el prefijo .syncthing.*.tmp, que Syncthing ignora siempre, para que
    no intente sincronizarlo si coincide con un escaneo.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".syncthing.{path.name}.tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.chmod(tmp, 0o664)
    os.replace(tmp, path)
