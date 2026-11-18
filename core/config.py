"""Configuración centralizada. Todo se puede sobreescribir con variables de entorno MCP_*."""
import os
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.getenv(f"MCP_{name}", default)


@dataclass(frozen=True)
class Settings:
    server_name: str = field(default_factory=lambda: _env("SERVER_NAME", "oracle-lab-mcp"))
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))
    transport: str = field(default_factory=lambda: _env("TRANSPORT", "streamable-http"))  # o "sse"
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    # Perfil de permisos (permissions.json): "admin" carga todo, "agent" solo lo seguro
    profile: str = field(default_factory=lambda: _env("PROFILE", "admin"))
    permissions_file: Path = field(default_factory=lambda: Path(_env("PERMISSIONS_FILE", str(ROOT / "permissions.json"))))

    vault_path: Path = field(default_factory=lambda: Path(_env("VAULT_PATH", "/vault")))
    db_path: Path = field(default_factory=lambda: Path(_env("DB_PATH", "/data/personal_data.db")))

    # Carpetas de la bóveda que ninguna tool puede ver (separadas por comas). Ver core/vault.py.
    vault_deny: tuple[str, ...] = field(default_factory=lambda: tuple(
        c.strip() for c in _env("VAULT_DENY", "99_Private").split(",") if c.strip()
    ))

    # Parte diario (core/parte_diario.py): dónde se escribe y cada cuánto se regenera
    daily_dir: str = field(default_factory=lambda: _env("DAILY_DIR", "02_Daily/_sistema"))
    daily_interval_min: int = field(default_factory=lambda: int(_env("DAILY_INTERVAL_MIN", "2")))
    daily_desde: time = field(default_factory=lambda: time.fromisoformat(_env("DAILY_DESDE", "09:00")))
    tz: str = field(default_factory=lambda: _env("TZ", os.getenv("TZ", "Europe/Madrid")))

    max_rows: int = field(default_factory=lambda: int(_env("MAX_ROWS", "100")))


settings = Settings()
