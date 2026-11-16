# Despliegue continuo

Cada push a `main` lanza [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml):

1. **test** — instala las dependencias, ejecuta `scripts/smoke_test.py` (comprueba que
   todos los módulos de `tools/` cargan en los perfiles `admin` y `agent`) y construye la
   imagen Docker arrancándola para ver que escucha en el 8000.
2. **deploy** — solo si lo anterior está en verde y no es un pull request: entra en el
   tailnet con la acción de Tailscale (`tag:ci`), monta la clave SSH y ejecuta
   `ssh -p 2222 $VPS_USER@$VPS_HOST "mcp-server"`.
3. El propio `deploy.sh` comprueba al final que el MCP responde en `127.0.0.1:8000`
   y devuelve error si no, así que un despliegue en verde significa servidor vivo.

## Cómo llega el push al contenedor

La clave de `VPS_SSH_KEY` tiene un **comando forzado** en el `authorized_keys` del VPS:
no da shell, solo acepta el nombre de un repositorio y lo traduce a un script.
`mcp-server` ya está dado de alta y resuelve a:

```
/home/ubuntu/apps/lab-ai/mcp-server/deploy.sh
```

Ese script es [`deploy.sh`](deploy.sh), que viaja **dentro de este repositorio**: trae
`origin/main`, hace `docker compose up -d --build`, comprueba que el MCP responde y
deja los logs del arranque en la salida del job de Actions. Es decir, el propio repo decide cómo se despliega, y para
cambiarlo basta con editar `deploy.sh` y empujar.

`up -d --build` sirve para los dos casos: si solo cambió un `.py` de `tools/`, las capas
de la imagen se reutilizan y el contenedor se reinicia en segundos; si cambió
`requirements.txt` o el `Dockerfile`, reconstruye.

## Secretos (organización `vps-didac`, visibilidad "Public repositories")

| Secreto | Para qué |
|---|---|
| `TS_OAUTH_CLIENT_ID` / `TS_OAUTH_SECRET` | Cliente OAuth de Tailscale, con la etiqueta `tag:ci` |
| `VPS_HOST` / `VPS_USER` | Destino del SSH (IP del tailnet y usuario) |
| `VPS_SSH_KEY` | Clave privada del deploy, atada al comando forzado |
| `VPS_KNOWN_HOSTS` | Huella del host, para no desactivar la verificación |

> La organización está en plan Free: los secretos de organización **solo los leen
> repositorios públicos**. Por eso este repo es público y por eso no hay ni una
> credencial en el código (ver `secrets.env.example`).

## Puesta en marcha en el VPS (una sola vez)

El directorio del VPS todavía es una copia suelta de archivos. Hay que convertirlo en
un clon de este repositorio para que exista `deploy.sh` y funcione el `git reset`:

```bash
cd /home/ubuntu/apps/lab-ai
cp -a mcp-server mcp-server.bak-$(date +%F)        # red de seguridad
cd mcp-server
git init -b main
git remote add origin https://github.com/vps-didac/mcp-server.git
git fetch origin
git reset --hard origin/main                        # sobrescribe con lo del repo
```

Y crear el `secrets.env` con la contraseña RCON, que ya no está en
`docker-compose.yml` (este repo es público). No se sube nunca y el deploy no lo toca,
porque `git reset --hard` no borra archivos sin seguimiento:

```bash
cp secrets.env.example secrets.env
nano secrets.env          # MC_RCON_PASSWORD=la-de-siempre
chmod 600 secrets.env
```

> **Por qué `secrets.env` y no `.env`.** El contenedor monta `./:/app`, y fastmcp
> auto-parsea cualquier `/app/.env` al arrancar. Además corre como `user: "1000:1000"`
> mientras que el usuario del VPS es uid **1001**, así que un `.env` con permisos 600
> es ilegible desde dentro y el servidor muere con `PermissionError` en bucle. Con
> `env_file: secrets.env` lo lee docker compose en el host y al contenedor solo le
> llegan las variables.

Comprobarlo a mano antes de confiar en Actions:

```bash
./deploy.sh
```

A partir de ahí, **Actions → Test y deploy al VPS → Run workflow** (o cualquier push
a `main`) hace lo mismo solo.

## Cosas sabidas

- **Las versiones de `requirements.txt` están fijadas.** Con despliegue continuo cada
  push reconstruye la imagen, y dependencias flotantes significan que un día cualquiera
  el servidor se levanta con una versión mayor distinta. Ya pasó con `mcp` 2.x. Para
  actualizar: cambia la versión, empuja y mira el job de tests.
- **`MCP_HOST` no hace nada.** `server.py` lo pasa por la variable `FASTMCP_HOST`, que
  mcp 1.30 ignora, así que el servidor siempre escucha en `127.0.0.1:8000` pase lo que
  pase en la configuración. No molesta porque quien lo expone es el túnel de Cloudflare,
  pero el día que haga falta alcanzarlo por el tailnet hay que pasarle el host al
  constructor: `FastMCP(nombre, host=..., port=...)`.

## Si algo falla

| Síntoma en Actions | Causa probable |
|---|---|
| `deploy.sh: No such file or directory` | Falta la puesta en marcha de arriba: el directorio del VPS aún no es un clon |
| `ERROR: falta secrets.env` | Falta el `secrets.env` junto al `docker-compose.yml` del VPS |
| El paso de Tailscale falla | El cliente OAuth no tiene permiso sobre `tag:ci` en la ACL del tailnet |
| `Host key verification failed` | `VPS_KNOWN_HOSTS` no cubre el puerto 2222 (`ssh-keyscan -p 2222 <host>`) |
| `Permission denied (publickey)` | La pública de `VPS_SSH_KEY` no está en el `authorized_keys` del VPS |
| El deploy pasa pero el MCP no levanta | Los logs salen al final del propio job; si no, `docker compose logs --tail 50 mcp-lab-server` |
