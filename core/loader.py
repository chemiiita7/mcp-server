"""
Cargador dinámico de herramientas.

Recorre el paquete tools/ (y tools/custom/ si existe), importa cada módulo y llama a
register_tools(mcp). Reglas:
  - Los archivos que empiezan por "_" se ignoran (plantillas, borradores).
  - Si un módulo falla al importar o registrar, se registra el error y el resto sigue.
  - permissions.json decide qué módulos carga cada perfil.
"""
import importlib
import json
import logging
import pkgutil
from pathlib import Path

from core.config import settings

log = logging.getLogger("loader")

TOOL_PACKAGES = ["tools", "tools.custom"]


def _allowed_modules(profile: str) -> set[str] | None:
    """None = todos permitidos. Si no hay permissions.json, se permite todo."""
    path = Path(settings.permissions_file)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = data.get("profiles", {}).get(profile)
    if allowed is None:
        raise ValueError(f"Perfil '{profile}' no definido en {path}")
    return None if "*" in allowed else set(allowed)


def discover() -> list[str]:
    names = []
    for pkg_name in TOOL_PACKAGES:
        try:
            pkg = importlib.import_module(pkg_name)
        except ModuleNotFoundError:
            continue
        for info in pkgutil.iter_modules(pkg.__path__):
            if info.ispkg or info.name.startswith("_"):
                continue
            names.append(f"{pkg_name}.{info.name}")
    return sorted(names)


def load_tools(mcp, profile: str = "admin") -> dict:
    allowed = _allowed_modules(profile)
    report = {"loaded": [], "skipped": [], "failed": {}}

    for full_name in discover():
        short = full_name.removeprefix("tools.")      # "obsidian" o "custom.mi_tool"
        if allowed is not None and short not in allowed:
            report["skipped"].append(short)
            continue
        try:
            module = importlib.import_module(full_name)
            register = getattr(module, "register_tools", None)
            if not callable(register):
                raise AttributeError("no define register_tools(mcp)")
            register(mcp)
            report["loaded"].append(short)
        except Exception as exc:  # un módulo roto no tumba el servidor
            log.exception("Error cargando %s", full_name)
            report["failed"][short] = f"{type(exc).__name__}: {exc}"

    return report
