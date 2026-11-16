"""Configuración centralizada. Todo se puede sobreescribir con variables de entorno MCP_*."""
import os
from dataclasses import dataclass, field
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

    max_rows: int = field(default_factory=lambda: int(_env("MAX_ROWS", "100")))


settings = Settings()
