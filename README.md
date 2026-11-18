# mcp-lab-server (modular)

Servidor MCP del VPS: un entrypoint genérico que descubre y carga módulos de `tools/`.
Cada push a `main` se despliega solo en el VPS — ver [DEPLOY.md](DEPLOY.md).

```
mcp-server/
├── server.py            # genérico: config → BD → carga tools/ → run. No se toca.
├── core/
│   ├── config.py        # variables MCP_* (puerto, rutas, perfil...)
│   ├── db.py            # conexiones SQLite, solo lectura (el esquema es de vps-didac/ingesta)
│   ├── vault.py         # rutas de la bóveda: nada fuera de ella y nada de 99_Private
│   ├── parte_diario.py  # genera la nota diaria del sistema (también CLI y servicio)
│   └── loader.py        # descubre tools/*.py y tools/custom/*.py y llama register_tools(mcp)
├── tools/
│   ├── obsidian.py      # list / read / append notas
│   ├── health_db.py     # query_personal_db
│   ├── minecraft.py     # control del servidor de Minecraft (RCON + Docker)
│   ├── parte_diario.py  # generar_parte_diario: forzar a mano la nota del sistema
│   └── _plantilla.py    # ignorado (empieza por "_"): cópialo para crear un módulo nuevo
├── scripts/
│   ├── smoke_test.py    # CI: comprueba que todos los módulos cargan
│   └── test_parte_diario.py  # CI: parte diario y carpeta privada, sin red
├── permissions.json     # qué módulos ve cada perfil (admin = todo, agent = lista blanca)
├── Dockerfile
├── docker-compose.yml   # monta ./:/app → editar código = reiniciar, sin build
├── secrets.env.example  # copiar a secrets.env EN EL VPS (contraseña RCON); nunca se sube
└── requirements.txt     # mcp fijado a <2 (mcp 2.x renombra FastMCP)
```

## Datos de salud

`personal_data.db` (Hevy, Garmin) la crea y la rellena el repo
[vps-didac/ingesta](https://github.com/vps-didac/ingesta), que monta la misma carpeta
`/data`. Aquí solo se lee: `query_personal_db` para consultas y `ingest_status` para
saber cuándo fue la última sincronización y si falló. Si allí cambia una tabla, hay que
actualizar la docstring de `query_personal_db` en `tools/health_db.py`.

## Parte diario

El servicio `nota-diaria` (mismo código, contenedor aparte sin red y que solo monta
`02_Daily/_sistema`) escribe `02_Daily/_sistema/YYYY-MM-DD.md`: sueño, HRV, FC en reposo y
Body Battery frente a la media de 7 días, un semáforo con los guardarraíles (rojo: dos
noches < 6 h o HRV bajando 3 días), y los entrenos, pasos y estrés de ese mismo día. La de
hoy aparece a las 9:00 y se reescribe cada vez que la ingesta trae datos (franjas de 9,
12, 17 y 22, reintentos del sueño y webhook de Hevy); la de ayer se sigue completando. Solo reescribe si cambia algo y nunca toca una nota sin `origen: sistema` en el
frontmatter. Tu nota de diario (`02_Daily/YYYY-MM-DD.md`) es tuya y lo incrusta:

```markdown
![[02_Daily/_sistema/{{date:YYYY-MM-DD}}#Parte del día]]
```

## Carpeta privada (99_Private)

Ninguna herramienta puede verla, en dos capas:

1. **Docker**: un `tmpfs` vacío tapa `/vault/99_Private` dentro de `mcp-lab-server`, y
   `nota-diaria` ni siquiera monta la bóveda entera. `deploy.sh` comprueba tras cada
   despliegue que sigue tapada y, si no, para el MCP.
2. **Código**: `core/vault.py` veta las carpetas de `MCP_VAULT_DENY` (por defecto
   `99_Private`, sin distinguir mayúsculas, también por `..` o enlaces simbólicos): no se
   listan, no se leen, no se escriben, y el error es el mismo que el de una ruta fuera de
   la bóveda.

Toda tool nueva que toque archivos de la bóveda tiene que usar `core.vault.safe_path`.

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

Tras un despliegue, comprobar en Antigravity que el MCP sigue en verde con sus
herramientas. El servidor escucha en `0.0.0.0:8000` (`MCP_HOST` en el
`docker-compose.yml`), que con el cortafuegos del VPS significa localhost y tailnet:
vale tanto el túnel de Cloudflare como `http://<IP-tailnet-del-VPS>:8000/mcp` directo.

## Secretos

Este repositorio es **público** (lo exige el plan Free de la organización para poder
usar sus secretos de Actions). No se escribe ninguna credencial en el código:
`MC_RCON_PASSWORD` vive en un `secrets.env` junto al `docker-compose.yml` del VPS, que
está en `.gitignore` y que el despliegue no toca. No puede llamarse `.env`: el
contenedor monta `./:/app`, fastmcp auto-parsea cualquier `/app/.env` y encima corre
como uid 1000 mientras que el usuario del VPS es el 1001.

## Perfil "agent" (Capa 1, puerto 8001) — cuando toque

Duplicar el servicio en `docker-compose.yml` con `container_name: mcp-lab-agent`,
`MCP_PROFILE: agent` y `MCP_PORT: "8001"`, y apuntar el túnel de Cloudflare a 8001
en lugar de 8000. Mismo código, distinta lista de herramientas.
