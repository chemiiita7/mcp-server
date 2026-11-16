# Despliegue continuo

Cada push a `main` lanza [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml):

1. **test** — instala las dependencias, ejecuta `scripts/smoke_test.py` (comprueba que
   todos los módulos de `tools/` cargan en los perfiles `admin` y `agent`) y construye la
   imagen Docker arrancándola para ver que escucha en el 8000.
2. **deploy** — solo si el paso anterior está en verde y no es un pull request:
   entra en el tailnet con la acción de Tailscale (`tag:ci`), monta la clave SSH y ejecuta
   `ssh -p 2222 $VPS_USER@$VPS_HOST "mcp-server"`.
3. Una comprobación final, informativa, mira si el puerto 8000 del VPS responde.

## Secretos (organización `vps-didac`, visibilidad "Public repositories")

| Secreto | Para qué |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` | Cliente OAuth de Tailscale, con la etiqueta `tag:ci` |
| `VPS_HOST` / `VPS_USER` | Destino del SSH (IP del tailnet y usuario) |
| `VPS_SSH_KEY` | Clave privada del deploy, con **comando forzado** en el VPS |
| `VPS_KNOWN_HOSTS` | Huella del host, para no desactivar la verificación |

> La organización está en plan Free: los secretos de organización **solo los leen
> repositorios públicos**. Por eso este repo es público y por eso no hay ni una
> credencial en el código (ver `.env.example`).

## Lo que tiene que haber en el VPS

### 1. El directorio como clon de git

```bash
cd /home/ubuntu/apps/lab-ai/mcp-server
cp -a . ../mcp-server.bak-$(date +%F)          # red de seguridad
git init -b main
git remote add origin https://github.com/vps-didac/mcp-server.git
git fetch origin
git reset --hard origin/main
```

### 2. El `.env` con la contraseña RCON

No está en el repo y el deploy no lo toca (`git reset --hard` no borra ficheros sin
seguimiento). Crearlo una sola vez:

```bash
cd /home/ubuntu/apps/lab-ai/mcp-server
cp .env.example .env
nano .env        # MC_RCON_PASSWORD=...
chmod 600 .env
```

### 3. El nombre `mcp-server` dado de alta en el script del comando forzado

La clave de `VPS_SSH_KEY` está en `~/.ssh/authorized_keys` con
`command="/ruta/al/script",no-pty,no-port-forwarding,...`, y recibe el nombre del
repositorio en `SSH_ORIGINAL_COMMAND`. Hay que añadirle el caso `mcp-server`:

```bash
case "${SSH_ORIGINAL_COMMAND:-}" in
  beira)      dir=/home/ubuntu/apps/beira ;;
  mcp-server) dir=/home/ubuntu/apps/lab-ai/mcp-server ;;
  *) echo "repositorio no permitido: ${SSH_ORIGINAL_COMMAND:-vacío}" >&2; exit 1 ;;
esac

cd "$dir"
git fetch --prune origin
git reset --hard origin/main
docker compose up -d --build
docker compose ps
```

**Importante:** nada de `git clean -fdx` en ese script — borraría el `.env`.

`up -d --build` sirve para los dos casos: si solo cambió un `.py` de `tools/`, la capa
de la imagen se reutiliza y el contenedor se reinicia en segundos; si cambió
`requirements.txt` o el `Dockerfile`, reconstruye.

## Despliegue a mano

Pestaña **Actions → Test y deploy al VPS → Run workflow**. O desde el VPS:

```bash
cd /home/ubuntu/apps/lab-ai/mcp-server && git pull && docker compose up -d --build
```

## Si algo falla

| Síntoma en Actions | Causa probable |
|---|---|
| El paso de Tailscale falla | El cliente OAuth no tiene permiso sobre `tag:ci` en la ACL del tailnet |
| `Host key verification failed` | `VPS_KNOWN_HOSTS` no cubre el puerto 2222 (`ssh-keyscan -p 2222 <host>`) |
| `Permission denied (publickey)` | La pública de `VPS_SSH_KEY` no está en `authorized_keys` del VPS |
| `repositorio no permitido` | Falta el caso `mcp-server` en el script del comando forzado |
| El deploy pasa pero el MCP no levanta | `docker compose logs --tail 50 mcp-lab-server` en el VPS; casi siempre falta el `.env` |
