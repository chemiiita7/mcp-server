# mcp-lab-server (modular)

Servidor MCP del VPS: un entrypoint genérico que descubre y carga módulos de `tools/`.
Cada push a `main` se despliega solo en el VPS — ver [DEPLOY.md](DEPLOY.md).

```
mcp-server/
├── server.py            # genérico: config → BD → carga tools/ → run. No se toca.
├── core/
│   ├── config.py        # variables MCP_* (puerto, rutas, perfil...)
│   ├── db.py            # esquema SQLite + conexiones (solo lectura por defecto)
│   └── loader.py        # descubre tools/*.py y tools/custom/*.py y llama register_tools(mcp)
├── tools/
│   ├── obsidian.py      # list / read / append notas
│   ├── health_db.py     # query_personal_db
│   ├── minecraft.py     # control del servidor de Minecraft (RCON + Docker)
│   └── _plantilla.py    # ignorado (empieza por "_"): cópialo para crear un módulo nuevo
├── scripts/
│   └── smoke_test.py    # CI: comprueba que todos los módulos cargan
├── permissions.json     # qué módulos ve cada perfil (admin = todo, agent = lista blanca)
├── Dockerfile
├── docker-compose.yml   # monta ./:/app → editar código = reiniciar, sin build
├── .env.example         # copiar a .env EN EL VPS (contraseña RCON); .env nunca se sube
└── requirements.txt     # mcp fijado a <2 (mcp 2.x renombra FastMCP)
```

## Contrato de un módulo

Cualquier `tools/<nombre>.py` con:

```python
def register_tools(mcp):
    @mcp.tool()
    def mi_herramienta(arg: str) -> str:
        """Descripción para el LLM."""
        ...
```

Si un módulo falla, se registra en el log y el resto del servidor sigue funcionando.
En CI, en cambio, un módulo roto **hace fallar el build y no se despliega**.

## Día a día

| Cambio | Qué hacer |
|---|---|
| Editar/añadir un `.py` en `tools/` | `git push` a `main`: CI comprueba y el VPS reinicia el contenedor |
| Cambiar `requirements.txt` o `Dockerfile` | igual; el deploy hace `up -d --build` y reconstruye |
| Probar antes de empujar | `python scripts/smoke_test.py` |
| Ver qué cargó | en el VPS: `docker compose logs --tail 20 mcp-lab-server` |

Tras un despliegue, comprobar en Antigravity que `http://<IP-tailnet-del-VPS>:8000/mcp`
sigue en verde con sus herramientas.

## Secretos

Este repositorio es **público** (lo exige el plan Free de la organización para poder
usar sus secretos de Actions). No se escribe ninguna credencial en el código:
`MC_RCON_PASSWORD` vive en un `.env` junto al `docker-compose.yml` del VPS, que está
en `.gitignore` y que el despliegue no toca.

## Perfil "agent" (Capa 1, puerto 8001) — cuando toque

Duplicar el servicio en `docker-compose.yml` con `container_name: mcp-lab-agent`,
`MCP_PROFILE: agent` y `MCP_PORT: "8001"`, y apuntar el túnel de Cloudflare a 8001
en lugar de 8000. Mismo código, distinta lista de herramientas.
