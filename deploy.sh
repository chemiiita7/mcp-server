#!/usr/bin/env bash
#
# Lo ejecuta el comando forzado del VPS cuando GitHub Actions hace
#   ssh -p 2222 ubuntu@<host> "mcp-server"
# que resuelve a /home/ubuntu/apps/lab-ai/mcp-server/deploy.sh
#
# Todo el cuerpo va dentro de { } a propósito: bash lee el bloque entero antes
# de ejecutarlo, así el "git reset --hard" puede reescribir este mismo archivo
# a mitad de ejecución sin romper nada.

{
  set -euo pipefail

  cd "$(dirname "$(readlink -f "$0")")"
  echo "==> $(pwd)"

  if [ ! -f secrets.env ]; then
    echo "ERROR: falta secrets.env. Cópialo de secrets.env.example y pon MC_RCON_PASSWORD." >&2
    exit 1
  fi

  echo "==> Trayendo main desde origin"
  git fetch --prune origin
  git reset --hard origin/main          # nunca 'git clean': borraría secrets.env
  git --no-pager log --oneline -1

  # Las carpetas que se montan por separado tienen que existir ANTES del "up": si no,
  # Docker las crea como root dentro de la bóveda y Syncthing deja de poder escribir en
  # ellas. Se crean con el uid de los contenedores (1001 = hermes), el dueño de la bóveda.
  VAULT=/home/hermes/vault
  UIDGID=1001:1001
  carpetas=(/vault/99_Private)
  # El parte diario solo cuenta si su servicio está activo (ver "profiles" en el compose)
  parte_diario=0
  if docker compose config --services | grep -qx nota-diaria; then
    parte_diario=1
    carpetas+=(/vault/02_Daily/_sistema)
  fi
  echo "==> Carpetas de la bóveda: ${carpetas[*]}"
  docker run --rm --user "$UIDGID" --network none -v "$VAULT:/vault" python:3.11-slim \
    mkdir -p "${carpetas[@]}"

  # Datos (personal_data.db) y Minecraft: montajes del MCP que también tienen que ser suyos
  docker run --rm --network none -v /home/ubuntu/apps:/apps python:3.11-slim \
    sh -c "mkdir -p /apps/lab-ai/data /apps/minecraft && chown $UIDGID /apps/lab-ai/data /apps/minecraft"

  echo "==> docker compose up -d --build"
  docker compose up -d --build
  docker compose ps

  echo "==> Comprobando que responde"
  code=000
  for _ in $(seq 1 20); do
    # 406 es lo normal: streamable-http rechaza un GET pelado, pero significa
    # que el servidor está vivo. 000 es que ni siquiera conecta.
    code="$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8000/mcp || true)"
    [ "$code" != "000" ] && break
    sleep 2
  done

  echo "==> Logs del arranque"
  docker compose logs --tail 25 mcp-lab-server

  if [ "$code" = "000" ]; then
    echo "ERROR: el servidor no responde en 127.0.0.1:8000 tras el despliegue." >&2
    exit 1
  fi
  echo "OK: el MCP responde (HTTP $code)."

  echo "==> 99_Private tapado dentro del MCP"
  tipo="$(docker compose exec -T mcp-lab-server stat -f -c %T /vault/99_Private || true)"
  visibles="$(docker compose exec -T mcp-lab-server sh -c 'ls -A /vault/99_Private | wc -l' || echo '?')"
  if [ "$tipo" != "tmpfs" ] || [ "$visibles" != "0" ]; then
    echo "ERROR: /vault/99_Private no está tapado dentro del MCP (tipo=$tipo, archivos=$visibles)." >&2
    echo "       Paro el contenedor para no exponer el diario privado." >&2
    docker compose stop mcp-lab-server
    exit 1
  fi
  echo "OK: el MCP ve 99_Private vacío (tmpfs)."

  echo "==> Parte diario"
  if [ "$parte_diario" = 1 ]; then
    sleep 5
    if [ "$(docker inspect -f '{{.State.Status}}' nota-diaria)" != "running" ]; then
      echo "ERROR: el contenedor nota-diaria no está en marcha." >&2
      docker compose logs --tail 30 nota-diaria
      exit 1
    fi
    docker compose logs --tail 5 nota-diaria
  else
    echo "Desactivado (profiles en docker-compose.yml)."
  fi

  exit 0
}
